"""Allocating orders to warehouses (ALLOCATION.md).

State is rebuilt from events.jsonl: "allocated", "confirmed" and "cancelled" events, each
carrying everything needed to apply it again.
"""

import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from fulfil.catalog import load
from fulfil.geo import distance_km

HOLD = timedelta(minutes=30)


class ReservationError(Exception):
    pass


def _offset(text):
    sign = -1 if text.startswith("-") else 1
    hours, minutes = text.lstrip("+-").split(":")
    return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))


def ship_date(now, warehouse):
    local = now.astimezone(_offset(warehouse["utc_offset"]))
    cutoff = time.fromisoformat(warehouse["cutoff"])
    day = local.date()
    if local.weekday() < 5 and local.time() < cutoff:
        return day
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


class Fulfilment:
    def __init__(self, data_dir, clock):
        self.dir = Path(data_dir)
        self.clock = clock
        warehouses, stock, inbound = load(self.dir)
        self.warehouses = sorted(warehouses, key=lambda w: w["id"])
        self.stock = {w["id"]: dict(stock.get(w["id"], {})) for w in self.warehouses}
        self.inbound = sorted(enumerate(inbound), key=lambda p: (p[1]["arrives"], p[1]["warehouse"], p[0]))
        self.promised = {}  # inbound index -> units promised
        self.orders = {}  # order id -> {"status", "expires", "plan", "holds", "promises"}
        self.events = self.dir / "events.jsonl"
        if self.events.exists():
            for line in self.events.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._apply(json.loads(line))

    # --- events ----------------------------------------------------------------------------

    def _record(self, event):
        self._apply(event)
        with self.events.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def _release_promises(self, entry):
        for idx, qty in entry.get("promises", []):
            self.promised[idx] -= qty
        entry["promises"] = []

    def _apply(self, event):
        oid = event["order"]
        if event["type"] == "allocated":
            old = self.orders.get(oid)
            if old:
                self._release_promises(old)
            for idx, qty in event["promises"]:
                self.promised[idx] = self.promised.get(idx, 0) + qty
            self.orders[oid] = {"status": "active", "expires": event["expires"],
                                "plan": event["plan"], "holds": event["holds"],
                                "promises": [list(p) for p in event["promises"]]}
        elif event["type"] == "confirmed":
            entry = self.orders[oid]
            for wid, lines in entry["holds"].items():
                for sku, qty in lines.items():
                    self.stock[wid][sku] = self.stock[wid].get(sku, 0) - qty
            entry["status"] = "confirmed"
        elif event["type"] == "cancelled":
            entry = self.orders[oid]
            self._release_promises(entry)
            entry["status"] = "cancelled"

    # --- reading ---------------------------------------------------------------------------

    def _active(self, entry, now):
        return entry["status"] == "active" and now < datetime.fromisoformat(entry["expires"])

    def _available(self, now):
        avail = {wid: dict(skus) for wid, skus in self.stock.items()}
        for entry in self.orders.values():
            if self._active(entry, now):
                for wid, lines in entry["holds"].items():
                    for sku, qty in lines.items():
                        avail[wid][sku] = avail[wid].get(sku, 0) - qty
        return avail

    def availability(self, sku):
        avail = self._available(self.clock())
        return {wid: avail[wid].get(sku, 0) for wid in sorted(avail)}

    # --- allocating ------------------------------------------------------------------------

    def allocate(self, order):
        now = self.clock()
        oid = order["id"]
        entry = self.orders.get(oid)
        if entry and entry["status"] == "confirmed":
            raise ReservationError(f"order {oid} is already confirmed")
        if entry and self._active(entry, now):
            return entry["plan"]
        avail = self._available(now)
        lines = order["lines"]
        to = order["ship_to"]
        dist = {w["id"]: distance_km(w["lat"], w["lon"], to["lat"], to["lon"]) for w in self.warehouses}
        need = [l["qty"] for l in lines]
        chosen = []  # (warehouse id, [qty per line])
        full = [w["id"] for w in self.warehouses
                if all(avail[w["id"]].get(l["sku"], 0) >= l["qty"] for l in lines)]
        if full:
            wid = min(full, key=lambda w: (dist[w], w))
            chosen.append((wid, list(need)))
            need = [0] * len(lines)
        else:
            left = {wid: dict(skus) for wid, skus in avail.items()}
            while any(need):
                def supply(wid):
                    return sum(min(max(left[wid].get(l["sku"], 0), 0), n) for l, n in zip(lines, need))
                best = min(self.warehouses, key=lambda w: (-supply(w["id"]), dist[w["id"]], w["id"]))
                if supply(best["id"]) == 0:
                    break
                give = [min(max(left[best["id"]].get(l["sku"], 0), 0), n) for l, n in zip(lines, need)]
                for l, g in zip(lines, give):
                    left[best["id"]][l["sku"]] = left[best["id"]].get(l["sku"], 0) - g
                need = [n - g for n, g in zip(need, give)]
                chosen.append((best["id"], give))

        by_id = {w["id"]: w for w in self.warehouses}
        shipments, holds = [], {}
        for wid, give in chosen:
            shipments.append({"warehouse": wid,
                              "lines": [{"sku": l["sku"], "qty": g} for l, g in zip(lines, give) if g],
                              "distance_km": round(dist[wid], 1),
                              "ships_on": ship_date(now, by_id[wid]).isoformat()})
            for l, g in zip(lines, give):
                if g:
                    holds.setdefault(wid, {})
                    holds[wid][l["sku"]] = holds[wid].get(l["sku"], 0) + g

        # backorders: the old promises of this order don't count against it
        promised = dict(self.promised)
        if entry:
            for idx, qty in entry.get("promises", []):
                promised[idx] -= qty
        backorders, promises = [], []
        for l, n in zip(lines, need):
            if n <= 0:
                continue
            gathered, used, expected = 0, [], None
            for idx, inb in self.inbound:
                if inb["sku"] != l["sku"]:
                    continue
                free = inb["qty"] - promised.get(idx, 0)
                if free <= 0:
                    continue
                take = min(free, n - gathered)
                used.append([idx, take])
                gathered += take
                if gathered >= n:
                    expected = inb["arrives"]
                    break
            if expected is not None:
                for idx, take in used:
                    promised[idx] = promised.get(idx, 0) + take
                promises += used
            backorders.append({"sku": l["sku"], "qty": n, "expected": expected})

        plan = {"order": oid, "reservation": f"R-{oid}", "shipments": shipments,
                "backorders": backorders}
        self._record({"type": "allocated", "order": oid, "at": now.isoformat(),
                      "expires": (now + HOLD).isoformat(), "plan": plan, "holds": holds,
                      "promises": promises})
        return plan

    def _require_active(self, order_id):
        entry = self.orders.get(order_id)
        if entry is None or not self._active(entry, self.clock()):
            raise ReservationError(f"order {order_id} has no active reservation")
        return entry

    def confirm(self, order_id):
        self._require_active(order_id)
        self._record({"type": "confirmed", "order": order_id, "at": self.clock().isoformat()})
        return {"status": "confirmed"}

    def cancel(self, order_id):
        self._require_active(order_id)
        self._record({"type": "cancelled", "order": order_id, "at": self.clock().isoformat()})
        return {"status": "cancelled"}

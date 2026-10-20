# Order allocation (approved spec)

`fulfil.service.Fulfilment(data_dir, clock)` decides where each order ships from, holds the
stock while the customer pays, and promises dates for what isn't in stock. `clock()`
returns the current time as an aware `datetime`.

## Data

`data_dir` holds, as the catalog team exports them (`fulfil/catalog.py` reads them):

- `warehouses.json`: `[{"id", "name", "lat", "lon", "utc_offset" ("+01:00"), "cutoff"
  ("15:00")}]`
- `stock.json`: `{"<warehouse id>": {"<sku>": units}}` (a missing SKU is 0)
- `inbound.json`: `[{"warehouse", "sku", "qty", "arrives" ("YYYY-MM-DD")}]`

The service writes its own state to `events.jsonl` in the same folder (below).

## Available stock

A warehouse's **available** units of a SKU are its stock minus the units held by **active**
reservations there. A reservation is active until it is confirmed, cancelled, or reaches
its expiry time (at the expiry time it no longer holds anything).

`availability(sku)` returns `{"<warehouse id>": available units}` for every warehouse.

## Allocating an order

`allocate(order)` takes `{"id", "lines": [{"sku", "qty"}], "ship_to": {"lat", "lon"}}` and
returns a **plan**. Distances are great-circle distances (`fulfil.geo.distance_km`) from the
warehouse to `ship_to`.

1. **One warehouse.** If some warehouses can supply every line in full, ship everything
   from the nearest of them (ties: lowest warehouse id).
2. **Split.** Otherwise, repeat: take the warehouse that can supply the most of what is
   still needed (the sum over lines of `min(available, still needed)`; ties: nearer, then
   lowest id), and give it, line by line, `min(available, still needed)`. Stop when nothing
   is needed or no warehouse has any of it.
3. **Backorders.** What's left of each line is backordered. Its **expected** date: go through
   the inbound deliveries of that SKU (any warehouse) by date, then warehouse id, using only
   the units not promised to earlier backorders; it's the date at which the units you've
   gathered first cover the rest of the line. Those units are now promised to this order.
   If all inbound units together aren't enough, the date is `null` and nothing is promised.
4. **Holding stock.** The plan's shipments are held by one reservation, `R-<order id>`,
   which expires **30 minutes** after the allocation.

The plan:

```json
{"order": "A-1", "reservation": "R-A-1",
 "shipments": [{"warehouse": "W2", "lines": [{"sku": "S1", "qty": 3}],
                "distance_km": 412.7, "ships_on": "2026-06-02"}],
 "backorders": [{"sku": "S9", "qty": 2, "expected": "2026-06-10"}]}
```

- Shipments are in the order the warehouses were chosen; a shipment's lines follow the
  order's line order and leave out lines it supplies nothing of. `distance_km` is rounded
  to one decimal.
- Backorders follow the order's line order, only for lines with something left.
- **Ship date**: in the warehouse's local time (its `utc_offset`), an allocation on a
  weekday before the warehouse's `cutoff` ships that day; otherwise it ships on the next
  weekday.

**Again.** Allocating an order whose reservation is still active returns the same plan
again and changes nothing. Once its reservation has expired or was cancelled, allocating it
works out a new plan; the old backorder promises are given up first. Allocating a confirmed
order raises `ReservationError`.

## After allocating

- `confirm(order_id)`: the stock leaves the warehouses (stock goes down by the reserved
  units) and the reservation stops holding anything. Returns `{"status": "confirmed"}`.
  Backorder promises stay.
- `cancel(order_id)`: the reservation and the order's backorder promises are released.
  Returns `{"status": "cancelled"}`.
- Both raise `fulfil.service.ReservationError` when the order has no active reservation
  (never allocated, expired, already confirmed or cancelled).

## Surviving a restart

Every change is appended to `events.jsonl` as one JSON object per line. A new
`Fulfilment` on the same folder replays them and continues exactly where the old one
stopped (availability, reservations, promises, confirmed stock). `stock.json` is never
written: confirmed stock lives in the events.

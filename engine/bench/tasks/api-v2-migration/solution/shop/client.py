"""The SDK other teams use. A transport is a function
transport(method, path, headers, query, body) -> Response (shop.app.Response)."""

import uuid


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"{status}: {body}")
        self.status = status
        self.body = body
        error = body.get("error") if isinstance(body, dict) else None
        self.code = error.get("code") if isinstance(error, dict) else None


class Client:
    """The v1 client."""

    def __init__(self, transport):
        self.transport = transport

    def _call(self, method, path, query=None, body=None):
        r = self.transport(method, path, {}, query or {}, body)
        if r.status >= 400:
            raise ApiError(r.status, r.body)
        return r.body

    def list_orders(self, page=1, per_page=20, status=None):
        query = {"page": page, "per_page": per_page}
        if status:
            query["status"] = status
        return self._call("GET", "/v1/orders", query)["orders"]

    def get_order(self, order_id):
        return self._call("GET", f"/v1/orders/{order_id}")

    def create_order(self, customer, items):
        return self._call("POST", "/v1/orders", body={"customer": customer, "items": items})

    def cancel_order(self, order_id):
        return self._call("POST", f"/v1/orders/{order_id}/cancel")


class ClientV2:
    def __init__(self, transport, retries=2):
        self.transport = transport
        self.retries = retries

    def _call(self, method, path, query=None, body=None, headers=None, retry=False):
        tries = 1 + (self.retries if retry else 0)
        for attempt in range(tries):
            try:
                r = self.transport(method, path, dict(headers or {}), query or {}, body)
            except ConnectionError:
                if attempt + 1 == tries:
                    raise
                continue
            if r.status == 503 and attempt + 1 < tries:
                continue
            if r.status >= 400:
                raise ApiError(r.status, r.body)
            return r.body
        raise AssertionError("unreachable")

    def iter_orders(self, status=None, customer_id=None, limit=100):
        query = {"limit": limit}
        if status:
            query["status"] = status
        if customer_id:
            query["customer_id"] = customer_id
        while True:
            page = self._call("GET", "/v2/orders", query)
            yield from page["data"]
            if not page["next_cursor"]:
                return
            query = {**query, "cursor": page["next_cursor"]}

    def get_order(self, order_id):
        return self._call("GET", f"/v2/orders/{order_id}")

    def cancel_order(self, order_id):
        return self._call("POST", f"/v2/orders/{order_id}/cancel")

    def create_order(self, customer_id, currency, lines, idempotency_key=None):
        key = idempotency_key or str(uuid.uuid4())
        body = {"customer_id": customer_id, "currency": currency, "lines": lines}
        return self._call("POST", "/v2/orders", body=body, headers={"Idempotency-Key": key},
                          retry=True)

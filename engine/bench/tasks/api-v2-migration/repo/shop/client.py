"""The SDK other teams use. A transport is a function
transport(method, path, headers, query, body) -> Response (shop.app.Response)."""


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"{status}: {body}")
        self.status = status
        self.body = body


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

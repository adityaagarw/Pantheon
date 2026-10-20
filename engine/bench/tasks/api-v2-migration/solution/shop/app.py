"""The service's front door: one request in, one response out (the HTTP server, not part
of this repo, turns real requests into calls to App.dispatch)."""

from dataclasses import dataclass, field

from shop import api_v1, api_v2

DEPRECATION = {
    "Deprecation": "true",
    "Sunset": "Wed, 30 Sep 2026 00:00:00 GMT",
    "Link": '</v2/orders>; rel="successor-version"',
}


@dataclass
class Response:
    status: int
    body: dict
    headers: dict = field(default_factory=dict)


class App:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock  # a function returning the current UTC datetime (tests set it)

    def dispatch(self, method, path, headers=None, query=None, body=None):
        now = self.clock() if self.clock else None
        if path.startswith("/v1/"):
            status, data = api_v1.handle(self.store, method, path, query, body, now=now)
            return Response(status, data, dict(DEPRECATION))
        if path.startswith("/v2/"):
            status, data, extra = api_v2.handle(self.store, method, path, headers, query, body,
                                                now=now)
            return Response(status, data, extra)
        return Response(404, {"error": "not found"})

Ship the orders API v2 as designed in `API_V2.md`: the new storage schema and its
migration command, the v2 endpoints (with cursor paging, validation, idempotency and
cancel), the v1 deprecation headers, and `ClientV2` in the SDK.

v1 stays up until its sunset and three teams compare its responses byte for byte, so
**v1 must answer exactly as it does today**, before and after the migration (only the
deprecation headers are new). `data/orders.jsonl` is a copy of production's orders and
has the quirks of years of v1: read it before deciding anything about money or times.

Do this:

1. Agree the storage shape and the v2 resource first; then storage and migration, the
   v2 endpoints, idempotency and the SDK can be built at the same time.
2. Keep `data/orders.jsonl` as it is (the operations team migrates production).
3. Add tests in `tests/` (unittest) for v1 compatibility and every v2 rule.

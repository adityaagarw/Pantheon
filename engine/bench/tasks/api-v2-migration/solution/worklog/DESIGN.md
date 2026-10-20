# Design

- Storage: schema 2 rows (API_V2.md). `Store` upgrades v1 rows on read: money via
  `Decimal(str(value)) * 100` (never float maths; 19.99 stored as a JSON number is 1999),
  `canceled` -> `cancelled`, `created` (with or without offset) -> UTC `...Z`.
- v1 serializes schema 2 rows back to exactly what it showed before.
- v2: keyset cursors carry (created_at, id), the filters and the newest id at the first
  page, so orders created later never show up, even with a clock running late.
- Idempotency keys: idempotency.json next to the orders file, written atomically.
- Owners: storage + migration, v1, v2 endpoints, SDK.

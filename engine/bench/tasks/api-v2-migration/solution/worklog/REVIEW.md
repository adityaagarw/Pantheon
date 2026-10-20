# Review against API_V2.md

- Storage and migration: idempotent (second run: 0, same bytes), atomic, exact money.
- Paging: complete, stable, filter-bound cursors, bad cursor/limit fields.
- Create: validation order and fields, booleans rejected, idempotency (replay header,
  conflict, restart, any header case, failed validation doesn't use the key).
- Cancel rules, 404 and 405 codes, v1 headers, SDK retries with one key.

# Orders API v2 (approved design)

v2 replaces v1. Both run side by side until v1's sunset; **v1's responses must not change**
(other teams compare them byte for byte), except for the deprecation headers below.

## Storage (schema 2)

Orders stay in the JSON Lines file, one per line, now in this shape:

```json
{"schema": 2, "id": 17, "customer_id": "c-12", "currency": "USD",
 "lines": [{"sku": "A1", "quantity": 2, "unit_price": 999}],
 "total": 1998, "status": "paid", "created_at": "2026-01-05T10:22:01Z"}
```

- Money is an integer number of **minor units** (cents). `total` is the sum of
  `quantity × unit_price`.
- v1 rows have no `currency` (they are `USD`) or a `currency` field; keep it.
- `status` is `pending`, `paid`, `shipped` or `cancelled`. Old rows say `canceled`.
- `created_at` is UTC, `YYYY-MM-DDTHH:MM:SSZ`. v1 rows have `created` without an offset
  (UTC), or with one (`2026-01-05T11:22:01+01:00`).

`Store` reads rows of both shapes and always writes schema 2.

**Migration.** `python -m shop.migrate <orders file>` rewrites the file in schema 2. It
replaces the file in one step (write a new file, then rename it over the old one), prints
`migrated N of M orders` (`N` rows were v1), and can run again safely: a second run
migrates 0 and leaves the file byte for byte the same.

## The v2 resource

```json
{"id": "ord_17", "customer_id": "c-12", "currency": "USD",
 "lines": [{"sku": "A1", "quantity": 2, "unit_price": {"amount": 999, "currency": "USD"}}],
 "total": {"amount": 1998, "currency": "USD"},
 "status": "paid", "created_at": "2026-01-05T10:22:01Z"}
```

## Errors

Every v2 error body is `{"error": {"code": ..., "message": ...}}`, plus `"field"` for
`invalid_request`:

| status | code | when |
| --- | --- | --- |
| 400 | `invalid_request` | a parameter or the body is wrong; `field` names it |
| 404 | `not_found` | no such order or route (including ids like `17` or `ord_x`) |
| 405 | `method_not_allowed` | the route exists, the method doesn't |
| 409 | `invalid_state` | the order can't do that now |
| 422 | `idempotency_conflict` | an `Idempotency-Key` was used before with another body |

## Endpoints

### `GET /v2/orders`

Query: `limit` (1 to 100, default 25), `cursor`, `status`, `customer_id`.

- Newest first: by `created_at`, then `id`, both descending.
- Answers `{"data": [orders], "next_cursor": string or null}`. `next_cursor` is `null` on
  the last page.
- Paging is **stable**: following `next_cursor` gives every order that matched when the
  first page was read exactly once, and never one created after it. Orders may be created
  between page requests.
- A cursor only works with the filters it was made with. A malformed cursor, or one used
  with other filters, is `invalid_request` with `field: "cursor"`. A bad `limit` is
  `invalid_request` with `field: "limit"`.

### `GET /v2/orders/{id}`

The order, or `not_found`.

### `POST /v2/orders`

Body: `{"customer_id": string, "currency": string, "lines": [{"sku": string, "quantity": int,
"unit_price": int}]}`. Creates a `pending` order (status 201) with `created_at` now, and
computes `total`.

Validation, checked in this order, reporting the first problem found:

1. `customer_id`: a non-empty string.
2. `currency`: three uppercase letters (`USD`).
3. `lines`: a non-empty list.
4. For each line in order: `sku` a non-empty string, `quantity` an integer of at least 1,
   `unit_price` an integer of at least 0. Booleans are not integers. The field is written
   `lines[0].quantity`.

**Idempotency.** With an `Idempotency-Key` header (any case of the header name):

- the first request with a key creates the order as usual;
- the same key with the same body (the same JSON value) answers exactly as the first time
  (status 201 and the same body) with the header `Idempotent-Replayed: true`, and creates
  nothing;
- the same key with a different body is `idempotency_conflict`;
- keys are remembered across restarts (a new `Store` on the same file knows them): keep
  them next to the orders file, in `idempotency.json`;
- a request that fails validation doesn't use up its key.

### `POST /v2/orders/{id}/cancel`

`pending` and `paid` orders become `cancelled` (200, the order). Cancelling a cancelled
order answers 200 with the order unchanged. A `shipped` order is `invalid_state`.

## v1 deprecation

Every v1 response gets these headers, its status and body unchanged:

```
Deprecation: true
Sunset: Wed, 30 Sep 2026 00:00:00 GMT
Link: </v2/orders>; rel="successor-version"
```

## SDK: `shop.client.ClientV2`

`ClientV2(transport, retries=2)`, next to the v1 `Client`:

- `iter_orders(status=None, customer_id=None, limit=100)`: yields every order, following
  the cursors.
- `get_order(order_id)`, `cancel_order(order_id)`: the order.
- `create_order(customer_id, currency, lines, idempotency_key=None)`: the new order. It
  makes up a key (a UUID) when none is given. If the transport raises `ConnectionError` or
  answers 503, it tries again with **the same key**, up to `retries` more times.
- Errors raise `shop.client.ApiError` with `status`, `body` and `code` (the v2 error code).

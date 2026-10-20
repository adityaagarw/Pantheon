# Orders service

The orders service behind the shop. `shop/app.py` is the front door: the HTTP server (not
in this repo) turns each request into `App.dispatch(method, path, headers, query, body)`.

- `shop/api_v1.py`: the v1 API, used by the storefront, the warehouse and finance.
- `shop/store.py`: orders in a JSON Lines file.
- `shop/client.py`: the SDK other teams use.
- `data/orders.jsonl`: a copy of production's orders, for development. Production's file
  is migrated by the operations team with the migration command; don't edit this copy.
- `API_V2.md`: the approved v2 design.

```
python -m unittest discover -s tests
```

Python 3.10+, standard library only.

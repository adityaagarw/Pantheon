# Fulfilment

Decides which warehouse ships each order. `ALLOCATION.md` is the approved spec for
`fulfil.service.Fulfilment`, which checkout calls when a customer places an order.

- `fulfil/catalog.py` reads the catalog team's exports; `fulfil/geo.py` measures distances.
- `data/` holds development exports (four warehouses).

```
python -m unittest discover -s tests
```

Python 3.10+, standard library only.

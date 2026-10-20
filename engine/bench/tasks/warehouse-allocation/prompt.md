Build order allocation as specified in `ALLOCATION.md`: `fulfil.service.Fulfilment`
with single-warehouse and split shipments, backorders promised against inbound deliveries,
reservations that hold stock for 30 minutes, ship dates by each warehouse's local cut-off,
confirm and cancel, and an event log that survives restarts.

Checkout depends on every rule being exact, including ties, boundaries (cut-off times,
expiry times) and what happens when the same order is allocated twice.

Plan it first: decide the state and the events, then build allocation, reservations and
promises, ship dates and the event log, and review the whole against the spec. Add tests in
`tests/` (unittest) for each rule. Don't change `ALLOCATION.md`, `fulfil/geo.py` or
`fulfil/catalog.py`.

# Plan

State: stock per warehouse (minus confirmed), orders (status, expiry, plan, holds,
promises), units promised per inbound delivery. Events: allocated (with plan, holds and
promises), confirmed, cancelled; replayed on start.

Parts: allocation (single, greedy split, ties), backorder promises, reservations and
expiry, ship dates, event log. Each with tests against ALLOCATION.md.

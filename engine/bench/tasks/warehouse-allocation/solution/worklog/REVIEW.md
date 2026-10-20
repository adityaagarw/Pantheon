# Review against ALLOCATION.md

Checked: nearest single warehouse, greedy split ties (supply, then distance, then id),
lines left out of shipments they don't supply, backorder dates across warehouses and
promise accounting (cancel and re-allocation release), holds ending exactly at 30 minutes,
idempotent re-allocation, errors for confirm and cancel, ship dates around cut-offs in
local time and weekends, and restarts rebuilding the same state without touching stock.json.

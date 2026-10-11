# Incident INC-0914: evidence pack

On 2026-09-14 customers could not place orders for some time.
This folder has the evidence that the on-call team collected.

## System overview

```
            +--> catalog  (products, search) --> catalog-cache (Redis), read replica
            |
client --> api-gateway --> accounts (users) --> read replica
            |
            +--> orders   (orders, checkout) --> orders-db (primary, connection pool "orders-db")
            |
            +--> payments (payments) --> payments-db, external bank API
```

- `/api/checkout` calls the `orders` service.
- `catalog` and `accounts` do not use `orders-db`.
- The `orders-db` connection pool has a fixed maximum size. A request waits up to
  5000 ms for a free connection, then it fails.

## Files

| File | Content |
| --- | --- |
| `logs/api-gateway.log` | One line for each request that the gateway handled. |
| `logs/orders.log` | Logs of the orders service. |
| `logs/payments.log` | Logs of the payments service. |
| `logs/catalog.log` | Warnings of the catalog service. |
| `logs/db-pool.log` | State of the `orders-db` connection pool, every 15 seconds. |
| `logs/deploys.log` | Deploys, rollbacks and manual actions. |
| `alerts.json` | Alerts that fired during the day. |

All times are UTC.

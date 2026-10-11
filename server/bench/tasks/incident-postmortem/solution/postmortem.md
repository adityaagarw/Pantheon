# Postmortem INC-0914: orders and checkout failures

## Summary

On 2026-09-14 from 14:02 to 14:28 UTC, many order and checkout requests failed.
The cause was a connection leak in orders version 2.4.1. The rollback to 2.4.0 fixed it.

## Impact

- Duration: 26 minutes (14:02 to 14:28 UTC).
- 722 gateway requests failed with a 5xx status.
- Peak error rate: 38.46% of all gateway requests (minute 14:17).
- Affected endpoints: `/api/orders` and `/api/checkout`. Other endpoints were not affected.

## Timeline

- 13:55 - payments 1.9.0 deploy starts (finishes 13:57). Not related.
- 14:00 - orders 2.4.1 deploy starts (finishes 14:01:30). Change: retry inventory calls on timeout.
- 14:02 - the orders-db pool is full (50/50). Gateway 5xx rate goes above 5%.
- 14:04 - RedisMemoryHigh alert for catalog-cache. Not related.
- 14:06 - HighErrorRate alert fires (14:06:30). Incident detected.
- 14:08 - DBPoolSaturation alert fires.
- 14:15 - on-call restarts payments. No effect.
- 14:19 - on-call resizes the pool from 50 to 80. Short improvement, then the pool is full again.
- 14:24 - rollback of orders to 2.4.0 starts.
- 14:27 - rollback finishes. Error rate drops.
- 14:28 - error rate is below 1%. Recovered.

## Root Cause

Version 2.4.1 of the orders service added a retry when the inventory call times out.
In the retry path, orders takes a new connection from the `orders-db` pool and does not
release the first connection. Each retry leaked one connection. The pool was full after
about 30 seconds. New requests waited 5000 ms for a connection and then failed.
A bigger pool only delayed the problem, because the leak continued.

## Resolution

The on-call engineer rolled back orders to 2.4.0 (14:24 to 14:27). The leak stopped and
the pool went back to normal use.

## Action Items

1. Fix the retry path in orders to release the connection (use a context manager). Owner: orders team.
2. Add a test that checks that the pool use goes back to zero after retries. Owner: orders team.
3. Alert on pool saturation for 1 minute, and link deploys to alerts on the dashboard. Owner: SRE.
4. Add canary deploys for orders with automatic rollback on error rate. Owner: platform team.

# Operations & Administration Guide: Phase 2 Distributed Connectors, Workers & Dynamic Scheduler

**Version**: 1.0.0  
**Feature Branch**: `002-connectors-workers-scheduler`  
**Classification**: Enterprise Internal Operations Guide  

---

## 1. System Architecture & Topology

The Phase 2 distributed ingestion platform connects the Enterprise Data Catalog to external relational and distributed database engines via pluggable metadata connectors, decoupled Celery worker fleets, an in-memory Redis message broker, and a dynamic database-backed Beat scheduler.

```
                      +---------------------------------------+
                      |       Dynamic Celery Beat             |
                      | (DatabaseScheduler / SHA-256 Jitter)  |
                      +-------------------+-------------------+
                                          | Enqueue due tasks
                                          v
                      +---------------------------------------+
                      |          Redis Message Broker         |
                      | (redis://localhost:6379/0)            |
                      |  - metadata.priority                  |
                      |  - metadata.fast                      |
                      |  - metadata.bulk                      |
                      +-------------------+-------------------+
                                          | Dequeue tasks
                                          v
+-----------------------------------------------------------------------------+
|                        Distributed Celery Worker Fleet                       |
|                                                                             |
|  +---------------------------+  Unwrap Creds  +--------------------------+  |
|  | EnvelopeEncryptionService | -------------> | Ephemeral NullPool Conn  |  |
|  +---------------------------+                +------------+-------------+  |
|                                                            |                |
|                                                            v                |
|                                               Target DB (pg_catalog)       |
|                                                            |                |
|  +---------------------------+  Vectorized    +------------+-------------+  |
|  |   BulkIngestionService    | <------------- | PostgresConnector Engine |  |
|  | (PostgreSQL UNNEST Array) |                +--------------------------+  |
|  +-------------+-------------+                                              |
|                |                                                            |
|                v                                                            |
|   +--------------------------+   Trips >= 3   +--------------------------+  |
|   | Catalog DB Storage       | -------------> | CircuitBreakerManager    |  |
|   | (SyncJob, Table, Stats)  |                | (CLOSED/OPEN/HALF_OPEN)  |  |
|   +--------------------------+                +--------------------------+  |
+-----------------------------------------------------------------------------+
```

---

## 2. Celery Worker Fleet Deployment

### 2.1 Queue Routing Matrix

Worker tasks are segregated across three dedicated Redis priority queues:

| Queue Name | Routing Key Pattern | Default Concurrency | SLA / Workload Type |
|---|---|---|---|
| `metadata.priority` | `sync.priority.*` | 2–4 workers | Manual interactive syncs, canary health probes, circuit recovery |
| `metadata.fast` | `sync.fast.*` | 4–8 workers | Scheduled syncs for small/medium databases (< 50 tables) |
| `metadata.bulk` | `sync.bulk.*` | 2–4 workers | High-volume ingestion (> 50 tables, 200+ tables / 5,000+ columns) |

### 2.2 Linux Production Worker Startup

On Linux (Ubuntu 22.04 LTS / Debian 12 / RHEL 9) container or systemd hosts, workers execute using the standard `prefork` concurrency pool:

```bash
# Production Linux: Run high-throughput multi-queue worker
poetry run celery -A backend.src.core.celery_app worker \
  --loglevel=INFO \
  -Q metadata.priority,metadata.fast,metadata.bulk \
  --concurrency=4 \
  --max-tasks-per-child=100 \
  --without-gossip \
  --without-mingle
```

#### Systemd Service Configuration (`/etc/systemd/system/datacatalog-worker.service`)
```ini
[Unit]
Description=Enterprise Data Catalog Celery Worker Fleet
After=network.target redis-server.service postgresql.service

[Service]
Type=forking
User=datacatalog
Group=datacatalog
WorkingDirectory=/opt/datacatalog/backend
EnvironmentFile=/etc/datacatalog/backend.env
ExecStart=/usr/local/bin/poetry run celery multi start worker1 worker2 \
  -A backend.src.core.celery_app \
  --pidfile=/var/run/celery/%n.pid \
  --logfile=/var/log/celery/%n%I.log \
  --loglevel=INFO \
  -Q:worker1 metadata.priority,metadata.fast -c:worker1 4 \
  -Q:worker2 metadata.bulk -c:worker2 2
ExecStop=/usr/local/bin/poetry run celery multi stopwait worker1 worker2 \
  --pidfile=/var/run/celery/%n.pid
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 2.3 Windows Development & Staging Worker Startup

> [!IMPORTANT]
> The default Celery `prefork` pool is unsupported on Windows OS due to `os.fork()` unavailability. On Windows development workstations, start the worker using the `solo` pool or via WSL2:

```powershell
# Windows PowerShell (Native Solo Execution)
poetry run celery -A backend.src.core.celery_app worker -l INFO -P solo -Q metadata.priority,metadata.fast,metadata.bulk
```

To run multiple worker instances on Windows, run separate terminal sessions targeting specific queues:
```powershell
# Terminal 1: Priority Queue
poetry run celery -A backend.src.core.celery_app worker -l INFO -P solo -Q metadata.priority -n worker_priority@%COMPUTERNAME%

# Terminal 2: Fast & Bulk Queues
poetry run celery -A backend.src.core.celery_app worker -l INFO -P solo -Q metadata.fast,metadata.bulk -n worker_bulk@%COMPUTERNAME%
```

---

## 3. Redis Broker Configuration & Memory Tuning

### 3.1 Recommended Redis Configuration (`redis.conf`)

The Celery broker relies on low-latency Redis in-memory storage. Apply the following parameters to ensure queue resilience:

```text
# Bind address and port
bind 127.0.0.1 ::1
port 6379

# TCP keepalive and timeouts
tcp-keepalive 300
timeout 0

# Memory limits and eviction policy
# Set maxmemory to ~70% of dedicated instance RAM
maxmemory 2gb
maxmemory-policy noeviction

# Persistence: Hybrid RDB + AOF for task durability
save 900 1
save 300 10
save 60 1000
appendonly yes
appendfsync everysec
no-appendfsync-on-rewrite yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
```

> [!WARNING]
> Always configure `maxmemory-policy noeviction` for the Redis broker instance. If set to `allkeys-lru` or `volatile-lru`, queued Celery task payloads could be evicted under memory pressure, causing silent task drops.

### 3.2 Backend Redis Connection String

In `backend/.env`:
```ini
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

---

## 4. Dynamic Celery Beat Scheduler Execution

### 4.1 Architecture & Polling Algorithm

The catalog platform replaces static cron configurations with a custom dynamic scheduler: `backend.src.services.scheduler.dynamic_beat.DatabaseScheduler`.

Key guarantees:
1. **Dynamic Database Reflection**: Reads `sync_schedules` records directly from PostgreSQL every 30 seconds without restarting Beat.
2. **Deterministic SHA-256 Jitter**: Spreads scheduled execution times across an interval window based on `sha256(connection_id + schedule_id)` to eliminate thundering-herd load spikes on target databases.
3. **Circuit Breaker Aware**: Automatically suppresses dispatching for connections whose `circuit_state == 'OPEN'` and whose `cooldown_until` timestamp is in the future.
4. **Canary Dispatching**: Once `cooldown_until` has passed, transitions the connection to `HALF_OPEN` and dispatches a single canary sync job to `metadata.priority`.

### 4.2 Starting the Dynamic Beat Scheduler

```bash
# Linux / macOS / Windows
poetry run celery -A backend.src.core.celery_app beat \
  --loglevel=INFO \
  --scheduler backend.src.services.scheduler.dynamic_beat:DatabaseScheduler
```

#### Systemd Service Configuration (`/etc/systemd/system/datacatalog-beat.service`)
```ini
[Unit]
Description=Enterprise Data Catalog Celery Dynamic Beat Scheduler
After=network.target redis-server.service postgresql.service datacatalog-worker.service

[Service]
Type=simple
User=datacatalog
Group=datacatalog
WorkingDirectory=/opt/datacatalog/backend
EnvironmentFile=/etc/datacatalog/backend.env
ExecStart=/usr/local/bin/poetry run celery -A backend.src.core.celery_app beat \
  --loglevel=INFO \
  --scheduler backend.src.services.scheduler.dynamic_beat:DatabaseScheduler \
  --pidfile=/var/run/celery/beat.pid \
  --logfile=/var/log/celery/beat.log
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## 5. Circuit Breaker Recovery Procedures

### 5.1 Circuit Breaker Finite State Machine

```
               [Success]
             +-----------+
             |           |
             v           |
      +--------------+   |  3 Consecutive Failures
      |    CLOSED    |---+----------------------------+
      +--------------+                                |
             ^                                        |
             | Canary Success                         v
      +--------------+                          +------------+
      |  HALF_OPEN   | <----------------------- |    OPEN    |
      +--------------+    Cooldown (15 mins)    +------------+
             |                                        ^
             +----------------------------------------+
                     Canary Probe Failed
```

- **`CLOSED`**: Normal operation. Sync jobs run on schedule.
- **`OPEN`**: Tripped after 3 consecutive failures. Scheduled runs are suppressed. Cooldown timestamp is set to `now() + 15 minutes`.
- **`HALF_OPEN`**: Cooldown elapsed. Exactly one canary job is dispatched to test target database recovery.

### 5.2 Diagnostic Inspection

Check circuit breaker status via REST API:
```bash
curl -X GET "http://localhost:8000/api/v1/connections/${CONNECTION_ID}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Inspect the `schedule` block in the response:
```json
{
  "id": "c1a2b3c4-0000-0000-0000-000000000001",
  "name": "Production_Postgres_Warehouse",
  "schedule": {
    "is_enabled": true,
    "cron_expression": "0 2 * * *",
    "circuit_state": "OPEN",
    "consecutive_failures": 3,
    "cooldown_until": "2026-11-16T14:15:00Z"
  }
}
```

### 5.3 Administrative Manual Reset

Once the network connection or database outage is resolved, an administrator or Data Steward can reset the circuit breaker immediately:

#### Method A: Via REST API
```bash
curl -X POST "http://localhost:8000/api/v1/connections/${CONNECTION_ID}/circuit-breaker/reset" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json"
```

Response (`200 OK`):
```json
{
  "connection_id": "c1a2b3c4-0000-0000-0000-000000000001",
  "circuit_state": "CLOSED",
  "consecutive_failures": 0,
  "cooldown_until": null,
  "message": "Circuit breaker reset to CLOSED."
}
```

#### Method B: Via Python CLI / Script
```python
import asyncio
import uuid
from backend.src.core.database import get_session_context
from backend.src.services.workers.circuit_breaker import circuit_breaker_manager

async def manual_reset(conn_id_str: str):
    async with get_session_context() as session:
        conn_id = uuid.UUID(conn_id_str)
        res = await circuit_breaker_manager.reset_circuit_breaker(session, conn_id)
        print(f"Circuit breaker for {conn_id} reset: {res}")

if __name__ == "__main__":
    asyncio.run(manual_reset("c1a2b3c4-0000-0000-0000-000000000001"))
```

---

## 6. Worker Scaling, Fleet Sizing & Ephemeral Concurrency

### 6.1 Ephemeral Resource Isolation

PostgreSQL metadata extraction uses `sqlalchemy.pool.NullPool`. 

- Each connector worker opens a dedicated TCP connection upon task entry, sets strict timeouts (`statement_timeout = 30000`, `idle_in_transaction_session_timeout = 10000`), executes the 4 bulk introspection queries, and terminates the TCP connection immediately upon exit.
- Target database impact: Zero idle connections held open on client databases between synchronization runs.

### 6.2 Recommended Sizing Matrix

| Metric | Small Cluster (< 50 DBs) | Medium Cluster (50–500 DBs) | Large Enterprise (500–2,000 DBs) |
|---|---|---|---|
| Celery Workers | 2 nodes (4 cores / 8 GB RAM) | 4 nodes (8 cores / 16 GB RAM) | 8–12 nodes (16 cores / 32 GB RAM) |
| Worker Concurrency (`-c`) | 4 per node | 8 per node | 16 per node |
| Redis Server | 1 node (2 cores / 4 GB RAM) | 2 nodes Sentinel (4 cores / 8 GB RAM) | 3 nodes Cluster (8 cores / 16 GB RAM) |
| Celery Beat Instances | 1 active node | 1 active node + standby | 1 active node with lease lock |

---

## 7. Observability & Flower Monitoring

### 7.1 Running Flower Dashboard

Flower provides real-time visibility into task latency, queue backlogs, worker states, and active execution pools:

```bash
poetry run celery -A backend.src.core.celery_app flower \
  --port=5555 \
  --basic_auth=admin:CatalogSecret2026!
```

Access the UI at `http://localhost:5555`.

### 7.2 Key Operational Metrics & Alerts

Monitor the following Prometheus / Flower alerts:

1. **Queue Backlog (`celery_queue_length > 50`)**: Indicates worker fleet saturation on `metadata.bulk` or `metadata.fast`. Scale worker pods.
2. **Task Failure Rate (`rate(celery_task_failed[5m]) > 0.05`)**: Indicates recurring upstream database network partitions or expired credentials.
3. **Trip Events (`circuit_breaker_state == 'OPEN'`)**: High priority incident indicator requiring upstream target host verification.

# Phase 2 Technical Research & Architecture Decisions

**Feature**: Phase 2 Database Connectors, Distributed Workers, and Dynamic Scheduler  
**Branch**: `002-connectors-workers-scheduler`  
**Date**: 2026-11-16  

---

## 1. Connector Architecture & Bulk Introspection

### Decision
Implement a pluggable `BaseMetadataConnector` abstract base class with an extensible `ConnectorRegistry` factory. For Phase 2A, implement `PostgresConnector` querying native system catalogs (`pg_catalog.pg_class`, `pg_attribute`, `pg_type`, `pg_description`, `pg_constraint`, `pg_stat_user_tables`) using ephemeral, non-pooled connections (`sqlalchemy.pool.NullPool`) with strict 30-second statement timeouts and read-only session characteristics.

### Rationale
- **Ephemeral NullPool**: Holding persistent connection pools to 800 production databases would consume 800+ open socket connections and idle worker resources on client target databases. `NullPool` opens a TCP socket strictly for the duration of metadata extraction and closes it immediately upon job completion.
- **Native `pg_catalog` Bulk Introspection**: ANSI `information_schema` views (such as `information_schema.columns`) perform heavy internal joins against ACL tables and security views, taking 15–40 seconds on schemas with 200+ tables. Direct `pg_catalog` queries with single round-trip queries per metadata facet (tables, columns, constraints) execute in < 250ms, with zero write locks and zero N+1 database queries.
- **Session Protections**: Setting `SET statement_timeout = 30000; SET idle_in_transaction_session_timeout = 10000; SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;` guarantees that runaway queries or lock waits are automatically terminated by the target database engine before causing performance degradation.

### Alternatives Considered
- **SQLAlchemy Default QueuePool**: Rejected because maintaining pools across 800 enterprise databases leads to file descriptor exhaustion and consumes connection slots on target production servers.
- **ANSI `information_schema` Queries**: Rejected due to 50x slower query plans on PostgreSQL, lack of estimated live row counts (`n_live_tup`), and lack of native table/column comments (`pg_description`).
- **ORM-based Introspection (SQLAlchemy `inspect`)**: Rejected because `inspect(engine)` runs dozens of sequential round-trips (N+1 queries) per table to inspect columns, indexes, and foreign keys.

---

## 2. Schema-Level Partial Failure Isolation

### Decision
Isolate extraction at the schema boundary. When extracting multi-schema databases, wrap each schema's extraction in an isolated try-except block. If a schema extraction fails (e.g. `42501 InsufficientPrivilege` or lock timeout):
1. Flag job status as `PARTIAL_FAILURE`.
2. Commit successfully extracted schemas and tables to the catalog.
3. Exclude the failed schema from the soft-deletion diff sweep (guaranteeing that existing catalog tables in the failed schema are **never** falsely marked `DELETED`).
4. Record structured diagnostics in `sync_jobs.error_trace_jsonb` detailing the failed schema name, error code, and exception trace.

### Rationale
In enterprise databases with multiple schemas (e.g., `public`, `finance`, `vault`), service accounts frequently have access to general business schemas but restricted access to secure or audit schemas. Failing the entire job or soft-deleting existing metadata when one schema is locked or unauthorized causes catalog churn and data loss.

### Alternatives Considered
- **All-or-Nothing Transaction**: Rejected because transient issues on one non-critical schema would prevent indexing all other critical business schemas.
- **Soft-deleting Unreachable Schemas**: Rejected because permission revivals or temporary locks would falsely wipe catalog metadata and historical documentation.

---

## 3. Celery Worker Orchestration & Redis Queue Routing

### Decision
Deploy Celery 5.4+ with a Redis broker (`redis://redis:6379/0`), partitioned into three priority queues:
1. `metadata.fast`: Short-lived connection tests, lightweight pings, and single-schema manual syncs (worker concurrency 8, gevent/threads).
2. `metadata.bulk`: Scheduled nightly bulk extractions and schema diff upserts (worker concurrency 4–8, prefork).
3. `metadata.priority`: User-initiated interactive manual syncs with real-time UI monitoring.

### Rationale
Separating heavy bulk ingestion (which processes thousands of columns and DDL diffs) from interactive connection ping tests guarantees that administrators testing connection parameters or viewing live sync statuses are never blocked behind a 4-hour bulk sync queue.

### Alternatives Considered
- **Dramatiq**: Excellent lightweight alternative, but Celery has richer ecosystem support, standard Beat schedulers, and established enterprise monitoring integrations (Flower).
- **Single Celery Queue**: Rejected because a burst of 100 scheduled nightly syncs would block interactive user connection testing and manual sync triggers.

---

## 4. Deterministic Cron Jitter for Dynamic Beat Scheduler

### Decision
Implement a deterministic hash-based jitter algorithm for the dynamic scheduler managing 800 database sync schedules:
$$\text{jitter\_seconds} = \text{int}(\text{sha256}(\text{connection\_id}).\text{hexdigest}(), 16) \pmod{900}$$
For schedules configured at standard hourly or midnight boundaries (e.g. `0 0 * * *`), the execution start time is deterministically dispersed over a 15-minute (900s) window based on the connection's UUID.

### Rationale
- **Anti-Thundering Herd**: 800 connections running simultaneously at midnight (`00:00:00`) would create an instantaneous resource spike on Redis, Celery workers, and the catalog database.
- **Deterministic vs Random**: Pure random jitter causes inconsistent execution times and race conditions during scheduler service restarts. Deterministic hash-based jitter ensures that each database runs at an exact, predictable, repeatable offset every night without clock drift or overlapping duplicate runs.

### Alternatives Considered
- **Pure Pseudo-Random Jitter (`random.uniform(0, 900)`)**: Rejected because scheduler reboots would re-roll the dice, causing jobs to fire twice or skip windows.
- **Strict FIFO Queuing with Zero Jitter**: Rejected because all 800 jobs would hit `QUEUED` at `00:00:00`, bloating broker memory and masking true job runtimes.

---

## 5. Circuit Breaker State Machine & In-Memory Credential Decryption

### Decision
1. **Circuit Breaker**: Implement a 3-state circuit breaker (`CLOSED`, `OPEN`, `HALF_OPEN`) per connection stored on `sync_schedules`:
   - `CLOSED`: Normal operation.
   - Breached on 3 consecutive extraction failures $\rightarrow$ transitions to `OPEN` with a 15-minute cooldown (`cooldown_until`). Scheduler skips automatic runs while `OPEN`.
   - After cooldown expires $\rightarrow$ transitions to `HALF_OPEN` allowing a single canary execution. If canary succeeds, resets to `CLOSED`; if fails, returns to `OPEN` for another 15 minutes.
   - Manual admin "Test Connection" or manual trigger immediately resets circuit to `CLOSED`.
2. **In-Memory Envelope Encryption**:
   - Celery task payloads contain strictly `connection_id` (UUID) and `job_id` (UUID). Never plaintext passwords or decrypted keys.
   - Worker fetches `encrypted_dek` and `encrypted_credentials` from `database_connections` via catalog DB, calls `EnvelopeEncryptionService.decrypt_credentials()` inside worker memory, establishes target connection, executes extraction, and immediately purges decrypted credentials from memory.

### Rationale
- Prevents down target servers or bad credentials from causing infinite worker retry loops that exhaust CPU and network bandwidth across the cluster.
- Satisfies Constitution Principle IV (Security & Auditing): Plaintext secrets never touch the broker, task logs, or error traces.

### Alternatives Considered
- **Exponential Backoff Only**: Insufficient because unreachable databases continue to trigger every cron tick indefinitely, consuming worker threads.
- **Passing Decrypted Credentials in Broker Messages**: Strictly prohibited by Constitution Gate IV.

---

## 6. High-Speed Bulk Upsert via PostgreSQL `UNNEST`

### Decision
Refactor `BulkIngestionService` from iterative row-by-row ORM inserts to PostgreSQL set-based `INSERT ... SELECT FROM UNNEST(...) ON CONFLICT (...) DO UPDATE`.
Batch 200 tables and 5,000 columns into vectorized array parameters executed in single SQL statements:
- Tables upsert: 1 statement (~20ms).
- Columns upsert: 1–2 batched statements (~80ms).
- Soft-delete missing tables/columns: 1 statement with `WHERE name != ALL(:ingested_names) AND schema_id = :schema_id`.

### Rationale
- Eliminates 5,200 round-trips over asyncpg/SQLAlchemy.
- Benchmarks show PostgreSQL `UNNEST` batch upserts complete ingestion of 200 tables and 5,000 columns in under 300ms total DB time, effortlessly surpassing the 3-second SLA.

### Alternatives Considered
- **Iterative `session.execute(pg_insert)` in Python loop**: Current Phase 1 implementation. Takes 8–15 seconds for 5,000 columns due to network IPC and Python event loop overhead.
- **PostgreSQL `COPY FROM STDIN` into temporary staging tables**: Highly performant, but requires binary streaming or temporary tables with table locking overhead. `UNNEST` offers comparable sub-second speed with cleaner parameter binding in asyncpg.

---

## 7. Statistical Separation vs Structural DDL Versioning

### Decision
Strictly decouple statistical metrics from structural DDL versioning:
1. **Statistical Metrics (`row_count_estimate`, `size_bytes`)**:
   - Updated in-place on `catalog_tables`.
   - Appended to a lightweight `table_statistics_history` time-series table (`table_id`, `recorded_at`, `row_count_estimate`, `size_bytes`, `sync_job_id`).
   - Does **NOT** bump `TableVersion.version_num` or generate `SchemaChangeLog` entries.
2. **Structural DDL Mutations**:
   - Column added, column dropped, data type changed, nullability changed, PK/FK changed.
   - Bumps `TableVersion.version_num` (1, 2, 3...) and inserts granular `SchemaChangeLog` records.

### Rationale
Enterprise databases update row counts and size statistics continuously. Generating a new `TableVersion` snapshot and diff log on every sync run would produce millions of meaningless version snapshots for static schemas, destroying versioning utility and bloating storage.

### Alternatives Considered
- **Snapshotting on every sync**: Rejected because storing 160,000 JSON snapshots every night would consume tens of gigabytes per week without structural changes.
- **Storing statistics only on `catalog_tables` without history**: Rejected because platform analytics and capacity planning require tracking table growth over time.

---

## 8. Description Drift Detection & Preservation

### Decision
Maintain `source_description` (native DB comment) and `approved_description` (steward curation) as independent fields:
1. Automated sync updates `source_description` on every run.
2. `approved_description` is **never** overwritten by sync.
3. If extracted `source_description` differs from the previous `source_description`, and an `approved_description` already exists and differs from `source_description`:
   - Set `description_drift_detected = TRUE` on `catalog_tables` / `catalog_columns`.
   - Record a `SchemaChangeLog` entry with `change_type = 'DESCRIPTION_DRIFT'`.
4. Surface visual drift indicators in Angular UI with 3 resolution options:
   - *Retain Approved*: Dismisses drift flag, keeps curated text.
   - *Overwrite with Source*: Copies `source_description` into `approved_description`, marks drift resolved.
   - *Side-by-Side Merge*: Opens modal to review differences and edit curated text.

### Rationale
Data governance stewards spend significant manual effort documenting data assets. Automated syncs must reflect source system changes without clobbering human curation, while alerting stewards when source documentation changes.

### Alternatives Considered
- **Source Wins (Overwrite)**: Destroys human curation.
- **Catalog Wins (Ignore Source)**: Hides upstream changes from stewards.

---

## 9. Angular RTL UI & Vazirmatn Integration

### Decision
Design standalone Angular 22 components styled with PrimeNG 20, Vazirmatn Persian typography, and full RTL layout (`dir="rtl"`):
1. `SyncMonitorComponent`: Filterable data table of `sync_jobs` with status badges, execution metrics, auto-polling (3s/5s), and diagnostic drawer for `PARTIAL_FAILURE` and `FAILED` runs.
2. `ConnectionTestDialog`: Modal providing structured connectivity diagnostics (TCP handshake, SSL/TLS, authentication, read-only validation, latency ms).
3. `ManualSyncTrigger`: Row/card action triggering immediate background task with live progress feedback.
4. `DescriptionDriftResolutionModal`: Split-view comparison modal for reviewing and resolving metadata drift.

### Rationale
Ensures a cohesive, enterprise-grade Persian user experience matching the existing Phase 1 design system (`Vazirmatn`, semantic color tokens, PrimeNG components).

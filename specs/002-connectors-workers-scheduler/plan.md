# Implementation Plan: Phase 2 Database Connectors, Distributed Workers, and Dynamic Scheduler

**Branch**: `002-connectors-workers-scheduler` | **Date**: 2026-11-16 | **Spec**: [specs/002-connectors-workers-scheduler/spec.md](spec.md)

**Input**: Feature specification from `specs/002-connectors-workers-scheduler/spec.md` and user requirements for Phase 2 architectural expansion.

---

## Summary

Phase 2 builds upon the Phase 1 Core Platform to deliver an enterprise-scale, autonomous metadata extraction, worker orchestration, and dynamic scheduling subsystem:
1. Standardizes a pluggable `BaseMetadataConnector` interface and `ConnectorRegistry` with a production-grade Phase 2A `PostgresConnector` querying native `pg_catalog` via ephemeral `NullPool` connections with strict 30-second timeouts.
2. Implements schema-level `PARTIAL_FAILURE` isolation so failures on individual schemas (e.g. permission denied) do not falsely soft-delete existing catalog tables in that schema or fail the overall job.
3. Deploys a distributed Celery worker fleet backed by Redis, partitioned across priority queues (`metadata.priority`, `metadata.fast`, `metadata.bulk`), with transient in-memory credential decryption via `EnvelopeEncryptionService` (zero plaintext secrets in Redis).
4. Implements a dynamic Celery Beat scheduler executing 800 dynamic database sync schedules with a deterministic hash-based jitter algorithm to eliminate thundering herd spikes at midnight boundaries.
5. Employs a 3-state circuit breaker (`CLOSED`, `OPEN`, `HALF_OPEN`) with a 15-minute cooldown window to prevent worker exhaustion on down or unreachable databases.
6. Re-engineers `BulkIngestionService` using PostgreSQL vectorized `UNNEST` bulk upserts, achieving complete extraction and diff ingestion of 200 tables and 5,000 columns in under 3 seconds end-to-end.
7. Decouples structural DDL mutations (`TableVersion` & `SchemaChangeLog`) from statistical metrics (`table_statistics_history` time-series table) without bumping `version_num`.
8. Enforces independent tracking of `source_description` and `approved_description`, flagging description drift and surfacing interactive resolution controls.
9. Delivers Angular 22 RTL UI additions using Vazirmatn typography and PrimeNG 20: real-time/polled Sync Job Monitor, diagnostic Connection Test button, Manual Trigger Sync, Description Drift resolution controls, and Schema Diff Viewer integration.

---

## Technical Context

**Language/Version**: Python 3.12 (backend), TypeScript 5.4+ / Angular 22 (frontend).  
**Primary Dependencies**:
- Backend: FastAPI, SQLAlchemy 2.0 (asyncpg), Alembic, Celery 5.4+, Redis 5.0+ client, Cryptography (AES-256-GCM), Pydantic v2.
- Frontend: Angular 22 (standalone components), PrimeNG 20, PrimeIcons 8, ECharts 6, RxJS 7.8, Vitest.  
**Storage**: PostgreSQL 16+ (catalog relational store, JSONB, tsvector, pg_trgm), Redis 7+ (broker & Celery result backend).  
**Testing**: `pytest` (with `pytest-asyncio` against real PostgreSQL test database), Vitest / Angular testing utilities.  
**Target Platform**: Containerized Linux server (Docker / Kubernetes), modern evergreen web browsers (Chrome, Edge, Firefox, Safari) with bidirectional Persian RTL.  
**Project Type**: Enterprise Distributed Web Service & Background Worker Fleet.  
**Performance Goals**:
- Bulk metadata ingestion of 200 tables / 5,000 columns in `< 3.0 seconds`.
- Ephemeral connection ping / test latency `< 100ms` for reachable target databases.
- Dynamic Beat scheduler dispatch latency `< 50ms` per cron tick.
- UI Sync Job Monitor polling latency `< 150ms`.  
**Constraints**:
- Ephemeral non-pooled target connections (`NullPool`): 0 residual idle connections on target customer databases.
- Envelope encryption: Plaintext credentials NEVER stored in PostgreSQL, Celery message payloads, or worker logs.
- Zero N+1 queries in connector extraction and ingestion diff pipelines.
- No duplicate or parallel catalog tables: build strictly upon Phase 1 schema.  
**Scale/Scope**: 800 connected databases, 160,000 tables, 5,000,000+ columns, 800 dynamic cron schedules.

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Plan Compliance Status | Validation Mechanism |
|---|---|---|---|
| **I. Ambiguity Gate** | Zero guessing on business logic, permissions, or schema edge cases. Clarify upfront. | **PASSED** | All 5 ambiguities clarified and ratified in `spec.md` Session 2026-11-16 (Postgres priority, independent descriptions, NullPool, partial failure isolation, statistical separation). |
| **II. Zero-Laziness Policy** | No `# TODO`, mocks in service paths, or unindexed queries. Built for 800 DBs, 160k tables, 5M columns. | **PASSED** | Set-based PostgreSQL `UNNEST` bulk upsert; keyset pagination on `sync_jobs`; single bulk queries against `pg_catalog`. |
| **III. Strict TDD Protocol** | Real PostgreSQL database testing; no SQLite or mocked sessions. Failing test first. | **PASSED** | Integration tests execute against real PostgreSQL instance; test plan covers Red-Green-Refactor sequence for all modules. |
| **IV. Security & Auditing** | AES-256-GCM envelope encryption; zero secrets in broker; immutable audit logging. | **PASSED** | Celery payloads contain only UUIDs; credentials unwrapped in ephemeral memory; all manual triggers, resets, and drift resolutions generate `AuditLog` records. |

---

## Project Structure

### Documentation (this feature)

```text
specs/002-connectors-workers-scheduler/
├── spec.md              # Ratified Phase 2 feature specification
├── plan.md              # This implementation plan
├── research.md          # Architecture decisions & trade-off rationale
├── data-model.md        # Extended entities, state machines & vectors
├── quickstart.md        # End-to-end validation scenarios & test commands
├── contracts/
│   └── openapi.yaml     # Phase 2 OpenAPI 3.1.0 interface contract
└── tasks.md             # Implementation tasks (generated via /speckit-tasks)
```

### Source Code Layout (Repository Root)

```text
backend/
├── alembic/versions/
│   └── 007_phase2_connectors_workers_scheduler.py  # Alembic migration for sync_jobs, table_statistics_history, circuit breaker & drift
├── src/
│   ├── api/v1/
│   │   ├── connections.py       # Updated test & manual sync endpoints
│   │   ├── sync_jobs.py         # NEW: Sync job monitor, history & retry endpoints
│   │   ├── drift.py             # NEW: Description drift review & resolution endpoints
│   │   └── statistics.py        # NEW: Table statistics history endpoints
│   ├── core/
│   │   ├── config.py            # Celery broker & worker settings
│   │   └── celery_app.py        # NEW: Celery application instance & queue routing
│   ├── models/
│   │   ├── connections.py       # Extended with circuit breaker columns
│   │   ├── catalog.py           # Extended with description_drift_detected
│   │   ├── sync_jobs.py         # NEW: SyncJob model
│   │   └── statistics.py        # NEW: TableStatisticsHistory model
│   ├── schemas/
│   │   ├── sync_jobs.py         # NEW: Pydantic request/response schemas
│   │   └── drift.py             # NEW: Drift resolution schemas
│   └── services/
│       ├── connectors/
│       │   ├── base.py          # NEW: BaseMetadataConnector abstract base class & domain models
│       │   ├── registry.py      # NEW: ConnectorRegistry factory
│       │   └── postgres.py      # NEW: PostgresConnector (pg_catalog bulk extraction)
│       ├── workers/
│       │   ├── tasks.py         # NEW: Celery tasks (metadata extraction, ingestion)
│       │   └── circuit_breaker.py # NEW: Circuit breaker state machine manager
│       ├── scheduler/
│       │   ├── dynamic_beat.py  # NEW: Custom database-backed dynamic Celery Beat scheduler
│       │   └── jitter.py        # NEW: Deterministic hash-based jitter algorithm
│       └── ingestion/
│           ├── bulk_ingestion_service.py # Vectorized UNNEST bulk upsert engine
│           └── drift_service.py # NEW: Description drift detection & resolution logic
frontend/
└── src/
    └── app/
        ├── features/
        │   ├── connections/
        │   │   ├── connection-list.component.ts   # Added Test & Manual Sync action buttons
        │   │   ├── connection-list.component.html # Persian RTL table with diagnostic modal trigger
        │   │   └── connection-dialog.component.ts # Enhanced with live diagnostic test output
        │   ├── sync-monitor/                      # NEW Feature Module
        │   │   ├── sync-monitor.component.ts      # Live/polled execution monitor table
        │   │   ├── sync-monitor.component.html    # PrimeNG RTL data table with status badges
        │   │   ├── sync-monitor.component.css     # Vazirmatn layout & status styling
        │   │   └── diagnostic-dialog.component.ts # Modal displaying structured error_trace_jsonb
        │   ├── drift/                             # NEW Feature Module
        │   │   ├── drift-banner.component.ts      # Visual drift notification banner
        │   │   └── drift-resolution-dialog.component.ts # Radio selection & side-by-side editor
        │   └── versioning/
        │       └── version-diff.component.ts      # Integrated with sync job execution changes
```

**Structure Decision**: Modular extension preserving existing clean architecture in both backend and frontend. Connectors, workers, scheduler, and ingestion components reside in dedicated service namespaces without polluting core platform modules.

---

## Detailed Architectural Design

### 1. Connector Architecture & Bulk Introspection

#### 1.1 `BaseMetadataConnector` Interface & Registry Pattern
The connector layer provides a unified abstraction across heterogeneous RDBMS engines.

```python
class BaseMetadataConnector(ABC):
    def __init__(self, connection_config: Dict[str, Any]):
        self.config = connection_config

    @abstractmethod
    async def __aenter__(self) -> "BaseMetadataConnector":
        """Establishes ephemeral connection using NullPool with strict timeouts."""
        pass

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Immediately disconnects and releases TCP socket."""
        pass

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Executes lightweight ping, validating auth, SSL, and read-only mode."""
        pass

    @abstractmethod
    async def extract_schemas(self) -> List[SchemaMetadata]:
        """Lists accessible schemas excluding system/internal schemas."""
        pass

    @abstractmethod
    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        """Bulk extracts tables, columns, constraints, and statistics for a single schema in 4 round-trips."""
        pass
```

- **Ephemeral Connection Lifecycle**: Connectors instantiate an asynchronous engine with `poolclass=NullPool`. When the `async with connector:` block exits, the connection is immediately terminated, ensuring zero persistent connection footprint on target enterprise databases.
- **Session Protections**: Every session executes:
  ```sql
  SET statement_timeout = 30000;
  SET idle_in_transaction_session_timeout = 10000;
  SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;
  ```
- **Registry Pattern (`ConnectorRegistry`)**: Maps `DatabaseEngine` enum to concrete implementations (`POSTGRESQL -> PostgresConnector`). Registration is extensible for Phase 2B (`MSSQL`, `ORACLE`).

#### 1.2 Phase 2A `PostgresConnector` Implementation
Queries native `pg_catalog` system tables to achieve zero N+1 round-trips:
1. **Schemas Query**:
   ```sql
   SELECT n.nspname, pg_catalog.pg_get_userbyid(n.nspowner) AS owner, d.description
   FROM pg_catalog.pg_namespace n
   LEFT JOIN pg_catalog.pg_description d ON d.objoid = n.oid AND d.objsubid = 0
   WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
     AND n.nspname NOT LIKE 'pg_temp%'
     AND n.nspname NOT LIKE 'pg_toast%'
   ORDER BY n.nspname;
   ```
2. **Tables Query (Bulk per Schema)**:
   ```sql
   SELECT 
       c.relname AS table_name,
       CASE c.relkind 
           WHEN 'r' THEN 'TABLE'
           WHEN 'v' THEN 'VIEW'
           WHEN 'm' THEN 'MATERIALIZED_VIEW'
           WHEN 'p' THEN 'PARTITIONED_TABLE'
           WHEN 'f' THEN 'EXTERNAL_TABLE'
           ELSE 'TABLE'
       END AS table_type,
       COALESCE(s.n_live_tup, c.reltuples::bigint, 0) AS row_count_estimate,
       pg_catalog.pg_total_relation_size(c.oid) AS size_bytes,
       d.description AS source_description,
       pg_catalog.pg_get_userbyid(c.relowner) AS owner
   FROM pg_catalog.pg_class c
   JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   LEFT JOIN pg_catalog.pg_stat_user_tables s ON s.relid = c.oid
   LEFT JOIN pg_catalog.pg_description d ON d.objoid = c.oid AND d.objsubid = 0
   WHERE n.nspname = :schema_name AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
   ORDER BY c.relname;
   ```
3. **Columns Query (Bulk for ALL Tables in Schema - Single Query)**:
   ```sql
   SELECT 
       c.relname AS table_name,
       a.attname AS column_name,
       a.attnum AS ordinal_position,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       t.typname AS raw_data_type,
       NOT a.attnotnull AS is_nullable,
       COALESCE(pk.is_pk, false) AS is_primary_key,
       COALESCE(fk.is_fk, false) AS is_foreign_key,
       pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) AS default_value,
       d.description AS source_description
   FROM pg_catalog.pg_attribute a
   JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
   JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
   LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
   LEFT JOIN pg_catalog.pg_description d ON d.objoid = a.attrelid AND d.objsubid = a.attnum
   LEFT JOIN (
       SELECT conrelid, unnest(conkey) AS attnum, true AS is_pk
       FROM pg_catalog.pg_constraint WHERE contype = 'p'
   ) pk ON pk.conrelid = a.attrelid AND pk.attnum = a.attnum
   LEFT JOIN (
       SELECT conrelid, unnest(conkey) AS attnum, true AS is_fk
       FROM pg_catalog.pg_constraint WHERE contype = 'f'
   ) fk ON fk.conrelid = a.attrelid AND fk.attnum = a.attnum
   WHERE n.nspname = :schema_name 
     AND a.attnum > 0 
     AND NOT a.attisdropped
   ORDER BY c.relname, a.attnum;
   ```
4. **Foreign Keys Query (Bulk for Schema)**:
   Extracts referential constraints in a single query against `pg_catalog.pg_constraint`.

#### 1.3 Schema-Level Partial Failure Isolation
When a database has multiple schemas, extraction iterates schema by schema:
- If `extract_schema_metadata("finance")` succeeds, its tables and columns are staged for upsert.
- If `extract_schema_metadata("vault")` raises an exception (e.g. `InsufficientPrivilege` `42501` or statement timeout), the error is caught:
  ```python
  schema_errors.append({
      "schema_name": "vault",
      "error_code": exc.pgcode if hasattr(exc, "pgcode") else "EXTRACTION_ERROR",
      "error_message": str(exc)
  })
  ```
- **Soft-Delete Safety Guarantee**: The soft-deletion diff logic only runs for schemas that were successfully extracted. Failed schemas are **strictly omitted** from soft-deletion, guaranteeing that existing catalog metadata is not erased.
- Job status transitions to `PARTIAL_FAILURE` if `schema_errors` is non-empty and at least one schema succeeded.

---

### 2. Celery & Beat Worker Orchestration

#### 2.1 Worker Topology & Redis Queues
Celery 5.4 is configured with Redis 7 (`redis://redis:6379/0`):
- **Queues**:
  - `metadata.priority`: High-priority interactive manual syncs triggered by administrators.
  - `metadata.fast`: Ephemeral connection ping tests, credential verification, and status probes.
  - `metadata.bulk`: Scheduled nightly bulk extractions and schema diff upserts.
- **Worker Configuration**:
  - Task serialization: `json`.
  - Acks late enabled (`task_acks_late = True`) to prevent lost tasks if a worker crashes during extraction.
  - Prefetch multiplier: `1` for `metadata.bulk` (prevents worker hogging heavy jobs).
  - Time limits: Hard limit 1800s (30 mins), soft limit 1500s (25 mins).

#### 2.2 In-Memory Envelope Credential Decryption
Strictly adheres to Constitution Principle IV:
- Task parameters passed to Celery: `{"connection_id": "<UUID>", "job_id": "<UUID>"}`.
- Inside worker task execution:
  1. Worker fetches `encrypted_dek` and `encrypted_credentials` from `database_connections` via catalog PostgreSQL.
  2. Invokes `EnvelopeEncryptionService.decrypt_credentials(encrypted_dek, encrypted_credentials)`.
  3. Uses decrypted credentials in ephemeral memory to construct target database connection URL.
  4. Connects, extracts, and disconnects.
  5. Decrypted credentials object is deleted from memory scope; zero secrets written to Redis or Celery task logs.

#### 2.3 Deterministic Schedule Jitter Algorithm
To distribute 800 scheduled jobs without midnight thundering herd:
```python
def calculate_schedule_jitter(connection_id: uuid.UUID, max_jitter_seconds: int = 900) -> int:
    """
    Computes a deterministic, repeatable offset in seconds based on SHA-256 hash of connection_id.
    Range: [0, max_jitter_seconds) (default: 0 to 15 minutes).
    """
    digest = hashlib.sha256(connection_id.bytes).hexdigest()
    return int(digest, 16) % max_jitter_seconds
```
- A connection with cron `0 0 * * *` (midnight) and jitter 412 seconds will reliably trigger at `00:06:52` every night.
- Repeatable across Celery Beat scheduler reboots, eliminating random clustering or duplicate execution runs.

#### 2.4 Circuit Breaker State Machine
Tracked directly in `sync_schedules`:
- **State `CLOSED`**: Default state. All scheduled and manual syncs proceed.
- **State `OPEN`**: Triggered when `consecutive_failures >= 3`. Automatic scheduled runs are suppressed. `cooldown_until` is set to `now() + timedelta(minutes=15)`.
- **State `HALF_OPEN`**: Entered when `now() >= cooldown_until`. Next scheduled run executes as a canary test:
  - If canary run succeeds: `circuit_state = 'CLOSED'`, `consecutive_failures = 0`.
  - If canary run fails: `circuit_state = 'OPEN'`, `cooldown_until = now() + timedelta(minutes=15)`.
- **Manual Override**: Administrator clicking "Test Connection" or "Manual Sync" in UI forces a test that, upon success, immediately resets `circuit_state = 'CLOSED'` and `consecutive_failures = 0`.

---

### 3. High-Speed Bulk Diff, Upsert & Statistical Separation

#### 3.1 Vectorized PostgreSQL `UNNEST` Ingestion
Replaces iterative Python loops with set-based PostgreSQL bulk operations:

1. **Table Bulk Upsert (1 SQL Statement for 200 Tables)**:
   ```sql
   INSERT INTO catalog_tables (
       id, schema_id, name, table_type, row_count_estimate, size_bytes,
       source_description, owner, status, custom_properties, updated_at
   )
   SELECT 
       v.id, v.schema_id, v.name, v.table_type, v.row_count_estimate, v.size_bytes,
       v.source_description, v.owner, 'ACTIVE'::lifecycle_status_enum, v.custom_properties,
       clock_timestamp()
   FROM UNNEST(
       :ids, :schema_ids, :names, :table_types, :row_counts, :sizes,
       :source_descriptions, :owners, :custom_props
   ) AS v(id, schema_id, name, table_type, row_count_estimate, size_bytes, source_description, owner, custom_properties)
   ON CONFLICT (schema_id, name) DO UPDATE SET
       table_type = EXCLUDED.table_type,
       row_count_estimate = EXCLUDED.row_count_estimate,
       size_bytes = EXCLUDED.size_bytes,
       source_description = EXCLUDED.source_description,
       owner = COALESCE(EXCLUDED.owner, catalog_tables.owner),
       status = 'ACTIVE',
       custom_properties = EXCLUDED.custom_properties,
       updated_at = clock_timestamp()
   RETURNING id, name;
   ```

2. **Column Bulk Upsert (1 SQL Statement for 5,000 Columns)**:
   ```sql
   INSERT INTO catalog_columns (
       id, table_id, name, ordinal_position, data_type, raw_data_type,
       is_nullable, is_primary_key, is_foreign_key, default_value,
       source_description, classification_level, status, updated_at
   )
   SELECT 
       v.id, v.table_id, v.name, v.ordinal_position, v.data_type, v.raw_data_type,
       v.is_nullable, v.is_primary_key, v.is_foreign_key, v.default_value,
       v.source_description, v.classification_level, 'ACTIVE'::lifecycle_status_enum,
       clock_timestamp()
   FROM UNNEST(
       :ids, :table_ids, :names, :ordinal_positions, :data_types, :raw_data_types,
       :is_nullables, :is_primary_keys, :is_foreign_keys, :default_values,
       :source_descriptions, :classification_levels
   ) AS v(id, table_id, name, ordinal_position, data_type, raw_data_type, is_nullable, is_primary_key, is_foreign_key, default_value, source_description, classification_level)
   ON CONFLICT (table_id, name) DO UPDATE SET
       ordinal_position = EXCLUDED.ordinal_position,
       data_type = EXCLUDED.data_type,
       raw_data_type = EXCLUDED.raw_data_type,
       is_nullable = EXCLUDED.is_nullable,
       is_primary_key = EXCLUDED.is_primary_key,
       is_foreign_key = EXCLUDED.is_foreign_key,
       default_value = EXCLUDED.default_value,
       source_description = EXCLUDED.source_description,
       status = 'ACTIVE',
       updated_at = clock_timestamp()
   RETURNING id, table_id, name;
   ```

3. **Soft-Delete Dropped Tables (Single CTE Query)**:
   ```sql
   UPDATE catalog_tables
   SET status = 'DELETED', updated_at = clock_timestamp()
   WHERE schema_id = :schema_id
     AND name != ALL(:scanned_table_names)
     AND status != 'DELETED'
   RETURNING id, name;
   ```
   *Safety Note: Only executed for schemas that completed extraction successfully.*

#### 3.2 Statistical Separation vs Structural DDL Versioning
- **Statistical Recording**: Every sync run batch-inserts extracted row counts and size estimates into `table_statistics_history` in a single query:
  ```sql
  INSERT INTO table_statistics_history (id, table_id, recorded_at, row_count_estimate, size_bytes, sync_job_id)
  SELECT gen_random_uuid(), v.table_id, clock_timestamp(), v.row_count, v.size, :sync_job_id
  FROM UNNEST(:table_ids, :row_counts, :sizes) AS v(table_id, row_count, size);
  ```
- **Structural DDL Isolation**:
  - `VersionService.compute_column_deltas()` compares exclusively structural properties: `data_type`, `raw_data_type`, `is_nullable`, `is_primary_key`, `is_foreign_key`, and column additions/deletions.
  - Changes to `row_count_estimate` and `size_bytes` do **NOT** increment `TableVersion.version_num` or trigger `SchemaChangeLog` entries.

#### 3.3 Description Drift Detection & Preservation
- Ingestion preserves `approved_description` on all tables and columns by omitting it from the `ON CONFLICT DO UPDATE SET` clause.
- **Drift Logic**:
  If `extracted.source_description != existing.source_description` AND `existing.approved_description IS NOT NULL` AND `extracted.source_description != existing.approved_description`:
  - Set `description_drift_detected = TRUE` on the table or column.
  - Log `SchemaChangeLog(change_type='DESCRIPTION_DRIFT', old_value={'source': existing.source_description}, new_value={'source': extracted.source_description})`.

---

### 4. Angular RTL UI Additions (Vazirmatn Design System)

#### 4.1 Sync Job Monitor & Execution History View (`SyncMonitorComponent`)
- **Route**: `/sync-monitor` and embedded tab inside Connection Management.
- **Features**:
  - Real-time polling with RxJS `timer` (3-second interval) when any job is `RUNNING` or `QUEUED`. Pause/Resume button.
  - PrimeNG `p-table` with Vazirmatn typography, Persian date formatting (Shamsi via `Intl.DateTimeFormat('fa-IR')`), and RTL layout (`dir="rtl"`).
  - Status badges with semantic styling:
    - `SUCCESS`: سبز (Green, `--success-main`)
    - `PARTIAL_FAILURE`: نارنجی / هشدار (Orange/Amber, `--warning-main`)
    - `FAILED`: قرمز (Red, `--danger-main`)
    - `RUNNING`: آبی در حال چرخش (Blue spinner, `--primary-600`)
    - `QUEUED`: خاکستری (Slate, `--slate-500`)
  - Diagnostic error drawer/modal (`p-dialog`) displaying formatted JSON and schema failure breakdown from `error_trace_jsonb`.

#### 4.2 Connection Test Button & Structured Feedback
- Added to `ConnectionListComponent` and `ConnectionDialogComponent`.
- "آزمون اتصال" button with PrimeNG spinner state.
- Modal presents structured verification checklist:
  - شبکه و درگاه (TCP Reachability)
  - اعتبارسنجی ورود (Authentication)
  - امنیت انتقال (SSL/TLS Encryption)
  - حالت فقط-خواندنی (Read-Only Enforcement)
  - زمان تأخیر اتصال (Latency ms)

#### 4.3 Manual Trigger Sync Action
- "همگام‌سازی دستی" button on connection cards with confirmation dialog.
- Enqueues task to Celery `metadata.priority` queue and navigates to or opens the Sync Job Monitor with the job highlighted.

#### 4.4 Description Drift Resolution Controls
- Visual amber banner on Table and Column details: "تغییر در توضیحات پایگاه‌داده مبدأ شناسایی شد".
- Resolution dialog (`DescriptionDriftResolutionModal`):
  - **گزینه ۱: حفظ توضیحات تأییدشده** (Retains curated approved description, clears drift flag).
  - **گزینه ۲: اعمال توضیحات پایگاه‌داده مبدأ** (Overwrites approved description with native DB comment, clears drift flag).
  - **گزینه ۳: مقایسه و ادغام هم‌زمان** (Side-by-side diff editor allowing manual merge).

---

## Complexity Tracking & Constitution Compliance

No constitutional exceptions or violations are required. The design fully complies with:
- Keyset pagination and bounded queries for high-volume catalogs.
- Zero N+1 query ban via vectorized `pg_catalog` inspection and PostgreSQL `UNNEST` batch upserts.
- Envelope encryption (AES-256-GCM) with transient in-memory decryption.
- Immutable auditing on all state mutations.

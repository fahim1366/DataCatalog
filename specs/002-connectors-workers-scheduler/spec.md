# Feature Specification: Phase 2 Database Connectors, Distributed Workers, and Dynamic Scheduler

**Feature Branch**: `002-connectors-workers-scheduler`  
**Created**: 2026-11-16  
**Status**: Draft  
**Input**: User description: "We are building Phase 2 (Database Connectors, Distributed Workers, and Dynamic Scheduler) on top of the Phase 1 Core Platform."

## Clarifications

### Session 2026-11-16

- Q: Which database engine should be the primary target implemented in Phase 2A, and which engines should follow in Phase 2B? (FR-003) → A: Phase 2A focuses exclusively on PostgreSQL; Phase 2B immediately follows with Microsoft SQL Server, then Oracle.
- Q: How should the connector handle native database comments during sync when human-curated descriptions also exist in the Catalog UI? (FR-013) → A: Maintain independent 'source_description' and 'approved_description' fields. The connector continuously updates 'source_description' from native database comments while preserving 'approved_description'. When source comments change or conflict with curated descriptions, the system flags a drift event and provides UI resolution options for the steward (e.g., retain approved description, overwrite with source comment, prioritize either, or view side-by-side to choose).
- Q: Should workers connect to external target databases using ephemeral, non-pooled connections ('NullPool') that disconnect immediately after extraction, or maintain persistent connection pools? (FR-002, FR-016) → A: Ephemeral non-pooled connections ('NullPool'). Target database connections are established strictly for the duration of the metadata extraction job and disconnected immediately upon completion to avoid holding idle connections on target enterprise servers.
- Q: When extraction encounters a failure on one schema (e.g., permission denied) while other schemas succeed, how should the sync job record execution status and handle tables in the failed schema? (FR-009, Edge Cases) → A: Mark status as 'PARTIAL_FAILURE'. Commit the successfully extracted schemas and tables to the catalog, preserve existing catalog tables for the failed schema without soft-deleting them, and record detailed per-schema error diagnostics in 'sync_jobs.error_trace_jsonb'.
- Q: Should routine updates to table row counts and size estimates generate new TableVersion snapshots, or should snapshots be strictly restricted to structural DDL schema changes? (FR-018) → A: Structural DDL changes increment TableVersion and record SchemaChangeLog entries; statistical metadata updates (row counts, size estimates) are logged into a separate lightweight 'table_statistics_history' time-series table and updated in-place on catalog_tables without bumping TableVersion.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automated Scheduled Metadata Extraction via Distributed Workers (Priority: P1)

As a Data Platform Administrator, I want the system to dynamically execute scheduled metadata extractions across all configured enterprise databases without manual intervention, so that the catalog accurately reflects the latest schema state across the entire data estate.

**Why this priority**: Automated synchronization is the foundational requirement for Phase 2. Without a reliable scheduler and distributed worker fleet, metadata must be pushed manually via REST API, which does not scale across 800 production databases.

**Independent Test**: Can be fully tested by creating a connection with an active sync schedule, triggering the scheduler tick, verifying that a distributed worker securely claims the job, establishes an ephemeral read-only session with strict timeouts, and successfully records a completed `sync_jobs` execution record.

**Acceptance Scenarios**:
1. **Given** an enabled sync schedule with a valid cron expression in `sync_schedules`, **When** the dynamic scheduler detects the scheduled time has arrived, **Then** a sync task is enqueued with jitter to prevent load spikes, transitioned to `QUEUED`, and picked up by an available worker pool.
2. **Given** a queued sync task, **When** a distributed worker begins execution, **Then** the job status transitions to `RUNNING`, the worker securely unwraps the database credentials in worker memory without logging secrets or transmitting plaintext over the broker, and executes metadata extraction using an ephemeral read-only session (`NullPool`) with strict statement timeouts.
3. **Given** a database that completes extraction successfully, **When** the worker finishes processing, **Then** the worker immediately disconnects and closes the target connection, the job status transitions to `SUCCESS`, `last_run_at` and `last_run_status` are updated in `sync_schedules`, and metrics (duration, tables scanned, changes detected) are recorded.

---

### User Story 2 - Pluggable System-Catalog Connector Extraction (Priority: P1)

As a Data Architect, I want standardized metadata connectors that query low-level native database system catalogs (`pg_catalog` for PostgreSQL, `sys.*` for MS SQL Server) rather than slow ANSI `information_schema` views, so that schema extraction across hundreds of tables and thousands of columns completes in minimal round-trips without locking production workloads.

**Why this priority**: Querying standard `information_schema` views against large production databases causes catastrophic query execution times and table locks. High-speed native system catalog extraction is critical for meeting SLA limits across 800 databases.

**Independent Test**: Can be fully tested by running `BaseMetadataConnector.extract_tables()` and `extract_columns()` against a test PostgreSQL instance with 200+ tables and verifying that all tables, columns, data types, nullability, primary/foreign keys, table comments, and row count estimates are extracted in under 5 seconds with zero write lock acquisitions and zero residual idle connections.

**Acceptance Scenarios**:
1. **Given** a valid PostgreSQL connection configuration, **When** `test_connection()` is invoked, **Then** the connector verifies network reachability, validates authentication, ensures read-only session capability, and returns a verified status within the connection timeout.
2. **Given** an active PostgreSQL database with multiple schemas and partitioned tables, **When** `extract_tables()` and `extract_columns()` are executed, **Then** native queries against `pg_catalog.pg_class`, `pg_attribute`, `pg_type`, `pg_description`, and `pg_stat_user_tables` retrieve all schema metadata in minimal bulk round-trips over an ephemeral non-pooled connection.
3. **Given** any metadata extraction session, **When** the connector connects to the target database, **Then** it explicitly sets a strict `statement_timeout` and transaction read-only flag so runaway queries are terminated before degrading target database performance, closing the session immediately after reading.

---

### User Story 3 - High-Speed Diffing, Human-Curation Preservation, and Schema Versioning (Priority: P2)

As a Data Governance Steward, I want the ingestion engine to bulk-compare incoming schema snapshots against existing catalog records, detecting structural additions, deletions, and type modifications while strictly preserving human-authored descriptions, steward assignments, and governance tags, so that automated updates never overwrite human curation.

**Why this priority**: Automated ingestion must coexist seamlessly with human data governance. Overwriting human-curated business descriptions or tags during an automated sync destroys stewardship work.

**Independent Test**: Can be fully tested by curating approved descriptions and governance tags on a set of tables and columns, altering source database column types and adding new tables, running the sync pipeline, and verifying that new tables/columns are added, modified types are updated, all curated descriptions and tags remain intact, corresponding `TableVersion` and `SchemaChangeLog` entries are created, and statistical history is appended to `table_statistics_history`.

**Acceptance Scenarios**:
1. **Given** a table or column with both `source_description` and a human-curated `approved_description`, **When** the ingestion pipeline upserts an extracted snapshot with new or modified source comments, **Then** `source_description` is updated to the latest database comment, `approved_description` remains untouched, and a description drift event is recorded for steward review.
2. **Given** an existing catalog table that has been dropped or removed from the source database, **When** the diff engine processes the snapshot, **Then** the table and its columns are soft-deleted (`status = DELETED`), and a `SchemaChangeLog` event (`COLUMN_DROPPED` / `TABLE_DROPPED`) is recorded. Tables in schemas that failed extraction are not marked as deleted.
3. **Given** any structural alteration (column added, column dropped, data type changed, nullability altered), **When** the upsert transaction commits, **Then** a new immutable `TableVersion` snapshot is generated incrementing `version_num`, and individual diffs are logged in `schema_change_logs`. Statistical changes (row count, size bytes) update `catalog_tables` in place and append to `table_statistics_history` without incrementing `version_num`.

---

### User Story 4 - Resilient Execution, Concurrency Pools, and Circuit Breaking (Priority: P3)

As an Operations Engineer, I want the ingestion engine to manage concurrency pools, apply backoff retries, and trip circuit breakers for unreachable databases, so that network outages or down databases do not exhaust worker resources or cascade into platform failures.

**Why this priority**: In an estate of 800 databases, intermittent network blips and maintenance windows are common. Without circuit breakers and exponential backoff, worker queues become clogged with failing retries.

**Independent Test**: Can be fully tested by pointing sync jobs to unreachable and timeout-triggering endpoints, verifying that retry attempts back off exponentially, and after exceeding failure thresholds, the circuit breaker transitions the connection to a suspended/tripped state, recording detailed error diagnostics in `sync_jobs`.

**Acceptance Scenarios**:
1. **Given** 800 scheduled sync jobs configured across the data estate, **When** the scheduler processes midnight schedules, **Then** execution start times are jittered across a configurable time window to prevent a thundering herd against Redis and workers.
2. **Given** a database host that is temporarily unreachable, **When** a worker attempts extraction, **Then** the worker applies exponential backoff retries (10s, 30s, 90s) up to 3 attempts before marking the run as failed.
3. **Given** a database that fails 3 consecutive sync attempts, **When** the threshold is breached, **Then** the circuit breaker trips open into `OPEN` state, suspending future automatic runs for a 15-minute cooldown window or until an administrator tests and resets the connection.

---

### Edge Cases

- **Target Database Read-Only Rejection**: How does the connector handle databases where setting `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` fails due to restricted privileges? The connector verifies read-only session readiness; if rejected, extraction proceeds under read-only queries with a warning logged in `sync_jobs.error_trace_jsonb` while statement timeouts remain strictly enforced.
- **Massive Schema Volume**: How does the diff engine handle schemas containing over 5,000 tables or 100,000 columns without running out of worker memory or exceeding transaction limits? Batched upserts and keyset chunking in blocks of 500 tables prevent memory bloat.
- **Partial Extraction Failure**: When individual schemas fail during extraction (e.g., permission denied or locked catalog) while others succeed, the job commits the successful schemas, preserves existing tables for the failed schemas in their current state without triggering deletion events, marks job status as `PARTIAL_FAILURE`, and stores per-schema diagnostic details in `sync_jobs.error_trace_jsonb`.
- **Partitioned Tables and Inheritance**: How does the connector represent PostgreSQL partitioned tables (declarative partitioning) to avoid redundant duplicate entries for dozens of partition child tables? Partition roots are captured as tables with type `PARTITIONED_TABLE`, while child partitions are linked or filtered based on catalog configuration.
- **Description Drift Conflict Resolution**: When source DB comments change after a human steward has curated `approved_description`, the system preserves human curation by default and surfaces a drift conflict in the UI with explicit resolution options (keep approved, overwrite with source, prioritize either, or side-by-side comparison).
- **Stale Schema Drift during Ingestion**: If an administrator modifies an approved description while a worker is mid-ingestion, the atomic upsert targets `source_description` and leaves `approved_description` unchanged, guaranteeing that the administrator's edit is never clobbered.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a standardized pluggable connector interface (`BaseMetadataConnector`) defining:
  - `test_connection() -> ConnectionTestResult`
  - `extract_schemas() -> List[SchemaMetadata]`
  - `extract_tables(schema_names: List[str]) -> List[TableMetadata]`
  - `extract_columns(schema_names: List[str]) -> List[ColumnMetadata]`
  - `extract_foreign_keys(schema_names: List[str]) -> List[ForeignKeyMetadata]`
  - `extract_table_statistics(schema_names: List[str]) -> List[TableStatisticsMetadata]`
- **FR-002**: All connector implementations MUST enforce read-only execution sessions, strict query timeouts (`statement_timeout` / query timeout), and ephemeral non-pooled connections (`NullPool`) that connect, execute extraction, and immediately disconnect upon job completion without holding idle connections on target databases.
- **FR-003**: System MUST provide an extensible connector registry (`ConnectorRegistry`) allowing dynamic lookup and instantiation of connectors by `DatabaseEngine` type. In Phase 2A, the system MUST implement the `POSTGRESQL` connector; Phase 2B will implement `MSSQL` followed by `ORACLE`.
- **FR-004**: System MUST implement a production-grade PostgreSQL connector in Phase 2A querying native system catalogs (`pg_catalog.pg_class`, `pg_namespace`, `pg_attribute`, `pg_type`, `pg_constraint`, `pg_description`, `pg_stat_user_tables`) instead of ANSI `information_schema` views.
- **FR-005**: System MUST implement a distributed background worker engine using Celery (or Dramatiq) backed by a Redis broker.
- **FR-006**: System MUST implement a dynamic database-backed scheduler that periodically reads cron schedules from `sync_schedules` and schedules execution tasks without requiring worker restarts.
- **FR-007**: System MUST support configurable concurrency pools and queues, isolating high-frequency lightweight jobs from long-running bulk extractions.
- **FR-008**: System MUST apply schedule jitter (randomized time offset) to distributed job dispatching to prevent thundering herd spikes at common cron boundaries (e.g. midnight).
- **FR-009**: System MUST record full job execution history in a dedicated `sync_jobs` table with statuses: `QUEUED`, `RUNNING`, `SUCCESS`, `PARTIAL_FAILURE`, `FAILED`, including execution duration, tables scanned, changes detected, and error traces. If individual schemas fail extraction (e.g., permission denied), the sync job MUST record `PARTIAL_FAILURE`, persist all successful schemas, preserve the existing catalog state of the failed schema without soft-deleting its tables, and log per-schema error details in `error_trace_jsonb`.
- **FR-010**: System MUST enforce envelope encryption standards (AES-256-GCM) during worker execution: Celery task payloads MUST NOT contain plaintext credentials or decrypted secrets; workers MUST retrieve the encrypted DEK and credentials from PostgreSQL and decrypt in worker memory using the master KEK.
- **FR-011**: System MUST implement circuit breaker protection for database connections: after a configurable number of consecutive failed sync attempts (default: 3), the connection schedule MUST enter an `OPEN` (tripped/suspended) state with a 15-minute cooldown period to prevent continuous worker exhaustion.
- **FR-012**: System MUST provide an ingestion diff pipeline that compares extracted snapshots against existing catalog entities and performs bulk upserts (`INSERT ... ON CONFLICT DO UPDATE`) in batches.
- **FR-013**: Ingestion upsert logic MUST maintain independent `source_description` (extracted from native DB comments) and `approved_description` (curated by stewards). Every sync updates `source_description` with the latest extracted comment while strictly preserving `approved_description`. If source comments change while an `approved_description` exists, the system MUST flag a description drift event, allowing stewards via UI controls (radio selection) to choose between retaining curated descriptions, accepting source updates, or merging side-by-side.
- **FR-014**: System MUST detect removed tables and columns and transition their lifecycle status to `DELETED` (soft deletion) while retaining historical version audit logs. Tables belonging to schemas that encountered extraction errors MUST NOT be marked `DELETED`.
- **FR-015**: System MUST automatically generate an immutable `TableVersion` snapshot and corresponding `SchemaChangeLog` entries whenever a structural schema change is detected.
- **FR-016**: Connector execution MUST use ephemeral non-pooled database connections (`NullPool`) configured with explicit session-level read-only parameters (`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`) and strict statement timeouts (default: 30s), ensuring zero persistent idle connection overhead on target database engines.
- **FR-017**: Distributed job orchestration MUST handle transient failures using connection timeouts (10s), statement timeouts (30s), exponential backoff retries (3 attempts: 10s, 30s, 90s), and transition connection state to `OPEN` (tripped) after 3 consecutive failed runs with a 15-minute cooldown window.
- **FR-018**: Schema diff engine MUST restrict `TableVersion` snapshot generation and `SchemaChangeLog` entries strictly to structural DDL changes (added/removed columns, modified types/nullability, primary/foreign key alterations). Routine statistical metadata updates (`row_count_estimate`, `size_bytes`) MUST update `catalog_tables` in place and append an entry to a dedicated lightweight `table_statistics_history` time-series table without incrementing `version_num`.

---

### Key Entities

- **SyncJob (`sync_jobs`)**: Represents an individual execution run of a metadata extraction and ingestion job for a database connection. Attributes: `id`, `connection_id`, `schedule_id`, `status` (`QUEUED`, `RUNNING`, `SUCCESS`, `PARTIAL_FAILURE`, `FAILED`), `triggered_by` (`SCHEDULE`, `MANUAL`, `RETRY`), `started_at`, `completed_at`, `duration_ms`, `tables_scanned`, `tables_created`, `tables_updated`, `tables_deleted`, `columns_scanned`, `changes_detected`, `error_message`, `error_trace_jsonb`.
- **BaseMetadataConnector**: Abstract base class defining uniform metadata extraction contracts across diverse database management systems.
- **PostgresMetadataConnector**: Reference implementation of `BaseMetadataConnector` executing optimized queries against PostgreSQL `pg_catalog`.
- **ConnectorRegistry**: Central registry mapping `DatabaseEngine` enum values to concrete connector classes with configuration validation.
- **CircuitBreakerState**: Runtime and persistent tracking of failure counts, circuit state (`CLOSED`, `OPEN`, `HALF_OPEN`), and cooldown expiration timestamps per connection.
- **TableStatisticsHistory (`table_statistics_history`)**: Lightweight time-series log recording historical table metrics without bumping structural table versions. Attributes: `id`, `table_id`, `recorded_at`, `row_count_estimate`, `size_bytes`, `sync_job_id`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Metadata extraction of a 200-table, 5,000-column PostgreSQL database completes in under 10 seconds end-to-end (from worker claim to ingestion completion).
- **SC-002**: Distributed scheduler and worker engine successfully orchestrates and processes 800 database sync jobs within a 4-hour nightly window without worker crashes or broker queue starvation.
- **SC-003**: 100% of human-curated approved descriptions, custom tags, and steward assignments remain intact and unaltered following repeated automated sync and bulk upsert operations.
- **SC-004**: 100% of structural schema modifications (new columns, dropped columns, altered data types, altered nullability) automatically generate accurate `SchemaChangeLog` diff entries and incremented `TableVersion` snapshots.
- **SC-005**: Zero plaintext credentials or unwrapped Data Encryption Keys appear in Redis message queues, Celery task logs, or error traces under any condition.
- **SC-006**: Target production databases experience zero table lock contention or blocking transactions caused by catalog extraction queries, and zero idle connection retention on target servers, verified by `NullPool` lifecycle tests.
- **SC-007**: When target databases experience network unavailability, circuit breakers trip within 3 consecutive failed runs, preventing 100% of redundant reconnection attempts during the cooldown period.
- **SC-008**: Job execution history and status transitions (`QUEUED` -> `RUNNING` -> `SUCCESS` / `FAILED`) are accurately recorded in `sync_jobs` with latency under 100ms.

---

## Assumptions

- **Phase 1 Foundation**: Reuses existing Phase 1 models (`DatabaseConnection`, `SyncSchedule`, `DatabaseSchema`, `CatalogTable`, `CatalogColumn`, `TableVersion`, `SchemaChangeLog`, `AuditLog`) and AES-256-GCM `EnvelopeEncryptionService`.
- **Broker Infrastructure**: Redis instance is deployed and accessible to both the dynamic Beat scheduler and Celery worker fleet.
- **Network Topology**: Distributed workers reside in network zones with routing access to the target customer databases and the centralized PostgreSQL catalog.
- **Target DB Permissions**: The credentials stored for target databases grant `CONNECT` and `SELECT` access to native system catalog views (`pg_catalog`, `sys.*`).
- **Worker Scaling**: Worker instances can scale horizontally across multiple worker nodes running Celery worker processes.

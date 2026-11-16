# Quickstart Validation Guide: Phase 2 Connectors, Workers & Dynamic Scheduler

**Feature Branch**: `002-connectors-workers-scheduler`  
**Date**: 2026-11-16  

This guide provides end-to-end operational scenarios to validate the implementation of Phase 2 against real PostgreSQL databases and Celery worker fleets.

---

## 1. Prerequisites & Environment Setup

### 1.1 Infrastructure Services
Ensure local services are running:
- PostgreSQL catalog database on port `5432` (`catalog_db`)
- Target PostgreSQL test database on port `5433` (with sample 200-table schema)
- Redis broker on port `6379` (`redis://localhost:6379/0`)

```powershell
# Verify Redis broker availability
redis-cli -h localhost -p 6379 ping
# Expected: PONG

# Run Alembic migrations for Phase 2
poetry run alembic upgrade head
```

### 1.2 Start Background Workers & Dynamic Beat Scheduler
Open two terminal windows:

**Terminal 1: Celery Distributed Worker Fleet**
```powershell
poetry run celery -A backend.src.core.celery_app worker -l INFO -Q metadata.priority,metadata.fast,metadata.bulk -c 4
```

**Terminal 2: Celery Dynamic Beat Scheduler**
```powershell
poetry run celery -A backend.src.core.celery_app beat -l INFO --scheduler backend.src.services.scheduler.dynamic_beat:DatabaseScheduler
```

---

## 2. Validation Scenarios

### Scenario 1: Ephemeral Connection Test with Structured Diagnostics (FR-001, FR-002, FR-016)
**Goal**: Verify target database reachability, in-memory credential unwrapping, read-only session enforcement, and statement timeout.

1. **Send Diagnostic Ping Request**:
```bash
curl -X POST "http://localhost:8000/api/v1/connections/${CONNECTION_ID}/test" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

2. **Expected Outcome**:
- Status code `200 OK`
- JSON payload matches `ConnectionDiagnosticTestResponse` ([contracts/openapi.yaml](contracts/openapi.yaml)):
  - `success`: `true`
  - `latency_ms`: `< 50.0`
  - `dialect`: `"POSTGRESQL"`
  - `diagnostic_details.read_only_supported`: `true`
  - `diagnostic_details.statement_timeout_ms`: `30000`
- Target PostgreSQL server shows `0` residual active or idle connections from the catalog worker.

---

### Scenario 2: High-Speed Introspection & Bulk `UNNEST` Upsert (< 3 Seconds) (FR-004, FR-012, SC-001)
**Goal**: Verify bulk introspection and vectorized `UNNEST` upsert of 200 tables and 5,000 columns in under 3 seconds end-to-end.

1. **Trigger Manual Synchronization**:
```bash
curl -X POST "http://localhost:8000/api/v1/connections/${CONNECTION_ID}/sync" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

2. **Poll Sync Job Execution Status**:
```bash
curl -X GET "http://localhost:8000/api/v1/sync-jobs/${JOB_ID}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

3. **Expected Outcome**:
- Status transitions: `QUEUED` $\rightarrow$ `RUNNING` $\rightarrow$ `SUCCESS`
- `tables_scanned`: `200`
- `columns_scanned`: `5000`
- `duration_ms`: `< 3000`
- All tables and columns appear in `/api/v1/schemas/{schema_id}/tables`.

---

### Scenario 3: Human Curation Preservation & Description Drift Detection (FR-013, SC-003)
**Goal**: Verify automated sync preserves human-curated descriptions while capturing native DB comments and flagging drift.

1. **Curate an Approved Description on a Target Table**:
```bash
curl -X PATCH "http://localhost:8000/api/v1/tables/${TABLE_ID}/description" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"approved_description": "Curated by Data Governance Steward"}'
```

2. **Alter Native Comment on Target Database**:
```sql
-- On target database:
COMMENT ON TABLE users IS 'Updated database comment from DBA';
```

3. **Re-run Sync**:
```bash
curl -X POST "http://localhost:8000/api/v1/connections/${CONNECTION_ID}/sync" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

4. **Verify Table Detail**:
```bash
curl -X GET "http://localhost:8000/api/v1/tables/${TABLE_ID}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

5. **Expected Outcome**:
- `approved_description`: `"Curated by Data Governance Steward"` (strictly preserved)
- `source_description`: `"Updated database comment from DBA"`
- `description_drift_detected`: `true`
- Table drift details available at `/api/v1/tables/${TABLE_ID}/drift`

6. **Resolve Drift**:
```bash
curl -X POST "http://localhost:8000/api/v1/tables/${TABLE_ID}/drift/resolve" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"resolution_action": "RETAIN_APPROVED"}'
```
- `description_drift_detected` transitions to `false`.

---

### Scenario 4: Statistical Separation vs DDL Versioning (FR-015, FR-018, SC-004)
**Goal**: Prove row count and size updates do NOT increment `TableVersion`, while column DDL mutations do.

1. **Record Baseline Table Version**:
```bash
curl -X GET "http://localhost:8000/api/v1/tables/${TABLE_ID}/versions" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
# Let baseline version_num = 1
```

2. **Insert Rows in Target Database and Re-Sync**:
```sql
-- On target database:
INSERT INTO users (name) SELECT 'test_' || i FROM generate_series(1, 1000) AS i;
ANALYZE users;
```
Trigger sync.

3. **Verify Version & Statistics**:
- `GET /api/v1/tables/${TABLE_ID}/versions` still returns `version_num: 1` (unchanged).
- `GET /api/v1/tables/${TABLE_ID}/statistics` returns new entry in `TableStatisticsHistory` reflecting the updated row count.

4. **Perform Structural Alteration and Re-Sync**:
```sql
-- On target database:
ALTER TABLE users ADD COLUMN loyalty_tier VARCHAR(50);
```
Trigger sync.

5. **Expected Outcome**:
- `GET /api/v1/tables/${TABLE_ID}/versions` returns new `version_num: 2`.
- `SchemaChangeLog` contains record with `change_type: COLUMN_ADDED`, `column_name: "loyalty_tier"`.

---

### Scenario 5: Schema-Level Partial Failure Isolation (FR-009, Edge Cases)
**Goal**: Prove failures on one schema do not soft-delete its tables or fail other schemas.

1. **Revoke Access to one Schema on Target DB**:
```sql
REVOKE USAGE ON SCHEMA restricted_schema FROM catalog_worker_role;
```

2. **Trigger Sync**:
```bash
curl -X POST "http://localhost:8000/api/v1/connections/${CONNECTION_ID}/sync" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

3. **Expected Outcome**:
- `SyncJob.status` is `PARTIAL_FAILURE`.
- Tables in `public` schema are successfully ingested and updated.
- Tables in `restricted_schema` retain their existing catalog status (`ACTIVE`, not `DELETED`).
- `SyncJob.error_trace_jsonb` logs `{"failed_schemas": [{"schema_name": "restricted_schema", "error_code": "42501"}]}`.

---

### Scenario 6: Circuit Breaker State Transition (FR-011, FR-017, SC-007)
**Goal**: Verify circuit breaker trips after 3 consecutive failures and suspends worker retries.

1. **Point Connection to an Unreachable Host / Port**:
Update connection with host `192.0.2.1` (TEST-NET unreachable).

2. **Trigger 3 Sync Runs**:
- Run 1 fails: `consecutive_failures = 1`, `circuit_state = 'CLOSED'`.
- Run 2 fails: `consecutive_failures = 2`, `circuit_state = 'CLOSED'`.
- Run 3 fails: `consecutive_failures = 3`, `circuit_state = 'OPEN'`, `cooldown_until` set to `now() + 15m`.

3. **Dynamic Beat Scheduler Check**:
- Dynamic scheduler skips execution for this connection while `circuit_state == 'OPEN'`.
- Calling `POST /api/v1/connections/{id}/circuit-breaker/reset` resets state back to `CLOSED`.

---

## 3. Automated Test Verification

Execute the complete backend test suite:

```powershell
# Run unit tests
poetry run pytest tests/unit/ -v

# Run integration tests against real PostgreSQL container
poetry run pytest tests/integration/test_connector_postgres.py -v
poetry run pytest tests/integration/test_bulk_ingestion_unnest.py -v
poetry run pytest tests/integration/test_circuit_breaker.py -v
poetry run pytest tests/integration/test_description_drift.py -v

# Run frontend tests
cd frontend
npm test
```

# Quickstart Validation Guide (Phase 1)

**Feature**: Enterprise Data Catalog (Phase 1 - Core Platform)  
**Branch**: `001-core-platform`  
**Date**: 2026-11-09  

---

## 1. Prerequisites & Environment Setup

- Python 3.12+ with virtualenv or Poetry
- Node.js 20+ & Angular CLI 18+
- PostgreSQL 16 server (or Docker container)
- Environment Variables:
  ```bash
  export DATABASE_URL="postgresql+asyncpg://catalog_admin:secret@localhost:5432/enterprise_datacatalog"
  export CATALOG_MASTER_KEK="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" # 64 hex chars (256 bits)
  export JWT_SECRET_KEY="super-secret-jwt-signing-key-for-development"
  ```

---

## 2. Database Initialization & Migration

```bash
# 1. Start PostgreSQL 16 test container
docker run -d --name catalog-pg -p 5432:5432 -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=enterprise_datacatalog postgres:16-alpine

# 2. Run Alembic migrations to apply DDL, GIN indexes, partitions, and materialized views
cd backend
poetry run alembic upgrade head
```

---

## 3. Core Validation Scenarios

### Scenario 1: Envelope Encryption & Connection Profile Registration
- **Action**: Register a PostgreSQL source connection with sensitive password and custom SSL parameters.
- **Verification**:
  1. Inspect PostgreSQL `database_connections` table directly.
  2. Verify `encrypted_dek` and `encrypted_credentials` are binary byte arrays.
  3. Verify plaintext password is not present anywhere in database rows or logs.
  4. Query `/api/v1/connections/{id}` and confirm credentials are redacted.

### Scenario 2: High-Performance Search & Keyset Cursor Navigation (160k Tables)
- **Action**: Run the synthetic benchmark seeder to populate 160,000 tables and 5,000,000 columns.
  ```bash
  poetry run python -m scripts.seed_benchmark --databases 800 --tables 160000 --columns 5000000
  ```
- **Verification**:
  1. Execute `/api/v1/search?q=customer_order&limit=50`.
  2. Confirm response time is `< 100ms`.
  3. Extract `next_cursor` from response.
  4. Execute `/api/v1/search?q=customer_order&limit=50&cursor={next_cursor}` and verify seamless sequential pagination without table scanning.

### Scenario 3: SQL-Level RBAC Enforcement
- **Action**:
  1. Create User `AnalystA` granted `VIEW_METADATA` only on `Database_Analytics`.
  2. Add an explicit DENY rule on table `Database_Analytics.public.salary_records`.
  3. Issue search query `/api/v1/search?q=salary` under `AnalystA` JWT token.
- **Verification**:
  1. Query executes via SQLAlchemy with push-down subquery filter.
  2. Zero rows from `salary_records` appear in results.
  3. Attempting direct access to `/api/v1/tables/{salary_table_id}` returns HTTP 403 Forbidden.

### Scenario 4: Immutable Month-Partitioned Audit Trail
- **Action**: Update table description via `PATCH /api/v1/tables/{table_id}/description`.
- **Verification**:
  1. Query `audit_logs` table for that table ID.
  2. Confirm audit record was created in the current month partition (`audit_logs_y2026m11`).
  3. Confirm `diff_jsonb` contains `{ "old": ..., "new": ... }`.
  4. Attempt direct SQL `DELETE FROM audit_logs WHERE id = ...;` and confirm it is blocked by the immutability rule.

### Scenario 5: Angular 18 CDK Virtual Scroll 60 FPS Test
- **Action**: Open Angular client at `http://localhost:4200/catalog/tables`.
- **Verification**:
  1. Open Chrome DevTools Performance monitor.
  2. Fast-scroll through 10,000 tables in the virtual viewport.
  3. Verify DOM node count remains constant (~40 items rendered in viewport DOM).
  4. Verify frame rate stays at 60 FPS without memory leaks.


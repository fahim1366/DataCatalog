# Technical Research & Architecture Decisions (Phase 0)

**Feature**: Enterprise Data Catalog (Phase 1 - Core Platform)  
**Branch**: `001-core-platform`  
**Date**: 2026-11-09  

---

## 1. High-Performance Metadata Search & Keyset Pagination (160k Tables, 5M Columns)

### Context & Target
The catalog must store and search 800 databases, 160,000 tables, and 5,000,000 columns. API responses for full-text search, prefix matching, and catalog table navigation must return in `< 100ms` for p95 requests while simultaneously enforcing user RBAC filters.

### Decisions
1. **Search Indexing Strategy**:
   - Utilize PostgreSQL 16 `GENERATED ALWAYS AS (to_tsvector('english', coalesce(name, '') || ' ' || coalesce(source_description, '') || ' ' || coalesce(approved_description, ''))) STORED` on `catalog_tables` and `catalog_columns`.
   - Index the generated `tsvector` columns with PostgreSQL **GIN** (`gin(search_vector)`).
   - Add `pg_trgm` GIN indexes on `name gin_trgm_ops` to support lightning-fast substring and trigram matching (`ILIKE '%prefix%'` or typo-tolerant search) without table scans.
2. **Keyset / Cursor-Based Pagination**:
   - Offset-based pagination (`OFFSET 50000 LIMIT 50`) degrades to $O(N)$ scanning all previous rows and disk pages, resulting in several seconds of query latency at 160,000 tables.
   - We mandate **deterministic composite keyset pagination**:
     `WHERE (t.name, t.id) > (:cursor_name, :cursor_id) ORDER BY t.name ASC, t.id ASC LIMIT :limit`.
   - Base-64 encoded opaque cursors contain `(sort_value, id)`.
   - Composite B-Tree index: `CREATE INDEX idx_tables_keyset_nav ON catalog_tables (schema_id, name ASC, id ASC) WHERE status != 'DELETED'`.

### Alternatives Evaluated & Rejected
- *Elasticsearch / OpenSearch*: Adding an external search cluster introduces dual-write consistency hazards, distributed transactions, and synchronization latency during Phase 1. PostgreSQL 16 GIN + `pg_trgm` provides native ACID compliance and comfortably handles 5,000,000 column rows in < 50ms with appropriate memory (`shared_buffers`, `work_mem`).
- *Offset / Limit Pagination*: Prohibited by the Enterprise Constitution. Scans thousands of index tuples; unusable at 160k tables.

---

## 2. SQL-Level RBAC & Policy Enforcement via FastAPI Dependency Injection

### Context & Target
Users and groups have permissions granted at the DatabaseSource level (cascading to schemas and tables), overridden by explicit table-level DENY rules or tag policies (e.g., PII), plus Category-tree RBAC. Loading unauthorized rows into Python memory and filtering with list comprehensions or Pydantic filters would consume gigabytes of RAM, take multiple seconds, and violate security isolation.

### Decisions
1. **Push-Down Filtering to PostgreSQL**:
   - RBAC rules MUST be injected into the `WHERE` clause of every SQLAlchemy query as an `EXISTS` or `IN` subquery.
   - Direct SQL Predicate:
     ```sql
     WHERE (
       -- User is superadmin OR has direct global permission
       :is_admin = TRUE
       OR (
         -- Inherited from DatabaseSource (ALLOW)
         EXISTS (
           SELECT 1 FROM permission_rules pr
           JOIN user_roles ur ON ur.role_id = pr.role_id
           WHERE ur.user_id = :user_id
             AND pr.action = 'VIEW_METADATA'
             AND pr.effect = 'ALLOW'
             AND (
               (pr.resource_type = 'GLOBAL')
               OR (pr.resource_type = 'DATABASE' AND pr.resource_id = d.id)
               OR (pr.resource_type = 'SCHEMA' AND pr.resource_id = s.id)
               OR (pr.resource_type = 'TABLE' AND pr.resource_id = t.id)
             )
         )
         -- AND NOT explicitly DENIED on table
         AND NOT EXISTS (
           SELECT 1 FROM permission_rules pr_deny
           JOIN user_roles ur_deny ON ur_deny.role_id = pr_deny.role_id
           WHERE ur_deny.user_id = :user_id
             AND pr_deny.action = 'VIEW_METADATA'
             AND pr_deny.effect = 'DENY'
             AND (
               (pr_deny.resource_type = 'TABLE' AND pr_deny.resource_id = t.id)
               OR (pr_deny.resource_type = 'SCHEMA' AND pr_deny.resource_id = s.id)
             )
         )
         -- AND NOT restricted by confidential tag unless user has tag permission
         AND NOT EXISTS (
           SELECT 1 FROM tag_assignments ta
           JOIN catalog_tags ct ON ct.id = ta.tag_id
           WHERE ta.entity_type = 'TABLE' AND ta.entity_id = t.id
             AND ct.name = 'PII'
             AND NOT EXISTS (
               SELECT 1 FROM user_roles ur_tag
               JOIN role_permissions rp_tag ON rp_tag.role_id = ur_tag.role_id
               WHERE ur_tag.user_id = :user_id AND rp_tag.permission_name = 'VIEW_PII_METADATA'
             )
         )
       )
     )
     ```
2. **FastAPI Dependency Injection Chain**:
   - `get_current_user`: Validates JWT token, loads user claims, active roles, and permissions into `UserContext`.
   - `get_rbac_filter(action: ActionType)`: Returns an executable SQLAlchemy clause generator `Callable[[Select], Select]` that applies pre-compiled filter clauses directly to the query object before execution.

---

## 3. AES-256-GCM Envelope Encryption for Database Credentials

### Context & Target
Database connection profiles hold host, port, username, password, SSL certificates, and custom JDBC/ODBC parameters. Plaintext storage is strictly forbidden. Encryption/decryption must complete in `< 10ms`.

### Decisions
1. **Two-Tier Envelope Key Hierarchy**:
   - **Key Encryption Key (KEK)**: 256-bit master key held securely outside the database (loaded from environment variable `CATALOG_MASTER_KEK` in Phase 1 via `LocalKMSProvider`, pluggable with AWS KMS / HashiCorp Vault).
   - **Data Encryption Key (DEK)**: Unique 256-bit cryptographically secure symmetric key generated randomly per database connection (`os.urandom(32)`).
2. **Cryptographic Algorithm**:
   - Standard: **AES-256-GCM** (Galois/Counter Mode) via Python `cryptography.hazmat.primitives.ciphers.aead.AESGCM`.
   - DEK is encrypted using KEK via AES-256-GCM with a 96-bit (12-byte) initialization vector (IV/nonce).
   - Connection credentials payload (JSON serialized dict) is encrypted using DEK with its own unique 96-bit IV.
3. **Database Representation**:
   - `encrypted_dek`: BYTEA (Nonce 12 bytes + Encrypted DEK 32 bytes + Auth Tag 16 bytes = 60 bytes).
   - `encrypted_credentials`: BYTEA (Nonce 12 bytes + Ciphertext variable length + Auth Tag 16 bytes).
   - `kek_version`: INT (tracks which KEK version encrypted the DEK, facilitating instant zero-downtime KEK rotation without touching credentials ciphertext).

---

## 4. Immutable Partitioned Audit Logging

### Context & Target
Compliance frameworks (SOC 2, ISO 27001, GDPR) require tamper-evident logging of all state mutations in the same transaction. Scale: millions of audit log records per year.

### Decisions
1. **PostgreSQL 16 Declarative Range Partitioning**:
   - Partition `audit_logs` by `RANGE (created_at)`.
   - Monthly partition scheme: `audit_logs_y2026m11`, `audit_logs_y2026m12`, etc.
   - Primary key includes partition key: `PRIMARY KEY (id, created_at)`.
2. **Immutability Enforcement**:
   - Create a PostgreSQL trigger and rule on `audit_logs` that raises an exception on `UPDATE` or `DELETE`:
     `CREATE RULE audit_logs_no_update AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;`
     `CREATE RULE audit_logs_no_delete AS ON DELETE TO audit_logs DO INSTEAD NOTHING;`
   - Database user granted only `INSERT` and `SELECT` on `audit_logs`.
3. **Transaction Binding**:
   - All state-altering repository methods accept an active `AsyncSession` and must write an `AuditLog` record within the same session flush/commit.

---

## 5. Sub-200ms Executive Dashboard via PostgreSQL Materialized Views

### Context & Target
Executive metrics require aggregating statistics (completeness %, PII distribution, modification velocity, connector health) across 160,000 tables and 5,000,000 columns. Live `COUNT(*)` or `GROUP BY` queries take 1-3 seconds under heavy load.

### Decisions
1. **Materialized Views**:
   - `mv_metadata_completeness`: Pre-aggregates table count, schema count, descriptions filled, owners assigned by database and schema.
   - `mv_classification_distribution`: Pre-aggregates column and table classification tag counts (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `PII`).
   - `mv_schema_stability`: Tracks schema change frequency over 7, 30, and 90 days.
2. **Concurrent Refresh Cadence**:
   - Refreshed concurrently via `REFRESH MATERIALIZED VIEW CONCURRENTLY <view_name>` (requiring a unique index on each MV).
   - Triggered periodically via background schedule (hourly) or on-demand after bulk ingestion runs.
   - Dashboard endpoints query these views directly, returning aggregated metrics in `< 20ms`.

---

## 6. Angular 18 Enterprise Frontend Architecture

### Context & Target
The catalog web client must display thousands of schemas, tables, and columns with instantaneous response, sub-100ms navigation, zero UI freezes, and support executive charts.

### Decisions
1. **Angular 18 Standalone Architecture**:
   - 100% Standalone components and directives (`standalone: true`), no NgModules.
   - Native Angular Signals (`signal()`, `computed()`, `effect()`) for reactive state management.
2. **Virtual Scrolling Strategy for 10k+ Items**:
   - Angular CDK Virtual Scroll (`<cdk-virtual-scroll-viewport itemSize="48">`) for table listings, column inspectors, and audit log streams.
   - Custom `DataSource` implementation connecting the virtual scroll viewport with backend keyset cursor pagination (`scrollIndex` triggers next page prefetch).
3. **Security & Route Protection**:
   - Functional route guards: `canActivate: [authGuard, roleGuard('VIEW_AUDIT_LOGS')]`.
   - Structural directive: `*hasPermission="'EDIT_DESCRIPTION'"` for hiding/disabling inline editing actions.
4. **Data Visualization**:
   - Apache ECharts (`ngx-echarts`) for Executive Reporting: Completeness gauge charts, Sunburst classification hierarchy, and stability trend line charts. Hardware-accelerated canvas rendering ensures 60 FPS performance.


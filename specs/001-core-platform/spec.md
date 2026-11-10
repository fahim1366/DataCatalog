# Feature Specification: Enterprise Data Catalog (Phase 1 - Core Platform)

**Feature Branch**: `001-core-platform`  
**Created**: 2026-11-09  
**Status**: Draft  
**Input**: User description: "We are building Phase 1 (Core Platform) of an Enterprise Data Catalog capable of managing 800 databases, 160,000 tables, and 5,000,000 columns. Tech Stack: Python 3.12 (FastAPI, SQLAlchemy 2.0 Async, Alembic, Pydantic v2), PostgreSQL 16, Angular 18+ (Standalone components, Signals, Angular CDK Virtual Scroll)."

---

## Executive Summary & Scope

The Enterprise Data Catalog (Phase 1 - Core Platform) serves as the central metadata registry, security governance plane, and compliance hub for heterogeneous enterprise data assets. Built to effortlessly scale to **800 databases, 160,000 tables, and 5,000,000 columns**, Phase 1 establishes the foundational data access layers, granular access governance, credential security, schema version tracking, immutable auditing, and executive governance dashboards.

---

## Clarifications

### Session 2026-11-09
- Q: When a user is granted access to a DatabaseSource, should permissions automatically cascade down to all child schemas and tables unless blocked by an explicit DENY rule or tag-based restriction? → A: Hierarchical inheritance with DENY precedence: Access to a DatabaseSource cascades to all schemas and tables by default; explicit table DENY rules or tag policies (e.g., PII) override and revoke access.
- Q: When a table or column disappears from a source database during synchronization, how should its metadata, historical comments, tags, and lineage be preserved and displayed? → A: Soft-delete with historical preservation: Mark status as DELETED, preserve all historical tags, descriptions, and version logs in place as read-only, and display an 'Archived' visual banner in the UI (filtered out of default search results unless explicitly toggled).
- Q: Should executive management reports query live transactional tables directly or use PostgreSQL Materialized Views refreshed periodically to guarantee sub-200ms response times across 160,000 tables? → A: Periodic PostgreSQL Materialized Views: Use PostgreSQL Materialized Views refreshed concurrently (e.g., hourly or post-sync) to guarantee sub-200ms executive dashboard response times without OLTP lock contention.
- Q: If a user edits a column description or attribute in the UI, how should that manual metadata be protected from being overwritten during future automated syncs? → A: Database description precedence with fallback to UI: If a source database comment/description exists, it takes precedence; if no database description is present, the approved/curated description entered via the UI is preserved and displayed. Automated sync updates `source_description` without clearing `approved_description`.
- Q: To guarantee sub-200ms search and table listing latency across 160,000 tables, what authorization evaluation architecture should enforce the hierarchical RBAC and DENY rules? → A: Dual-dimension RBAC (Database-level and Category-tree): RBAC is assigned at the Database level (cascading to child tables) and on a dedicated `Category` entity featuring an unlimited hierarchical tree structure (`parent_id`), where permissions assigned to a category node cascade down its entire sub-tree to categorized data assets.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Metadata Discovery & High-Performance Search with RBAC (Priority: P1)

As a Data Analyst or Engineer, I need to search across 160,000 tables and 5,000,000 columns using keywords, filters, and classifications, and instantly see only the databases, schemas, tables, and columns that my security roles permit me to view.

**Why this priority**: Discoverability is the primary value proposition of an enterprise data catalog. Without sub-second search and strict access filtering, users cannot find or safely explore enterprise data assets.

**Independent Test**:
Can be fully tested by creating metadata for multiple databases with varying access permissions, issuing search queries with different user roles, and verifying that search results return matching tables/columns in < 500ms while strictly filtering out unauthorized entities.

**Acceptance Scenarios**:
1. **Given** a catalog containing 160,000 tables and 5,000,000 columns, **When** a user searches for a keyword (e.g., `customer_churn`), **Then** PostgreSQL GIN full-text search returns ranked results within 500ms.
2. **Given** User A has `VIEW_METADATA` on `Database_Finance` but no permissions on `Database_HR`, **When** User A searches for `salary`, **Then** no tables or columns from `Database_HR` appear in results, summaries, or counts.
3. **Given** a table with 200 columns where 3 columns are classified as `PII` and the user lacks `VIEW_PII_METADATA`, **When** the user opens the table details page, **Then** non-PII columns are visible and PII columns are either masked or excluded according to governance policy.

---

### User Story 2 - Granular Access Control & Hierarchical Permissions (Priority: P1)

As a Data Governance Administrator, I need to configure users, roles, groups, and hierarchical permission rules (`Global -> DatabaseSource -> Schema -> Table -> Column`) with fine-grained actions (`VIEW_METADATA`, `EDIT_DESCRIPTION`, `MANAGE_TAGS`, `MANAGE_CONNECTIONS`, `VIEW_AUDIT_LOGS`, `EXPORT_REPORTS`).

**Why this priority**: Enterprise metadata contains critical structural IP and sensitive data classifications. Robust RBAC/ABAC is required before any connection or catalog data is shared.

**Independent Test**:
Can be fully tested by creating a hierarchy of permissions across a database, schema, and table, assigning them to roles/groups, and executing API calls under those credentials to verify that allowed actions succeed and unauthorized actions fail with HTTP 403 and produce audit log entries.

**Acceptance Scenarios**:
1. **Given** a role granted `VIEW_METADATA` on `DatabaseSource_A`, **When** a user in that role accesses any schema or table within `DatabaseSource_A`, **Then** access is inherited and allowed.
2. **Given** an explicit permission override denying access to `Table_Confidential` within `Schema_Public`, **When** the user attempts to view `Table_Confidential`, **Then** the explicit denial or lower-level override takes precedence and access is rejected.
3. **Given** a user with only `EDIT_DESCRIPTION` permission on a table, **When** they attempt to update table tags or modify connection parameters, **Then** the request is rejected with HTTP 403 Forbidden.

---

### User Story 3 - Database Connection Registry & Secure Credential Storage (Priority: P1)

As a Data Platform Administrator, I need to register, configure, and secure connection profiles for heterogeneous database engines (PostgreSQL, MS SQL Server, Oracle, MySQL, ClickHouse) with cron-based sync schedules and AES-256-GCM envelope encrypted credentials.

**Why this priority**: Database connection profiles hold sensitive connection strings and credentials required for Phase 2 automated ingestion workers. Secrets must never be stored in plaintext.

**Independent Test**:
Can be fully tested by creating connection profiles for each supported dialect, verifying that credentials stored in PostgreSQL are cryptographically encrypted via AES-256-GCM with individual Data Encryption Keys (DEKs), and validating cron schedule syntax.

**Acceptance Scenarios**:
1. **Given** a database connection profile creation request with database type, host, port, username, and password, **When** saved to the registry, **Then** the password and secret connection parameters are encrypted using AES-256-GCM envelope encryption prior to persisting to PostgreSQL.
2. **Given** an authorized administrator inspecting database connections, **When** retrieving connection details via API or UI, **Then** credentials are never returned in plaintext (masked/redacted).
3. **Given** a connection schedule configured with a cron expression, **When** an invalid cron string (e.g., `invalid * * *`) is provided, **Then** the API rejects the request with standard validation errors.

---

### User Story 4 - Table History & Schema Versioning (Priority: P2)

As a Data Steward or Engineer, I need to inspect the complete version history of any catalog table and view detailed diffs showing who changed column types, added or dropped columns, or altered documentation and tags over time.

**Why this priority**: Schema drift and undocumented structural changes break downstream ETL and analytics. Complete traceability prevents silent failures and aids root cause analysis.

**Independent Test**:
Can be tested by updating table columns, modifying descriptions, and adding tags, then querying `table_versions` and `schema_change_logs` to ensure an exact snapshot diff is captured with timestamp, actor, old value, and new value.

**Acceptance Scenarios**:
1. **Given** an existing table at Version 1, **When** two new columns are added and one column's data type is changed from `VARCHAR(50)` to `TEXT`, **Then** Version 2 is generated with a snapshot and three discrete change records in `schema_change_logs`.
2. **Given** a user inspecting the table history in the Angular UI, **When** selecting two versions to compare, **Then** a visual diff is displayed showing added columns (green), removed columns (red), and modified attributes (amber).

---

### User Story 5 - Immutable Audit Logging & Security Auditing (Priority: P2)

As an Information Security Auditor, I need an immutable, month-partitioned audit log capturing all authentication events, permission modifications, connection edits, metadata updates, and unauthorized access attempts.

**Why this priority**: Enterprise compliance frameworks (SOC 2, ISO 27001, GDPR, HIPAA) mandate tamper-evident audit trails for all sensitive metadata interactions and credential access.

**Independent Test**:
Can be tested by triggering logins, permission grants, connection updates, and unauthorized requests, then querying the partitioned `audit_logs` table to verify records are written in the same transaction with correct actor details, IP, user-agent, and structured JSONB diffs.

**Acceptance Scenarios**:
1. **Given** any metadata modification or permission update transaction, **When** the transaction commits, **Then** an audit log record is created in the current month's partition containing `actor_id`, `ip_address`, `user_agent`, `entity_type`, `entity_id`, `action`, `diff_jsonb`, and `timestamp`.
2. **Given** an audit log record, **When** any user (including administrators) attempts to update or delete rows from `audit_logs`, **Then** the database rejects the operation (immutable / append-only).

---

### User Story 6 - Executive & Governance Reporting Dashboard (Priority: P2)

As a Chief Data Officer or Governance Manager, I need an executive dashboard visualizing metadata completeness, classification & PII distribution, schema stability trends, connector health, and steward activity across all 800 databases.

**Why this priority**: Leadership requires high-level governance visibility to identify compliance gaps, monitor unowned data assets, and track data stewardship velocity.

**Independent Test**:
Can be tested by querying reporting summary endpoints on a simulated dataset of 800 databases and verifying that aggregated metrics (completeness score, PII counts, stability index) compute within acceptable latency and render via Angular signals and charts.

**Acceptance Scenarios**:
1. **Given** 800 databases with 160,000 tables, **When** the Executive Dashboard loads, **Then** the overall metadata completeness percentage (% with descriptions and owners) renders along with department/database breakdown.
2. **Given** multiple tables with PII and Confidential tags, **When** viewing the Classification Distribution widget, **Then** counts of Public, Internal, Confidential, and PII assets are accurately aggregated.

---

### Edge Cases

- **Scale & Deep Pagination**: A user searches for a common prefix matching 50,000 tables; the API and Angular CDK Virtual Scroll must use keyset/cursor pagination without allocating gigabytes of memory or timing out.
- **Concurrent Schema Modifications**: Multiple data stewards or automated processes update metadata on the same table simultaneously; optimistic locking or version increment checks must prevent overwrite collisions.
- **Permission Revocation Ripple**: When a role's permission on a `DatabaseSource` is revoked, all cached permission evaluation records or active sessions must immediately reflect the revocation for schemas, tables, and columns underneath it.
- **Envelope Encryption KEK Rotation**: When the Master Key (KEK) is rotated, existing Data Encryption Keys (DEKs) can be re-encrypted without requiring re-encryption of all database credentials.
- **Partition Boundary Roll**: As calendar months transition, new monthly audit log partitions must be automatically managed without failing active transactions.
- **Partial Permissions on Columns**: When a user has `VIEW_METADATA` on a table but an ABAC policy restricts `PII` columns, the table metadata must load while omitting or redacting restricted column definitions.

---

## Requirements *(mandatory)*

### Functional Requirements

#### Module 1: Authentication & Granular RBAC/ABAC Engine
- **FR-001**: System MUST support User, Role, and Group management, with users assignable to multiple roles and groups.
- **FR-002**: System MUST enforce access control across two primary dimensions: (1) Direct Database-level RBAC (`Global -> DatabaseSource -> Schema -> Table -> Column`) using DENY-precedence where DatabaseSource grants cascade down to all child schemas and tables unless blocked by an explicit table DENY rule or tag restriction (e.g., PII), and (2) Category-level RBAC governing an unlimited hierarchical tree of categories (`Category` self-referencing tree via `parent_id`), where permissions assigned to a category node cascade down its entire sub-tree to categorized data assets.
- **FR-003**: System MUST support fine-grained action permissions: `VIEW_METADATA`, `EDIT_DESCRIPTION`, `MANAGE_TAGS`, `MANAGE_CONNECTIONS`, `VIEW_AUDIT_LOGS`, and `EXPORT_REPORTS`.
- **FR-004**: System MUST dynamically filter all table listings, search results, and reporting metrics so users only see entities to which their roles or groups grant explicit or inherited access.
- **FR-005**: System MUST authenticate users via Local JWT bearer tokens with secure password hashing (Argon2id/bcrypt), structured with a pluggable authentication provider interface to enable future enterprise OIDC/OAuth2/SSO integrations without redesign.
- **FR-006**: System MUST enforce authorization policies using an indexed permission evaluation strategy combining role-to-database direct grants and category sub-tree traversal (via PostgreSQL recursive CTEs / closure index), pre-filtering search and catalog queries in SQL to guarantee sub-200ms response times across 160,000 tables.

#### Module 2: Database Connection & Schedule Registry
- **FR-007**: System MUST provide registry CRUD for heterogeneous database connections supporting PostgreSQL, MS SQL Server, Oracle, MySQL, and ClickHouse.
- **FR-008**: System MUST store sensitive connection credentials using AES-256-GCM envelope encryption with individual Data Encryption Keys (DEKs).
- **FR-009**: System MUST manage the Master Key (KEK) via an environment-provided 256-bit cryptographically secure key (`CATALOG_MASTER_KEK`) abstracted behind a pluggable `KMSProvider` interface (supporting local AES-256-GCM envelope unwrapping in Phase 1 with seamless migration to Cloud KMS or HashiCorp Vault).
- **FR-010**: System MUST validate and persist standard 5-part cron expressions for database synchronization schedules.
- **FR-011**: System MUST provide an asynchronous live connection test endpoint (`/api/v1/connections/{id}/test`) that attempts a lightweight dialect-specific connection ping with a strict 5-second timeout and returns structured status, latency, and detailed diagnostic error messages.

#### Module 3: Metadata Catalog Engine
- **FR-012**: System MUST model and store metadata hierarchically: `DatabaseSource` -> `DatabaseSchema` -> `CatalogTable` -> `CatalogColumn`.
- **FR-013**: System MUST record table attributes: `name`, `table_type` (TABLE, VIEW, MATERIALIZED_VIEW), `row_count_estimate`, `size_bytes`, `source_description` (synced from DB comments), `approved_description` (curated/approved in UI), `custom_properties` (JSONB), `owner`, `status` (ACTIVE, DEPRECATED, DELETED). When resolving effective descriptions in the API and UI, `source_description` takes precedence if present; if null or empty, `approved_description` is displayed. When a table or column disappears from a source database during sync, the system MUST soft-delete the entity by transitioning status to `DELETED`, lock historical descriptions, tags, and lineage as read-only preserved records, generate a corresponding change record in `schema_change_logs`, and display an "Archived" badge in the UI while filtering deleted entities out of default search views unless explicitly enabled.
- **FR-014**: System MUST record column attributes: `name`, `ordinal_position`, `data_type`, `raw_data_type`, `is_nullable`, `is_primary_key`, `is_foreign_key`, `default_value`, `source_description` (synced from DB comments), `approved_description` (curated/approved in UI), `classification_level` (PUBLIC, INTERNAL, CONFIDENTIAL, PII), and `status` (ACTIVE, DELETED). Column description resolution follows the same precedence rule (`source_description` takes priority; fallback to `approved_description` if absent).
- **FR-015**: System MUST implement a governed Tagging System with hierarchical or standardized tags assignable to Databases, Schemas, Tables, and Columns.
- **FR-016**: System MUST implement high-performance Full-Text Search using PostgreSQL GIN indexing and `tsvector` across all tables and columns, returning results filtered by user RBAC within 500ms.
- **FR-017**: System MUST provide bulk metadata ingestion endpoints utilizing PostgreSQL batch upsert (`INSERT ... ON CONFLICT DO UPDATE`) to process incoming catalog data at enterprise scale, ensuring that automated syncs update `source_description` and physical attributes without overwriting, nullifying, or clearing user-curated `approved_description` values.

#### Module 4: Table History & Schema Versioning
- **FR-018**: System MUST maintain immutable table snapshot versions in `table_versions`, incrementing version identifiers upon structural schema changes.
- **FR-019**: System MUST maintain detailed column-level and attribute-level change logs in `schema_change_logs` capturing `actor_id`, `timestamp`, `change_type` (COLUMN_ADDED, COLUMN_DROPPED, TYPE_CHANGED, DESCRIPTION_UPDATED, TAG_MODIFIED), `old_value`, and `new_value`.
- **FR-020**: System MUST provide a comparison API and Angular UI view displaying side-by-side snapshot diffs between any two versions of a table.

#### Module 5: Immutable Audit Log System
- **FR-021**: System MUST log all state-mutating events (logins, permission changes, connection edits, metadata modifications, unauthorized attempts) to a PostgreSQL table partitioned by month (`audit_logs`).
- **FR-022**: System MUST write audit log entries within the same database transaction as the mutating action, capturing `actor_id`, `ip_address`, `user_agent`, `entity_type`, `entity_id`, `action`, `diff_jsonb`, and `timestamp`.
- **FR-023**: System MUST enforce immutability on `audit_logs` preventing UPDATE and DELETE operations via PostgreSQL table rules/triggers and role permissions.

#### Module 6: Executive & Management Reporting Dashboard
- **FR-024**: System MUST calculate and display the Metadata Completeness Score (% of tables/columns with descriptions and designated owners) across all 800 databases and drill-down by database/schema.
- **FR-025**: System MUST aggregate and visualize Classification & PII Distribution across all databases, identifying counts of PII and Confidential data assets.
- **FR-026**: System MUST compute a Schema Stability Report identifying the most frequently modified tables and schemas over configurable time windows.
- **FR-027**: System MUST provide a Connector Health and Schedule Compliance summary reporting last run status, schedule adherence, and connector status.
- **FR-028**: System MUST display Data Steward Activity trends highlighting top contributors and recent metadata curation actions.
- **FR-029**: Executive dashboard aggregation queries MUST be served via dedicated PostgreSQL Materialized Views (e.g., `mv_metadata_completeness`, `mv_classification_distribution`) refreshed concurrently on a scheduled cadence (or on-demand post-ingestion) to guarantee sub-200ms API response times without causing read contention on transactional tables.

---

### Key Entities

```
+--------------------+        +---------------------+        +--------------------+
|   DatabaseSource   | 1    * |   DatabaseSchema    | 1    * |    CatalogTable    |
|--------------------|--------|---------------------|--------|--------------------|
| id: UUID           |        | id: UUID            |        | id: UUID           |
| name: VARCHAR      |        | database_id: UUID   |        | schema_id: UUID    |
| engine_type: ENUM  |        | name: VARCHAR       |        | name: VARCHAR      |
| connection_id: UUID|        | description: TEXT   |        | table_type: ENUM   |
| owner: VARCHAR     |        +---------------------+        | row_count: BIGINT  |
+--------------------+                                       | size_bytes: BIGINT |
                                                             | source_desc: TEXT  |
                                                             | approved_desc: TEXT|
                                                             | status: ENUM       |
                                                             | custom_props: JSONB|
                                                             +--------------------+
                                                                       | 1
                                                                       | *
+--------------------+        +---------------------+        +--------------------+
|     CatalogTag     | *    * |   TagAssignment     | *    * |   CatalogColumn    |
|--------------------|--------|---------------------|--------|--------------------|
| id: UUID           |        | tag_id: UUID        |        | id: UUID           |
| name: VARCHAR      |        | entity_type: ENUM   |        | table_id: UUID     |
| color: VARCHAR     |        | entity_id: UUID     |        | name: VARCHAR      |
| is_governed: BOOL  |        +---------------------+        | ordinal_pos: INT   |
+--------------------+                                       | data_type: VARCHAR |
                                                             | is_nullable: BOOL  |
                                                             | is_pk: BOOL        |
                                                             | is_fk: BOOL        |
                                                             | source_desc: TEXT  |
                                                             | approved_desc: TEXT|
                                                             | classification:ENUM|
                                                             | status: ENUM       |
                                                             +--------------------+

+--------------------+        +---------------------+        +--------------------+
|  ConnectionProfile |        |    TableVersion     |        |  SchemaChangeLog   |
|--------------------|        |---------------------|        |--------------------|
| id: UUID           |        | id: UUID            |        | id: UUID           |
| db_source_id: UUID |        | table_id: UUID      |        | table_id: UUID     |
| host: VARCHAR      |        | version_num: INT    |        | version_id: UUID   |
| port: INT          |        | snapshot_jsonb:JSONB|        | change_type: ENUM  |
| enc_cred_dek: BYTEA|        | created_at: TIMESTMP|        | old_value: JSONB   |
| enc_cred_data:BYTEA|        +---------------------+        | new_value: JSONB   |
| cron_schedule: STR |                                       | actor_id: UUID     |
+--------------------+                                       +--------------------+

+--------------------+        +---------------------+        +--------------------+
|  CatalogCategory   | 1    * | CategoryAssignment  |        |   PermissionRule   |
| (Hierarchical Tree)|        |---------------------|        |--------------------|
|--------------------|        | category_id: UUID   |        | id: UUID           |
| id: UUID           |        | entity_type: ENUM   |        | subject_type: ENUM |
| name: VARCHAR      |        | entity_id: UUID     |        | subject_id: UUID   |
| parent_id: UUID?   |        +---------------------+        | resource_type: ENUM|
| description: TEXT  |                                       | (DB, CATEGORY, etc)|
+--------------------+                                       | resource_id: UUID  |
                                                             | action: ENUM       |
+--------------------+        +---------------------+        | effect: ALLOW/DENY |
|     AuditLog       |        |   User / Role / Grp |        +--------------------+
| (Monthly Partition)|        |---------------------|
|--------------------|        | id: UUID            |
| id: UUID (PK)      |        | username: VARCHAR   |
| timestamp: TIMESTMP|        | role_name: VARCHAR  |
| actor_id: UUID     |        | group_name: VARCHAR |
| action: VARCHAR    |        +---------------------+
| entity_type: ENUM  |
| entity_id: UUID    |
| diff_jsonb: JSONB  |
| ip_address: INET   |
+--------------------+
```

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Full-text search across 160,000 tables and 5,000,000 columns returns the first page of ranked, RBAC-filtered results in **under 500 milliseconds** under concurrent user load.
- **SC-002**: Metadata catalog table listings and schema navigation respond in **under 250 milliseconds** using keyset/cursor pagination.
- **SC-003**: System supports simultaneous metadata storage and query indexing for **800 distinct database sources, 160,000 tables, and 5,000,000 columns** without unbounded memory growth or backend process crashes.
- **SC-004**: Credential encryption and decryption operations add **less than 10 milliseconds** overhead per connection operation; zero plaintext secrets are persisted to storage, logs, or API responses.
- **SC-005**: Every mutating transaction records an audit log entry in the appropriate monthly partition with **100% integrity** (atomic transaction guarantee; no un-audited state mutation can succeed).
- **SC-006**: Executive Dashboard aggregation endpoints deliver complete governance and completeness metrics across all 800 databases in **under 200 milliseconds** via periodically refreshed PostgreSQL Materialized Views.
- **SC-007**: Angular 18 frontend maintains a smooth **60 FPS** scroll rate when virtual scrolling through datasets of 10,000+ displayed items using Angular CDK Virtual Scroll.
- **SC-008**: 100% of fine-grained access control policies (`VIEW_METADATA`, `EDIT_DESCRIPTION`, `MANAGE_TAGS`, `MANAGE_CONNECTIONS`, `VIEW_AUDIT_LOGS`, `EXPORT_REPORTS`) are verified via automated integration tests against real PostgreSQL instances.

---

## Assumptions

- **Ingestion Execution in Phase 1**: Actual distributed scheduling workers that connect to external databases and run extraction jobs belong to Phase 2; Phase 1 delivers the complete Connection Registry, Credential Encryption, Cron Validation, and Bulk Ingestion APIs to receive and store metadata payloads.
- **Database Engine Support**: Connection profile definitions cover PostgreSQL, Oracle, MS SQL Server, MySQL, and ClickHouse dialects, including engine-specific parameters (e.g., Oracle SID/Service Name, ClickHouse HTTP/Native ports).
- **Hosting & Infrastructure**: Deployment targets Linux container environments with PostgreSQL 16 server possessing adequate disk storage and memory for GIN indexing and partitioned tables.
- **Data Stewards & Ownership**: Owners can be assigned at Database, Schema, or Table level, with child entities inheriting ownership unless explicitly overridden.

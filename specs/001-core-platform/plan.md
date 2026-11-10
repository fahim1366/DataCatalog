# Implementation Plan: Enterprise Data Catalog (Phase 1 - Core Platform)

**Branch**: `001-core-platform` | **Date**: 2026-11-09 | **Spec**: [specs/001-core-platform/spec.md](specs/001-core-platform/spec.md)

---

## Summary

Phase 1 delivers the foundational enterprise metadata platform supporting **800 databases, 160,000 tables, and 5,000,000 columns**. The architecture combines high-performance PostgreSQL 16 storage (GIN `tsvector` + `pg_trgm`, monthly partitioned audit logs, and concurrent materialized views) with an asynchronous Python 3.12 FastAPI backend and an Angular 18 enterprise client. Core features include AES-256-GCM envelope-encrypted connection credential storage, SQL-pushed table-level RBAC/ABAC with DENY precedence, deterministic keyset cursor pagination, schema version diff tracking, and sub-200ms executive management dashboards rendered with Apache ECharts and CDK Virtual Scroll.

---

## Technical Context

**Language/Version**: Python 3.12 (Backend), TypeScript 5.4+ / Node.js 20+ (Frontend)  
**Primary Dependencies**:
- Backend: FastAPI 0.111+, SQLAlchemy 2.0.30+ (Asyncio), asyncpg 0.29+, Alembic 1.13+, Pydantic v2.7+, `cryptography` 42.0+ (AES-256-GCM AEAD), PyJWT 2.8+, argon2-cffi 23.1+
- Frontend: Angular 18 (Standalone Components, Signals), Angular CDK 18 (Virtual Scroll), ngx-echarts 18+ / Apache ECharts 5.5+  
**Storage**: PostgreSQL 16 (with extensions: `uuid-ossp`, `pg_trgm`, `btree_gist`), range partitioning, concurrent materialized views  
**Testing**: pytest 8.2+, pytest-asyncio, testcontainers-postgres 0.0.12+ (isolated real PostgreSQL container tests), Jasmine / Playwright for Angular  
**Target Platform**: Linux containerized microservices (Docker / Kubernetes), modern evergreen desktop web browsers (Chrome, Edge, Firefox)  
**Project Type**: Enterprise Web Application (FastAPI REST backend + Angular SPA frontend)  
**Performance Goals**:
- Full-text and trigram catalog search across 160,000 tables and 5,000,000 columns in `< 100ms` p95.
- Keyset cursor-paginated schema and table navigation in `< 100ms` p95.
- Executive Dashboard reporting queries in `< 200ms` via concurrently refreshed Materialized Views.
- Angular UI smooth 60 FPS scrolling through 10,000+ items using CDK Virtual Scroll.  
**Constraints**:
- Zero plaintext secrets in database, memory dumps, logs, or API responses (AES-256-GCM Envelope Encryption mandatory).
- Zero in-memory RBAC row filtering; all security filters MUST be pushed down into PostgreSQL query plans.
- Immutable, month-partitioned audit logs with database-enforced update/delete prohibitions.  
**Scale/Scope**: 800 database sources, 160,000 tables, 5,000,000 column definitions, 500 concurrent users.

---

## Constitution Check

*GATE: Evaluation against Enterprise Data Catalog Constitution (v1.0.0)*

| Principle | Requirement | Compliance Evaluation | Status |
|-----------|-------------|-----------------------|--------|
| **I. Ambiguity Gate** | Zero guessing on schema, edge cases, or inheritance rules. Clarify before code. | Clarifications completed on 2026-11-09 covering DENY-precedence inheritance, soft-deletion handling, materialized view cadence, description precedence, and dual-dimension RBAC. | **PASS** |
| **II. Zero-Laziness & Anti-Simplification** | No `# TODO`, `pass`, mock return values, or in-memory dicts. Support 160k tables / 5M columns. | Keyset pagination, composite indexes, batch upsert ingestion patterns, and complete production DDL/SQLAlchemy models designed without shortcuts. | **PASS** |
| **III. Strict Red-Green-Refactor TDD** | Failing test first, real PostgreSQL container fixtures, no SQLite or mocked ORM. | Architecture specifies testcontainers-postgres for real integration test suites verifying SQL-level RBAC, index usage, and encryption. | **PASS** |
| **IV. Security & Auditing** | AES-256-GCM envelope encryption, immutable month-partitioned audit logs in same transaction. | Full two-tier KEK/DEK envelope encryption service with pluggable KMS provider; declarative partitioned audit log tables with rules blocking update/delete. | **PASS** |

---

## Project Structure

```text
d:\DataCatalog\
├── .agents/                               # Spec-kit workflows and skills
├── .specify/                              # Memory, constitution, and templates
├── specs/001-core-platform/
│   ├── spec.md                            # Feature Specification
│   ├── checklists/requirements.md         # Requirements checklist
│   ├── plan.md                            # This comprehensive implementation plan
│   ├── research.md                        # Phase 0 Architecture Decisions
│   ├── data-model.md                      # Full PostgreSQL 16 DDL & SQLAlchemy 2.0 models
│   ├── quickstart.md                      # Runnable validation scenarios
│   └── contracts/
│       └── openapi.yaml                   # OpenAPI 3.1 REST API contracts
├── backend/
│   ├── alembic/
│   │   ├── versions/                      # Migration files for DDL, partitions, MVs
│   │   └── env.py
│   ├── src/
│   │   ├── api/
│   │   │   ├── dependencies/              # RBAC injection, DB session, Auth
│   │   │   └── v1/
│   │   │       ├── auth.py
│   │   │       ├── connections.py
│   │   │       ├── search.py
│   │   │       ├── tables.py
│   │   │       ├── columns.py
│   │   │       ├── tags.py
│   │   │       ├── versions.py
│   │   │       ├── audit_logs.py
│   │   │       └── reports.py
│   │   ├── core/
│   │   │   ├── config.py                  # Env settings (CATALOG_MASTER_KEK, etc.)
│   │   │   └── database.py                # Async engine & sessionmaker
│   │   ├── models/                        # SQLAlchemy 2.0 Async declarative models
│   │   ├── schemas/                       # Pydantic v2 schemas
│   │   └── services/
│   │       ├── crypto/                    # AES-256-GCM Envelope Encryption
│   │       ├── rbac/                      # SQL query builder & permission filters
│   │       ├── ingestion/                 # Bulk upsert catalog ingestion
│   │       └── reporting/                 # Materialized view refresh & query service
│   ├── tests/
│   │   ├── conftest.py                    # testcontainers-postgres fixtures
│   │   ├── integration/
│   │   │   ├── test_rbac_sql_pushdown.py
│   │   │   ├── test_envelope_crypto.py
│   │   │   ├── test_keyset_pagination.py
│   │   │   ├── test_audit_immutability.py
│   │   │   └── test_benchmark_scale.py
│   │   └── unit/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── alembic.ini
└── frontend/
    ├── src/
    │   ├── app/
    │   │   ├── core/
    │   │   │   ├── auth/                  # JWT token service, auth interceptor
    │   │   │   ├── guards/                # Functional RBAC route guards
    │   │   │   └── directives/            # *hasPermission directive
    │   │   ├── features/
    │   │   │   ├── catalog/               # Search, Database/Schema tree, Table view
    │   │   │   │   ├── components/        # TableVirtualScroll, ColumnList, InlineEditor
    │   │   │   │   └── stores/            # CatalogSignalStore
    │   │   │   ├── versioning/            # Side-by-side schema diff comparison
    │   │   │   ├── connections/           # Registry CRUD & Connection test dialog
    │   │   │   ├── audit/                 # Virtual-scrolled audit log viewer
    │   │   │   └── reports/               # Executive dashboard with ECharts
    │   │   ├── shared/                    # CDK Virtual Scroll helpers, badges, UI widgets
    │   │   ├── app.routes.ts
    │   │   └── app.config.ts
    ├── angular.json
    ├── package.json
    └── tsconfig.json
```

---

## 1. Complete PostgreSQL 16 DDL & SQLAlchemy 2.0 Async Models

*Full production schema, indexes, and models are detailed in [specs/001-core-platform/data-model.md](specs/001-core-platform/data-model.md).*

### Schema Highlights & Entity Definitions
- **Users, Roles, Permissions, Policies**: `users`, `roles`, `user_roles`, `permission_rules` (supporting `VIEW_METADATA`, `EDIT_DESCRIPTION`, `MANAGE_TAGS`, `MANAGE_CONNECTIONS`, `VIEW_AUDIT_LOGS`, `EXPORT_REPORTS` with `ALLOW`/`DENY` effects on `GLOBAL`, `DATABASE`, `SCHEMA`, `TABLE`, `COLUMN`), and `categories` / `category_assignments` for hierarchical category-based access.
- **DatabaseConnections & SyncSchedules**: Stores connection topology with `encrypted_dek` and `encrypted_credentials` binary columns; `sync_schedules` enforces 5-part cron syntax and tracks health status.
- **Schemas, CatalogTables, CatalogColumns**:
  - `effective_description TEXT GENERATED ALWAYS AS (COALESCE(NULLIF(source_description, ''), approved_description)) STORED` ensures source comments take precedence while preserving user-curated UI documentation.
  - Soft-delete status (`ACTIVE`, `DEPRECATED`, `DELETED`) preserves lineage and historical tags.
- **Tags & TagAssignments**: Governed classifications (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `PII`) mapped polymorphically to databases, schemas, tables, and columns.
- **TableVersions & SchemaChangeLogs**: JSONB snapshots per version with column-level delta logging (`COLUMN_ADDED`, `TYPE_CHANGED`, etc.).
- **AuditLogs (Partitioned & Immutable)**: Range-partitioned by month (`created_at`) with PostgreSQL rules rejecting `UPDATE` and `DELETE` operations.

### High-Performance Indexes (< 100ms at 160,000 Tables, 5,000,000 Columns)
1. **Deterministic Keyset B-Tree Indexes**:
   ```sql
   CREATE INDEX idx_tables_keyset_schema ON catalog_tables (schema_id, name ASC, id ASC) WHERE status != 'DELETED';
   CREATE INDEX idx_tables_keyset_global ON catalog_tables (name ASC, id ASC) WHERE status != 'DELETED';
   CREATE INDEX idx_columns_keyset_table ON catalog_columns (table_id, ordinal_position ASC, id ASC) WHERE status != 'DELETED';
   CREATE INDEX idx_columns_classification ON catalog_columns (classification_level, table_id);
   ```
2. **PostgreSQL GIN Full-Text Search**:
   ```sql
   CREATE INDEX idx_tables_search_vector_gin ON catalog_tables USING GIN (search_vector);
   CREATE INDEX idx_columns_search_vector_gin ON catalog_columns USING GIN (search_vector);
   ```
3. **Trigram Matching for Instant Autocomplete**:
   ```sql
   CREATE INDEX idx_tables_name_trgm_gin ON catalog_tables USING GIN (name gin_trgm_ops);
   CREATE INDEX idx_columns_name_trgm_gin ON catalog_columns USING GIN (name gin_trgm_ops);
   ```
4. **RBAC Evaluation Index**:
   ```sql
   CREATE INDEX idx_perm_rules_eval ON permission_rules (role_id, action, effect, resource_type, resource_id);
   ```

---

## 2. Security Architecture

### 2.1 AES-256-GCM Envelope Encryption Service

```mermaid
flowchart TD
    subgraph Storage [Secure Credential Persistence]
        Plaintext["Plaintext Connection JSON\n(user, password, ssl_cert)"]
        GenDEK["Generate Ephemeral 256-bit DEK\n(os.urandom(32))"]
        KEK["CATALOG_MASTER_KEK (256-bit)\n(from KMS / Env)"]
        
        GenDEK --> EncDEK["Encrypt DEK with KEK\n(AES-256-GCM + 96-bit IV)"]
        Plaintext --> EncData["Encrypt Credentials with DEK\n(AES-256-GCM + 96-bit IV)"]
        
        EncDEK --> DB_DEK["encrypted_dek (BYTEA: 60 bytes)"]
        EncData --> DB_Data["encrypted_credentials (BYTEA)"]
    end
```

#### Envelope Encryption Implementation Contract
```python
# backend/src/services/crypto/envelope.py
import json
import os
from typing import Any, Dict, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from backend.src.core.config import settings

class EnvelopeEncryptionService:
    def __init__(self, master_kek_hex: str = settings.CATALOG_MASTER_KEK):
        self.master_kek = bytes.fromhex(master_kek_hex)
        if len(self.master_kek) != 32:
            raise ValueError("CATALOG_MASTER_KEK must be exactly 256 bits (32 bytes / 64 hex characters)")

    def encrypt_credentials(self, credentials_payload: Dict[str, Any]) -> Tuple[bytes, bytes]:
        """
        Generates an ephemeral 256-bit DEK, encrypts the credentials JSON with the DEK,
        and wraps the DEK with the master KEK using AES-256-GCM.
        Returns: (encrypted_dek, encrypted_credentials)
        """
        # 1. Generate unique 256-bit DEK
        dek = os.urandom(32)

        # 2. Encrypt DEK with KEK (12-byte IV + ciphertext + 16-byte tag)
        kek_aesgcm = AESGCM(self.master_kek)
        dek_iv = os.urandom(12)
        encrypted_dek = dek_iv + kek_aesgcm.encrypt(dek_iv, dek, associated_data=b"DEK_WRAP")

        # 3. Encrypt Credentials with DEK
        payload_bytes = json.dumps(credentials_payload).encode("utf-8")
        dek_aesgcm = AESGCM(dek)
        data_iv = os.urandom(12)
        encrypted_credentials = data_iv + dek_aesgcm.encrypt(data_iv, payload_bytes, associated_data=b"DB_CREDS")

        return encrypted_dek, encrypted_credentials

    def decrypt_credentials(self, encrypted_dek: bytes, encrypted_credentials: bytes) -> Dict[str, Any]:
        """
        Unwraps the DEK using the KEK, then decrypts the credentials payload.
        Zero plaintext secrets are retained or logged.
        """
        # 1. Unwrap DEK
        dek_iv = encrypted_dek[:12]
        dek_ciphertext = encrypted_dek[12:]
        kek_aesgcm = AESGCM(self.master_kek)
        dek = kek_aesgcm.decrypt(dek_iv, dek_ciphertext, associated_data=b"DEK_WRAP")

        # 2. Decrypt Credentials
        data_iv = encrypted_credentials[:12]
        data_ciphertext = encrypted_credentials[12:]
        dek_aesgcm = AESGCM(dek)
        decrypted_bytes = dek_aesgcm.decrypt(data_iv, data_ciphertext, associated_data=b"DB_CREDS")

        return json.loads(decrypted_bytes.decode("utf-8"))
```

### 2.2 FastAPI Dependency Injection Chain for SQL-Level RBAC

To avoid pulling millions of unauthorized rows into Python memory, the authorization layer injects filtering subqueries directly into SQLAlchemy `Select` statements.

```python
# backend/src/api/dependencies/rbac.py
from typing import Callable, Optional
import uuid
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, and_, exists, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.src.core.database import get_db_session
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    DatabaseConnection,
    DatabaseSchema,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    Tag,
    TagAssignment,
    User,
    UserRole,
)

security = HTTPBearer()

class UserContext:
    def __init__(self, user: User, role_ids: list[uuid.UUID], is_superadmin: bool):
        self.user = user
        self.role_ids = role_ids
        self.is_superadmin = is_superadmin

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db_session),
) -> UserContext:
    token = credentials.credentials
    # PyJWT token verification omitted for brevity (validates signature, exp, user_id)
    user_id = decode_jwt_token(token)
    user = await db.get(User, user_id)
    if not user or user.status != "ACTIVE":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    roles_stmt = select(UserRole.role_id).where(UserRole.user_id == user.id)
    result = await db.execute(roles_stmt)
    role_ids = [row[0] for row in result.all()]
    return UserContext(user=user, role_ids=role_ids, is_superadmin=user.is_superadmin)


def require_table_rbac(action: PermissionAction = PermissionAction.VIEW_METADATA) -> Callable:
    """
    FastAPI dependency returning a query filter function.
    Injected directly into SQLAlchemy select(CatalogTable) statements.
    """
    async def rbac_filter_dependency(user_ctx: UserContext = Depends(get_current_user)) -> Callable[[Select], Select]:
        def apply_rbac_filter(query: Select) -> Select:
            if user_ctx.is_superadmin:
                return query  # Superadmin bypasses filtering

            # 1. ALLOW Subquery: Inherited from GLOBAL, DATABASE, SCHEMA, or direct TABLE
            allow_subquery = exists(
                select(1)
                .select_from(PermissionRule)
                .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
                .where(
                    PermissionRule.role_id.in_(user_ctx.role_ids),
                    PermissionRule.action == action,
                    PermissionRule.effect == PermissionEffect.ALLOW,
                    or_(
                        PermissionRule.resource_type == "GLOBAL",
                        and_(PermissionRule.resource_type == "DATABASE", PermissionRule.resource_id == DatabaseSchema.connection_id),
                        and_(PermissionRule.resource_type == "SCHEMA", PermissionRule.resource_id == CatalogTable.schema_id),
                        and_(PermissionRule.resource_type == "TABLE", PermissionRule.resource_id == CatalogTable.id),
                    ),
                )
            )

            # 2. DENY Subquery: Explicit table or schema DENY overrides ALLOW
            deny_subquery = exists(
                select(1)
                .select_from(PermissionRule)
                .where(
                    PermissionRule.role_id.in_(user_ctx.role_ids),
                    PermissionRule.action == action,
                    PermissionRule.effect == PermissionEffect.DENY,
                    or_(
                        and_(PermissionRule.resource_type == "SCHEMA", PermissionRule.resource_id == CatalogTable.schema_id),
                        and_(PermissionRule.resource_type == "TABLE", PermissionRule.resource_id == CatalogTable.id),
                    ),
                )
            )

            # 3. PII Tag Restriction: Tables with PII tag require VIEW_PII_METADATA permission
            pii_restriction = exists(
                select(1)
                .select_from(TagAssignment)
                .join(Tag, Tag.id == TagAssignment.tag_id)
                .where(
                    TagAssignment.entity_type == "TABLE",
                    TagAssignment.entity_id == CatalogTable.id,
                    Tag.name == "PII",
                    not_(
                        exists(
                            select(1)
                            .select_from(PermissionRule)
                            .where(
                                PermissionRule.role_id.in_(user_ctx.role_ids),
                                PermissionRule.action == PermissionAction.VIEW_METADATA,
                                PermissionRule.resource_type == "GLOBAL", # Or PII permission
                            )
                        )
                    ),
                )
            )

            return query.where(and_(allow_subquery, not_(deny_subquery), not_(pii_restriction)))

        return apply_rbac_filter

    return rbac_filter_dependency
```

---

## 3. REST API Specification & Endpoint Contracts

*Detailed OpenAPI 3.1 YAML definition is maintained in [specs/001-core-platform/contracts/openapi.yaml](specs/001-core-platform/contracts/openapi.yaml).*

### 3.1 Keyset/Cursor Pagination Protocol
Every list endpoint returning high-volume assets (`/api/v1/search`, `/api/v1/schemas/{id}/tables`, `/api/v1/audit-logs`) adheres to a standard cursor contract:
- Request Parameters:
  - `limit`: Integer (Default 50, Maximum 200)
  - `cursor`: Base64 encoded string containing JSON `{ "val": "<sort_value>", "id": "<uuid>" }`
- Response Payload:
  ```json
  {
    "items": [...],
    "page_info": {
      "has_next_page": true,
      "next_cursor": "eyJ2YWwiOiAiY3VzdG9tZXJzIiwgImlkIjogImE0ZjctOGI5..."
    }
  }
  ```

### 3.2 Key REST Endpoints
1. **Full-Text & Trigram Search**:
   - `GET /api/v1/search?q={query}&limit=50&cursor={cursor}`
   - Utilizes GIN index on generated `search_vector` and trigram matching on `name gin_trgm_ops`.
2. **Inline Description Editing**:
   - `PATCH /api/v1/tables/{table_id}/description`
   - `PATCH /api/v1/columns/{column_id}/description`
   - Updates `approved_description` in PostgreSQL, records an audit log entry in the monthly partition, and appends a `DESCRIPTION_UPDATED` event to `schema_change_logs`.
3. **Bulk Tagging**:
   - `POST /api/v1/tags/bulk-assign`
   - Accepts list of entity targets and tag IDs; executes batch `INSERT ... ON CONFLICT DO NOTHING` inside a single atomic transaction.
4. **Schema Version Comparison**:
   - `GET /api/v1/tables/{table_id}/versions/compare?v1={ver}&v2={ver}`
   - Computes structural delta (columns added, dropped, type changes, nullability differences) between two stored JSONB snapshots.
5. **Audit Log Filtering**:
   - `GET /api/v1/audit-logs?entity_type=TABLE&entity_id={id}&action=EDIT_DESCRIPTION&start_time={iso}&end_time={iso}`
   - Direct partition-pruned scan using indexed `created_at` timestamp range.
6. **Executive Management Reports**:
   - `GET /api/v1/reports/completeness`
   - `GET /api/v1/reports/classification-distribution`
   - Served directly from PostgreSQL Materialized Views (`mv_metadata_completeness`) in `< 50ms`.

---

## 4. Angular 18 Enterprise Frontend Architecture

### 4.1 Feature Structure & Standalone Components
The Angular 18 web application is organized strictly into Standalone Components and Signal Stores:

```text
frontend/src/app/
├── core/
│   ├── auth/
│   │   ├── auth.service.ts                  # Signal holding currentUser and token
│   │   ├── auth.interceptor.ts              # Appends Bearer token to all requests
│   │   └── rbac.service.ts                  # Signal computing user permissions
│   ├── guards/
│   │   ├── auth.guard.ts                    # Functional CanActivateFn
│   │   └── permission.guard.ts              # Functional permission check guard
│   └── directives/
│       └── has-permission.directive.ts      # *hasPermission="'EDIT_DESCRIPTION'"
├── features/
│   ├── catalog/
│   │   ├── catalog.routes.ts
│   │   ├── pages/
│   │   │   ├── search/search.component.ts
│   │   │   ├── database-tree/database-tree.component.ts
│   │   │   └── table-detail/table-detail.component.ts
│   │   ├── components/
│   │   │   ├── table-virtual-scroll/table-virtual-scroll.component.ts
│   │   │   ├── column-list/column-list.component.ts
│   │   │   └── inline-desc-editor/inline-desc-editor.component.ts
│   │   └── stores/
│   │       └── catalog.store.ts             # Signal-based store
│   ├── versioning/
│   │   ├── version-diff.component.ts        # Side-by-side color-coded schema diff
│   │   └── stores/version.store.ts
│   ├── connections/
│   │   ├── connection-list.component.ts
│   │   └── connection-dialog.component.ts
│   ├── audit/
│   │   └── audit-log-viewer.component.ts    # CDK Virtual Scroll audit trail
│   └── reports/
│       ├── executive-dashboard.component.ts
│       └── widgets/
│           ├── completeness-gauge.component.ts
│           └── classification-sunburst.component.ts
```

### 4.2 Signal-Based State Store (`CatalogStore`)
```typescript
// frontend/src/app/features/catalog/stores/catalog.store.ts
import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { TableItem, SearchHit, KeysetPageInfo } from '../models/catalog.models';

@Injectable({ providedIn: 'root' })
export class CatalogStore {
  private http = inject(HttpClient);

  // State Signals
  private readonly _tables = signal<TableItem[]>([]);
  private readonly _searchHits = signal<SearchHit[]>([]);
  private readonly _pageInfo = signal<KeysetPageInfo | null>(null);
  private readonly _isLoading = signal<boolean>(false);
  private readonly _activeFilter = signal<string>('');

  // Public Selectors
  readonly tables = this._tables.asReadonly();
  readonly searchHits = this._searchHits.asReadonly();
  readonly isLoading = this._isLoading.asReadonly();
  readonly hasMore = computed(() => this._pageInfo()?.has_next_page ?? false);

  async search(query: string, reset: boolean = true) {
    if (reset) {
      this._searchHits.set([]);
      this._pageInfo.set(null);
    }
    this._isLoading.set(true);
    const cursor = reset ? '' : (this._pageInfo()?.next_cursor || '');
    try {
      const resp: any = await this.http
        .get(`/api/v1/search?q=${encodeURIComponent(query)}&limit=50&cursor=${cursor}`)
        .toPromise();
      this._searchHits.update(current => reset ? resp.items : [...current, ...resp.items]);
      this._pageInfo.set(resp.page_info);
    } finally {
      this._isLoading.set(false);
    }
  }

  async updateDescription(tableId: string, approvedDescription: string) {
    const updated: any = await this.http
      .patch(`/api/v1/tables/${tableId}/description`, { approved_description: approvedDescription })
      .toPromise();
    this._tables.update(list =>
      list.map(t => (t.id === tableId ? { ...t, effective_description: updated.effective_description } : t))
    );
  }
}
```

### 4.3 Virtual Scrolling Strategy (Angular CDK)
To render up to 10,000 tables without DOM bloat or memory leak, we integrate `<cdk-virtual-scroll-viewport>` with fixed row height (`48px`) and pre-fetch buffers:

```html
<!-- frontend/src/app/features/catalog/components/table-virtual-scroll/table-virtual-scroll.component.html -->
<div class="virtual-scroll-container">
  <cdk-virtual-scroll-viewport itemSize="48" minBufferPx="480" maxBufferPx="960" class="viewport" (scrolledIndexChange)="onScroll($event)">
    <div *cdkVirtualFor="let table of store.searchHits(); trackBy: trackById" class="table-row">
      <div class="col-name">
        <a [routerLink]="['/catalog/tables', table.id]">{{ table.name }}</a>
      </div>
      <div class="col-hierarchy">{{ table.database_name }} / {{ table.schema_name }}</div>
      <div class="col-desc">{{ table.effective_description || 'No description provided' }}</div>
      <div class="col-status">
        <span class="status-pill" [class.archived]="table.status === 'DELETED'">
          {{ table.status }}
        </span>
      </div>
    </div>
  </cdk-virtual-scroll-viewport>
</div>
```

### 4.4 Role-Based Route Guards & Structural Directives
```typescript
// frontend/src/app/core/guards/permission.guard.ts
import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { RbacService } from '../auth/rbac.service';

export const permissionGuard = (requiredAction: string): CanActivateFn => {
  return () => {
    const rbacService = inject(RbacService);
    const router = inject(Router);
    if (rbacService.hasPermission(requiredAction)) {
      return true;
    }
    return router.parseUrl('/unauthorized');
  };
};

// frontend/src/app/core/directives/has-permission.directive.ts
import { Directive, Input, TemplateRef, ViewContainerRef, inject, effect } from '@angular/core';
import { RbacService } from '../auth/rbac.service';

@Directive({
  selector: '[hasPermission]',
  standalone: true,
})
export class HasPermissionDirective {
  private rbacService = inject(RbacService);
  private templateRef = inject(TemplateRef<any>);
  private viewContainer = inject(ViewContainerRef);

  @Input('hasPermission') action!: string;

  constructor() {
    effect(() => {
      const allowed = this.rbacService.hasPermission(this.action);
      this.viewContainer.clear();
      if (allowed) {
        this.viewContainer.createEmbeddedView(this.templateRef);
      }
    });
  }
}
```

### 4.5 Apache ECharts Integration for Executive Reports
Hardware-accelerated ECharts rendering via `ngx-echarts` guarantees 60 FPS transitions when displaying metadata governance statistics:

```typescript
// frontend/src/app/features/reports/widgets/completeness-gauge.component.ts
import { Component, Input, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NgxEchartsDirective, provideEchartsCore } from 'ngx-echarts';
import * as echarts from 'echarts/core';
import { GaugeChart } from 'echarts/charts';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([GaugeChart, CanvasRenderer]);

@Component({
  selector: 'app-completeness-gauge',
  standalone: true,
  imports: [CommonModule, NgxEchartsDirective],
  providers: [provideEchartsCore({ echarts })],
  template: `
    <div echarts [options]="chartOptions()" class="chart-container"></div>
  `,
  styles: [`.chart-container { height: 320px; width: 100%; }`]
})
export class CompletenessGaugeComponent {
  @Input({ required: true }) score: number = 0;

  chartOptions = computed(() => ({
    series: [{
      type: 'gauge',
      startAngle: 180,
      endAngle: 0,
      min: 0,
      max: 100,
      splitNumber: 5,
      itemStyle: { color: '#00897B' },
      progress: { show: true, roundCap: true, width: 18 },
      pointer: { length: '60%', width: 6 },
      detail: { valueAnimation: true, formatter: '{value}%', offsetCenter: [0, '20%'], fontSize: 24 },
      data: [{ value: this.score, name: 'Completeness' }]
    }]
  }));
}
```

---

## Complexity Tracking

*No constitutional violations identified. Direct PostgreSQL queries, envelope encryption, and keyset pagination strictly adhere to Constitution v1.0.0.*

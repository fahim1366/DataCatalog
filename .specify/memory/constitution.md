<!--
# Sync Impact Report
- Version change: Uninitialized template -> 1.0.0 (Initial Ratification)
- List of modified principles:
  - [PRINCIPLE_1_NAME] -> I. Ambiguity Gate (STOP & ASK MANDATE)
  - [PRINCIPLE_2_NAME] -> II. Zero-Laziness & Anti-Simplification Policy
  - [PRINCIPLE_3_NAME] -> III. Strict Red-Green-Refactor TDD Protocol
  - [PRINCIPLE_4_NAME] -> IV. Security & Auditing Standards
  - [PRINCIPLE_5_NAME] -> Consolidated into the 4 non-negotiable core pillars
- Added sections:
  - Enterprise Scale & Architecture Constraints
  - Development Workflow & Quality Gates
  - Governance (Amendment procedure, versioning policy, compliance review)
- Removed sections: None
- Follow-up TODOs: None (all template slots concretely defined)
-->

# Enterprise Data Catalog Constitution

## Core Principles

### I. Ambiguity Gate (STOP & ASK MANDATE)
- **Strict Prohibition on Guessing:** Team members and AI agents are strictly forbidden from making assumptions about business logic, schema edge cases, permission inheritance, or UI behavior.
- **Mandatory Clarification:** Before generating any plan or writing any implementation code, if ANY requirement has more than one valid architectural or functional interpretation, execution MUST halt immediately. A structured list of clarifying questions MUST be produced (via `/speckit-clarify`). Do not proceed until explicit human confirmation and clarification are provided.
- **Rationale:** Unchecked assumptions at the specification and planning phase multiply into severe architectural flaws, schema rework, and system instability at enterprise scale.

### II. Zero-Laziness & Anti-Simplification Policy
- **BANNED PATTERNS:** Never write `# TODO`, `# implement later`, `pass`, mock return values in service layers, or simplified in-memory dictionaries instead of real PostgreSQL queries.
- **ENTERPRISE SCALE ENFORCEMENT:** Every database query, API endpoint, and UI component MUST be designed for **800 databases, 160,000 tables, and 5,000,000+ columns**:
  - Unbounded `SELECT *` or endpoints without cursor/keyset or strict limit/offset pagination are strictly forbidden.
  - N+1 query patterns in SQLAlchemy are fatal errors; always use explicit `selectinload`, `joinedload`, or optimized Common Table Expressions (CTEs).
  - Bulk operations (metadata ingestion) MUST use PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` (Upsert) in batches, never row-by-row ORM inserts.
- **Rationale:** Prototypes and lazy shortcuts invariably leak into production. Building for target volume from day one prevents catastrophic outages and performance degradation.

### III. Strict Red-Green-Refactor TDD Protocol
- **FAILING TEST FIRST:** No bug fix or feature implementation is accepted without first writing an automated test (`pytest` for Python, `Jasmine/Karma` or `Playwright` for Angular) that reproduces the exact requirement or failure.
- **VERIFICATION PROOF:** The test suite MUST be executed and verified to fail (Red) for the expected reason before writing any implementation code, and pass (Green) after the implementation.
- **REAL DATABASE TESTING:** Backend integration tests MUST run against a real PostgreSQL test container or database instance (managed via isolated transactions and test fixtures), NEVER against SQLite or mocked ORM sessions.
- **Rationale:** Mocks hide subtle SQL dialect disparities, constraint violations, and concurrency locks. Real database verification ensures deterministic software reliability.

### IV. Security & Auditing Standards
- **Envelope Encryption for Credentials:** Database connector credentials MUST be encrypted at rest using AES-256-GCM (Envelope Encryption) before touching PostgreSQL. Plaintext passwords or secrets in logs, API responses, or database columns are fatal violations.
- **Immutable Audit Logging:** Every state-mutating operation (Create, Update, Delete, Permission Change, Schema Diff) MUST generate an immutable record in the `audit_logs` table within the same database transaction.
- **Rationale:** Enterprise metadata catalogs are high-value targets containing sensitive infrastructure topology. Tamper-evident auditing and zero-plaintext secrets are non-negotiable security requirements.

## Enterprise Scale & Architecture Constraints
- **Target Data Volume:** Systems, data access layers, indexing schemes, and front-end state management MUST support 800 connected databases, 160,000 tables, and 5,000,000+ column definitions without memory exhaustion or UI thread lockup.
- **Query & Pagination Rules:** Keyset/cursor-based pagination MUST be preferred for high-volume catalogs. Limit/offset pagination is permitted only with strict upper bounds. All queries MUST restrict column projections to requested attributes.
- **ORM & Ingestion Performance:** SQLAlchemy query plans MUST be analyzed to eliminate N+1 fetches. Ingestion pipelines MUST process metadata in bulk upsert chunks with conflict handling.

## Development Workflow & Quality Gates
- **Clarification Gate:** Requirement analysis MUST identify all ambiguities prior to technical spec approval. Unresolved questions MUST be addressed with `/speckit-clarify`.
- **TDD Enforcement:** Implementation follows strict Red-Green-Refactor sequence. Pull requests missing failing tests or verification proof MUST be rejected.
- **Continuous Integration Validation:** CI pipelines MUST execute backend integration suites against containerized PostgreSQL environments matching production major versions.
- **Code Quality & Completeness:** Code submissions containing placeholder logic (`pass`, mock stubs in production paths, `# TODO`) fail quality gate evaluation automatically.

## Governance
- **Supremacy & Compliance:** This Constitution establishes the non-negotiable standards for the Enterprise Data Catalog project and supersedes all conflicting conventions or local shortcuts. All contributions, automated agent workflows, and pull requests MUST verify compliance before merging.
- **Amendment Procedure:** Amendments require an explicit written proposal outlining rationale, architectural impact, and any necessary data/code migration plans, subject to formal stakeholder review and approval.
- **Versioning Policy:** This document is versioned using Semantic Versioning (MAJOR.MINOR.PATCH):
  - MAJOR: Removal or backward-incompatible redefinition of core principles or governance policies.
  - MINOR: Addition of new principles, structural constraints, or materially expanded governance rules.
  - PATCH: Wording adjustments, typo fixes, or non-semantic clarifications.
- **Compliance Review:** Any deviation from these rules requires an approved architectural exception document and an explicit justification recorded prior to implementation.

**Version**: 1.0.0 | **Ratified**: 2026-11-09 | **Last Amended**: 2026-11-09

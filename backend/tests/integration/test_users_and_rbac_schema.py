import os
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.exc import IntegrityError

from backend.src.models.identity import User, Role, UserRole, UserStatus
from backend.src.models.rbac import (
    PermissionRule,
    PermissionAction,
    PermissionEffect,
    ResourceType,
    Category,
    CategoryAssignment,
    EntityType,
)

# Database URL for real PostgreSQL 18 instance
TEST_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:12345678@127.0.0.1:5432/enterprise_datacatalog"
)

@pytest_asyncio.fixture(scope="function")
async def async_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    yield engine
    await engine.dispose()

@pytest_asyncio.fixture(scope="function")
async def db_session(async_engine):
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
        await session.rollback()

@pytest.mark.asyncio
async def test_users_and_rbac_tables_exist(async_engine):
    """Verify that all Users and RBAC tables exist in PostgreSQL after Alembic migration."""
    required_tables = {
        "users",
        "roles",
        "user_roles",
        "permission_rules",
        "categories",
        "category_assignments",
    }
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        existing_tables = {row[0] for row in result.fetchall()}
        missing = required_tables - existing_tables
        assert not missing, f"Missing required tables in PostgreSQL: {missing}"

@pytest.mark.asyncio
async def test_indexes_exist(async_engine):
    """Verify composite indexes for RBAC evaluation exist."""
    required_indexes = {
        "idx_perm_rules_eval",
        "idx_cat_assign_lookup",
    }
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            )
        )
        existing_indexes = {row[0] for row in result.fetchall()}
        missing = required_indexes - existing_indexes
        assert not missing, f"Missing composite indexes: {missing}"

@pytest.mark.asyncio
async def test_user_and_role_crud(db_session: AsyncSession):
    """Test inserting Users, Roles, and UserRoles using SQLAlchemy models."""
    # 1. Create User
    unique_suffix = uuid.uuid4().hex[:8]
    user = User(
        username=f"admin_{unique_suffix}",
        email=f"admin_{unique_suffix}@enterprise.corp",
        hashed_password="$argon2id$v=19$m=65536,t=3,p=4$dummyhash",
        full_name="Catalog Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()
    assert user.id is not None

    # 2. Create Role
    role = Role(
        name=f"DataSteward_{unique_suffix}",
        description="Data Governance Steward Role",
        is_system_role=False,
    )
    db_session.add(role)
    await db_session.flush()
    assert role.id is not None

    # 3. Assign Role to User
    user_role = UserRole(user_id=user.id, role_id=role.id)
    db_session.add(user_role)
    await db_session.flush()

    # 4. Create Permission Rule with Table-Level ACL
    target_table_id = uuid.uuid4()
    perm_rule = PermissionRule(
        role_id=role.id,
        resource_type=ResourceType.TABLE,
        resource_id=target_table_id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    db_session.add(perm_rule)
    await db_session.flush()
    assert perm_rule.id is not None

    # 5. Create Hierarchical Category and Sub-category
    parent_cat = Category(name=f"Finance_{unique_suffix}", description="Financial Assets")
    db_session.add(parent_cat)
    await db_session.flush()

    child_cat = Category(
        name=f"Billing_{unique_suffix}",
        description="Billing Sub-domain",
        parent_id=parent_cat.id,
    )
    db_session.add(child_cat)
    await db_session.flush()
    assert child_cat.parent_id == parent_cat.id

    # 6. Assign Category
    cat_assignment = CategoryAssignment(
        category_id=child_cat.id,
        entity_type=EntityType.TABLE,
        entity_id=target_table_id,
    )
    db_session.add(cat_assignment)
    await db_session.flush()
    assert cat_assignment.id is not None

@pytest.mark.asyncio
async def test_duplicate_user_raises_integrity_error(db_session: AsyncSession):
    """Test that duplicate username violates unique constraint."""
    uname = f"unique_user_{uuid.uuid4().hex[:8]}"
    u1 = User(
        username=uname,
        email=f"{uname}@corp.internal",
        hashed_password="hashed_pw_1",
        full_name="User One",
        status=UserStatus.ACTIVE,
    )
    db_session.add(u1)
    await db_session.flush()

    u2 = User(
        username=uname,  # Duplicate username
        email=f"other_{uuid.uuid4().hex[:8]}@corp.internal",
        hashed_password="hashed_pw_2",
        full_name="User Two",
        status=UserStatus.ACTIVE,
    )
    db_session.add(u2)
    with pytest.raises(IntegrityError):
        await db_session.flush()

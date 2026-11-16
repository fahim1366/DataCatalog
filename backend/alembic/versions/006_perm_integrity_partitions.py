"""Permission integrity constraints and audit logs monthly partitions 2026-2027

Revision ID: 006_perm_integrity_partitions
Revises: 005_admin_governance_upgrade
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '006_perm_integrity_partitions'
down_revision: Union[str, None] = '005_admin_governance_upgrade'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUDIT_PARTITIONS = [
    ("audit_logs_y2026m11", "2026-11-01 00:00:00+00", "2026-12-01 00:00:00+00"),
    ("audit_logs_y2026m12", "2026-12-01 00:00:00+00", "2027-01-01 00:00:00+00"),
    ("audit_logs_y2027m01", "2027-01-01 00:00:00+00", "2027-02-01 00:00:00+00"),
    ("audit_logs_y2027m02", "2027-02-01 00:00:00+00", "2027-03-01 00:00:00+00"),
    ("audit_logs_y2027m03", "2027-03-01 00:00:00+00", "2027-04-01 00:00:00+00"),
    ("audit_logs_y2027m04", "2027-04-01 00:00:00+00", "2027-05-01 00:00:00+00"),
    ("audit_logs_y2027m05", "2027-05-01 00:00:00+00", "2027-06-01 00:00:00+00"),
    ("audit_logs_y2027m06", "2027-06-01 00:00:00+00", "2027-07-01 00:00:00+00"),
    ("audit_logs_y2027m07", "2027-07-01 00:00:00+00", "2027-08-01 00:00:00+00"),
    ("audit_logs_y2027m08", "2027-08-01 00:00:00+00", "2027-09-01 00:00:00+00"),
    ("audit_logs_y2027m09", "2027-09-01 00:00:00+00", "2027-10-01 00:00:00+00"),
    ("audit_logs_y2027m10", "2027-10-01 00:00:00+00", "2027-11-01 00:00:00+00"),
    ("audit_logs_y2027m11", "2027-11-01 00:00:00+00", "2027-12-01 00:00:00+00"),
    ("audit_logs_y2027m12", "2027-12-01 00:00:00+00", "2028-01-01 00:00:00+00"),
]


def upgrade() -> None:
    # 1. CheckConstraint on permission_rules ensuring at least one principal is non-null
    op.create_check_constraint(
        "ck_perm_rules_has_principal",
        "permission_rules",
        "role_id IS NOT NULL OR user_id IS NOT NULL OR group_id IS NOT NULL"
    )

    # 2. Unique index preventing duplicate rules across (role_id, user_id, group_id, resource_type, resource_id, action)
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_perm_rules_distinct ON permission_rules (
        COALESCE(role_id, '00000000-0000-0000-0000-000000000000'),
        COALESCE(user_id, '00000000-0000-0000-0000-000000000000'),
        COALESCE(group_id, '00000000-0000-0000-0000-000000000000'),
        resource_type,
        COALESCE(resource_id, '00000000-0000-0000-0000-000000000000'),
        action
    );
    """)

    # 3. Pre-create monthly partitions for audit_logs covering the rest of 2026 and all of 2027
    for table_name, start_date, end_date in AUDIT_PARTITIONS:
        op.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} PARTITION OF audit_logs
            FOR VALUES FROM ('{start_date}') TO ('{end_date}');
        """)


def downgrade() -> None:
    # Drop partitions created in 2027m03 through 2027m12
    for table_name, _, _ in AUDIT_PARTITIONS[4:]:
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")

    op.execute("DROP INDEX IF EXISTS uq_perm_rules_distinct;")
    op.drop_constraint("ck_perm_rules_has_principal", "permission_rules", type_="check")

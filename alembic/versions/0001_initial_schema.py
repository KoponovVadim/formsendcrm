"""Initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-03-13 00:00:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "module_configs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("icon", sa.String(length=50), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("fields_schema", sa.JSON(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.UniqueConstraint("slug", name="uq_module_configs_slug"),
    )
    op.create_index("ix_module_configs_id", "module_configs", ["id"])

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_index("ix_roles_id", "roles", ["id"])

    op.create_table(
        "sync_logs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("module_slug", sa.String(length=100), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("records_affected", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sync_logs_id", "sync_logs", ["id"])

    op.create_table(
        "dynamic_records",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("module_slug", sa.String(length=100), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dynamic_records_id", "dynamic_records", ["id"])
    op.create_index("ix_dynamic_records_module_slug", "dynamic_records", ["module_slug"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("is_superuser", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_id", "users", ["id"])


def downgrade() -> None:
    op.drop_index("ix_users_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_dynamic_records_module_slug", table_name="dynamic_records")
    op.drop_index("ix_dynamic_records_id", table_name="dynamic_records")
    op.drop_table("dynamic_records")

    op.drop_index("ix_sync_logs_id", table_name="sync_logs")
    op.drop_table("sync_logs")

    op.drop_index("ix_roles_id", table_name="roles")
    op.drop_table("roles")

    op.drop_index("ix_module_configs_id", table_name="module_configs")
    op.drop_table("module_configs")

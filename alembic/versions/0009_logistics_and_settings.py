"""Add logistics deliveries and system settings

Revision ID: 0009_logistics_and_settings
Revises: 0008_add_order_comment
Create Date: 2026-03-22 21:20:00

"""

from alembic import op
import sqlalchemy as sa


revision = "0009_logistics_and_settings"
down_revision = "0008_add_order_comment"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _table_exists("system_settings"):
        op.create_table(
            "system_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(length=120), nullable=False),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_system_settings_key", "system_settings", ["key"], unique=True)
        op.create_index("ix_system_settings_updated_at", "system_settings", ["updated_at"], unique=False)

    if not _table_exists("logistics_deliveries"):
        op.create_table(
            "logistics_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
            sa.Column("pickup_location_id", sa.Integer(), sa.ForeignKey("locations.id"), nullable=True),
            sa.Column("dropoff_location_id", sa.Integer(), sa.ForeignKey("locations.id"), nullable=True),
            sa.Column("leg_type", sa.String(length=30), nullable=False, server_default="to_main"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="created"),
            sa.Column("courier_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("courier_fee", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("transport_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("payment_eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("courier_paid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_logistics_deliveries_order_id", "logistics_deliveries", ["order_id"], unique=False)
        op.create_index("ix_logistics_deliveries_status", "logistics_deliveries", ["status"], unique=False)
        op.create_index("ix_logistics_deliveries_courier_user_id", "logistics_deliveries", ["courier_user_id"], unique=False)


def downgrade() -> None:
    if _table_exists("logistics_deliveries"):
        op.drop_index("ix_logistics_deliveries_courier_user_id", table_name="logistics_deliveries")
        op.drop_index("ix_logistics_deliveries_status", table_name="logistics_deliveries")
        op.drop_index("ix_logistics_deliveries_order_id", table_name="logistics_deliveries")
        op.drop_table("logistics_deliveries")

    if _table_exists("system_settings"):
        op.drop_index("ix_system_settings_updated_at", table_name="system_settings")
        op.drop_index("ix_system_settings_key", table_name="system_settings")
        op.drop_table("system_settings")

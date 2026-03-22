"""Add client and partner price columns to order_items

Revision ID: 0007_order_items_client_partner_prices
Revises: 0006_locations_and_location_prices
Create Date: 2026-03-22 10:10:00

"""

from alembic import op
import sqlalchemy as sa


revision = "0007_order_items_client_partner_prices"
down_revision = "0006_locations_and_location_prices"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _column_exists("order_items", "price_client"):
        op.add_column("order_items", sa.Column("price_client", sa.Numeric(12, 2), nullable=False, server_default="0"))
    if not _column_exists("order_items", "price_partner"):
        op.add_column("order_items", sa.Column("price_partner", sa.Numeric(12, 2), nullable=False, server_default="0"))

    op.execute("UPDATE order_items SET price_client = COALESCE(unit_price, 0) WHERE price_client IS NULL OR price_client = 0")
    op.execute("UPDATE order_items SET price_partner = COALESCE(unit_price, 0) WHERE price_partner IS NULL OR price_partner = 0")


def downgrade() -> None:
    if _column_exists("order_items", "price_partner"):
        op.drop_column("order_items", "price_partner")
    if _column_exists("order_items", "price_client"):
        op.drop_column("order_items", "price_client")

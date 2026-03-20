"""Add calculator breakdown to order items

Revision ID: 0004_order_item_calculator_breakdown
Revises: 0003_operating_crm_core
Create Date: 2026-03-20 16:30:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004_order_item_calculator_breakdown"
down_revision = "0003_operating_crm_core"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _column_exists("order_items", "calculator_breakdown"):
        op.add_column("order_items", sa.Column("calculator_breakdown", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _column_exists("order_items", "calculator_breakdown"):
        op.drop_column("order_items", "calculator_breakdown")

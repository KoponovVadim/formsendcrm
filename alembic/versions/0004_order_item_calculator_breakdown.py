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


def upgrade() -> None:
    op.add_column("order_items", sa.Column("calculator_breakdown", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("order_items", "calculator_breakdown")

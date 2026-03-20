"""Add dedup key for incoming chat messages

Revision ID: 0005_chat_message_dedup_key
Revises: 0004_order_item_calculator_breakdown
Create Date: 2026-03-20 18:20:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005_chat_message_dedup_key"
down_revision = "0004_order_item_calculator_breakdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("dedup_key", sa.String(length=128), nullable=True))
    op.create_index("ix_messages_dedup_key", "messages", ["dedup_key"])
    op.create_index("ix_messages_conversation_dedup", "messages", ["conversation_id", "dedup_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_dedup", table_name="messages")
    op.drop_index("ix_messages_dedup_key", table_name="messages")
    op.drop_column("messages", "dedup_key")

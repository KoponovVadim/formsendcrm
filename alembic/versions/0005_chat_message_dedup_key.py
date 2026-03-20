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


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _column_exists("messages", "dedup_key"):
        op.add_column("messages", sa.Column("dedup_key", sa.String(length=128), nullable=True))

    if not _index_exists("messages", "ix_messages_dedup_key"):
        op.create_index("ix_messages_dedup_key", "messages", ["dedup_key"])

    if not _index_exists("messages", "ix_messages_conversation_dedup"):
        op.create_index("ix_messages_conversation_dedup", "messages", ["conversation_id", "dedup_key"], unique=True)


def downgrade() -> None:
    if _index_exists("messages", "ix_messages_conversation_dedup"):
        op.drop_index("ix_messages_conversation_dedup", table_name="messages")

    if _index_exists("messages", "ix_messages_dedup_key"):
        op.drop_index("ix_messages_dedup_key", table_name="messages")

    if _column_exists("messages", "dedup_key"):
        op.drop_column("messages", "dedup_key")

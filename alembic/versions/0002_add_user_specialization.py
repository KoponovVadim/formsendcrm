"""Add user specialization

Revision ID: 0002_add_user_specialization
Revises: 0001_initial_schema
Create Date: 2026-03-13 00:10:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_add_user_specialization"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("specialization", sa.String(length=255), nullable=True, server_default=""))


def downgrade() -> None:
    op.drop_column("users", "specialization")

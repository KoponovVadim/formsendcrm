"""Add username and point_id to users

Revision ID: 0010_add_user_username_and_point
Revises: 0009_logistics_and_settings
Create Date: 2026-03-26 00:00:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0010_add_user_username_and_point"
down_revision = "0009_logistics_and_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("point_id", sa.Integer(), nullable=True))

    users = sa.table(
        "users",
        sa.column("id", sa.Integer()),
        sa.column("email", sa.String(length=255)),
        sa.column("username", sa.String(length=100)),
    )
    op.execute(
        users.update()
        .where(users.c.username.is_(None))
        .where(users.c.email.is_not(None))
        .values(username=sa.func.lower(users.c.email))
    )
    op.execute(
        users.update()
        .where(users.c.username.is_(None))
        .values(username=sa.literal("user_") + sa.cast(users.c.id, sa.String()))
    )

    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("users", "username", existing_type=sa.String(length=100), nullable=False)

    op.create_index("ix_users_username", "users", ["username"], unique=False)
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_foreign_key(
        "fk_users_point_id_locations",
        source_table="users",
        referent_table="locations",
        local_cols=["point_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_point_id_locations", "users", type_="foreignkey")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_index("ix_users_username", table_name="users")

    users = sa.table(
        "users",
        sa.column("email", sa.String(length=255)),
        sa.column("username", sa.String(length=100)),
    )
    op.execute(
        users.update()
        .where(users.c.email.is_(None))
        .values(email=users.c.username)
    )
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)

    op.drop_column("users", "point_id")
    op.drop_column("users", "username")

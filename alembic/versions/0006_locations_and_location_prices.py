"""Add locations and location prices

Revision ID: 0006_locations_and_location_prices
Revises: 0005_chat_message_dedup_key
Create Date: 2026-03-20 22:40:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0006_locations_and_location_prices"
down_revision = "0005_chat_message_dedup_key"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


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


def _ensure_locations_table() -> None:
    if _table_exists("locations"):
        return

    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_index("ix_locations_name", "locations", ["name"], unique=True)
    op.create_index("ix_locations_is_active", "locations", ["is_active"], unique=False)


def _ensure_location_prices_table() -> None:
    if _table_exists("location_prices"):
        return

    op.create_table(
        "location_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )

    op.create_index("ix_location_prices_location_id", "location_prices", ["location_id"], unique=False)
    op.create_index("ix_location_prices_service_id", "location_prices", ["service_id"], unique=False)
    op.create_index("ix_location_prices_location_service", "location_prices", ["location_id", "service_id"], unique=True)


def _ensure_order_executor_columns() -> None:
    if not _column_exists("orders", "location_id"):
        op.add_column("orders", sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id"), nullable=True))
    if not _index_exists("orders", "ix_orders_location_id"):
        op.create_index("ix_orders_location_id", "orders", ["location_id"], unique=False)

    if not _column_exists("executors", "location_id"):
        op.add_column("executors", sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id"), nullable=True))
    if not _index_exists("executors", "ix_executors_location_id"):
        op.create_index("ix_executors_location_id", "executors", ["location_id"], unique=False)


def _migrate_point_prices_to_location_prices() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "services" not in inspector.get_table_names() or "location_prices" not in inspector.get_table_names():
        return

    services_rows = bind.execute(sa.text("SELECT id, base_price, calculator_schema FROM services")).fetchall()
    if not services_rows:
        return

    existing_names = {
        row[1]: row[0]
        for row in bind.execute(sa.text("SELECT id, name FROM locations")).fetchall()
        if row[1]
    }

    for service_id, base_price, calculator_schema in services_rows:
        schema = calculator_schema if isinstance(calculator_schema, dict) else {}
        point_prices = schema.get("point_prices")
        if not isinstance(point_prices, dict):
            continue

        for point_name, price_value in point_prices.items():
            name = str(point_name or "").strip()
            if not name:
                continue

            location_id = existing_names.get(name)
            if not location_id:
                created = bind.execute(
                    sa.text("INSERT INTO locations (name, is_active) VALUES (:name, true) RETURNING id"),
                    {"name": name},
                ).fetchone()
                if not created:
                    continue
                location_id = created[0]
                existing_names[name] = location_id

            numeric_price = float(price_value if price_value is not None else (base_price or 0))
            bind.execute(
                sa.text(
                    """
                    INSERT INTO location_prices (location_id, service_id, price)
                    VALUES (:location_id, :service_id, :price)
                    ON CONFLICT (location_id, service_id)
                    DO UPDATE SET price = EXCLUDED.price
                    """
                ),
                {
                    "location_id": int(location_id),
                    "service_id": int(service_id),
                    "price": numeric_price,
                },
            )


def upgrade() -> None:
    _ensure_locations_table()
    _ensure_location_prices_table()
    _ensure_order_executor_columns()
    _migrate_point_prices_to_location_prices()


def downgrade() -> None:
    if _index_exists("executors", "ix_executors_location_id"):
        op.drop_index("ix_executors_location_id", table_name="executors")
    if _column_exists("executors", "location_id"):
        op.drop_column("executors", "location_id")

    if _index_exists("orders", "ix_orders_location_id"):
        op.drop_index("ix_orders_location_id", table_name="orders")
    if _column_exists("orders", "location_id"):
        op.drop_column("orders", "location_id")

    if _table_exists("location_prices"):
        if _index_exists("location_prices", "ix_location_prices_location_service"):
            op.drop_index("ix_location_prices_location_service", table_name="location_prices")
        if _index_exists("location_prices", "ix_location_prices_service_id"):
            op.drop_index("ix_location_prices_service_id", table_name="location_prices")
        if _index_exists("location_prices", "ix_location_prices_location_id"):
            op.drop_index("ix_location_prices_location_id", table_name="location_prices")
        op.drop_table("location_prices")

    if _table_exists("locations"):
        if _index_exists("locations", "ix_locations_is_active"):
            op.drop_index("ix_locations_is_active", table_name="locations")
        if _index_exists("locations", "ix_locations_name"):
            op.drop_index("ix_locations_name", table_name="locations")
        op.drop_table("locations")

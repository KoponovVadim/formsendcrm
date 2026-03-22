from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import inspect, text
from app.config import settings

if settings.DATABASE_URL.startswith("postgresql://"):
    db_url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif settings.DATABASE_URL.startswith("sqlite://"):
    db_url = settings.DATABASE_URL.replace("sqlite://", "sqlite+aiosqlite://", 1)
else:
    db_url = settings.DATABASE_URL

engine = create_async_engine(db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_backward_compatible_columns)


def _ensure_backward_compatible_columns(sync_conn):
    inspector = inspect(sync_conn)
    table_names = set(inspector.get_table_names())

    if "order_items" in table_names:
        columns = {column["name"] for column in inspector.get_columns("order_items")}
        if "price_client" not in columns:
            sync_conn.execute(text("ALTER TABLE order_items ADD COLUMN price_client NUMERIC(12,2) DEFAULT 0 NOT NULL"))
        if "price_partner" not in columns:
            sync_conn.execute(text("ALTER TABLE order_items ADD COLUMN price_partner NUMERIC(12,2) DEFAULT 0 NOT NULL"))

    if "orders" in table_names:
        columns = {column["name"] for column in inspector.get_columns("orders")}
        if "comment" not in columns:
            sync_conn.execute(text("ALTER TABLE orders ADD COLUMN comment TEXT DEFAULT '' NOT NULL"))

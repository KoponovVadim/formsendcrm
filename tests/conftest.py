import pytest
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from api.catalog import router as catalog_router
from api.chats import router as chats_router
from api.orders import router as orders_router
from app.auth import get_current_user
from app.database import Base
from app.database import get_db

# Ensure all models are imported so metadata is complete.
import app.models  # noqa: F401
import models.crm  # noqa: F401
import models.omnichannel  # noqa: F401


@pytest.fixture()
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture()
def current_user_stub():
    return SimpleNamespace(id=1, is_superuser=True)


@pytest.fixture()
async def api_app(db_session, current_user_stub):
    app = FastAPI()
    app.include_router(catalog_router)
    app.include_router(orders_router)
    app.include_router(chats_router)

    async def _override_get_db():
        yield db_session

    async def _override_get_current_user():
        return current_user_stub

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    yield app

    app.dependency_overrides.clear()


@pytest.fixture()
async def async_client(api_app):
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

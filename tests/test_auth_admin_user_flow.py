from types import SimpleNamespace
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import get_current_user, get_current_user_optional
from app.database import get_db
from app.models import Role, User
from app.routes_admin import router as admin_router
from app.routes_auth import router as auth_router
from models.crm import Location


@pytest.mark.asyncio
async def test_public_register_is_disabled_and_redirects_to_login(db_session: Any):
    app = FastAPI()
    app.include_router(auth_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user_optional():
        return None

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user_optional] = _override_get_current_user_optional

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client:
        get_response = await client.get("/register")
        post_response = await client.post(
            "/register",
            data={"email": "user@test.ru", "password": "123456", "password2": "123456"},
        )

    assert get_response.status_code == 302
    assert get_response.headers.get("location") == "/login?registration=disabled"
    assert post_response.status_code == 302
    assert post_response.headers.get("location") == "/login?registration=disabled"


@pytest.mark.asyncio
async def test_admin_creates_user_with_role_and_point(db_session: Any):
    app = FastAPI()
    app.include_router(admin_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    role = Role(name="Менеджер тест", description="", permissions={})
    location = Location(name="Точка Тест", is_active=True)
    db_session.add_all([role, location])
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client:
        response = await client.post(
            "/admin/users/create",
            data={
                "email": "manager@test.ru",
                "password": "strongpass",
                "role_id": str(role.id),
                "location_name": location.name,
                "is_active": "on",
            },
        )

    assert response.status_code == 302
    assert response.headers.get("location") == "/admin/users?created=1"

    created = (await db_session.execute(select(User).where(User.email == "manager@test.ru"))).scalar_one_or_none()
    assert created is not None
    assert created.role_id == role.id
    assert created.specialization == location.name
    assert created.is_active is True

from types import SimpleNamespace
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth import get_current_user
from app.database import get_db
from app.routes_admin import router as admin_router


@pytest.mark.asyncio
async def test_admin_services_page_contains_matrix_and_category_filters(db_session: Any):
    app = FastAPI()
    app.include_router(admin_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, role=SimpleNamespace(permissions={}))

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/admin/services")

    assert response.status_code == 200
    html = response.text
    assert 'id="price-category-filter"' in html
    assert 'data-category="all"' in html
    assert 'data-category="Приставки"' in html
    assert 'data-category="Геймпады"' in html
    assert 'data-category="Телефоны"' in html
    assert 'id="point-prices-table"' in html
    assert 'id="services-table"' in html

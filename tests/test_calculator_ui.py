from types import SimpleNamespace
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models import DynamicRecord, ModuleConfig
from api.orders import router as orders_router
from app.auth import get_current_user
from app.database import get_db
from app.routes_calculator import router as calculator_router
from models.crm import Executor, Location, LocationPrice, Service


@pytest.mark.asyncio
async def test_calculator_locations_sorted_and_cheapest_flag(db_session: Any):
    app = FastAPI()
    app.include_router(calculator_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, role=SimpleNamespace(permissions={}), specialization="")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    service = Service(slug="calc-ui-1", name="Calc UI 1", category="repair", is_active=True, base_price=1200, calculator_schema={"fields": []})
    point_a = Location(name="Point Z", is_active=True)
    point_b = Location(name="Point A", is_active=True)
    db_session.add_all([service, point_a, point_b])
    await db_session.flush()

    db_session.add(LocationPrice(location_id=point_a.id, service_id=service.id, price=3000))
    db_session.add(LocationPrice(location_id=point_b.id, service_id=service.id, price=2000))
    db_session.add(Executor(name="Master A", is_active=True, location_id=point_a.id, current_active_tasks=4, max_active_tasks=5))
    db_session.add(Executor(name="Master B", is_active=True, location_id=point_b.id, current_active_tasks=1, max_active_tasks=5))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/calculator/locations?service_id={service.id}")

    assert response.status_code == 200
    html = response.text
    assert html.index("Point A") < html.index("Point Z")
    assert 'data-cheapest="1"' in html
    assert "самый дешевый" in html


@pytest.mark.asyncio
async def test_calculator_locations_partner_scope_and_hidden_own_price(db_session: Any):
    app = FastAPI()
    app.include_router(calculator_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(
            id=7,
            is_superuser=False,
            specialization="",
            role=SimpleNamespace(
                permissions={
                    "v2": {
                        "services": {
                            "partner_mode": True,
                            "location": "Partner Point",
                        }
                    }
                }
            ),
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    service = Service(slug="calc-ui-2", name="Calc UI 2", category="repair", is_active=True, base_price=1500, calculator_schema={"fields": []})
    allowed_point = Location(name="Partner Point", is_active=True)
    hidden_point = Location(name="Other Point", is_active=True)
    db_session.add_all([service, allowed_point, hidden_point])
    await db_session.flush()

    db_session.add(LocationPrice(location_id=allowed_point.id, service_id=service.id, price=1700))
    db_session.add(LocationPrice(location_id=hidden_point.id, service_id=service.id, price=1900))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/calculator/locations?service_id={service.id}")

    assert response.status_code == 200
    html = response.text
    assert "Partner Point" in html
    assert "Other Point" not in html
    assert "Наша цена:" not in html


@pytest.mark.asyncio
async def test_point_detail_page_contains_orders_executors_and_prices(db_session: Any):
    app = FastAPI()
    app.include_router(calculator_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=9, is_superuser=True, role=SimpleNamespace(permissions={}), specialization="")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    service = Service(slug="point-detail-service", name="Point detail service", category="repair", is_active=True, base_price=800, calculator_schema={"fields": []})
    point = Location(name="Point Detail", is_active=True)
    db_session.add_all([service, point])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=point.id, service_id=service.id, price=950))
    db_session.add(Executor(name="Point Detail Master", is_active=True, location_id=point.id, current_active_tasks=2, max_active_tasks=6))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/points/{point.id}")

    assert response.status_code == 200
    html = response.text
    assert "Точка: Point Detail" in html
    assert "Point Detail Master" in html
    assert "Point detail service" in html


@pytest.mark.asyncio
async def test_point_detail_page_allows_price_edit(db_session: Any):
    app = FastAPI()
    app.include_router(calculator_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=12, is_superuser=True, role=SimpleNamespace(permissions={}), specialization="")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    service = Service(slug="point-edit-service", name="Point edit service", category="repair", is_active=True, base_price=900, calculator_schema={"fields": []})
    point = Location(name="Point Edit", is_active=True)
    db_session.add_all([service, point])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=point.id, service_id=service.id, price=1000))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client:
        save_response = await client.post(
            f"/points/{point.id}/prices",
            data={f"price_{service.id}": "1234"},
        )

    assert save_response.status_code == 302
    assert save_response.headers.get("location") == f"/points/{point.id}?saved=1"

    updated = (
        await db_session.execute(
            select(LocationPrice).where(LocationPrice.location_id == point.id, LocationPrice.service_id == service.id)
        )
    ).scalar_one_or_none()
    assert updated is not None
    assert float(updated.price or 0) == 1234


@pytest.mark.asyncio
async def test_e2e_like_create_order_via_calculator_flow(db_session: Any):
    app = FastAPI()
    app.include_router(calculator_router)
    app.include_router(orders_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=11, is_superuser=True, role=SimpleNamespace(permissions={}), specialization="")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    service = Service(slug="e2e-flow-service", name="E2E flow service", category="repair", is_active=True, base_price=1400, calculator_schema={"fields": []})
    point = Location(name="E2E Point", is_active=True)
    db_session.add_all([service, point])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=point.id, service_id=service.id, price=1800))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        locations_response = await client.get(f"/calculator/locations?service_id={service.id}")
        assert locations_response.status_code == 200
        assert "E2E Point" in locations_response.text

        create_response = await client.post(
            "/api/v1/orders",
            json={
                "client": {"name": "E2E Client", "phone": "79998887766"},
                "service_id": service.id,
                "location_id": point.id,
                "items": [{"service_id": service.id, "quantity": 1, "unit_price": 1400}],
            },
        )
        assert create_response.status_code == 200

        order_payload = create_response.json()
        get_response = await client.get(f"/api/v1/orders/{order_payload['id']}")
        assert get_response.status_code == 200
        order_data = get_response.json()
        assert order_data["location"]["name"] == "E2E Point"
        assert order_data["items"][0]["unit_price"] == 1400


@pytest.mark.asyncio
async def test_calculator_services_partial_has_no_quick_search(db_session: Any):
    app = FastAPI()
    app.include_router(calculator_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=13, is_superuser=True, role=SimpleNamespace(permissions={}), specialization="")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    service = Service(slug="partial-service", name="Partial service", category="repair", is_active=True, base_price=500, calculator_schema={"fields": []})
    db_session.add(service)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/calculator/services?category=repair")

    assert response.status_code == 200
    html = response.text
    assert 'id="calc-service-search"' not in html
    assert 'id="calc-service-select"' in html


@pytest.mark.asyncio
async def test_calculator_executors_uses_orders_panel_load_not_stale_counter(db_session: Any):
    app = FastAPI()
    app.include_router(calculator_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=14, is_superuser=True, role=SimpleNamespace(permissions={}), specialization="")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    point = Location(name="Load Point", is_active=True)
    executor = Executor(name="Load Master", is_active=True, location_id=None, current_active_tasks=99, max_active_tasks=10)
    db_session.add(point)
    await db_session.flush()
    executor.location_id = point.id
    db_session.add(executor)

    db_session.add(
        ModuleConfig(
            slug="orders",
            sheet_name="Заказы",
            display_name="Заказы",
            icon="bi-clipboard-check",
            enabled=True,
            fields_schema=[
                {"name": "№ заказа", "type": "TEXT"},
                {"name": "Мастер", "type": "TEXT"},
                {"name": "Статус", "type": "TEXT"},
            ],
            sort_order=0,
        )
    )
    db_session.add(
        DynamicRecord(
            module_slug="orders",
            row_index=2,
            data={"№ заказа": "ORD-LIVE-1", "Мастер": "Load Master", "Статус": "В работе"},
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/calculator/executors?location_id={point.id}")

    assert response.status_code == 200
    html = response.text
    assert "Load Master (1/10)" in html
    assert "Load Master (99/10)" not in html

from types import SimpleNamespace
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import get_current_user, get_current_user_optional
from app.database import get_db
from app.models import DynamicRecord, ModuleConfig, Role, User
from app.routes_admin import router as admin_router
from app.routes_auth import router as auth_router
from app.routes_modules import router as modules_router
from models.crm import Client, Executor, Location, Order, Task


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
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

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


@pytest.mark.asyncio
async def test_module_page_has_pull_and_push_sync_buttons_for_superuser(db_session: Any):
    app = FastAPI()
    app.include_router(modules_router)
    app.include_router(admin_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    db_session.add(
        ModuleConfig(
            slug="orders",
            sheet_name="Заказы",
            display_name="Заказы",
            icon="bi-clipboard-check",
            enabled=True,
            fields_schema=[{"name": "№ заказа", "type": "TEXT"}],
            sort_order=0,
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/modules/orders")

    assert response.status_code == 200
    html = response.text
    assert '/admin/sync/pull/orders' in html
    assert '/admin/sync/push/orders' in html


@pytest.mark.asyncio
async def test_analytics_module_hides_sync_and_add_controls(db_session: Any):
    app = FastAPI()
    app.include_router(modules_router)
    app.include_router(admin_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    db_session.add(
        ModuleConfig(
            slug="analytics",
            sheet_name="Аналитика",
            display_name="Аналитика",
            icon="bi-graph-up",
            enabled=True,
            fields_schema=[{"name": "Месяц", "type": "TEXT"}, {"name": "Выручка", "type": "TEXT"}],
            sort_order=0,
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/modules/analytics")

    assert response.status_code == 200
    html = response.text
    assert '/admin/sync/pull/analytics' not in html
    assert '/admin/sync/push/analytics' not in html
    assert '/modules/analytics/new' not in html


@pytest.mark.asyncio
async def test_orders_module_filters_records_by_period(db_session: Any):
    app = FastAPI()
    app.include_router(modules_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    db_session.add(
        ModuleConfig(
            slug="orders",
            sheet_name="Заказы",
            display_name="Заказы",
            icon="bi-clipboard-check",
            enabled=True,
            fields_schema=[
                {"name": "№ заказа", "type": "TEXT"},
                {"name": "Дата приёма", "type": "DATE"},
                {"name": "Клиент", "type": "TEXT"},
            ],
            sort_order=0,
        )
    )
    db_session.add_all(
        [
            DynamicRecord(module_slug="orders", row_index=2, data={"№ заказа": "ORD-APR-01", "Дата приёма": "2026-04-01", "Клиент": "A"}),
            DynamicRecord(module_slug="orders", row_index=3, data={"№ заказа": "ORD-APR-15", "Дата приёма": "2026-04-15", "Клиент": "B"}),
            DynamicRecord(module_slug="orders", row_index=4, data={"№ заказа": "ORD-MAY-01", "Дата приёма": "2026-05-01", "Клиент": "C"}),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/modules/orders?date_from=2026-04-10&date_to=2026-04-30")

    assert response.status_code == 200
    html = response.text
    assert "ORD-APR-15" in html
    assert "ORD-APR-01" not in html
    assert "ORD-MAY-01" not in html


@pytest.mark.asyncio
async def test_orders_module_shows_virtual_price_column_when_price_field_missing(db_session: Any):
    app = FastAPI()
    app.include_router(modules_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    db_session.add(
        ModuleConfig(
            slug="orders",
            sheet_name="Заказы",
            display_name="Заказы",
            icon="bi-clipboard-check",
            enabled=True,
            fields_schema=[
                {"name": "№ заказа", "type": "TEXT"},
                {"name": "Дата приёма", "type": "DATE"},
                {"name": "Клиент", "type": "TEXT"},
                {"name": "Статус", "type": "TEXT"},
            ],
            sort_order=0,
        )
    )
    await db_session.flush()

    client = Client(name="Price Client", phone="79990002233", email="")
    db_session.add(client)
    await db_session.flush()

    db_session.add(
        Order(
            order_no="ORD-PRICE-1",
            client_id=client.id,
            status="Новый",
            total_amount=4321,
            currency="RUB",
            source_channel="tests",
        )
    )
    db_session.add(
        DynamicRecord(
            module_slug="orders",
            row_index=2,
            data={"№ заказа": "ORD-PRICE-1", "Дата приёма": "2026-04-04", "Клиент": "Price Client", "Статус": "Новый"},
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client_http:
        response = await client_http.get("/modules/orders")

    assert response.status_code == 200
    html = response.text
    assert "Цена" in html
    assert "4321" in html


@pytest.mark.asyncio
async def test_finance_module_is_derived_from_orders_and_readonly(db_session: Any):
    app = FastAPI()
    app.include_router(modules_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    db_session.add_all(
        [
            ModuleConfig(
                slug="orders",
                sheet_name="Заказы",
                display_name="Заказы",
                icon="bi-clipboard-check",
                enabled=True,
                fields_schema=[
                    {"name": "№ заказа", "type": "TEXT"},
                    {"name": "Дата приёма", "type": "DATE"},
                    {"name": "Дата выдачи", "type": "DATE"},
                    {"name": "Клиент", "type": "TEXT"},
                ],
                sort_order=0,
            ),
            ModuleConfig(
                slug="finance",
                sheet_name="Финансы",
                display_name="Финансы",
                icon="bi-cash-stack",
                enabled=True,
                fields_schema=[
                    {"name": "№ заказа", "type": "TEXT"},
                    {"name": "Цена для клиента", "type": "TEXT"},
                    {"name": "Стоимость работы", "type": "TEXT"},
                ],
                sort_order=1,
            ),
        ]
    )
    await db_session.flush()

    client = Client(name="Finance Client", phone="79990001122", email="")
    db_session.add(client)
    await db_session.flush()

    db_session.add(
        Order(
            order_no="ORD-DERIVED-1",
            client_id=client.id,
            status="Новый",
            total_amount=1500,
            currency="RUB",
            source_channel="tests",
        )
    )
    db_session.add(
        DynamicRecord(
            module_slug="orders",
            row_index=2,
            data={"№ заказа": "ORD-DERIVED-1", "Дата приёма": "2026-04-04", "Дата выдачи": "2026-04-05", "Клиент": "Finance Client"},
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client_http:
        response = await client_http.get("/modules/finance")
        readonly_response = await client_http.get("/modules/finance/new")

    assert response.status_code == 200
    html = response.text
    assert "ORD-DERIVED-1" in html
    assert "1500" in html
    assert '/modules/finance/new' not in html
    assert readonly_response.status_code == 403


@pytest.mark.asyncio
async def test_admin_executors_page_uses_live_orders_panel_load(db_session: Any):
    app = FastAPI()
    app.include_router(admin_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    location = Location(name="Live Point", is_active=True)
    db_session.add(location)
    await db_session.flush()
    db_session.add_all(
        [
            Executor(name="Master Live", is_active=True, location_id=location.id, current_active_tasks=3, max_active_tasks=10),
            Executor(name="Master Idle", is_active=True, location_id=location.id, current_active_tasks=2, max_active_tasks=10),
        ]
    )
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
    db_session.add_all(
        [
            DynamicRecord(module_slug="orders", row_index=2, data={"№ заказа": "ORD-1", "Мастер": "Master Live", "Статус": "В работе"}),
            DynamicRecord(module_slug="orders", row_index=3, data={"№ заказа": "ORD-2", "Мастер": "Master Live", "Статус": "Выдан"}),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/admin/executors")

    assert response.status_code == 200
    html = response.text
    assert "Master Live" in html
    assert "Master Live" in html and "1/10" in html


@pytest.mark.asyncio
async def test_admin_can_update_and_delete_location_and_executor(db_session: Any):
    app = FastAPI()
    app.include_router(admin_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    location = Location(name="To Rename", is_active=True)
    db_session.add(location)
    await db_session.flush()

    executor = Executor(name="To Edit", is_active=True, location_id=location.id, current_active_tasks=0, max_active_tasks=10)
    db_session.add(executor)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client:
        update_location = await client.post(f"/admin/locations/{location.id}/update", data={"name": "Renamed Point"})
        assert update_location.status_code == 302

        update_executor = await client.post(
            f"/admin/executors/{executor.id}/update",
            data={"name": "Edited Master", "max_active_tasks": "12", "location_id": "", "return_to": "/admin/executors"},
        )
        assert update_executor.status_code == 302

        delete_executor = await client.post(
            f"/admin/executors/{executor.id}/delete",
            data={"return_to": "/admin/executors"},
        )
        assert delete_executor.status_code == 302

        delete_location = await client.post(f"/admin/locations/{location.id}/delete")
        assert delete_location.status_code == 302

    updated_location = (await db_session.execute(select(Location).where(Location.name == "Renamed Point"))).scalar_one_or_none()
    assert updated_location is None


@pytest.mark.asyncio
async def test_order_modal_can_add_supply_and_link_to_order(db_session: Any):
    app = FastAPI()
    app.include_router(modules_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    db_session.add_all(
        [
            ModuleConfig(
                slug="orders",
                sheet_name="Заказы",
                display_name="Заказы",
                icon="bi-clipboard-check",
                enabled=True,
                fields_schema=[{"name": "№ заказа", "type": "TEXT"}, {"name": "Статус", "type": "TEXT"}],
                sort_order=0,
            ),
            ModuleConfig(
                slug="supplies",
                sheet_name="Расходники",
                display_name="Расходники",
                icon="bi-box-seam",
                enabled=True,
                fields_schema=[
                    {"name": "№ п/п", "type": "TEXT"},
                    {"name": "Дата покупки", "type": "DATE"},
                    {"name": "Наименование расходника", "type": "TEXT"},
                    {"name": "Происхождение", "type": "TEXT"},
                    {"name": "Стоимость", "type": "NUMBER"},
                ],
                sort_order=1,
            ),
        ]
    )
    await db_session.flush()

    order_record = DynamicRecord(
        module_slug="orders",
        row_index=2,
        data={"№ заказа": "JX-00000001", "Статус": "В работе"},
    )
    db_session.add(order_record)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/modules/orders/record/{order_record.id}/supplies",
            data={
                "supply_date": "2026-01-10",
                "supply_name": "Флюс",
                "supply_cost": "150.5",
                "supply_origin": "Заказ JX-00000001",
            },
            headers={"HX-Request": "true"},
        )

    assert response.status_code == 200
    assert "Расходник добавлен" in response.text

    created_supply = (
        await db_session.execute(
            select(DynamicRecord)
            .where(DynamicRecord.module_slug == "supplies")
            .order_by(DynamicRecord.id.desc())
        )
    ).scalars().first()
    assert created_supply is not None
    assert created_supply.data.get("Наименование расходника") == "Флюс"
    assert created_supply.data.get("Происхождение") == "Заказ JX-00000001"

    edited_executor = (await db_session.execute(select(Executor).where(Executor.name == "Edited Master"))).scalar_one_or_none()
    assert edited_executor is None


@pytest.mark.asyncio
async def test_admin_executor_delete_blocked_with_open_tasks(db_session: Any):
    app = FastAPI()
    app.include_router(admin_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    location = Location(name="Guard Point", is_active=True)
    db_session.add(location)
    await db_session.flush()

    executor = Executor(name="Busy Master", is_active=True, location_id=location.id, current_active_tasks=0, max_active_tasks=10)
    db_session.add(executor)
    await db_session.flush()

    db_session.add(Task(order_id=1, title="Guard task", status="assigned", executor_id=executor.id, priority=1, assignment_score=1))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client:
        response = await client.post(
            f"/admin/executors/{executor.id}/delete",
            data={"return_to": "/admin/executors"},
        )

    assert response.status_code == 302
    assert "error=executor_has_open_tasks" in str(response.headers.get("location") or "")

    exists = (await db_session.execute(select(Executor).where(Executor.id == executor.id))).scalar_one_or_none()
    assert exists is not None

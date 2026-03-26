from types import SimpleNamespace
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import get_current_user, get_current_user_optional, hash_password
from app.database import get_db
from app.models import DynamicRecord, ModuleConfig, Role, User
from app.routes_admin import router as admin_router
from app.routes_auth import router as auth_router
from app.routes_modules import router as modules_router
from models.crm import Client, Executor, Location, LogisticsDelivery, Order, Task


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

    created = (await db_session.execute(select(User).where(User.username == "manager@test.ru"))).scalar_one_or_none()
    assert created is not None
    assert created.role_id == role.id
    assert created.point_id == location.id
    assert created.specialization == ""
    assert created.is_active is True


@pytest.mark.asyncio
async def test_login_works_with_username(db_session: Any):
    app = FastAPI()
    app.include_router(auth_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user_optional():
        return None

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user_optional] = _override_get_current_user_optional

    db_session.add(
        User(
            username="manager_login",
            email="manager@test.local",
            password_hash=hash_password("strongpass"),
            is_active=True,
            is_superuser=False,
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client:
        response = await client.post(
            "/login",
            data={"login": "manager_login", "password": "strongpass"},
        )

    assert response.status_code == 302
    assert response.headers.get("location") == "/"


@pytest.mark.asyncio
async def test_admin_can_create_reception_role_preset(db_session: Any):
    app = FastAPI()
    app.include_router(admin_router)

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
                    {"name": "Клиент", "type": "TEXT"},
                    {"name": "Статус", "type": "TEXT"},
                ],
                sort_order=0,
            ),
            ModuleConfig(
                slug="clients",
                sheet_name="Клиенты",
                display_name="Клиенты",
                icon="bi-people",
                enabled=True,
                fields_schema=[
                    {"name": "ФИО название", "type": "TEXT"},
                    {"name": "Телефон", "type": "TEXT"},
                ],
                sort_order=1,
            ),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client:
        response = await client.post("/admin/roles/create-reception")

    assert response.status_code == 302

    role = (await db_session.execute(select(Role).where(Role.name == "Пункт приема заказов"))).scalar_one_or_none()
    assert role is not None
    assert role.permissions.get("logistics", {}).get("manage") is True
    assert role.permissions.get("v2", {}).get("services", {}).get("manage") is False
    assert role.permissions.get("orders", {}).get("visible") is True


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
async def test_admin_orders_page_and_delete_order(db_session: Any):
    app = FastAPI()
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
            fields_schema=[
                {"name": "№ заказа", "type": "TEXT"},
                {"name": "Статус", "type": "TEXT"},
            ],
            sort_order=0,
        )
    )

    client = Client(name="Order Admin Client", phone="79998887766")
    point = Location(name="Order Admin Point", is_active=True)
    db_session.add_all([client, point])
    await db_session.flush()

    order = Order(
        order_no="JX-ADMIN-ORDER-1",
        client_id=client.id,
        location_id=point.id,
        status="Новый",
        total_amount=500,
        currency="RUB",
    )
    db_session.add(order)
    db_session.add(
        DynamicRecord(
            module_slug="orders",
            row_index=10,
            data={"№ заказа": "JX-ADMIN-ORDER-1", "Статус": "Новый"},
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client_http:
        page_response = await client_http.get("/admin/orders")
        assert page_response.status_code == 200
        assert "JX-ADMIN-ORDER-1" in page_response.text

        delete_response = await client_http.post(f"/admin/orders/{order.id}/delete")

    assert delete_response.status_code == 302
    assert delete_response.headers.get("location") == "/admin/orders"

    deleted = (await db_session.execute(select(Order).where(Order.id == order.id))).scalar_one_or_none()
    assert deleted is None

    dashboard_copy = (
        await db_session.execute(
            select(DynamicRecord).where(DynamicRecord.module_slug == "orders")
        )
    ).scalars().all()
    assert dashboard_copy == []


@pytest.mark.asyncio
async def test_admin_user_delete_guards_and_cleanup_relations(db_session: Any):
    app = FastAPI()
    app.include_router(admin_router)

    current_user_state = {"id": 0}

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=current_user_state["id"], is_superuser=True, specialization="", email="admin@test.local", role=None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    self_user = User(email="self-delete@test.local", password_hash="x", is_active=True, is_superuser=True)
    db_session.add(self_user)
    await db_session.commit()
    current_user_state["id"] = int(self_user.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client_http:
        self_delete_response = await client_http.post(f"/admin/users/{self_user.id}/delete")
    assert self_delete_response.status_code == 302
    assert self_delete_response.headers.get("location") == "/admin/users?delete_error=self"

    self_after = (await db_session.execute(select(User).where(User.id == self_user.id))).scalar_one_or_none()
    assert self_after is not None

    lone_superuser = User(email="last-super@test.local", password_hash="x", is_active=True, is_superuser=True)
    db_session.add(lone_superuser)
    await db_session.commit()

    await db_session.delete(self_user)
    await db_session.commit()
    current_user_state["id"] = 999999

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client_http:
        last_super_response = await client_http.post(f"/admin/users/{lone_superuser.id}/delete")
    assert last_super_response.status_code == 302
    assert last_super_response.headers.get("location") == "/admin/users?delete_error=last_superuser"

    survivor = (await db_session.execute(select(User).where(User.id == lone_superuser.id))).scalar_one_or_none()
    assert survivor is not None

    manager = User(email="manager-delete@test.local", password_hash="x", is_active=True, is_superuser=False)
    point = Location(name="Delete User Point", is_active=True)
    client_entity = Client(name="Delete User Client", phone="70000000000")
    db_session.add_all([manager, point, client_entity])
    await db_session.flush()

    order = Order(
        order_no="JX-ADMIN-USER-DEL-1",
        client_id=client_entity.id,
        location_id=point.id,
        status="Новый",
        total_amount=100,
        currency="RUB",
    )
    db_session.add(order)
    await db_session.flush()

    executor = Executor(name="Delete Linked Executor", is_active=True, user_id=manager.id, location_id=point.id, current_active_tasks=0, max_active_tasks=5)
    delivery = LogisticsDelivery(order_id=order.id, courier_user_id=manager.id, status="created")
    db_session.add_all([executor, delivery])
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client_http:
        success_response = await client_http.post(f"/admin/users/{manager.id}/delete")
    assert success_response.status_code == 302
    assert success_response.headers.get("location") == "/admin/users?deleted=1"

    deleted_manager = (await db_session.execute(select(User).where(User.id == manager.id))).scalar_one_or_none()
    assert deleted_manager is None

    updated_executor = (await db_session.execute(select(Executor).where(Executor.id == executor.id))).scalar_one_or_none()
    assert updated_executor is not None
    assert updated_executor.user_id is None

    updated_delivery = (await db_session.execute(select(LogisticsDelivery).where(LogisticsDelivery.id == delivery.id))).scalar_one_or_none()
    assert updated_delivery is not None
    assert updated_delivery.courier_user_id is None


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

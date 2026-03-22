from types import SimpleNamespace

from sqlalchemy import select

from app.auth import get_current_user
from app.models import DynamicRecord, ModuleConfig
from models.crm import Executor, ExecutorSkill, Location, LocationPrice, Service
from models.omnichannel import Conversation, Message


async def test_catalog_calculate_endpoint_returns_breakdown(async_client, db_session):
    service = Service(
        slug="diag",
        name="Diagnostics",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={
            "currency": "RUB",
            "round_to": 2,
            "fields": [
                {"name": "hours", "type": "number", "coefficient": 50},
                {"name": "urgent", "type": "boolean", "true_price": 20},
            ],
        },
    )
    db_session.add(service)
    await db_session.commit()

    response = await async_client.post(
        f"/api/v1/catalog/services/{service.id}/calculate",
        json={"hours": 2, "urgent": True},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["ok"] is True
    assert data["currency"] == "RUB"
    assert data["total"] == 220
    assert isinstance(data["breakdown"], list)
    assert any(step["type"] == "base_price" for step in data["breakdown"])


async def test_catalog_create_and_patch_service_for_price_changes(async_client, db_session):
    create_response = await async_client.post(
        "/api/v1/catalog/services",
        json={
            "slug": "tv-repair",
            "name": "TV repair",
            "category": "repair",
            "base_price": 300,
            "calculator_schema": {
                "currency": "RUB",
                "fields": [
                    {
                        "name": "model",
                        "type": "select",
                        "options": [{"value": "default", "price": 0}],
                    }
                ],
            },
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()

    patch_response = await async_client.patch(
        f"/api/v1/catalog/services/{created['id']}",
        json={
            "base_price": 400,
            "calculator_schema": {
                "currency": "RUB",
                "fields": [
                    {
                        "name": "model",
                        "type": "select",
                        "options": [
                            {"value": "tv-default", "price": 0},
                            {"value": "tv-pro", "price": 100},
                        ],
                    },
                    {
                        "name": "complexity",
                        "type": "select",
                        "options": [
                            {"value": "normal", "multiplier": 1.0},
                            {"value": "hard", "multiplier": 1.3},
                        ],
                    },
                ],
            },
        },
    )
    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched["base_price"] == 400
    assert len((patched.get("calculator_schema") or {}).get("fields", [])) == 2

    calc_response = await async_client.post(
        f"/api/v1/catalog/services/{created['id']}/calculate",
        json={"model": "tv-pro", "complexity": "hard"},
    )
    assert calc_response.status_code == 200
    calc_data = calc_response.json()
    assert calc_data["ok"] is True
    # (400 + 100) * 1.3 = 650
    assert calc_data["total"] == 650


async def test_catalog_create_service_denied_when_no_manage_access(async_client, api_app):
    async def _deny_user():
        return SimpleNamespace(
            id=2,
            is_superuser=False,
            role=SimpleNamespace(permissions={"v2": {"services": {"manage": False}}}),
        )

    api_app.dependency_overrides[get_current_user] = _deny_user

    response = await async_client.post(
        "/api/v1/catalog/services",
        json={"slug": "no-access", "name": "No access", "base_price": 100},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "services_manage_access_denied"


async def test_catalog_list_services_hides_internal_pricing_for_non_manage(async_client, api_app, db_session):
    service = Service(
        slug="partner-safe",
        name="Partner safe",
        category="repair",
        is_active=True,
        base_price=120,
        calculator_schema={
            "currency": "RUB",
            "round_to": 2,
            "fields": [
                {
                    "name": "model",
                    "type": "select",
                    "label": "Model",
                    "options": [
                        {"value": "simple", "label": "Simple", "price": 10},
                        {"value": "vip", "label": "VIP", "price": 200, "internal_only": True},
                    ],
                },
                {
                    "name": "internal_margin",
                    "type": "number",
                    "coefficient": 50,
                    "internal_only": True,
                },
            ],
        },
    )
    db_session.add(service)
    await db_session.commit()

    async def _partner_user():
        return SimpleNamespace(
            id=3,
            is_superuser=False,
            role=SimpleNamespace(permissions={"v2": {"services": {"manage": False}}}),
        )

    api_app.dependency_overrides[get_current_user] = _partner_user

    list_response = await async_client.get("/api/v1/catalog/services")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert len(payload) == 1
    item = payload[0]

    assert item["base_price"] is None
    fields = (item.get("calculator_schema") or {}).get("fields") or []
    assert len(fields) == 1
    assert fields[0]["name"] == "model"
    assert fields[0]["options"] == [{"value": "simple", "label": "Simple"}]


async def test_catalog_calculate_hides_breakdown_for_non_manage(async_client, api_app, db_session):
    service = Service(
        slug="partner-total",
        name="Partner total",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={
            "currency": "RUB",
            "round_to": 2,
            "fields": [
                {"name": "hours", "type": "number", "coefficient": 50},
            ],
        },
    )
    db_session.add(service)
    await db_session.commit()

    async def _partner_user():
        return SimpleNamespace(
            id=4,
            is_superuser=False,
            role=SimpleNamespace(permissions={"v2": {"services": {"manage": False}}}),
        )

    api_app.dependency_overrides[get_current_user] = _partner_user

    calc_response = await async_client.post(
        f"/api/v1/catalog/services/{service.id}/calculate",
        json={"hours": 2},
    )
    assert calc_response.status_code == 200
    data = calc_response.json()
    assert data["ok"] is True
    assert data["total"] == 200
    assert data["breakdown"] == []


async def test_catalog_calculate_applies_commission_for_non_manage(async_client, api_app, db_session):
    service = Service(
        slug="partner-commission",
        name="Partner commission",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={
            "currency": "RUB",
            "round_to": 2,
            "fields": [{"name": "hours", "type": "number", "coefficient": 50}],
            "commission": {"enabled": True, "mode": "percent", "value": 10},
        },
    )
    db_session.add(service)
    await db_session.commit()

    async def _partner_user():
        return SimpleNamespace(
            id=5,
            is_superuser=False,
            role=SimpleNamespace(permissions={"v2": {"services": {"manage": False}}}),
        )

    api_app.dependency_overrides[get_current_user] = _partner_user

    calc_response = await async_client.post(
        f"/api/v1/catalog/services/{service.id}/calculate",
        json={"hours": 2},
    )
    assert calc_response.status_code == 200
    data = calc_response.json()
    assert data["ok"] is True
    assert data["total"] == 220
    assert data["breakdown"] == []


async def test_catalog_points_crud_and_point_prices_update(async_client, db_session):
    service_a = Service(
        slug="phone-a",
        name="Phone A",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={"fields": []},
    )
    service_b = Service(
        slug="phone-b",
        name="Phone B",
        category="repair",
        is_active=True,
        base_price=250,
        calculator_schema={"fields": []},
    )
    db_session.add_all([service_a, service_b])
    await db_session.commit()

    create_point_response = await async_client.post("/api/v1/catalog/points", json={"name": "Point 1"})
    assert create_point_response.status_code == 200
    assert create_point_response.json()["point"] == "Point 1"

    list_points_response = await async_client.get("/api/v1/catalog/points")
    assert list_points_response.status_code == 200
    assert list_points_response.json() == ["Point 1"]

    get_prices_response = await async_client.get("/api/v1/catalog/points/Point%201/prices")
    assert get_prices_response.status_code == 200
    prices_payload = get_prices_response.json()
    assert prices_payload["point"] == "Point 1"
    assert len(prices_payload["prices"]) == 2
    by_slug = {item["slug"]: item for item in prices_payload["prices"]}
    assert by_slug["phone-a"]["price"] == 100
    assert by_slug["phone-b"]["price"] == 250

    update_prices_response = await async_client.put(
        "/api/v1/catalog/points/Point%201/prices",
        json={
            "prices": [
                {"service_id": service_a.id, "price": 130},
                {"service_id": service_b.id, "price": 270},
            ]
        },
    )
    assert update_prices_response.status_code == 200
    assert update_prices_response.json()["updated"] == 2

    calculate_for_point_response = await async_client.post(
        f"/api/v1/catalog/services/{service_a.id}/calculate",
        json={"point": "Point 1"},
    )
    assert calculate_for_point_response.status_code == 200
    assert calculate_for_point_response.json()["total"] == 130

    delete_point_response = await async_client.delete("/api/v1/catalog/points/Point%201")
    assert delete_point_response.status_code == 200
    assert delete_point_response.json()["ok"] is True

    list_after_delete_response = await async_client.get("/api/v1/catalog/points")
    assert list_after_delete_response.status_code == 200
    assert list_after_delete_response.json() == []


async def test_catalog_service_enabled_points_create_and_update(async_client, db_session):
    point_a = Location(name="Point A", is_active=True)
    point_b = Location(name="Point B", is_active=True)
    point_c = Location(name="Point C", is_active=True)
    db_session.add_all([point_a, point_b, point_c])
    await db_session.commit()

    create_response = await async_client.post(
        "/api/v1/catalog/services",
        json={
            "slug": "point-enabled-service",
            "name": "Point Enabled Service",
            "category": "repair",
            "base_price": 500,
            "calculator_schema": {"fields": []},
            "enabled_points": ["Point A", "Point C"],
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    service_id = int(created["id"])

    location_rows = list((await db_session.execute(select(Location))).scalars().all())
    location_name_by_id = {int(location.id): str(location.name) for location in location_rows}

    initial_prices = list(
        (
            await db_session.execute(select(LocationPrice).where(LocationPrice.service_id == service_id))
        ).scalars().all()
    )
    initial_points = sorted([location_name_by_id[int(row.location_id)] for row in initial_prices])
    assert initial_points == ["Point A", "Point C"]

    update_response = await async_client.patch(
        f"/api/v1/catalog/services/{service_id}",
        json={"enabled_points": ["Point B"]},
    )
    assert update_response.status_code == 200

    updated_prices = list(
        (
            await db_session.execute(select(LocationPrice).where(LocationPrice.service_id == service_id))
        ).scalars().all()
    )
    updated_points = sorted([location_name_by_id[int(row.location_id)] for row in updated_prices])
    assert updated_points == ["Point B"]


async def test_orders_create_endpoint_smoke_from_calculator_payload(async_client, db_session):
    service = Service(
        slug="order-smoke-service",
        name="Order smoke service",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={"fields": []},
    )
    location = Location(name="Smoke point", is_active=True)
    db_session.add_all([service, location])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=location.id, service_id=service.id, price=135))
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/orders",
        json={
            "client": {"name": "Smoke Client", "phone": "79990000000"},
            "service_id": service.id,
            "location_id": location.id,
            "items": [
                {
                    "service_id": service.id,
                    "quantity": 1,
                    "unit_price": 135,
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("id"), int)
    assert str(payload.get("order_no", "")).startswith("ORD-")
    assert payload.get("status") == "new"


async def test_orders_create_mirrors_to_orders_and_clients_modules(async_client, db_session):
    db_session.add_all([
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
                {"name": "Устройство", "type": "TEXT"},
                {"name": "Неисправность", "type": "TEXT"},
                {"name": "Мастер", "type": "TEXT"},
                {"name": "Гарантия до", "type": "TEXT"},
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
                {"name": "№ заказа", "type": "TEXT"},
                {"name": "ФИО название", "type": "TEXT"},
                {"name": "Телефон", "type": "TEXT"},
                {"name": "Примечание", "type": "TEXT"},
            ],
            sort_order=1,
        ),
    ])

    service = Service(
        slug="mirror-service",
        name="Mirror Service",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={"fields": []},
    )
    location = Location(name="Mirror Point", is_active=True)
    db_session.add_all([service, location])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=location.id, service_id=service.id, price=135))
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/orders",
        json={
            "client": {"name": "Mirror Client", "phone": "79991112233"},
            "service_id": service.id,
            "location_id": location.id,
            "comment": "screen issue",
            "items": [{"service_id": service.id, "quantity": 1, "unit_price": 135}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    order_no = str(payload.get("order_no", ""))

    orders_records = (
        await db_session.execute(select(DynamicRecord).where(DynamicRecord.module_slug == "orders"))
    ).scalars().all()
    clients_records = (
        await db_session.execute(select(DynamicRecord).where(DynamicRecord.module_slug == "clients"))
    ).scalars().all()

    assert len(orders_records) == 1
    assert len(clients_records) == 1
    assert orders_records[0].data.get("№ заказа") == order_no
    assert orders_records[0].data.get("Клиент") == "Mirror Client"
    assert clients_records[0].data.get("№ заказа") == order_no
    assert clients_records[0].data.get("Телефон") == "79991112233"


async def test_catalog_point_prices_auto_collect_partner_prices_from_points(async_client, db_session):
    service = Service(
        slug="partners-screen",
        name="Partners screen",
        category="repair",
        is_active=True,
        base_price=1800,
        calculator_schema={"fields": []},
    )
    db_session.add(service)
    await db_session.commit()

    create_a = await async_client.post("/api/v1/catalog/points", json={"name": "Partner A"})
    create_b = await async_client.post("/api/v1/catalog/points", json={"name": "Partner B"})
    assert create_a.status_code == 200
    assert create_b.status_code == 200

    update_a = await async_client.put(
        "/api/v1/catalog/points/Partner%20A/prices",
        json={"prices": [{"service_id": service.id, "price": 3000}]},
    )
    update_b = await async_client.put(
        "/api/v1/catalog/points/Partner%20B/prices",
        json={"prices": [{"service_id": service.id, "price": 2000}]},
    )
    assert update_a.status_code == 200
    assert update_b.status_code == 200

    get_prices_response = await async_client.get("/api/v1/catalog/points/Partner%20A/prices")
    assert get_prices_response.status_code == 200
    payload = get_prices_response.json()
    assert payload["point"] == "Partner A"
    assert len(payload["prices"]) == 1

    row = payload["prices"][0]
    assert row["price"] == 3000
    assert row["partner_prices"]["Partner A"] == 3000
    assert row["partner_prices"]["Partner B"] == 2000


async def test_catalog_points_endpoints_denied_without_manage_access(async_client, api_app):
    async def _deny_user():
        return SimpleNamespace(
            id=6,
            is_superuser=False,
            role=SimpleNamespace(permissions={"v2": {"services": {"manage": False}}}),
        )

    api_app.dependency_overrides[get_current_user] = _deny_user

    list_response = await async_client.get("/api/v1/catalog/points")
    assert list_response.status_code == 403
    assert list_response.json()["detail"] == "services_manage_access_denied"

    create_response = await async_client.post("/api/v1/catalog/points", json={"name": "Point denied"})
    assert create_response.status_code == 403

    prices_response = await async_client.get("/api/v1/catalog/points/Point%20denied/prices")
    assert prices_response.status_code == 403

    update_response = await async_client.put(
        "/api/v1/catalog/points/Point%20denied/prices",
        json={"prices": []},
    )
    assert update_response.status_code == 403

    delete_response = await async_client.delete("/api/v1/catalog/points/Point%20denied")
    assert delete_response.status_code == 403


async def test_catalog_prices_matrix_cell_enable_creates_row(async_client, db_session):
    service = Service(slug="matrix-enable", name="Matrix Enable", category="Телефоны", is_active=True, base_price=100, calculator_schema={"fields": []})
    point = Location(name="Matrix Point", is_active=True)
    db_session.add_all([service, point])
    await db_session.commit()

    response = await async_client.patch(
        "/api/v1/catalog/prices/matrix/cell",
        json={"service_id": service.id, "point": point.name, "enabled": True, "price": 345},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["price"] == 345

    row = (
        await db_session.execute(
            select(LocationPrice).where(LocationPrice.location_id == point.id, LocationPrice.service_id == service.id)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert float(row.price or 0) == 345


async def test_catalog_prices_matrix_cell_disable_deletes_row(async_client, db_session):
    service = Service(slug="matrix-disable", name="Matrix Disable", category="Геймпады", is_active=True, base_price=100, calculator_schema={"fields": []})
    point = Location(name="Matrix Point Off", is_active=True)
    db_session.add_all([service, point])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=point.id, service_id=service.id, price=222))
    await db_session.commit()

    response = await async_client.patch(
        "/api/v1/catalog/prices/matrix/cell",
        json={"service_id": service.id, "point": point.name, "enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    row = (
        await db_session.execute(
            select(LocationPrice).where(LocationPrice.location_id == point.id, LocationPrice.service_id == service.id)
        )
    ).scalar_one_or_none()
    assert row is None


async def test_catalog_prices_matrix_cell_updates_price(async_client, db_session):
    service = Service(slug="matrix-update", name="Matrix Update", category="Приставки", is_active=True, base_price=100, calculator_schema={"fields": []})
    point = Location(name="Matrix Point Update", is_active=True)
    db_session.add_all([service, point])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=point.id, service_id=service.id, price=500))
    await db_session.commit()

    response = await async_client.patch(
        "/api/v1/catalog/prices/matrix/cell",
        json={"service_id": service.id, "point": point.name, "price": 777},
    )
    assert response.status_code == 200
    assert response.json()["price"] == 777

    row = (
        await db_session.execute(
            select(LocationPrice).where(LocationPrice.location_id == point.id, LocationPrice.service_id == service.id)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert float(row.price or 0) == 777


async def test_catalog_prices_matrix_filters_by_category(async_client, db_session):
    service_phone = Service(slug="matrix-phone", name="Phone Svc", category="Телефоны", is_active=True, base_price=100, calculator_schema={"fields": []})
    service_pad = Service(slug="matrix-pad", name="Pad Svc", category="Геймпады", is_active=True, base_price=100, calculator_schema={"fields": []})
    point = Location(name="Matrix Point Filter", is_active=True)
    db_session.add_all([service_phone, service_pad, point])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=point.id, service_id=service_phone.id, price=1000))
    db_session.add(LocationPrice(location_id=point.id, service_id=service_pad.id, price=2000))
    await db_session.commit()

    response = await async_client.get("/api/v1/catalog/prices/matrix?category=Телефоны")
    assert response.status_code == 200
    payload = response.json()
    assert payload["category"] == "телефоны"
    assert len(payload["services"]) == 1
    assert payload["services"][0]["slug"] == "matrix-phone"


async def test_catalog_prices_matrix_partner_can_edit_only_own_point(async_client, db_session, api_app):
    service = Service(slug="matrix-partner", name="Partner Svc", category="Телефоны", is_active=True, base_price=100, calculator_schema={"fields": []})
    own_point = Location(name="Partner Own Point", is_active=True)
    other_point = Location(name="Partner Other Point", is_active=True)
    db_session.add_all([service, own_point, other_point])
    await db_session.commit()

    async def _partner_user():
        return SimpleNamespace(
            id=55,
            is_superuser=False,
            role=SimpleNamespace(
                permissions={
                    "v2": {
                        "services": {
                            "manage": False,
                            "partner_mode": True,
                            "location": "Partner Own Point",
                        }
                    }
                }
            ),
            specialization="",
        )

    api_app.dependency_overrides[get_current_user] = _partner_user

    deny_response = await async_client.patch(
        "/api/v1/catalog/prices/matrix/cell",
        json={"service_id": service.id, "point": "Partner Other Point", "enabled": True, "price": 1000},
    )
    assert deny_response.status_code == 403
    assert deny_response.json()["detail"] == "point_access_denied"

    allow_response = await async_client.patch(
        "/api/v1/catalog/prices/matrix/cell",
        json={"service_id": service.id, "point": "Partner Own Point", "enabled": True, "price": 1111},
    )
    assert allow_response.status_code == 200
    assert allow_response.json()["enabled"] is True


async def test_orders_create_then_get_uses_fixed_price_flow(async_client, db_session):
    service = Service(
        slug="screen-repair-api",
        name="Screen repair API",
        category="repair",
        is_active=True,
        base_price=150,
        calculator_schema={"fields": [{"name": "hours", "type": "number", "coefficient": 25}]},
    )
    db_session.add(service)
    await db_session.flush()

    executor = Executor(name="Executor API", is_active=True, current_active_tasks=0, max_active_tasks=10)
    db_session.add(executor)
    await db_session.flush()
    db_session.add(ExecutorSkill(executor_id=executor.id, service_category="repair", level=3))
    await db_session.commit()

    create_response = await async_client.post(
        "/api/v1/orders",
        json={
            "priority": 1,
            "client": {"name": "Client API", "phone": "777"},
            "items": [
                {
                    "service_id": service.id,
                    "quantity": 2,
                    "calculator_payload": {"hours": 2},
                }
            ],
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["status"] == "new"

    get_response = await async_client.get(f"/api/v1/orders/{created['id']}")
    assert get_response.status_code == 200
    order_data = get_response.json()

    assert order_data["total_amount"] == 300
    assert len(order_data["items"]) == 1
    assert order_data["items"][0]["unit_price"] == 150
    assert order_data["items"][0]["price_client"] == 150
    assert order_data["items"][0]["price_partner"] == 150
    assert isinstance(order_data["items"][0]["calculator_breakdown"], list)
    assert order_data["items"][0]["calculator_breakdown"] == []


async def test_orders_create_returns_400_for_invalid_service_id(async_client):
    response = await async_client.post(
        "/api/v1/orders",
        json={
            "client": {"name": "Client Error"},
            "items": [{"service_id": 999999, "quantity": 1}],
        },
    )
    assert response.status_code == 400
    assert "Service not found" in response.json()["detail"]


async def test_orders_create_returns_400_when_no_price_source(async_client):
    response = await async_client.post(
        "/api/v1/orders",
        json={
            "client": {"name": "Client Error 2"},
            "items": [{"title": "Manual item without pricing"}],
        },
    )
    assert response.status_code == 400
    assert "Either unit_price or valid service_id is required" in response.json()["detail"]


async def test_orders_client_search_returns_matches(async_client, db_session):
    from models.crm import Client

    db_session.add_all(
        [
            Client(name="Alice", phone="79001112233", email=""),
            Client(name="Bob", phone="79005556677", email=""),
        ]
    )
    await db_session.commit()

    response = await async_client.get("/api/v1/orders/clients/search?phone=7900")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2
    phones = {item["phone"] for item in data}
    assert "79001112233" in phones
    assert "79005556677" in phones


async def test_orders_create_respects_preferred_executor(async_client, db_session):
    service = Service(
        slug="preferred-executor-service",
        name="Preferred executor service",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={"fields": []},
    )
    db_session.add(service)
    await db_session.flush()

    executor = Executor(name="Preferred Executor", is_active=True, current_active_tasks=0, max_active_tasks=5)
    db_session.add(executor)
    await db_session.flush()
    db_session.add(ExecutorSkill(executor_id=executor.id, service_category="repair", level=1))
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/orders",
        json={
            "preferred_executor_id": executor.id,
            "client": {"name": "Preferred Client", "phone": "70000000000"},
            "items": [{"service_id": service.id, "quantity": 1}],
        },
    )
    assert response.status_code == 200
    order_id = response.json()["id"]

    order_response = await async_client.get(f"/api/v1/orders/{order_id}")
    assert order_response.status_code == 200
    payload = order_response.json()
    assert len(payload["tasks"]) == 1
    assert payload["tasks"][0]["executor_id"] == executor.id


async def test_chats_endpoints_incoming_messages_send_and_suggest(async_client, db_session):
    incoming_response = await async_client.post(
        "/api/v1/chats/incoming",
        json={
            "channel_type": "custom",
            "external_thread_id": "chat-api-thread-1",
            "sender_name": "Bob",
            "text": "Хочу оформить заказ и оплатить",
        },
    )
    assert incoming_response.status_code == 200
    incoming_data = incoming_response.json()

    conversation_id = incoming_data["conversation_id"]

    inbox_response = await async_client.get("/api/v1/chats/inbox")
    assert inbox_response.status_code == 200
    assert any(c["id"] == conversation_id for c in inbox_response.json())

    messages_response = await async_client.get(f"/api/v1/chats/{conversation_id}/messages")
    assert messages_response.status_code == 200
    messages = messages_response.json()
    assert len(messages) == 1
    assert messages[0]["direction"] == "in"

    suggest_response = await async_client.get(f"/api/v1/chats/{conversation_id}/suggest-order")
    assert suggest_response.status_code == 200
    suggest_data = suggest_response.json()
    assert suggest_data["proposal"]["create_order"] is True

    send_empty_response = await async_client.post(f"/api/v1/chats/{conversation_id}/send", json={"text": "   "})
    assert send_empty_response.status_code == 400

    send_response = await async_client.post(f"/api/v1/chats/{conversation_id}/send", json={"text": "Ответили клиенту"})
    assert send_response.status_code == 200
    assert send_response.json()["direction"] == "out"

    all_messages = (
        await db_session.execute(select(Message).where(Message.conversation_id == conversation_id))
    ).scalars().all()
    assert len(all_messages) == 2

    conversation = (
        await db_session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one()
    assert conversation.last_message_at is not None


async def test_chats_incoming_unknown_connector_returns_400(async_client):
    response = await async_client.post(
        "/api/v1/chats/incoming",
        json={
            "channel_type": "unknown-channel",
            "external_thread_id": "thread-x",
            "sender_name": "User",
            "text": "hello",
        },
    )
    assert response.status_code == 400
    assert "Connector not configured" in response.json()["detail"]

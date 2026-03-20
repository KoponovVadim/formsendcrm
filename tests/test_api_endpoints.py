from types import SimpleNamespace

from sqlalchemy import select

from app.auth import get_current_user
from models.crm import Executor, ExecutorSkill, Service
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


async def test_orders_create_then_get_includes_calculator_breakdown(async_client, db_session):
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

    assert order_data["total_amount"] == 400
    assert len(order_data["items"]) == 1
    assert order_data["items"][0]["unit_price"] == 200
    assert isinstance(order_data["items"][0]["calculator_breakdown"], list)


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

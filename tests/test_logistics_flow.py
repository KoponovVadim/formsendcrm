from types import SimpleNamespace
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.orders import router as orders_router
from app.auth import get_current_user
from app.database import get_db
from app.routes_logistics import router as logistics_router
from models.crm import Client, Location, LocationPrice, LogisticsDelivery, Order, Service, SystemSetting


@pytest.mark.asyncio
async def test_order_creation_auto_creates_delivery_to_main_point(db_session: Any):
    app = FastAPI()
    app.include_router(orders_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, email="admin@test.local", role=SimpleNamespace(permissions={}), specialization="")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    main_point = Location(name="Main Point", is_active=True)
    intake_point = Location(name="Intake Point", is_active=True)
    service = Service(slug="logistics-service", name="Logistics service", category="repair", is_active=True, base_price=1000, calculator_schema={"fields": []})
    db_session.add_all([main_point, intake_point, service])
    await db_session.flush()

    db_session.add(SystemSetting(key="logistics_main_location_id", value=str(main_point.id)))
    db_session.add(LocationPrice(location_id=intake_point.id, service_id=service.id, price=1200))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/orders",
            json={
                "client": {"name": "Logistics Client", "phone": "79990000001"},
                "location_id": intake_point.id,
                "items": [{"service_id": service.id, "quantity": 1}],
            },
        )

    assert response.status_code == 200
    payload = response.json()

    order = (await db_session.execute(select(Order).where(Order.id == int(payload["id"])))).scalar_one()
    assert order.status == "Ожидает курьера"

    deliveries = (
        await db_session.execute(select(LogisticsDelivery).where(LogisticsDelivery.order_id == int(order.id)))
    ).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].leg_type == "to_main"
    assert int(deliveries[0].pickup_location_id or 0) == int(intake_point.id)
    assert int(deliveries[0].dropoff_location_id or 0) == int(main_point.id)


@pytest.mark.asyncio
async def test_courier_cabinet_updates_delivery_and_order_status(db_session: Any):
    app = FastAPI()
    app.include_router(logistics_router)

    async def _override_get_db() -> AsyncGenerator[Any, None]:
        yield db_session

    async def _override_get_current_user():
        return SimpleNamespace(
            id=77,
            is_superuser=False,
            email="courier@test.local",
            specialization="",
            role=SimpleNamespace(permissions={"courier": {"cabinet": True}}),
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    pickup = Location(name="Pickup", is_active=True)
    main_point = Location(name="Main", is_active=True)
    client = Client(name="Courier Client", phone="70000000002", email="")
    db_session.add_all([pickup, main_point, client])
    await db_session.flush()

    order = Order(order_no="ORD-COURIER-1", client_id=client.id, location_id=pickup.id, status="Ожидает курьера", total_amount=1000)
    db_session.add(order)
    await db_session.flush()

    delivery = LogisticsDelivery(
        order_id=order.id,
        pickup_location_id=pickup.id,
        dropoff_location_id=main_point.id,
        leg_type="to_main",
        status="awaiting_pickup",
        payment_eligible=False,
        courier_paid=False,
    )
    db_session.add(delivery)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False) as client_http:
        r1 = await client_http.post(f"/courier/deliveries/{delivery.id}/accept")
        r2 = await client_http.post(f"/courier/deliveries/{delivery.id}/pickup")
        r3 = await client_http.post(f"/courier/deliveries/{delivery.id}/deliver")

    assert r1.status_code == 302
    assert r2.status_code == 302
    assert r3.status_code == 302

    refreshed_delivery = (
        await db_session.execute(select(LogisticsDelivery).where(LogisticsDelivery.id == delivery.id))
    ).scalar_one()
    refreshed_order = (await db_session.execute(select(Order).where(Order.id == order.id))).scalar_one()

    assert refreshed_delivery.status == "delivered"
    assert int(refreshed_order.location_id or 0) == int(main_point.id)
    assert refreshed_order.status == "В ремонте"

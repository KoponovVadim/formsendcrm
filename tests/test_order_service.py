from decimal import Decimal

from sqlalchemy import select

from models.crm import Executor, ExecutorSkill, Location, LocationPrice, OrderItem, Service, Task
from services.order_service import OrderService


async def test_order_service_uses_base_price_without_dynamic_calculation(db_session):
    service = Service(
        slug="screen-repair",
        name="Screen repair",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={
            "fields": [
                {"name": "hours", "type": "number", "coefficient": 50},
            ]
        },
    )
    db_session.add(service)
    await db_session.flush()

    executor = Executor(name="Master One", is_active=True, current_active_tasks=1, max_active_tasks=10)
    db_session.add(executor)
    await db_session.flush()
    db_session.add(ExecutorSkill(executor_id=executor.id, service_category="repair", level=5))
    await db_session.commit()

    payload = {
        "priority": 1,
        "client": {"name": "Client A", "phone": "123"},
        "items": [
            {
                "service_id": service.id,
                "quantity": 2,
                "calculator_payload": {"hours": 2},
            }
        ],
    }

    created = await OrderService(db_session).create_order(payload, actor_user_id=None)

    assert created.total_amount == Decimal("200.0")

    item = (
        await db_session.execute(select(OrderItem).where(OrderItem.order_id == created.id))
    ).scalar_one()
    assert item.unit_price == Decimal("100.0")
    assert item.title == "Screen repair"
    assert isinstance(item.calculator_breakdown, list)
    assert item.calculator_breakdown == []

    task = (await db_session.execute(select(Task).where(Task.order_id == created.id))).scalar_one()
    assert task.executor_id == executor.id
    assert task.status == "assigned"


async def test_order_service_uses_location_price_when_location_selected(db_session):
    service = Service(
        slug="battery-replacement",
        name="Battery replacement",
        category="repair",
        is_active=True,
        base_price=900,
        calculator_schema={},
    )
    location = Location(name="NON-stop", is_active=True)
    db_session.add_all([service, location])
    await db_session.flush()

    db_session.add(LocationPrice(location_id=location.id, service_id=service.id, price=1200))
    await db_session.commit()

    created = await OrderService(db_session).create_order(
        {
            "location_id": location.id,
            "client": {"name": "Client L", "phone": "555"},
            "items": [{"service_id": service.id, "quantity": 1}],
        },
        actor_user_id=None,
    )

    assert created.total_amount == Decimal("1200.0")
    item = (await db_session.execute(select(OrderItem).where(OrderItem.order_id == created.id))).scalar_one()
    assert item.unit_price == Decimal("1200.0")

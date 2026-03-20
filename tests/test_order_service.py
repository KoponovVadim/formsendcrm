from decimal import Decimal

from sqlalchemy import select

from models.crm import Executor, ExecutorSkill, OrderItem, Service, Task
from services.order_service import OrderService


async def test_order_service_autocalculates_item_price_and_breakdown(db_session):
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

    assert created.total_amount == Decimal("400")

    item = (
        await db_session.execute(select(OrderItem).where(OrderItem.order_id == created.id))
    ).scalar_one()
    assert item.unit_price == Decimal("200")
    assert item.title == "Screen repair"
    assert isinstance(item.calculator_breakdown, list)
    assert item.calculator_breakdown[0]["type"] == "base_price"

    task = (await db_session.execute(select(Task).where(Task.order_id == created.id))).scalar_one()
    assert task.executor_id == executor.id
    assert task.status == "assigned"

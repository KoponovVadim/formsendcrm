from decimal import Decimal

from sqlalchemy import select

from app.models import DynamicRecord, ModuleConfig
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


async def test_order_service_assignment_uses_existing_orders_panel_records_only(db_session):
    location = Location(name="Panel Point", is_active=True)
    service = Service(
        slug="panel-load-service",
        name="Panel load service",
        category="repair",
        is_active=True,
        base_price=100,
        calculator_schema={},
    )
    db_session.add_all([location, service])
    await db_session.flush()
    db_session.add(LocationPrice(location_id=location.id, service_id=service.id, price=100))

    executor_a = Executor(name="Master A", is_active=True, location_id=location.id, current_active_tasks=0, max_active_tasks=10)
    executor_b = Executor(name="Master B", is_active=True, location_id=location.id, current_active_tasks=99, max_active_tasks=10)
    db_session.add_all([executor_a, executor_b])
    await db_session.flush()

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
            DynamicRecord(module_slug="orders", row_index=2, data={"№ заказа": "ORD-1", "Мастер": "Master A", "Статус": "В работе"}),
            DynamicRecord(module_slug="orders", row_index=3, data={"№ заказа": "ORD-2", "Мастер": "Master A", "Статус": "Новый"}),
        ]
    )
    await db_session.commit()

    created = await OrderService(db_session).create_order(
        {
            "location_id": location.id,
            "client": {"name": "Panel Client", "phone": "79990001122"},
            "items": [{"service_id": service.id, "quantity": 1}],
        },
        actor_user_id=None,
    )

    task = (await db_session.execute(select(Task).where(Task.order_id == created.id))).scalar_one()
    assert task.executor_id == executor_b.id

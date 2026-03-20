from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import event_bus
from models.crm import Order, OrderItem, Task
from repositories.crm_repository import CRMRepository
from services.assignment_service import choose_best_executor
from services.audit_service import write_audit
from services.pricing_service import calculate_total_with_breakdown


class OrderService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = CRMRepository(db)

    async def create_order(self, payload: dict, actor_user_id: int | None = None) -> Order:
        client_data = payload.get("client") or {}
        client = await self.repo.find_or_create_client(
            name=str(client_data.get("name", "Новый клиент")),
            phone=str(client_data.get("phone", "")),
            email=str(client_data.get("email", "")),
        )

        order = Order(
            order_no=str(payload.get("order_no") or f"ORD-{int(datetime.now(timezone.utc).timestamp())}"),
            client_id=client.id,
            status=str(payload.get("status", "new")),
            priority=int(payload.get("priority", 0)),
            source_channel=str(payload.get("source_channel", "manual")),
            currency=str(payload.get("currency", "RUB")),
            created_by_user_id=actor_user_id,
        )
        self.db.add(order)
        await self.db.flush()

        preferred_executor_id = int(payload.get("preferred_executor_id", 0) or 0)

        total = Decimal("0")
        for item_payload in payload.get("items", []):
            service_id = item_payload.get("service_id")
            service = await self.repo.get_service(service_id) if service_id else None
            if service_id and not service:
                raise ValueError(f"Service not found: {service_id}")

            qty = int(item_payload.get("quantity", 1))
            calculator_payload = item_payload.get("calculator_payload") or {}
            calculator_breakdown = item_payload.get("calculator_breakdown") or []

            if item_payload.get("unit_price") is None and service:
                computed_total, computed_breakdown = calculate_total_with_breakdown(
                    float(service.base_price or 0),
                    service.calculator_schema or {},
                    calculator_payload,
                )
                price = Decimal(str(computed_total))
                calculator_breakdown = computed_breakdown
            else:
                if item_payload.get("unit_price") is None and not service:
                    raise ValueError("Either unit_price or valid service_id is required for order item")
                price = Decimal(str(item_payload.get("unit_price", 0)))

            line_total = qty * price
            total += line_total

            item = OrderItem(
                order_id=order.id,
                service_id=service_id,
                title=str(item_payload.get("title") or (service.name if service else "")),
                quantity=qty,
                unit_price=price,
                line_total=line_total,
                status="new",
                calculator_payload=calculator_payload,
                calculator_breakdown=calculator_breakdown,
            )
            self.db.add(item)
            await self.db.flush()

            await self._create_and_assign_task(
                order=order,
                item=item,
                preferred_executor_id=preferred_executor_id,
            )

        order.total_amount = total

        await write_audit(
            self.db,
            entity_type="order",
            entity_id=order.id,
            action="create",
            actor_user_id=actor_user_id,
            before_data={},
            after_data={"order_no": order.order_no, "status": order.status, "total_amount": str(order.total_amount)},
        )
        await self.db.commit()

        await event_bus.publish("orders", {"type": "order_created", "order_id": order.id, "order_no": order.order_no})
        return order

    async def _create_and_assign_task(self, order: Order, item: OrderItem, preferred_executor_id: int = 0) -> Task:
        service = await self.repo.get_service(item.service_id) if item.service_id else None
        service_category = service.category if service else "repair"
        executors = await self.repo.active_executors()

        selected = None
        score = 0

        if preferred_executor_id > 0:
            selected = next((e for e in executors if int(e.id) == preferred_executor_id and bool(e.is_active)), None)
            if selected:
                score = 100

        if not selected:
            selected, score = choose_best_executor(executors, service_category, order.priority)

        task = Task(
            order_id=order.id,
            order_item_id=item.id,
            title=item.title or "Service task",
            description=f"Auto-created from order {order.order_no}",
            status="assigned" if selected else "open",
            priority=order.priority,
            executor_id=selected.id if selected else None,
            assignment_score=score if selected else 0,
        )
        self.db.add(task)

        if selected:
            selected.current_active_tasks = int(selected.current_active_tasks or 0) + 1

        return task

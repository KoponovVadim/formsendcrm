from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import event_bus
from models.crm import Order, OrderItem, Task
from repositories.crm_repository import CRMRepository
from services.order_backup_service import mirror_order_to_dynamic_modules
from services.assignment_service import choose_best_executor
from services.audit_service import write_audit


DEFAULT_ORDER_STATUS = "Новый"


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
            order_no=str(payload.get("order_no") or self._generate_order_no()),
            client_id=client.id,
            location_id=int(payload.get("location_id", 0) or 0) or None,
            status=str(payload.get("status") or DEFAULT_ORDER_STATUS),
            priority=int(payload.get("priority", 0)),
            source_channel=str(payload.get("source_channel", "manual")),
            comment=str(payload.get("comment") or payload.get("issue") or "").strip(),
            currency=str(payload.get("currency", "RUB")),
            created_by_user_id=actor_user_id,
        )
        self.db.add(order)
        await self.db.flush()

        preferred_executor_id = int(payload.get("preferred_executor_id", 0) or 0)
        first_item_title = ""
        assigned_master_name = ""

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
                location_id = int(payload.get("location_id", 0) or 0)
                if location_id > 0:
                    location_price = await self.repo.get_location_price(location_id=location_id, service_id=int(service.id))
                    if location_price is not None:
                        price = Decimal(str(location_price))
                    else:
                        price = Decimal(str(float(service.base_price or 0)))
                else:
                    price = Decimal(str(float(service.base_price or 0)))
                calculator_breakdown = []
            else:
                if item_payload.get("unit_price") is None and not service:
                    raise ValueError("Either unit_price or valid service_id is required for order item")
                price = Decimal(str(item_payload.get("unit_price", 0)))

            line_total = qty * price
            total += line_total

            price_partner = Decimal(str(item_payload.get("price_partner", item_payload.get("partner_price", price))))
            price_client = Decimal(str(item_payload.get("price_client", item_payload.get("client_price", price))))

            item = OrderItem(
                order_id=order.id,
                service_id=service_id,
                title=str(item_payload.get("title") or (service.name if service else "")),
                quantity=qty,
                price_client=price_client,
                price_partner=price_partner,
                unit_price=price,
                line_total=line_total,
                status="new",
                calculator_payload=calculator_payload,
                calculator_breakdown=calculator_breakdown,
            )
            self.db.add(item)
            await self.db.flush()

            if not first_item_title:
                first_item_title = str(item.title or "")

            task = await self._create_and_assign_task(
                order=order,
                item=item,
                preferred_executor_id=preferred_executor_id,
            )
            if task and task.executor_id and not assigned_master_name:
                executors = await self.repo.active_executors(location_id=order.location_id)
                selected = next((executor for executor in executors if int(executor.id) == int(task.executor_id)), None)
                if selected:
                    assigned_master_name = str(selected.name or "")

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

        issue_text = str((payload.get("comment") or payload.get("issue") or "")).strip()
        try:
            await mirror_order_to_dynamic_modules(
                self.db,
                order_no=str(order.order_no),
                accepted_at=getattr(order, "created_at", None),
                client_name=str(client.name or ""),
                client_phone=str(client.phone or ""),
                device_name=first_item_title,
                issue_text=issue_text,
                master_name=assigned_master_name,
                status=str(order.status or DEFAULT_ORDER_STATUS),
                total_amount=total,
                warranty_until=str(payload.get("warranty_until") or ""),
            )
        except Exception:
            # Order flow must not fail because backup/sync failed.
            pass

        await event_bus.publish("orders", {"type": "order_created", "order_id": order.id, "order_no": order.order_no})
        return order

    def _generate_order_no(self) -> str:
        # Milliseconds + short random suffix minimize uniqueness collisions under concurrent creates.
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        suffix = uuid4().hex[:6].upper()
        return f"ORD-{ts_ms}-{suffix}"

    async def _create_and_assign_task(self, order: Order, item: OrderItem, preferred_executor_id: int = 0) -> Task:
        executors = await self.repo.active_executors(location_id=order.location_id)

        selected = None
        score = 0

        if preferred_executor_id > 0:
            selected = next((e for e in executors if int(e.id) == preferred_executor_id and bool(e.is_active)), None)
            if selected:
                score = 100

        if not selected:
            selected, score = choose_best_executor(executors, order.priority)

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

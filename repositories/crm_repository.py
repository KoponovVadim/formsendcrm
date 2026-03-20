from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.crm import Client, Executor, Order, OrderItem, Service, Task


class CRMRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def find_or_create_client(self, name: str, phone: str = "", email: str = "") -> Client:
        stmt = select(Client).where(Client.phone == phone) if phone else select(Client).where(Client.email == email)
        existing = (await self.db.execute(stmt)).scalar_one_or_none() if (phone or email) else None
        if existing:
            if name and existing.name != name:
                existing.name = name
            return existing

        client = Client(name=name, phone=phone or "", email=email or "")
        self.db.add(client)
        await self.db.flush()
        return client

    async def get_service(self, service_id: int) -> Service | None:
        return (await self.db.execute(select(Service).where(Service.id == service_id))).scalar_one_or_none()

    async def search_services(self, query: str, include_inactive: bool = False) -> list[Service]:
        stmt = select(Service)
        if not include_inactive:
            stmt = stmt.where(Service.is_active == True)
        if query:
            like = f"%{query}%"
            stmt = stmt.where(Service.name.ilike(like))
        stmt = stmt.order_by(Service.name.asc()).limit(30)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_order(self, order_id: int) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.client),
                selectinload(Order.items).selectinload(OrderItem.service),
                selectinload(Order.tasks).selectinload(Task.executor),
            )
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def active_executors(self) -> list[Executor]:
        stmt = select(Executor).where(Executor.is_active == True).options(selectinload(Executor.skills))
        return list((await self.db.execute(stmt)).scalars().all())

    async def open_tasks_count(self, executor_id: int) -> int:
        stmt = select(Task).where(Task.executor_id == executor_id, Task.status.in_(["open", "assigned", "in_progress"]))
        return len((await self.db.execute(stmt)).scalars().all())

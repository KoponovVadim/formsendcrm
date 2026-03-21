from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.crm import Client, Executor, Location, LocationPrice, Order, OrderItem, Service, Task


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

    async def list_service_categories(self) -> list[str]:
        rows = (await self.db.execute(select(Service.category).where(Service.is_active == True).distinct().order_by(Service.category.asc()))).all()
        return [str(row[0]) for row in rows if row and row[0]]

    async def list_services_by_category(self, category: str, query: str = "") -> list[Service]:
        stmt = select(Service).where(Service.is_active == True)
        if category:
            stmt = stmt.where(Service.category == category)
        if query:
            stmt = stmt.where(Service.name.ilike(f"%{query}%"))
        stmt = stmt.order_by(Service.name.asc()).limit(100)
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_locations_for_service(self, service_id: int) -> list[tuple[Location, float, int, list[dict]]]:
        rows = (
            await self.db.execute(
                select(Location, LocationPrice.price)
                .join(LocationPrice, LocationPrice.location_id == Location.id)
                .where(Location.is_active == True, LocationPrice.service_id == service_id)
                .order_by(Location.name.asc())
            )
        ).all()

        if not rows:
            return []

        location_ids = [int(location.id) for location, _ in rows]
        active_order_statuses = [
            "new",
            "open",
            "assigned",
            "in_progress",
            "в работе",
            "ожидание",
            "готов",
        ]
        counts = (
            await self.db.execute(
                select(Order.location_id, func.count(Order.id))
                .where(
                    Order.location_id.in_(location_ids),
                    func.lower(func.coalesce(Order.status, "")).in_(active_order_statuses),
                )
                .group_by(Order.location_id)
            )
        ).all()
        count_map = {int(location_id): int(total) for location_id, total in counts}

        all_partner_prices = [
            {
                "partner_name": str(location.name or "").strip(),
                "price": float(price or 0),
                "in_work": count_map.get(int(location.id), 0),
            }
            for location, price in rows
            if str(location.name or "").strip()
        ]

        return [
            (
                location,
                float(price or 0),
                count_map.get(int(location.id), 0),
                all_partner_prices,
            )
            for location, price in rows
        ]

    async def list_executors_for_location_and_category(self, location_id: int, category: str) -> list[Executor]:
        stmt = (
            select(Executor)
            .where(Executor.is_active == True, Executor.location_id == location_id)
            .options(selectinload(Executor.skills))
            .order_by(Executor.current_active_tasks.asc(), Executor.name.asc())
        )

        executors = list((await self.db.execute(stmt)).scalars().all())
        if not category:
            return executors

        result = []
        for executor in executors:
            has_category = any(str(skill.service_category or "") == str(category) for skill in (executor.skills or []))
            if has_category:
                result.append(executor)
        return result

    async def get_location_price(self, location_id: int, service_id: int) -> float | None:
        value = (
            await self.db.execute(
                select(LocationPrice.price)
                .where(LocationPrice.location_id == location_id, LocationPrice.service_id == service_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if value is None:
            return None
        return float(value)

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
                selectinload(Order.location),
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

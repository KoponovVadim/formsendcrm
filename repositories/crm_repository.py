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

    async def list_locations_for_service(
        self,
        service_id: int,
        allowed_point_names: list[str] | None = None,
    ) -> list[tuple[Location, float, float, int, list[dict], dict, list[str], bool]]:
        stmt = (
            select(Location, LocationPrice.price, Service.base_price)
            .join(LocationPrice, LocationPrice.location_id == Location.id)
            .join(Service, Service.id == LocationPrice.service_id)
            .where(Location.is_active == True, LocationPrice.service_id == service_id)
        )

        if allowed_point_names:
            stmt = stmt.where(Location.name.in_(allowed_point_names))

        rows = (
            await self.db.execute(
                stmt.order_by(LocationPrice.price.asc(), Location.name.asc())
            )
        ).all()

        if not rows:
            return []

        location_ids = [int(location.id) for location, _, _ in rows]
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

        exec_rows = (
            await self.db.execute(
                select(
                    Executor.location_id,
                    func.count(Executor.id),
                    func.coalesce(func.sum(Executor.current_active_tasks), 0),
                    func.coalesce(func.sum(Executor.max_active_tasks), 0),
                )
                .where(Executor.is_active == True, Executor.location_id.in_(location_ids))
                .group_by(Executor.location_id)
            )
        ).all()
        load_map = {
            int(location_id): {
                "masters_count": int(masters_count or 0),
                "active_tasks": int(active_tasks or 0),
                "capacity": int(capacity or 0),
            }
            for location_id, masters_count, active_tasks, capacity in exec_rows
        }

        all_partner_prices = [
            {
                "partner_name": str(location.name or "").strip(),
                "price": float(price or 0),
                "in_work": count_map.get(int(location.id), 0),
            }
            for location, price, _ in rows
            if str(location.name or "").strip()
        ]

        min_price = min(float(price or 0) for _, price, _ in rows) if rows else 0

        return [
            (
                location,
                float(price or 0),
                float(base_price or 0),
                count_map.get(int(location.id), 0),
                all_partner_prices,
                load_map.get(int(location.id), {"masters_count": 0, "active_tasks": 0, "capacity": 0}),
                [
                    *(["самый дешевый"] if float(price or 0) == min_price else []),
                    *(
                        ["перегружен"]
                        if (
                            load_map.get(int(location.id), {}).get("capacity", 0) > 0
                            and (load_map.get(int(location.id), {}).get("active_tasks", 0) / max(load_map.get(int(location.id), {}).get("capacity", 1), 1)) >= 0.85
                        )
                        else []
                    ),
                ],
                float(price or 0) == min_price,
            )
            for location, price, base_price in rows
        ]

    async def list_executors_for_location_and_category(self, location_id: int, category: str) -> list[Executor]:
        stmt = (
            select(Executor)
            .where(Executor.is_active == True, Executor.location_id == location_id)
            .order_by(Executor.current_active_tasks.asc(), Executor.name.asc())
        )

        executors = list((await self.db.execute(stmt)).scalars().all())
        return executors

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

    async def active_executors(self, location_id: int | None = None) -> list[Executor]:
        stmt = select(Executor).where(Executor.is_active == True)
        if location_id is not None and int(location_id or 0) > 0:
            stmt = stmt.where(Executor.location_id == int(location_id))
        return list((await self.db.execute(stmt)).scalars().all())

    async def open_tasks_count(self, executor_id: int) -> int:
        stmt = select(Task).where(Task.executor_id == executor_id, Task.status.in_(["open", "assigned", "in_progress"]))
        return len((await self.db.execute(stmt)).scalars().all())

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import DynamicRecord, ModuleConfig
from models.crm import Client, Executor, Location, LocationPrice, Order, OrderItem, Service, Task


ACTIVE_ORDER_STATUSES = {
    "принят",
    "ожидает курьера",
    "в пути в цо",
    "в ремонте",
    "готов к отправке",
    "в пути в точку выдачи",
    "готов к выдаче",
    "новый",
    "new",
    "open",
    "assigned",
    "in_progress",
    "в работе",
    "ожидание",
    "готов",
}


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
        count_map = await self._get_live_orders_count_by_location(location_ids)

        live_executor_load_by_location = await self._get_live_executor_load_by_location(location_ids)

        exec_rows = (
            await self.db.execute(
                select(
                    Executor.location_id,
                    func.count(Executor.id),
                    func.coalesce(func.sum(Executor.max_active_tasks), 0),
                )
                .where(Executor.is_active == True, Executor.location_id.in_(location_ids))
                .group_by(Executor.location_id)
            )
        ).all()
        load_map = {
            int(location_id): {
                "masters_count": int(masters_count or 0),
                "active_tasks": int(live_executor_load_by_location.get(int(location_id), 0)),
                "capacity": int(capacity or 0),
            }
            for location_id, masters_count, capacity in exec_rows
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
            .order_by(Executor.name.asc())
        )

        executors = list((await self.db.execute(stmt)).scalars().all())
        live_load = await self._get_live_executor_load_by_name(location_id=location_id)
        for executor in executors:
            executor.current_active_tasks = int(live_load.get(str(executor.name or "").strip().lower(), 0))

        executors.sort(key=lambda ex: (int(ex.current_active_tasks or 0), str(ex.name or "").lower()))
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
        executors = list((await self.db.execute(stmt)).scalars().all())
        live_load = await self._get_live_executor_load_by_name(location_id=(int(location_id) if location_id is not None else None))
        for executor in executors:
            executor.current_active_tasks = int(live_load.get(str(executor.name or "").strip().lower(), 0))
        executors.sort(key=lambda ex: (int(ex.current_active_tasks or 0), str(ex.name or "").lower()))
        return executors

    async def open_tasks_count(self, executor_id: int) -> int:
        stmt = select(Task).where(Task.executor_id == executor_id, Task.status.in_(["open", "assigned", "in_progress"]))
        return len((await self.db.execute(stmt)).scalars().all())

    async def get_live_executor_load_map(self, location_id: int | None = None) -> dict[str, int]:
        return await self._get_live_executor_load_by_name(location_id=location_id)

    async def apply_live_load(self, executors: list[Executor], location_id: int | None = None) -> list[Executor]:
        live_load = await self.get_live_executor_load_map(location_id=location_id)
        for executor in executors:
            executor.current_active_tasks = int(live_load.get(str(executor.name or "").strip().lower(), 0))
        executors.sort(key=lambda ex: (int(ex.current_active_tasks or 0), str(ex.name or "").lower()))
        return executors

    async def _get_live_executor_load_by_location(self, location_ids: list[int]) -> dict[int, int]:
        if not location_ids:
            return {}

        executors = (
            await self.db.execute(
                select(Executor).where(Executor.is_active == True, Executor.location_id.in_(location_ids))
            )
        ).scalars().all()

        live_by_name = await self._get_live_executor_load_by_name(location_id=None)

        by_location: dict[int, int] = {}
        for executor in executors:
            location_id = int(executor.location_id or 0)
            if location_id <= 0:
                continue
            master_name_key = str(executor.name or "").strip().lower()
            by_location[location_id] = by_location.get(location_id, 0) + int(live_by_name.get(master_name_key, 0))

        return by_location

    async def _get_live_executor_load_by_name(self, location_id: int | None) -> dict[str, int]:
        orders_module = (
            await self.db.execute(select(ModuleConfig).where(ModuleConfig.slug == "orders"))
        ).scalar_one_or_none()
        if not orders_module:
            return {}

        fields_schema = orders_module.fields_schema or []
        field_names = [str(field.get("name", "")).strip() for field in fields_schema if isinstance(field, dict)]

        master_field = self._find_field_name(field_names, ["Мастер", "партнер", "партнёр"])
        status_field = self._find_field_name(field_names, ["Статус"])
        if not master_field:
            return {}

        records = (
            await self.db.execute(select(DynamicRecord).where(DynamicRecord.module_slug == "orders"))
        ).scalars().all()

        if location_id is not None and int(location_id or 0) > 0:
            available_names = {
                str(executor.name or "").strip().lower()
                for executor in (
                    await self.db.execute(
                        select(Executor).where(Executor.is_active == True, Executor.location_id == int(location_id))
                    )
                ).scalars().all()
            }
        else:
            available_names = None

        load_by_name: dict[str, int] = {}
        for record in records:
            data = record.data or {}
            master_name = str(data.get(master_field, "")).strip().lower()
            if not master_name:
                continue
            if available_names is not None and master_name not in available_names:
                continue

            if status_field:
                status = str(data.get(status_field, "")).strip().lower()
                if status and status not in ACTIVE_ORDER_STATUSES:
                    continue

            load_by_name[master_name] = int(load_by_name.get(master_name, 0)) + 1

        return load_by_name

    async def _get_live_orders_count_by_location(self, location_ids: list[int]) -> dict[int, int]:
        if not location_ids:
            return {}

        locations = (
            await self.db.execute(
                select(Location).where(Location.id.in_(location_ids))
            )
        ).scalars().all()
        location_name_to_id = {
            str(location.name or "").strip().lower(): int(location.id)
            for location in locations
            if str(location.name or "").strip()
        }

        orders_module = (
            await self.db.execute(select(ModuleConfig).where(ModuleConfig.slug == "orders"))
        ).scalar_one_or_none()
        if not orders_module:
            return {}

        fields_schema = orders_module.fields_schema or []
        field_names = [str(field.get("name", "")).strip() for field in fields_schema if isinstance(field, dict)]
        location_field = self._find_field_name(field_names, ["Точка", "Точка выдачи", "Пункт", "ПВЗ", "Партнер", "Партнёр", "Локация"]) 
        status_field = self._find_field_name(field_names, ["Статус"])
        if not location_field or not status_field:
            return {}

        records = (
            await self.db.execute(select(DynamicRecord).where(DynamicRecord.module_slug == "orders"))
        ).scalars().all()

        count_map: dict[int, int] = {int(location_id): 0 for location_id in location_ids}
        sorted_location_names = sorted(location_name_to_id.keys(), key=len, reverse=True)

        for record in records:
            data = record.data or {}
            status = str(data.get(status_field, "")).strip().lower()
            if status and status not in ACTIVE_ORDER_STATUSES:
                continue

            raw_location = str(data.get(location_field, "")).strip().lower()
            if not raw_location:
                continue

            location_id = location_name_to_id.get(raw_location)
            if location_id is None:
                for location_name in sorted_location_names:
                    if location_name in raw_location:
                        location_id = location_name_to_id[location_name]
                        break
            if location_id is None or int(location_id) not in count_map:
                continue

            count_map[int(location_id)] = int(count_map.get(int(location_id), 0)) + 1

        return count_map

    @staticmethod
    def _find_field_name(field_names: list[str], candidates: list[str]) -> str | None:
        lowered = {str(name).lower(): name for name in field_names if str(name).strip()}
        for candidate in candidates:
            key = str(candidate or "").strip().lower()
            if key in lowered:
                return lowered[key]

        for candidate in candidates:
            key = str(candidate or "").strip().lower()
            for field_name in field_names:
                if key in str(field_name).lower():
                    return field_name

        return None

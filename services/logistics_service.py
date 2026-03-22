from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.crm import LogisticsDelivery, Location, Order, SystemSetting

MAIN_LOCATION_SETTING_KEY = "logistics_main_location_id"
COURIER_FIXED_FEE_SETTING_KEY = "logistics_courier_fixed_fee"

LOGISTICS_ORDER_STATUSES = {
    "new": "Новый",
    "awaiting_delivery_to_repair": "Ожидает доставки в ремонт",
    "in_transit_to_repair": "Едет в точку ремонта",
    "arrived_repair_point": "Приехал в точку ремонта",
    "in_repair": "В ремонте",
    "ready": "Готов",
    "in_transit_to_pickup": "Едет на выдачу",
    "awaiting_pickup": "Ожидает выдачи",
    "issued": "Выдан",
    "canceled": "Отменен",
}


async def get_main_location_id(db: AsyncSession) -> int | None:
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == MAIN_LOCATION_SETTING_KEY))).scalar_one_or_none()
    if not row:
        return None
    raw = str(row.value or "").strip()
    return int(raw) if raw.isdigit() else None


async def set_main_location_id(db: AsyncSession, location_id: int | None) -> None:
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == MAIN_LOCATION_SETTING_KEY))).scalar_one_or_none()
    value = str(int(location_id)) if location_id and int(location_id) > 0 else ""
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=MAIN_LOCATION_SETTING_KEY, value=value))
    await db.commit()


async def get_courier_fixed_fee(db: AsyncSession) -> Decimal:
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == COURIER_FIXED_FEE_SETTING_KEY))).scalar_one_or_none()
    if not row:
        return Decimal("0")
    raw = str(row.value or "").strip().replace(",", ".")
    try:
        return Decimal(raw)
    except Exception:
        return Decimal("0")


async def set_courier_fixed_fee(db: AsyncSession, fee: Decimal | float | int) -> None:
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == COURIER_FIXED_FEE_SETTING_KEY))).scalar_one_or_none()
    value = str(Decimal(str(fee or 0)))
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=COURIER_FIXED_FEE_SETTING_KEY, value=value))
    await db.commit()


async def ensure_delivery_to_main_for_order(db: AsyncSession, order: Order) -> LogisticsDelivery | None:
    main_location_id = await get_main_location_id(db)
    if not main_location_id or not order.location_id:
        return None
    if int(order.location_id) == int(main_location_id):
        return None

    existing = (
        await db.execute(
            select(LogisticsDelivery).where(
                LogisticsDelivery.order_id == int(order.id),
                LogisticsDelivery.leg_type == "to_main",
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    fixed_fee = await get_courier_fixed_fee(db)

    delivery = LogisticsDelivery(
        order_id=int(order.id),
        pickup_location_id=int(order.location_id),
        dropoff_location_id=int(main_location_id),
        leg_type="to_main",
        status="awaiting_dispatch",
        courier_fee=fixed_fee,
        transport_cost=Decimal("0"),
        payment_eligible=False,
        notes="Автоматически создано при приемке заказа на точке.",
    )
    db.add(delivery)
    order.status = LOGISTICS_ORDER_STATUSES["awaiting_delivery_to_repair"]
    await db.commit()
    return delivery


async def list_logistics_deliveries(db: AsyncSession) -> list[LogisticsDelivery]:
    return list(
        (
            await db.execute(
                select(LogisticsDelivery)
                .options(
                    selectinload(LogisticsDelivery.order),
                    selectinload(LogisticsDelivery.pickup_location),
                    selectinload(LogisticsDelivery.dropoff_location),
                )
                .order_by(LogisticsDelivery.created_at.desc(), LogisticsDelivery.id.desc())
            )
        ).scalars().all()
    )


async def courier_visible_deliveries(db: AsyncSession, courier_user_id: int | None) -> list[LogisticsDelivery]:
    stmt = (
        select(LogisticsDelivery)
        .options(
            selectinload(LogisticsDelivery.order),
            selectinload(LogisticsDelivery.pickup_location),
            selectinload(LogisticsDelivery.dropoff_location),
        )
        .where(LogisticsDelivery.status == "awaiting_dispatch")
    )
    return list((await db.execute(stmt.order_by(LogisticsDelivery.created_at.asc(), LogisticsDelivery.id.asc()))).scalars().all())


async def update_delivery_status(
    db: AsyncSession,
    delivery: LogisticsDelivery,
    *,
    next_status: str,
    courier_user_id: int | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    delivery.status = next_status

    if courier_user_id and not delivery.courier_user_id:
        delivery.courier_user_id = int(courier_user_id)

    if next_status == "accepted":
        delivery.accepted_at = now
    elif next_status in {"picked_up", "in_transit"}:
        delivery.picked_up_at = now
    elif next_status == "delivered":
        delivery.delivered_at = now

    order = (await db.execute(select(Order).where(Order.id == int(delivery.order_id)))).scalar_one_or_none()
    if order:
        if delivery.leg_type == "to_main":
            if next_status == "accepted":
                order.status = LOGISTICS_ORDER_STATUSES["awaiting_delivery_to_repair"]
            elif next_status in {"picked_up", "in_transit"}:
                order.status = LOGISTICS_ORDER_STATUSES["in_transit_to_repair"]
            elif next_status == "delivered":
                order.location_id = delivery.dropoff_location_id
                order.status = LOGISTICS_ORDER_STATUSES["arrived_repair_point"]
        elif delivery.leg_type == "to_point":
            if next_status == "accepted":
                order.status = LOGISTICS_ORDER_STATUSES["ready"]
            elif next_status in {"picked_up", "in_transit"}:
                order.status = LOGISTICS_ORDER_STATUSES["in_transit_to_pickup"]
            elif next_status == "delivered":
                order.location_id = delivery.dropoff_location_id
                order.status = LOGISTICS_ORDER_STATUSES["awaiting_pickup"]

    await db.commit()


async def create_delivery(
    db: AsyncSession,
    *,
    order_id: int,
    pickup_location_id: int,
    dropoff_location_id: int,
    leg_type: str,
    courier_fee: float,
    transport_cost: float,
    payment_eligible: bool,
    notes: str,
) -> LogisticsDelivery:
    order = (await db.execute(select(Order).where(Order.id == int(order_id)))).scalar_one_or_none()
    if not order:
        raise ValueError("order_not_found")

    pickup = (await db.execute(select(Location).where(Location.id == int(pickup_location_id)))).scalar_one_or_none()
    dropoff = (await db.execute(select(Location).where(Location.id == int(dropoff_location_id)))).scalar_one_or_none()
    if not pickup or not dropoff:
        raise ValueError("location_not_found")

    fixed_fee = await get_courier_fixed_fee(db)

    delivery = LogisticsDelivery(
        order_id=int(order.id),
        pickup_location_id=int(pickup.id),
        dropoff_location_id=int(dropoff.id),
        leg_type=str(leg_type or "to_main"),
        status="awaiting_dispatch",
        courier_fee=fixed_fee,
        transport_cost=Decimal(str(transport_cost or 0)),
        payment_eligible=bool(payment_eligible),
        notes=str(notes or "").strip(),
    )
    db.add(delivery)
    if str(leg_type or "") == "to_main":
        order.status = LOGISTICS_ORDER_STATUSES["awaiting_delivery_to_repair"]
    else:
        order.status = LOGISTICS_ORDER_STATUSES["ready"]
    await db.commit()
    return delivery

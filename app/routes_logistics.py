from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import can_access_courier_cabinet, can_manage_logistics, filter_visible_modules_for_user, get_current_user
from app.database import get_db
from app.schema_loader import get_all_modules
from models.crm import Client, LogisticsDelivery, Location, Order, OrderItem
from services.logistics_service import (
    LOGISTICS_ORDER_STATUSES,
    courier_visible_deliveries,
    create_delivery,
    get_main_location_id,
    list_logistics_deliveries,
    set_main_location_id,
    update_delivery_status,
)
from services.order_backup_service import mirror_order_to_dynamic_modules

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _deny_if_not_logistics_manager(user) -> None:
    if not can_manage_logistics(user):
        raise HTTPException(status_code=403, detail="logistics_access_denied")


def _deny_if_not_courier(user) -> None:
    if not can_access_courier_cabinet(user):
        raise HTTPException(status_code=403, detail="courier_access_denied")


async def _sync_order_status_to_modules(db: AsyncSession, order: Order) -> None:
    item_title = (
        (
            await db.execute(
                select(OrderItem.title)
                .where(OrderItem.order_id == int(order.id))
                .order_by(OrderItem.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        or ""
    )

    client = (await db.execute(select(Client).where(Client.id == int(order.client_id)))).scalar_one_or_none()
    client_name = str(client.name or "") if client else ""
    client_phone = str(client.phone or "") if client else ""

    try:
        await mirror_order_to_dynamic_modules(
            db,
            order_no=str(order.order_no),
            accepted_at=getattr(order, "created_at", None),
            client_name=client_name,
            client_phone=client_phone,
            device_name=str(item_title or ""),
            issue_text=str(order.comment or ""),
            master_name="",
            status=str(order.status or ""),
            total_amount=order.total_amount or 0,
            warranty_until="",
        )
    except Exception:
        return


@router.get("/admin/logistics", response_class=HTMLResponse)
async def admin_logistics_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _deny_if_not_logistics_manager(user)

    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    deliveries = await list_logistics_deliveries(db)
    locations = (await db.execute(select(Location).order_by(Location.name.asc()))).scalars().all()
    orders = (await db.execute(select(Order).order_by(Order.created_at.desc()).limit(200))).scalars().all()
    main_location_id = await get_main_location_id(db)

    return templates.TemplateResponse(
        "logistics.html",
        {
            "request": request,
            "user": user,
            "modules": modules,
            "deliveries": deliveries,
            "locations": locations,
            "orders": orders,
            "main_location_id": main_location_id,
            "status_map": LOGISTICS_ORDER_STATUSES,
        },
    )


@router.post("/admin/logistics/main-point")
async def admin_logistics_set_main_point(
    location_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _deny_if_not_logistics_manager(user)

    raw = str(location_id or "").strip()
    if raw and raw.isdigit():
        location = (await db.execute(select(Location).where(Location.id == int(raw)))).scalar_one_or_none()
        await set_main_location_id(db, int(location.id) if location else None)
    else:
        await set_main_location_id(db, None)

    return RedirectResponse("/admin/logistics", status_code=302)


@router.post("/admin/logistics/deliveries/create")
async def admin_logistics_create_delivery(
    order_id: int = Form(...),
    pickup_location_id: int = Form(...),
    dropoff_location_id: int = Form(...),
    route_type: str = Form("to_main"),
    leg_type: str = Form(""),
    courier_fee: float = Form(0),
    transport_cost: float = Form(0),
    payment_eligible: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _deny_if_not_logistics_manager(user)

    try:
        await create_delivery(
            db,
            order_id=int(order_id),
            pickup_location_id=int(pickup_location_id),
            dropoff_location_id=int(dropoff_location_id),
            leg_type=str(route_type or leg_type or "to_main"),
            courier_fee=float(courier_fee or 0),
            transport_cost=float(transport_cost or 0),
            payment_eligible=str(payment_eligible or "").lower() in {"on", "1", "true", "yes"},
            notes=str(notes or ""),
        )
    except ValueError:
        return RedirectResponse("/admin/logistics?error=create_failed", status_code=302)

    return RedirectResponse("/admin/logistics", status_code=302)


@router.post("/admin/logistics/deliveries/{delivery_id}/payment")
async def admin_logistics_update_payment(
    delivery_id: int,
    payment_eligible: str = Form(""),
    courier_paid: str = Form(""),
    courier_fee: float = Form(0),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _deny_if_not_logistics_manager(user)

    delivery = (await db.execute(select(LogisticsDelivery).where(LogisticsDelivery.id == int(delivery_id)))).scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="delivery_not_found")

    delivery.payment_eligible = str(payment_eligible or "").lower() in {"on", "1", "true", "yes"}
    delivery.courier_paid = str(courier_paid or "").lower() in {"on", "1", "true", "yes"}
    delivery.courier_fee = float(courier_fee or 0)
    await db.commit()

    return RedirectResponse("/admin/logistics", status_code=302)


@router.get("/courier", response_class=HTMLResponse)
async def courier_cabinet(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _deny_if_not_courier(user)

    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    deliveries = await courier_visible_deliveries(db, courier_user_id=int(user.id))

    return templates.TemplateResponse(
        "courier_cabinet.html",
        {
            "request": request,
            "user": user,
            "modules": modules,
            "deliveries": deliveries,
            "status_map": LOGISTICS_ORDER_STATUSES,
        },
    )


@router.post("/courier/deliveries/{delivery_id}/accept")
async def courier_accept_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _deny_if_not_courier(user)

    delivery = (await db.execute(select(LogisticsDelivery).where(LogisticsDelivery.id == int(delivery_id)))).scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="delivery_not_found")

    if delivery.courier_user_id and int(delivery.courier_user_id) != int(user.id):
        raise HTTPException(status_code=403, detail="delivery_owned_by_other_courier")

    await update_delivery_status(db, delivery, next_status="accepted", courier_user_id=int(user.id))

    order = (await db.execute(select(Order).where(Order.id == int(delivery.order_id)))).scalar_one_or_none()
    if order:
        await _sync_order_status_to_modules(db, order)

    return RedirectResponse("/courier", status_code=302)


@router.post("/courier/deliveries/{delivery_id}/pickup")
async def courier_pickup_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _deny_if_not_courier(user)

    delivery = (await db.execute(select(LogisticsDelivery).where(LogisticsDelivery.id == int(delivery_id)))).scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="delivery_not_found")

    if delivery.courier_user_id and int(delivery.courier_user_id) != int(user.id):
        raise HTTPException(status_code=403, detail="delivery_owned_by_other_courier")

    await update_delivery_status(db, delivery, next_status="picked_up", courier_user_id=int(user.id))

    order = (await db.execute(select(Order).where(Order.id == int(delivery.order_id)))).scalar_one_or_none()
    if order:
        await _sync_order_status_to_modules(db, order)

    return RedirectResponse("/courier", status_code=302)


@router.post("/courier/deliveries/{delivery_id}/deliver")
async def courier_deliver_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _deny_if_not_courier(user)

    delivery = (await db.execute(select(LogisticsDelivery).where(LogisticsDelivery.id == int(delivery_id)))).scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="delivery_not_found")

    if delivery.courier_user_id and int(delivery.courier_user_id) != int(user.id):
        raise HTTPException(status_code=403, detail="delivery_owned_by_other_courier")

    await update_delivery_status(db, delivery, next_status="delivered", courier_user_id=int(user.id))

    order = (await db.execute(select(Order).where(Order.id == int(delivery.order_id)))).scalar_one_or_none()
    if order:
        await _sync_order_status_to_modules(db, order)

    return RedirectResponse("/courier", status_code=302)

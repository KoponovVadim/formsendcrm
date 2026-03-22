from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import can_manage_services, filter_visible_modules_for_user, get_current_user, get_services_access_scope
from app.database import get_db
from app.schema_loader import get_all_modules
from models.crm import Client, Executor, Location, LocationPrice, Order, Service
from repositories.crm_repository import CRMRepository

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _can_access_point_prices(user, location_name: str) -> bool:
    if user.is_superuser:
        return True
    if can_manage_services(user):
        return True

    scope = get_services_access_scope(user)
    allowed_points = scope.get("allowed_points") or []
    return str(location_name or "") in allowed_points


@router.get("/calculator", response_class=HTMLResponse)
async def calculator_page(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    repo = CRMRepository(db)
    categories = await repo.list_service_categories()

    return templates.TemplateResponse(
        "calculator.html",
        {
            "request": request,
            "user": user,
            "modules": modules,
            "categories": categories,
        },
    )


@router.get("/calculator/services", response_class=HTMLResponse)
async def calculator_services_partial(
    request: Request,
    category: str = "",
    q: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    repo = CRMRepository(db)
    services = await repo.list_services_by_category(category=str(category or "").strip(), query=str(q or "").strip())

    return templates.TemplateResponse(
        "partials/calculator_services.html",
        {
            "request": request,
            "services": services,
            "selected_category": str(category or "").strip(),
            "query": str(q or "").strip(),
        },
    )


@router.get("/calculator/locations", response_class=HTMLResponse)
async def calculator_locations_partial(
    request: Request,
    service_id: int = 0,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    repo = CRMRepository(db)
    scope = get_services_access_scope(user)
    rows = (
        await repo.list_locations_for_service(
            int(service_id or 0),
            allowed_point_names=scope.get("allowed_points") or None,
        )
        if int(service_id or 0) > 0
        else []
    )

    return templates.TemplateResponse(
        "partials/calculator_locations.html",
        {
            "request": request,
            "rows": rows,
            "service_id": int(service_id or 0),
            "show_own_price": not bool(scope.get("hide_own_price")),
        },
    )


@router.get("/calculator/executors", response_class=HTMLResponse)
async def calculator_executors_partial(
    request: Request,
    location_id: int = 0,
    service_id: int = 0,
    category: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    repo = CRMRepository(db)

    selected_category = str(category or "").strip()
    if not selected_category and int(service_id or 0) > 0:
        service = await repo.get_service(int(service_id))
        selected_category = str(service.category or "").strip() if service else ""

    executors = []
    if int(location_id or 0) > 0:
        executors = await repo.list_executors_for_location_and_category(int(location_id), selected_category)

    return templates.TemplateResponse(
        "partials/calculator_executors.html",
        {
            "request": request,
            "executors": executors,
            "location_id": int(location_id or 0),
        },
    )


@router.get("/points/{location_id}", response_class=HTMLResponse)
async def point_detail_page(
    request: Request,
    location_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    location = (await db.execute(select(Location).where(Location.id == int(location_id)))).scalar_one_or_none()
    if not location:
        raise HTTPException(status_code=404, detail="point_not_found")

    if not _can_access_point_prices(user, str(location.name or "")):
        raise HTTPException(status_code=403, detail="point_access_denied")

    scope = get_services_access_scope(user)

    orders = (
        await db.execute(
            select(Order, Client.name)
            .join(Client, Client.id == Order.client_id)
            .where(Order.location_id == int(location.id))
            .order_by(Order.created_at.desc())
            .limit(100)
        )
    ).all()

    executors = (
        await db.execute(
            select(Executor)
            .where(Executor.location_id == int(location.id))
            .order_by(Executor.is_active.desc(), Executor.current_active_tasks.asc(), Executor.name.asc())
        )
    ).scalars().all()

    prices = (
        await db.execute(
            select(Service, LocationPrice.price)
            .join(LocationPrice, LocationPrice.service_id == Service.id)
            .where(LocationPrice.location_id == int(location.id))
            .order_by(Service.category.asc(), Service.name.asc())
        )
    ).all()

    return templates.TemplateResponse(
        "points_detail.html",
        {
            "request": request,
            "user": user,
            "modules": modules,
            "location": location,
            "orders": orders,
            "executors": executors,
            "prices": prices,
            "show_own_price": not bool(scope.get("hide_own_price")),
        },
    )


@router.post("/points/{location_id}/prices")
async def point_detail_prices_update(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    location = (await db.execute(select(Location).where(Location.id == int(location_id)))).scalar_one_or_none()
    if not location:
        raise HTTPException(status_code=404, detail="point_not_found")

    if not _can_access_point_prices(user, str(location.name or "")):
        raise HTTPException(status_code=403, detail="point_access_denied")

    form = await request.form()

    existing_rows = (
        await db.execute(
            select(LocationPrice).where(LocationPrice.location_id == int(location.id))
        )
    ).scalars().all()
    rows_by_service = {int(row.service_id): row for row in existing_rows}

    changed = False
    for key, value in form.items():
        if not str(key).startswith("price_"):
            continue

        raw_service_id = str(key)[6:]
        if not raw_service_id.isdigit():
            continue
        service_id = int(raw_service_id)

        raw_price = str(value or "").replace(",", ".").strip()
        try:
            parsed_price = float(raw_price) if raw_price else 0.0
        except ValueError:
            continue

        row = rows_by_service.get(service_id)
        if row is None:
            db.add(
                LocationPrice(
                    location_id=int(location.id),
                    service_id=service_id,
                    price=parsed_price,
                )
            )
            changed = True
        elif float(row.price or 0) != parsed_price:
            row.price = parsed_price
            changed = True

    if changed:
        await db.commit()

    return RedirectResponse(f"/points/{location_id}?saved=1", status_code=302)

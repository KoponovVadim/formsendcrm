from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import can_manage_services, get_current_user
from app.database import get_db
from models.crm import Location, LocationPrice, Service
from repositories.crm_repository import CRMRepository
from services.pricing_service import as_float, calculate_total_with_breakdown

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])

PUBLIC_SCHEMA_KEYS = {"currency", "round_to", "fields"}
PUBLIC_FIELD_KEYS = {"name", "type", "label", "hint", "default", "required", "placeholder", "min", "max", "step", "options"}
PUBLIC_OPTION_KEYS = {"value", "label", "name"}


def _calculate_total_with_breakdown(base_price: float, schema: dict, payload: dict) -> tuple[float, list[dict]]:
    return calculate_total_with_breakdown(base_price, schema, payload)


def _calculate_total(base_price: float, schema: dict, payload: dict) -> float:
    total, _ = _calculate_total_with_breakdown(base_price, schema, payload)

    return total


def _sanitize_schema_for_public_view(schema: dict | None) -> dict:
    source = schema if isinstance(schema, dict) else {}
    result = {k: source.get(k) for k in PUBLIC_SCHEMA_KEYS if k in source and k != "fields"}

    raw_fields = source.get("fields")
    fields_result = []
    if isinstance(raw_fields, list):
        for field in raw_fields:
            if not isinstance(field, dict):
                continue
            if bool(field.get("internal_only", False)):
                continue

            safe_field = {k: field.get(k) for k in PUBLIC_FIELD_KEYS if k in field and k != "options"}
            options = field.get("options")
            if isinstance(options, list):
                safe_options = []
                for option in options:
                    if not isinstance(option, dict):
                        continue
                    if bool(option.get("internal_only", False)):
                        continue
                    safe_option = {k: option.get(k) for k in PUBLIC_OPTION_KEYS if k in option}
                    safe_options.append(safe_option)
                safe_field["options"] = safe_options

            fields_result.append(safe_field)

    result["fields"] = fields_result
    return result


def _normalize_point_name(value: str) -> str:
    return str(value or "").strip()


def _build_auto_partner_prices(price_rows: list[LocationPrice], locations_by_id: dict[int, Location]) -> dict[int, dict[str, float]]:
    grouped: dict[int, dict[str, float]] = {}
    for row in price_rows:
        location = locations_by_id.get(int(row.location_id))
        if not location:
            continue
        location_name = str(location.name or "").strip()
        if not location_name:
            continue
        service_id = int(row.service_id)
        grouped.setdefault(service_id, {})[location_name] = float(row.price or 0)
    return grouped


@router.get("/services")
async def list_services(
    q: str = "",
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    repo = CRMRepository(db)
    can_manage = can_manage_services(user)
    services = await repo.search_services(q, include_inactive=include_inactive and can_manage)

    if can_manage:
        return [
            {
                "id": s.id,
                "slug": s.slug,
                "name": s.name,
                "category": s.category,
                "base_price": float(s.base_price or 0),
                "is_active": bool(s.is_active),
                "calculator_schema": s.calculator_schema,
            }
            for s in services
        ]

    return [
        {
            "id": s.id,
            "slug": s.slug,
            "name": s.name,
            "category": s.category,
            "base_price": None,
            "is_active": bool(s.is_active),
            "calculator_schema": _sanitize_schema_for_public_view(s.calculator_schema),
        }
        for s in services
    ]


@router.get("/points")
async def list_points(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    locations = list((await db.execute(select(Location).order_by(Location.name.asc()))).scalars().all())
    return [str(location.name or "") for location in locations if str(location.name or "").strip()]


@router.post("/points")
async def create_point(payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    point_name = _normalize_point_name(payload.get("name", ""))
    if not point_name:
        raise HTTPException(400, "point_name_required")

    location = (await db.execute(select(Location).where(Location.name == point_name))).scalar_one_or_none()
    if not location:
        location = Location(name=point_name, is_active=True)
        db.add(location)
        await db.flush()

    services = list((await db.execute(select(Service))).scalars().all())
    if services:
        existing = list(
            (
                await db.execute(
                    select(LocationPrice).where(LocationPrice.location_id == int(location.id))
                )
            ).scalars().all()
        )
        existing_map = {int(row.service_id): row for row in existing}
        for service in services:
            if int(service.id) in existing_map:
                continue
            db.add(
                LocationPrice(
                    location_id=int(location.id),
                    service_id=int(service.id),
                    price=float(service.base_price or 0),
                )
            )

    await db.commit()
    return {"ok": True, "point": point_name}


@router.delete("/points/{point_name}")
async def delete_point(point_name: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    normalized_name = _normalize_point_name(point_name)
    if not normalized_name:
        raise HTTPException(400, "point_name_required")

    location = (await db.execute(select(Location).where(Location.name == normalized_name))).scalar_one_or_none()
    if not location:
        return {"ok": True}

    await db.execute(delete(LocationPrice).where(LocationPrice.location_id == int(location.id)))
    await db.delete(location)

    await db.commit()
    return {"ok": True}


@router.get("/points/{point_name}/prices")
async def get_point_prices(point_name: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    normalized_name = _normalize_point_name(point_name)
    if not normalized_name:
        raise HTTPException(400, "point_name_required")

    location = (await db.execute(select(Location).where(Location.name == normalized_name))).scalar_one_or_none()
    if not location:
        raise HTTPException(404, "point_not_found")

    services = list((await db.execute(select(Service).order_by(Service.name.asc()))).scalars().all())
    all_locations = list((await db.execute(select(Location))).scalars().all())
    location_by_id = {int(loc.id): loc for loc in all_locations}

    all_price_rows = list(
        (
            await db.execute(select(LocationPrice))
        ).scalars().all()
    )
    partner_prices_by_service = _build_auto_partner_prices(all_price_rows, location_by_id)

    own_location_prices = list(
        (
            await db.execute(
                select(LocationPrice).where(LocationPrice.location_id == int(location.id))
            )
        ).scalars().all()
    )
    location_price_map = {int(row.service_id): float(row.price or 0) for row in own_location_prices}

    rows = []

    for service in services:
        service_partner_prices = partner_prices_by_service.get(int(service.id), {})
        rows.append(
            {
                "service_id": service.id,
                "slug": service.slug,
                "name": service.name,
                "category": service.category,
                "price": float(location_price_map.get(int(service.id), float(service.base_price or 0))),
                "base_price": float(service.base_price or 0),
                "partner_prices": service_partner_prices,
            }
        )

    return {"point": normalized_name, "prices": rows}


@router.put("/points/{point_name}/prices")
async def update_point_prices(point_name: str, payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    normalized_name = _normalize_point_name(point_name)
    if not normalized_name:
        raise HTTPException(400, "point_name_required")

    entries = payload.get("prices")
    if not isinstance(entries, list):
        raise HTTPException(400, "prices_list_required")

    location = (await db.execute(select(Location).where(Location.name == normalized_name))).scalar_one_or_none()
    if not location:
        raise HTTPException(404, "point_not_found")

    services = list((await db.execute(select(Service))).scalars().all())
    by_id = {service.id: service for service in services}

    existing_location_prices = list(
        (
            await db.execute(
                select(LocationPrice).where(LocationPrice.location_id == int(location.id))
            )
        ).scalars().all()
    )
    location_price_map = {int(row.service_id): row for row in existing_location_prices}

    updated = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        service_id = int(entry.get("service_id", 0) or 0)
        if service_id <= 0:
            continue
        service = by_id.get(service_id)
        if not service:
            continue

        price = as_float(entry.get("price", service.base_price or 0), float(service.base_price or 0))

        location_price_row = location_price_map.get(service_id)
        if location_price_row is None:
            location_price_row = LocationPrice(location_id=int(location.id), service_id=service_id, price=price)
            db.add(location_price_row)
            location_price_map[service_id] = location_price_row
        else:
            location_price_row.price = price

        updated += 1

    await db.commit()
    return {"ok": True, "point": normalized_name, "updated": updated}


@router.post("/services")
async def create_service(payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    service = Service(
        slug=str(payload.get("slug", "")).strip(),
        name=str(payload.get("name", "")).strip(),
        category=str(payload.get("category", "repair")),
        base_price=payload.get("base_price", 0),
        calculator_schema=payload.get("calculator_schema") or {},
        is_active=bool(payload.get("is_active", True)),
    )
    db.add(service)
    await db.commit()
    return {"id": service.id, "slug": service.slug}


@router.patch("/services/{service_id}")
async def update_service(service_id: int, payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    service = (await db.execute(select(Service).where(Service.id == service_id))).scalar_one_or_none()
    if not service:
        raise HTTPException(404, "service_not_found")

    if "slug" in payload:
        service.slug = str(payload.get("slug", service.slug)).strip()
    if "name" in payload:
        service.name = str(payload.get("name", service.name)).strip()
    if "category" in payload:
        service.category = str(payload.get("category", service.category)).strip()
    if "base_price" in payload:
        service.base_price = payload.get("base_price", service.base_price)
    if "calculator_schema" in payload:
        service.calculator_schema = payload.get("calculator_schema") or {}
    if "is_active" in payload:
        service.is_active = bool(payload.get("is_active"))

    await db.commit()
    await db.refresh(service)

    return {
        "ok": True,
        "id": service.id,
        "slug": service.slug,
        "name": service.name,
        "category": service.category,
        "base_price": float(service.base_price or 0),
        "is_active": bool(service.is_active),
        "calculator_schema": service.calculator_schema or {},
    }


@router.post("/services/{service_id}/calculate")
async def calculate_service(service_id: int, payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    service = (await db.execute(select(Service).where(Service.id == service_id))).scalar_one_or_none()
    if not service:
        return {"ok": False, "error": "service_not_found"}

    payload_data = payload or {}
    effective_base_price = float(service.base_price or 0)
    point_name = _normalize_point_name(payload_data.get("point") or payload_data.get("__point") or "")
    if point_name:
        location = (await db.execute(select(Location).where(Location.name == point_name))).scalar_one_or_none()
        if location:
            location_price = (
                await db.execute(
                    select(LocationPrice.price)
                    .where(LocationPrice.location_id == int(location.id), LocationPrice.service_id == int(service.id))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if location_price is not None:
                effective_base_price = float(location_price)

    schema = service.calculator_schema or {}
    total, breakdown = _calculate_total_with_breakdown(effective_base_price, schema, payload_data)
    rounding = int(as_float(schema.get("round_to", 2), 2))
    precision = max(rounding, 0)

    normalized_breakdown = []
    for item in breakdown:
        normalized_item = {}
        for key, value in item.items():
            normalized_item[key] = round(value, precision) if isinstance(value, float) else value
        normalized_breakdown.append(normalized_item)

    manage_access = can_manage_services(user)

    return {
        "ok": True,
        "service_id": service_id,
        "total": round(total, precision),
        "currency": schema.get("currency", "RUB"),
        "breakdown": normalized_breakdown if manage_access else [],
    }

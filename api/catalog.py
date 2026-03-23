import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import can_manage_services, get_current_user, get_services_access_scope
from app.database import get_db
from models.crm import Location, LocationPrice, OrderItem, Service
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


def _normalize_point_names(values) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    seen = set()
    for value in values:
        point_name = _normalize_point_name(value)
        if not point_name:
            continue
        lowered = point_name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(point_name)
    return result


def _normalize_point_prices_map(values) -> dict[str, float]:
    if not isinstance(values, dict):
        return {}
    result: dict[str, float] = {}
    for raw_name, raw_price in values.items():
        point_name = _normalize_point_name(raw_name)
        if not point_name:
            continue
        result[point_name] = as_float(raw_price, 0.0)
    return result


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


def _slugify_service(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = re.sub(r"\s+", "-", raw)
    raw = re.sub(r"[^a-z0-9а-яё\-_]", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw[:150]


def _normalize_service_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


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
        service_ids = [int(s.id) for s in services]
        enabled_points_by_service: dict[int, list[str]] = {}
        point_prices_by_service: dict[int, dict[str, float]] = {}

        if service_ids:
            price_rows = list(
                (
                    await db.execute(select(LocationPrice).where(LocationPrice.service_id.in_(service_ids)))
                ).scalars().all()
            )
            location_ids = sorted({int(row.location_id) for row in price_rows})
            locations_by_id = {}
            if location_ids:
                locations = list((await db.execute(select(Location).where(Location.id.in_(location_ids)))).scalars().all())
                locations_by_id = {int(location.id): str(location.name or "").strip() for location in locations}

            for row in price_rows:
                location_name = locations_by_id.get(int(row.location_id), "")
                if not location_name:
                    continue
                service_id = int(row.service_id)
                enabled_points_by_service.setdefault(service_id, []).append(location_name)
                point_prices_by_service.setdefault(service_id, {})[location_name] = float(row.price or 0)

            for service_id, names in enabled_points_by_service.items():
                enabled_points_by_service[service_id] = sorted(set(names))

        return [
            {
                "id": s.id,
                "slug": s.slug,
                "name": s.name,
                "category": s.category,
                "base_price": float(s.base_price or 0),
                "is_active": bool(s.is_active),
                "calculator_schema": s.calculator_schema,
                "enabled_points": enabled_points_by_service.get(int(s.id), []),
                "point_prices": point_prices_by_service.get(int(s.id), {}),
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


def _can_edit_point_prices(user) -> bool:
    scope = get_services_access_scope(user)
    return bool(user.is_superuser or can_manage_services(user) or scope.get("partner_mode"))


def _is_point_allowed_for_user(user, point_name: str) -> bool:
    if user.is_superuser or can_manage_services(user):
        return True
    scope = get_services_access_scope(user)
    allowed_points = [str(v or "").strip() for v in (scope.get("allowed_points") or []) if str(v or "").strip()]
    if not allowed_points:
        return False
    return str(point_name or "").strip() in allowed_points


@router.get("/prices/matrix")
async def get_prices_matrix(
    category: str = "all",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not _can_edit_point_prices(user):
        raise HTTPException(403, "services_manage_access_denied")

    scope = get_services_access_scope(user)
    hide_own_price = bool(scope.get("hide_own_price"))

    point_stmt = select(Location).where(Location.is_active == True).order_by(Location.name.asc())
    allowed_points = [str(v or "").strip() for v in (scope.get("allowed_points") or []) if str(v or "").strip()]
    if not (user.is_superuser or can_manage_services(user)) and allowed_points:
        point_stmt = point_stmt.where(Location.name.in_(allowed_points))
    points = list((await db.execute(point_stmt)).scalars().all())
    point_names = [str(point.name or "").strip() for point in points if str(point.name or "").strip()]
    point_id_to_name = {int(point.id): str(point.name or "").strip() for point in points}

    service_stmt = select(Service).where(Service.is_active == True)
    normalized_category = str(category or "all").strip().lower()
    services = list((await db.execute(service_stmt.order_by(Service.category.asc(), Service.slug.asc()))).scalars().all())
    if normalized_category and normalized_category != "all":
        services = [
            service
            for service in services
            if str(service.category or "").strip().lower() == normalized_category
        ]

    service_ids = [int(service.id) for service in services]
    location_prices = []
    if service_ids and point_id_to_name:
        location_prices = list(
            (
                await db.execute(
                    select(LocationPrice).where(
                        LocationPrice.service_id.in_(service_ids),
                        LocationPrice.location_id.in_(list(point_id_to_name.keys())),
                    )
                )
            ).scalars().all()
        )

    price_map = {}
    for row in location_prices:
        service_id = int(row.service_id)
        point_name = point_id_to_name.get(int(row.location_id))
        if not point_name:
            continue
        price_map.setdefault(service_id, {})[point_name] = float(row.price or 0)

    return {
        "category": normalized_category,
        "points": point_names,
        "can_edit_own_price": bool(user.is_superuser),
        "show_own_price": not hide_own_price,
        "services": [
            {
                "service_id": int(service.id),
                "name": str(service.name or ""),
                "slug": str(service.slug or ""),
                "category": str(service.category or ""),
                "own_price": float(service.base_price or 0),
                "point_prices": price_map.get(int(service.id), {}),
            }
            for service in services
        ],
    }


@router.patch("/prices/matrix/cell")
async def update_prices_matrix_cell(payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not _can_edit_point_prices(user):
        raise HTTPException(403, "services_manage_access_denied")

    service_id = int(payload.get("service_id", 0) or 0)
    point_name = _normalize_point_name(payload.get("point", ""))
    if service_id <= 0 or not point_name:
        raise HTTPException(400, "service_and_point_required")

    if not _is_point_allowed_for_user(user, point_name):
        raise HTTPException(403, "point_access_denied")

    service = (await db.execute(select(Service).where(Service.id == service_id))).scalar_one_or_none()
    if not service:
        raise HTTPException(404, "service_not_found")

    location = (await db.execute(select(Location).where(Location.name == point_name))).scalar_one_or_none()
    if not location:
        raise HTTPException(404, "point_not_found")

    row = (
        await db.execute(
            select(LocationPrice)
            .where(LocationPrice.location_id == int(location.id), LocationPrice.service_id == int(service.id))
            .limit(1)
        )
    ).scalar_one_or_none()

    enabled = payload.get("enabled")
    if enabled is not None:
        enabled = bool(enabled)

    has_price = "price" in payload
    parsed_price = as_float(payload.get("price", 0), 0)

    if enabled is False:
        if row is not None:
            await db.delete(row)
            await db.commit()
        return {"ok": True, "enabled": False, "price": None}

    if row is None:
        initial_price = parsed_price if has_price else 0.0
        row = LocationPrice(location_id=int(location.id), service_id=int(service.id), price=initial_price)
        db.add(row)
        await db.commit()
        return {"ok": True, "enabled": True, "price": float(initial_price)}

    if has_price:
        row.price = parsed_price
        await db.commit()

    return {"ok": True, "enabled": True, "price": float(row.price or 0)}


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

    scope = get_services_access_scope(user)
    allowed_points = scope.get("allowed_points") or []
    if allowed_points and normalized_name not in allowed_points and not user.is_superuser:
        raise HTTPException(403, "point_access_denied")

    location = (await db.execute(select(Location).where(Location.name == normalized_name))).scalar_one_or_none()
    if not location:
        raise HTTPException(404, "point_not_found")

    services = list((await db.execute(select(Service).order_by(Service.slug.asc()))).scalars().all())
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
                "own_price": None if bool(scope.get("hide_own_price")) else float(service.base_price or 0),
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

    normalized_name = _normalize_service_name(payload.get("name", ""))
    normalized_slug = _slugify_service(payload.get("slug", "") or normalized_name)
    base_price_value = payload.get("base_price", 0)
    if not user.is_superuser:
        base_price_value = 0

    service = Service(
        slug=normalized_slug,
        name=normalized_name,
        category=str(payload.get("category", "repair")),
        base_price=base_price_value,
        calculator_schema=payload.get("calculator_schema") or {},
        is_active=bool(payload.get("is_active", True)),
    )
    db.add(service)
    await db.flush()

    point_prices_map = _normalize_point_prices_map(payload.get("point_prices", {}))
    enabled_point_names = _normalize_point_names(payload.get("enabled_points", []))

    if point_prices_map:
        locations = list((await db.execute(select(Location))).scalars().all())
        location_by_name = {
            str(location.name or "").strip().lower(): location
            for location in locations
            if str(location.name or "").strip()
        }
        enabled_point_names = []
        for point_name, point_price in point_prices_map.items():
            location = location_by_name.get(point_name.lower())
            if not location:
                continue
            enabled_point_names.append(str(location.name or "").strip())
            db.add(
                LocationPrice(
                    location_id=int(location.id),
                    service_id=int(service.id),
                    price=float(point_price),
                )
            )
    elif enabled_point_names:
        locations = list((await db.execute(select(Location).where(Location.name.in_(enabled_point_names)))).scalars().all())
        for location in locations:
            db.add(
                LocationPrice(
                    location_id=int(location.id),
                    service_id=int(service.id),
                    price=float(service.base_price or 0),
                )
            )

    await db.commit()
    return {"id": service.id, "slug": service.slug, "enabled_points": enabled_point_names, "point_prices": point_prices_map}


@router.patch("/services/{service_id}")
async def update_service(service_id: int, payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    service = (await db.execute(select(Service).where(Service.id == service_id))).scalar_one_or_none()
    if not service:
        raise HTTPException(404, "service_not_found")

    if "slug" in payload:
        service.slug = _slugify_service(payload.get("slug", service.slug)) or service.slug
    if "name" in payload:
        service.name = _normalize_service_name(payload.get("name", service.name)) or service.name
    if "category" in payload:
        service.category = str(payload.get("category", service.category)).strip()
    if "base_price" in payload:
        if not user.is_superuser:
            raise HTTPException(403, "own_price_edit_forbidden")
        service.base_price = payload.get("base_price", service.base_price)
    if "calculator_schema" in payload:
        service.calculator_schema = payload.get("calculator_schema") or {}
    if "is_active" in payload:
        service.is_active = bool(payload.get("is_active"))

    enabled_point_names = None
    if "enabled_points" in payload or "point_prices" in payload:
        point_prices_map = _normalize_point_prices_map(payload.get("point_prices", {}))
        if point_prices_map:
            enabled_point_names = list(point_prices_map.keys())
        else:
            enabled_point_names = _normalize_point_names(payload.get("enabled_points", []))

        existing_rows = list(
            (
                await db.execute(select(LocationPrice).where(LocationPrice.service_id == int(service.id)))
            ).scalars().all()
        )
        existing_by_location = {int(row.location_id): row for row in existing_rows}

        locations = list((await db.execute(select(Location))).scalars().all())
        location_by_name = {str(location.name or "").strip().lower(): location for location in locations if str(location.name or "").strip()}

        target_location_ids = set()
        for point_name in enabled_point_names:
            location = location_by_name.get(point_name.lower())
            if location:
                target_location_ids.add(int(location.id))

        for location_id, row in existing_by_location.items():
            if location_id not in target_location_ids:
                await db.delete(row)

        location_name_by_id = {int(location.id): str(location.name or "").strip() for location in locations}

        for location_id in target_location_ids:
            if location_id in existing_by_location:
                if point_prices_map:
                    point_name = location_name_by_id.get(location_id, "")
                    existing_by_location[location_id].price = float(point_prices_map.get(point_name, 0.0))
                continue
            location_name = location_name_by_id.get(location_id, "")
            initial_price = float(service.base_price or 0)
            if point_prices_map and location_name:
                initial_price = float(point_prices_map.get(location_name, initial_price))
            db.add(
                LocationPrice(
                    location_id=location_id,
                    service_id=int(service.id),
                    price=initial_price,
                )
            )

        if point_prices_map:
            for location_id, row in existing_by_location.items():
                if location_id not in target_location_ids:
                    continue
                point_name = location_name_by_id.get(location_id, "")
                if not point_name:
                    continue
                row.price = float(point_prices_map.get(point_name, row.price or 0))

    await db.commit()
    await db.refresh(service)

    updated_rows = list(
        (
            await db.execute(select(LocationPrice).where(LocationPrice.service_id == int(service.id)))
        ).scalars().all()
    )
    updated_location_ids = [int(row.location_id) for row in updated_rows]
    updated_locations = list((await db.execute(select(Location).where(Location.id.in_(updated_location_ids)))).scalars().all()) if updated_location_ids else []
    location_name_by_id = {int(location.id): str(location.name or "").strip() for location in updated_locations}
    updated_point_prices = {
        location_name_by_id[int(row.location_id)]: float(row.price or 0)
        for row in updated_rows
        if location_name_by_id.get(int(row.location_id))
    }

    return {
        "ok": True,
        "id": service.id,
        "slug": service.slug,
        "name": service.name,
        "category": service.category,
        "base_price": float(service.base_price or 0),
        "is_active": bool(service.is_active),
        "calculator_schema": service.calculator_schema or {},
        "enabled_points": enabled_point_names,
        "point_prices": updated_point_prices,
    }


@router.delete("/services/{service_id}")
async def delete_service(service_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not can_manage_services(user):
        raise HTTPException(403, "services_manage_access_denied")

    service = (await db.execute(select(Service).where(Service.id == service_id))).scalar_one_or_none()
    if not service:
        raise HTTPException(404, "service_not_found")

    linked_items_count = (
        await db.execute(
            select(func.count())
            .select_from(OrderItem)
            .where(OrderItem.service_id == int(service.id))
        )
    ).scalar() or 0
    if int(linked_items_count) > 0:
        raise HTTPException(409, "service_has_orders")

    await db.execute(delete(LocationPrice).where(LocationPrice.service_id == int(service.id)))
    await db.delete(service)
    await db.commit()
    return {"ok": True}


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

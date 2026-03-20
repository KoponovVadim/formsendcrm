from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import can_manage_services, get_current_user
from app.database import get_db
from models.crm import Service
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

    schema = service.calculator_schema or {}
    total, breakdown = _calculate_total_with_breakdown(float(service.base_price or 0), schema, payload or {})
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

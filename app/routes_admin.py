"""
Admin panel routes – roles, permissions, modules management, sync.
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import json
import re

from app.database import get_db
from app.models import User, Role, ModuleConfig, SyncLog, DynamicRecord
from app.auth import (
    can_manage_services,
    get_current_user,
    require_superuser,
    hash_password,
    get_user_specializations,
)
from app.schema_loader import get_all_modules
from app import sync_service
from models.crm import Executor, Location, LocationPrice, Order, Service, Task
from repositories.crm_repository import CRMRepository

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")

DEFAULT_STATUS_OPTIONS = [
    "Новый",
    "Ожидает доставки в ремонт",
    "Едет в точку ремонта",
    "Приехал в точку ремонта",
    "В ремонте",
    "Готов",
    "Едет на выдачу",
    "Ожидает выдачи",
    "Выдан",
    "Отменен",
]

DEFAULT_STATUS_COLORS = {
    "Новый": "#93c5fd",
    "Ожидает доставки в ремонт": "#fbbf24",
    "Едет в точку ремонта": "#60a5fa",
    "Приехал в точку ремонта": "#38bdf8",
    "В ремонте": "#f59e0b",
    "Готов": "#22c55e",
    "Едет на выдачу": "#0ea5e9",
    "Ожидает выдачи": "#34d399",
    "Выдан": "#60a5fa",
    "Отменен": "#fecaca",
}


def _status_key(value: str) -> str:
    return (value or "").strip().lower()


def _detect_status_field_name(fields_schema: list) -> str:
    field_names = [f.get("name", "") for f in fields_schema if isinstance(f, dict)]
    exact_candidates = {"статус", "статус заказа", "status", "order status"}
    for name in field_names:
        if name.strip().lower() in exact_candidates:
            return name
    for name in field_names:
        lowered = name.strip().lower()
        if "статус" in lowered and "оплат" not in lowered:
            return name
    return ""


def _detect_specialization_field_name(fields_schema: list) -> str:
    field_names = [f.get("name", "") for f in fields_schema if isinstance(f, dict)]
    exact_candidates = {"устройство", "тип устройства", "категория", "специализация", "device", "category"}
    for name in field_names:
        if name.strip().lower() in exact_candidates:
            return name
    for name in field_names:
        lowered = name.strip().lower()
        if "устрой" in lowered or "категор" in lowered or "специал" in lowered:
            return name
    return ""


def _normalize_hex_color(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if not re.match(r"^#[0-9a-fA-F]{6}$", raw):
        return ""
    return raw.lower()


def _build_module_status_editor_state(mod: ModuleConfig) -> dict:
    fields_schema = mod.fields_schema or []
    status_field_candidates = [
        f.get("name", "")
        for f in fields_schema
        if isinstance(f, dict) and "статус" in f.get("name", "").strip().lower()
    ]
    detected_field = _detect_status_field_name(fields_schema)

    field_obj = None
    for field in fields_schema:
        if isinstance(field, dict) and field.get("name") == detected_field:
            field_obj = field
            break

    stored_options = field_obj.get("status_options") if field_obj else None
    options = [str(v).strip() for v in (stored_options or []) if str(v).strip()]
    if not options:
        options = list(DEFAULT_STATUS_OPTIONS)

    stored_colors = field_obj.get("status_colors") if field_obj else {}
    if not isinstance(stored_colors, dict):
        stored_colors = {}

    colors_lines = []
    for option in options:
        fallback_color = DEFAULT_STATUS_COLORS.get(option, "")
        color = _normalize_hex_color(str(stored_colors.get(option, fallback_color)))
        if color:
            colors_lines.append(f"{option}={color}")

    specialization_field_candidates = [
        f.get("name", "")
        for f in fields_schema
        if isinstance(f, dict)
    ]
    detected_specialization_field = _detect_specialization_field_name(fields_schema)
    specialization_field_obj = None
    for field in fields_schema:
        if isinstance(field, dict) and field.get("name") == detected_specialization_field:
            specialization_field_obj = field
            break

    specialization_options = specialization_field_obj.get("specialization_options") if specialization_field_obj else []
    specialization_options = [str(v).strip() for v in (specialization_options or []) if str(v).strip()]

    return {
        "status_field_candidates": status_field_candidates,
        "selected_status_field": detected_field,
        "status_options_text": "\n".join(options),
        "status_colors_text": "\n".join(colors_lines),
        "specialization_field_candidates": specialization_field_candidates,
        "selected_specialization_field": detected_specialization_field,
        "specialization_options_text": "\n".join(specialization_options),
    }


def _get_partner_specialization_catalog(modules: list[ModuleConfig]) -> list[str]:
    for mod in modules:
        for field in (mod.fields_schema or []):
            if not isinstance(field, dict):
                continue
            options = field.get("specialization_options")
            if isinstance(options, list) and options:
                cleaned = [str(v).strip() for v in options if str(v).strip()]
                if cleaned:
                    return cleaned
    return []


def _require_admin(user):
    require_superuser(user)


def _require_services_manager(user):
    if user.is_superuser:
        return
    if not can_manage_services(user):
        raise HTTPException(status_code=403, detail="services_manage_access_denied")


def _safe_admin_redirect_path(value: str, default: str = "/admin/locations") -> str:
    path = str(value or "").strip()
    if path.startswith("/admin/"):
        return path
    return default


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    modules = await get_all_modules(db)

    # Counts
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar()
    role_count = (await db.execute(select(func.count()).select_from(Role))).scalar()
    module_count = (await db.execute(select(func.count()).select_from(ModuleConfig))).scalar()

    # Recent syncs
    sync_logs = (await db.execute(
        select(SyncLog).order_by(SyncLog.created_at.desc()).limit(10)
    )).scalars().all()

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "user": user,
        "modules": modules,
        "user_count": user_count,
        "role_count": role_count,
        "module_count": module_count,
        "sync_logs": sync_logs,
    })


@router.get("/services", response_class=HTMLResponse)
async def admin_services(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    modules = await get_all_modules(db)
    return templates.TemplateResponse("admin/services.html", {
        "request": request,
        "user": user,
        "modules": modules,
    })


@router.get("/locations", response_class=HTMLResponse)
async def admin_locations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    modules = await get_all_modules(db)

    locations = (await db.execute(select(Location).order_by(Location.name.asc()))).scalars().all()
    repo = CRMRepository(db)
    executors = (await db.execute(select(Executor).order_by(Executor.name.asc()))).scalars().all()
    executors = await repo.apply_live_load(executors, location_id=None)
    services = (
        await db.execute(
            select(Service)
            .where(Service.is_active == True)
            .order_by(Service.category.asc(), Service.name.asc())
        )
    ).scalars().all()
    prices = (await db.execute(select(LocationPrice))).scalars().all()
    price_map = {(int(p.location_id), int(p.service_id)): float(p.price or 0) for p in prices}

    return templates.TemplateResponse("admin/locations.html", {
        "request": request,
        "user": user,
        "modules": modules,
        "locations": locations,
        "executors": executors,
        "services": services,
        "price_map": price_map,
    })


@router.get("/executors", response_class=HTMLResponse)
async def admin_executors(
    request: Request,
    q: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    modules = await get_all_modules(db)

    repo = CRMRepository(db)
    query = str(q or "").strip().lower()
    executors = (await db.execute(select(Executor).order_by(Executor.name.asc()))).scalars().all()
    executors = await repo.apply_live_load(executors, location_id=None)
    if query:
        executors = [ex for ex in executors if query in str(ex.name or "").lower()]
    locations = (await db.execute(select(Location).order_by(Location.name.asc()))).scalars().all()

    return templates.TemplateResponse("admin/executors.html", {
        "request": request,
        "user": user,
        "modules": modules,
        "executors": executors,
        "locations": locations,
        "query": query,
    })


@router.post("/locations/create")
async def admin_location_create(
    name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    normalized = str(name or "").strip()
    if not normalized:
        return RedirectResponse("/admin/locations", status_code=302)

    existing = (await db.execute(select(Location).where(Location.name == normalized))).scalar_one_or_none()
    if not existing:
        db.add(Location(name=normalized, is_active=True))
        await db.commit()

    return RedirectResponse("/admin/locations", status_code=302)


@router.post("/locations/{location_id}/toggle")
async def admin_location_toggle(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    location = (await db.execute(select(Location).where(Location.id == location_id))).scalar_one_or_none()
    if location:
        location.is_active = not bool(location.is_active)
        await db.commit()
    return RedirectResponse("/admin/locations", status_code=302)


@router.post("/locations/{location_id}/update")
async def admin_location_update(
    location_id: int,
    name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    location = (await db.execute(select(Location).where(Location.id == location_id))).scalar_one_or_none()
    if not location:
        raise HTTPException(status_code=404, detail="location_not_found")

    normalized_name = str(name or "").strip()
    if not normalized_name:
        return RedirectResponse("/admin/locations?error=location_name_required", status_code=302)

    duplicate = (
        await db.execute(select(Location).where(Location.name == normalized_name, Location.id != int(location.id)))
    ).scalar_one_or_none()
    if duplicate:
        return RedirectResponse("/admin/locations?error=location_name_exists", status_code=302)

    location.name = normalized_name
    await db.commit()
    return RedirectResponse("/admin/locations", status_code=302)


@router.post("/locations/{location_id}/delete")
async def admin_location_delete(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    location = (await db.execute(select(Location).where(Location.id == location_id))).scalar_one_or_none()
    if not location:
        raise HTTPException(status_code=404, detail="location_not_found")

    orders_count = (
        await db.execute(select(func.count()).select_from(Order).where(Order.location_id == int(location.id)))
    ).scalar() or 0
    if int(orders_count) > 0:
        return RedirectResponse("/admin/locations?error=location_has_orders", status_code=302)

    executors = (
        await db.execute(select(Executor).where(Executor.location_id == int(location.id)))
    ).scalars().all()
    for executor in executors:
        executor.location_id = None

    prices = (
        await db.execute(select(LocationPrice).where(LocationPrice.location_id == int(location.id)))
    ).scalars().all()
    for price in prices:
        await db.delete(price)

    await db.delete(location)
    await db.commit()
    return RedirectResponse("/admin/locations", status_code=302)


@router.post("/executors/create")
async def admin_executor_create(
    name: str = Form(""),
    location_id: str = Form(""),
    max_active_tasks: int = Form(10),
    return_to: str = Form("/admin/locations"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    redirect_to = _safe_admin_redirect_path(return_to)

    normalized = str(name or "").strip()
    if not normalized:
        return RedirectResponse(redirect_to, status_code=302)

    safe_max_tasks = max(1, min(int(max_active_tasks or 10), 100))
    parsed_location_id = None
    value = str(location_id or "").strip()
    if value.isdigit():
        target = (await db.execute(select(Location).where(Location.id == int(value)))).scalar_one_or_none()
        if target:
            parsed_location_id = int(target.id)

    db.add(
        Executor(
            name=normalized,
            location_id=parsed_location_id,
            max_active_tasks=safe_max_tasks,
            current_active_tasks=0,
            is_active=True,
        )
    )
    await db.commit()

    return RedirectResponse(redirect_to, status_code=302)


@router.post("/executors/{executor_id}/toggle")
async def admin_executor_toggle(
    executor_id: int,
    return_to: str = Form("/admin/locations"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    redirect_to = _safe_admin_redirect_path(return_to)
    executor = (await db.execute(select(Executor).where(Executor.id == executor_id))).scalar_one_or_none()
    if executor:
        executor.is_active = not bool(executor.is_active)
        await db.commit()
    return RedirectResponse(redirect_to, status_code=302)


@router.post("/executors/{executor_id}/location")
async def admin_executor_set_location(
    executor_id: int,
    location_id: str = Form(""),
    return_to: str = Form("/admin/locations"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    redirect_to = _safe_admin_redirect_path(return_to)
    executor = (await db.execute(select(Executor).where(Executor.id == executor_id))).scalar_one_or_none()
    if not executor:
        raise HTTPException(status_code=404, detail="executor_not_found")

    value = str(location_id or "").strip()
    if not value:
        executor.location_id = None
        await db.commit()
        return RedirectResponse(redirect_to, status_code=302)

    target = (await db.execute(select(Location).where(Location.id == int(value)))).scalar_one_or_none()
    if target:
        executor.location_id = int(target.id)
        await db.commit()

    return RedirectResponse(redirect_to, status_code=302)


@router.post("/executors/{executor_id}/update")
async def admin_executor_update(
    executor_id: int,
    name: str = Form(""),
    max_active_tasks: int = Form(10),
    location_id: str = Form(""),
    return_to: str = Form("/admin/locations"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    redirect_to = _safe_admin_redirect_path(return_to)
    executor = (await db.execute(select(Executor).where(Executor.id == executor_id))).scalar_one_or_none()
    if not executor:
        raise HTTPException(status_code=404, detail="executor_not_found")

    normalized_name = str(name or "").strip()
    if not normalized_name:
        return RedirectResponse(f"{redirect_to}?error=executor_name_required", status_code=302)

    executor.name = normalized_name
    executor.max_active_tasks = max(1, min(int(max_active_tasks or 10), 100))

    location_value = str(location_id or "").strip()
    if not location_value:
        executor.location_id = None
    else:
        target = (await db.execute(select(Location).where(Location.id == int(location_value)))).scalar_one_or_none()
        executor.location_id = int(target.id) if target else None

    await db.commit()
    return RedirectResponse(redirect_to, status_code=302)


@router.post("/executors/{executor_id}/delete")
async def admin_executor_delete(
    executor_id: int,
    return_to: str = Form("/admin/locations"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    redirect_to = _safe_admin_redirect_path(return_to)
    executor = (await db.execute(select(Executor).where(Executor.id == executor_id))).scalar_one_or_none()
    if not executor:
        raise HTTPException(status_code=404, detail="executor_not_found")

    open_tasks_count = (
        await db.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.executor_id == int(executor.id),
                Task.status.in_(["open", "assigned", "in_progress"]),
            )
        )
    ).scalar() or 0
    if int(open_tasks_count) > 0:
        return RedirectResponse(f"{redirect_to}?error=executor_has_open_tasks", status_code=302)

    tasks = (
        await db.execute(select(Task).where(Task.executor_id == int(executor.id)))
    ).scalars().all()
    for task in tasks:
        task.executor_id = None

    await db.delete(executor)
    await db.commit()
    return RedirectResponse(redirect_to, status_code=302)


@router.post("/locations/{location_id}/prices")
async def admin_location_prices_update(
    request: Request,
    location_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_services_manager(user)
    location = (await db.execute(select(Location).where(Location.id == location_id))).scalar_one_or_none()
    if not location:
        raise HTTPException(status_code=404, detail="location_not_found")

    services = (
        await db.execute(select(Service).where(Service.is_active == True))
    ).scalars().all()
    service_ids = {int(s.id) for s in services}
    existing = (
        await db.execute(select(LocationPrice).where(LocationPrice.location_id == location_id))
    ).scalars().all()
    existing_map = {int(p.service_id): p for p in existing}

    form = await request.form()
    changed = False
    for key, value in form.items():
        if not str(key).startswith("price_"):
            continue
        raw_service_id = str(key)[6:]
        if not raw_service_id.isdigit():
            continue
        service_id = int(raw_service_id)
        if service_id not in service_ids:
            continue

        raw_price = str(value or "").replace(",", ".").strip()
        try:
            price = float(raw_price) if raw_price else 0.0
        except ValueError:
            continue

        row = existing_map.get(service_id)
        if row is None:
            db.add(LocationPrice(location_id=location_id, service_id=service_id, price=price))
            changed = True
        else:
            current = float(row.price or 0)
            if current != price:
                row.price = price
                changed = True

    if changed:
        await db.commit()

    return RedirectResponse("/admin/locations", status_code=302)


# ─── ROLES ───────────────────────────────────────────────
@router.get("/roles", response_class=HTMLResponse)
async def roles_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    roles = (await db.execute(select(Role).order_by(Role.id))).scalars().all()
    modules = await get_all_modules(db)
    return templates.TemplateResponse("admin/roles.html", {
        "request": request, "user": user, "roles": roles, "modules": modules,
    })


@router.post("/roles/create", response_class=HTMLResponse)
async def role_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    role = Role(name=name, description=description, permissions={})
    db.add(role)
    await db.commit()
    return RedirectResponse("/admin/roles", status_code=302)


@router.get("/roles/{role_id}", response_class=HTMLResponse)
async def role_edit_page(
    request: Request,
    role_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if not role:
        raise HTTPException(404)
    all_modules = (await db.execute(
        select(ModuleConfig).order_by(ModuleConfig.sort_order)
    )).scalars().all()
    modules = await get_all_modules(db)
    return templates.TemplateResponse("admin/role_edit.html", {
        "request": request, "user": user, "role": role,
        "all_modules": all_modules, "modules": modules,
    })


@router.post("/roles/{role_id}", response_class=HTMLResponse)
async def role_update(
    request: Request,
    role_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if not role:
        raise HTTPException(404)

    form = await request.form()
    role.name = form.get("name", role.name)
    role.description = form.get("description", role.description)

    # Parse permissions from form
    all_modules_result = (await db.execute(
        select(ModuleConfig).order_by(ModuleConfig.sort_order)
    )).scalars().all()

    permissions = {}
    for mod in all_modules_result:
        slug = mod.slug
        visible = form.get(f"perm_{slug}_visible") == "on"
        fields_visible = form.getlist(f"perm_{slug}_fields_visible")
        fields_editable = form.getlist(f"perm_{slug}_fields_editable")
        permissions[slug] = {
            "visible": visible,
            "fields_visible": fields_visible,
            "fields_editable": fields_editable,
        }

    permissions["courier"] = {
        "cabinet": form.get("perm_courier_cabinet") == "on",
    }
    permissions["logistics"] = {
        "manage": form.get("perm_logistics_manage") == "on",
    }

    role.permissions = permissions
    await db.commit()
    return RedirectResponse("/admin/roles", status_code=302)


@router.post("/roles/{role_id}/delete")
async def role_delete(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role:
        await db.delete(role)
        await db.commit()
    return RedirectResponse("/admin/roles", status_code=302)


# ─── USERS ───────────────────────────────────────────────
@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    roles = (await db.execute(select(Role).order_by(Role.id))).scalars().all()
    locations = (await db.execute(select(Location).order_by(Location.name.asc()))).scalars().all()
    modules = await get_all_modules(db)
    all_modules = (await db.execute(select(ModuleConfig).order_by(ModuleConfig.sort_order))).scalars().all()
    specialization_catalog = _get_partner_specialization_catalog(all_modules)
    user_specializations_map = {u.id: get_user_specializations(u) for u in users}
    return templates.TemplateResponse("admin/users.html", {
        "request": request, "user": user, "users": users,
        "roles": roles, "modules": modules,
        "locations": locations,
        "specialization_catalog": specialization_catalog,
        "user_specializations_map": user_specializations_map,
    })


@router.post("/users/create")
async def users_create(
    email: str = Form(""),
    password: str = Form(""),
    role_id: str = Form(""),
    location_name: str = Form(""),
    is_active: str = Form("on"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)

    normalized_email = str(email or "").strip().lower()
    if not normalized_email or "@" not in normalized_email:
        return RedirectResponse("/admin/users?create_error=invalid_email", status_code=302)

    normalized_password = str(password or "")
    if len(normalized_password) < 6:
        return RedirectResponse("/admin/users?create_error=weak_password", status_code=302)

    existing = (await db.execute(select(User).where(User.email == normalized_email))).scalar_one_or_none()
    if existing:
        return RedirectResponse("/admin/users?create_error=email_exists", status_code=302)

    parsed_role_id = int(role_id) if str(role_id or "").isdigit() else None
    if parsed_role_id is not None:
        role = (await db.execute(select(Role).where(Role.id == parsed_role_id))).scalar_one_or_none()
        if not role:
            parsed_role_id = None

    assigned_point = str(location_name or "").strip()
    if assigned_point:
        location = (await db.execute(select(Location).where(Location.name == assigned_point))).scalar_one_or_none()
        if not location:
            assigned_point = ""

    new_user = User(
        email=normalized_email,
        password_hash=hash_password(normalized_password),
        role_id=parsed_role_id,
        specialization=assigned_point,
        is_active=str(is_active or "").lower() == "on",
        is_superuser=False,
    )
    db.add(new_user)
    await db.commit()

    return RedirectResponse("/admin/users?created=1", status_code=302)


@router.post("/users/{user_id}/update")
async def user_update(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404)
    form = await request.form()
    role_id = form.get("role_id")
    target.role_id = int(role_id) if role_id else None
    if "specializations" in form:
        selected = [v.strip() for v in form.getlist("specializations") if v and v.strip()]
        target.specialization = ", ".join(selected)
    elif "specialization" in form:
        target.specialization = (form.get("specialization") or "").strip()
    target.is_active = form.get("is_active") == "on"
    target.is_superuser = form.get("is_superuser") == "on"
    await db.commit()
    return RedirectResponse("/admin/users", status_code=302)


# ─── MODULES CONFIG ─────────────────────────────────────
@router.get("/modules", response_class=HTMLResponse)
async def modules_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    all_modules = (await db.execute(
        select(ModuleConfig).order_by(ModuleConfig.sort_order)
    )).scalars().all()
    status_editor = {mod.id: _build_module_status_editor_state(mod) for mod in all_modules}
    modules = await get_all_modules(db)
    return templates.TemplateResponse("admin/modules.html", {
        "request": request, "user": user, "all_modules": all_modules,
        "modules": modules, "status_editor": status_editor,
    })


@router.post("/modules/{module_id}/toggle")
async def module_toggle(
    module_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    mod = (await db.execute(select(ModuleConfig).where(ModuleConfig.id == module_id))).scalar_one_or_none()
    if mod:
        mod.enabled = not mod.enabled
        await db.commit()
    return RedirectResponse("/admin/modules", status_code=302)


@router.post("/modules/{module_id}/status-settings")
async def module_status_settings_update(
    module_id: int,
    status_field: str = Form(""),
    status_options_text: str = Form(""),
    status_colors_text: str = Form(""),
    specialization_field: str = Form(""),
    specialization_options_text: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    mod = (await db.execute(select(ModuleConfig).where(ModuleConfig.id == module_id))).scalar_one_or_none()
    if not mod:
        raise HTTPException(404)

    status_field = (status_field or "").strip()
    fields_schema = mod.fields_schema or []
    valid_field_names = {
        f.get("name", "")
        for f in fields_schema
        if isinstance(f, dict)
    }
    if status_field not in valid_field_names:
        return RedirectResponse("/admin/modules", status_code=302)

    specialization_field = (specialization_field or "").strip()
    if specialization_field and specialization_field not in valid_field_names:
        return RedirectResponse("/admin/modules", status_code=302)

    parsed_options = []
    for line in (status_options_text or "").splitlines():
        value = line.strip()
        if not value:
            continue
        if value not in parsed_options:
            parsed_options.append(value)
    if not parsed_options:
        parsed_options = list(DEFAULT_STATUS_OPTIONS)

    parsed_colors = {}
    for line in (status_colors_text or "").splitlines():
        raw = line.strip()
        if not raw or "=" not in raw:
            continue
        name, color = raw.split("=", 1)
        status_name = name.strip()
        normalized = _normalize_hex_color(color)
        if not status_name or not normalized:
            continue
        if not any(_status_key(status_name) == _status_key(opt) for opt in parsed_options):
            continue
        canonical_name = next(
            (opt for opt in parsed_options if _status_key(opt) == _status_key(status_name)),
            status_name,
        )
        parsed_colors[canonical_name] = normalized

    parsed_specializations = []
    for line in (specialization_options_text or "").splitlines():
        value = line.strip()
        if not value:
            continue
        if value not in parsed_specializations:
            parsed_specializations.append(value)

    updated_fields = []
    for field in fields_schema:
        if not isinstance(field, dict):
            updated_fields.append(field)
            continue

        field_copy = dict(field)
        if field_copy.get("name") == status_field:
            field_copy["status_options"] = parsed_options
            field_copy["status_colors"] = parsed_colors
        else:
            field_copy.pop("status_options", None)
            field_copy.pop("status_colors", None)

        if specialization_field and field_copy.get("name") == specialization_field:
            field_copy["specialization_options"] = parsed_specializations
        else:
            field_copy.pop("specialization_options", None)
        updated_fields.append(field_copy)

    mod.fields_schema = updated_fields
    await db.commit()
    return RedirectResponse("/admin/modules", status_code=302)


# ─── SYNC ────────────────────────────────────────────────
@router.post("/sync/pull", response_class=HTMLResponse)
async def sync_pull(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    results = await sync_service.pull_all(db)
    if request.headers.get("HX-Request"):
        html = '<div class="alert alert-info">'
        for slug, r in results.items():
            html += f'<div>{slug}: {r["message"]}</div>'
        html += '</div>'
        return HTMLResponse(html)
    return RedirectResponse("/admin/", status_code=302)


@router.post("/sync/push", response_class=HTMLResponse)
async def sync_push(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    results = await sync_service.push_all(db)
    if request.headers.get("HX-Request"):
        html = '<div class="alert alert-info">'
        for slug, r in results.items():
            html += f'<div>{slug}: {r["message"]}</div>'
        html += '</div>'
        return HTMLResponse(html)
    return RedirectResponse("/admin/", status_code=302)


@router.post("/sync/pull/{slug}", response_class=HTMLResponse)
async def sync_pull_module(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    from app.schema_loader import get_module_by_slug
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)
    result = await sync_service.pull_module(db, module)
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'<div class="alert alert-info">{result["message"]}</div>')
    return RedirectResponse("/admin/", status_code=302)


@router.post("/sync/push/{slug}", response_class=HTMLResponse)
async def sync_push_module(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    _require_admin(user)
    from app.schema_loader import get_module_by_slug

    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)

    result = await sync_service.push_module(db, module)
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'<div class="alert alert-info">{result["message"]}</div>')
    return RedirectResponse("/admin/", status_code=302)

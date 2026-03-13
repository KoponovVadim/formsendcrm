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
from app.auth import get_current_user, require_superuser, hash_password
from app.schema_loader import get_all_modules
from app import sync_service

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")

DEFAULT_STATUS_OPTIONS = [
    "Новый",
    "В работе",
    "Ожидание",
    "Готов",
    "Выдан",
    "Отменен",
]


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
        color = _normalize_hex_color(str(stored_colors.get(option, "")))
        if color:
            colors_lines.append(f"{option}={color}")

    return {
        "status_field_candidates": status_field_candidates,
        "selected_status_field": detected_field,
        "status_options_text": "\n".join(options),
        "status_colors_text": "\n".join(colors_lines),
    }


def _require_admin(user):
    require_superuser(user)


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
    modules = await get_all_modules(db)
    return templates.TemplateResponse("admin/users.html", {
        "request": request, "user": user, "users": users,
        "roles": roles, "modules": modules,
    })


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

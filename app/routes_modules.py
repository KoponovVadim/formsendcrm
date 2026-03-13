"""
Dynamic module routes – list, detail, create, update, delete for any module.
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
import re

from app.database import get_db
from app.models import DynamicRecord, ModuleConfig
from app.auth import (
    get_current_user, get_user_permissions, check_module_visible,
    get_visible_fields, get_editable_fields, filter_visible_modules_for_user,
)
from app.schema_loader import get_all_modules, get_module_by_slug

router = APIRouter()
templates = Jinja2Templates(directory="templates")

DEFAULT_STATUS_OPTIONS = [
    "Новый",
    "В работе",
    "Ожидание",
    "Готов",
    "Выдан",
    "Отменен",
]

DEFAULT_STATUS_COLORS = {
    "Новый": "#d8b4fe",
    "В работе": "#fde68a",
    "Ожидание": "#d1d5db",
    "Готов": "#bbf7d0",
    "Выдан": "#bfdbfe",
    "Отменен": "#fecaca",
}

SORT_OPTIONS = {"newest", "oldest", "updated_desc", "updated_asc"}


def _status_key(value: str) -> str:
    return (value or "").strip().lower()


def _get_status_field(all_fields: list[str]) -> str | None:
    exact_candidates = {"статус", "статус заказа", "status", "order status"}
    for field in all_fields:
        if field.strip().lower() in exact_candidates:
            return field
    for field in all_fields:
        lowered = field.strip().lower()
        if "статус" in lowered and "оплат" not in lowered:
            return field
    return None


def _normalize_hex_color(value: str) -> str:
    if not value:
        return ""
    raw = value.strip()
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if not re.match(r"^#[0-9a-fA-F]{6}$", raw):
        return ""
    return raw.lower()


def _hex_to_rgba(hex_color: str, alpha: float = 0.16) -> str:
    h = _normalize_hex_color(hex_color)
    if not h:
        return ""
    r = int(h[1:3], 16)
    g = int(h[3:5], 16)
    b = int(h[5:7], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _extract_status_settings(module: ModuleConfig) -> tuple[str | None, list[str], dict[str, str]]:
    all_field_names = [f.get("name", "") for f in (module.fields_schema or []) if isinstance(f, dict)]
    detected_status_field = _get_status_field(all_field_names)
    status_field_obj = None

    for field in (module.fields_schema or []):
        if not isinstance(field, dict):
            continue
        if field.get("name") == detected_status_field:
            status_field_obj = field
            break

    stored_options = status_field_obj.get("status_options") if status_field_obj else None
    options = [str(v).strip() for v in (stored_options or []) if str(v).strip()]
    if not options:
        options = list(DEFAULT_STATUS_OPTIONS)

    status_colors = {k: v for k, v in DEFAULT_STATUS_COLORS.items() if k in options}
    stored_colors = status_field_obj.get("status_colors") if status_field_obj else None
    if isinstance(stored_colors, dict):
        for status_name, color in stored_colors.items():
            if status_name not in options:
                continue
            normalized = _normalize_hex_color(str(color))
            if normalized:
                status_colors[str(status_name)] = normalized

    return detected_status_field, options, status_colors


def _row_bg_for_status(status_value: str, status_colors: dict[str, str]) -> str:
    key = _status_key(status_value)
    for status_name, color in status_colors.items():
        if _status_key(status_name) == key:
            return _hex_to_rgba(color)
    return ""


@router.get("/modules/{slug}", response_class=HTMLResponse)
async def module_list(
    request: Request,
    slug: str,
    page: int = 1,
    search: str = "",
    sort: str = "newest",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404, "Модуль не найден")

    permissions = get_user_permissions(user)
    if not check_module_visible(permissions, slug, user.is_superuser):
        raise HTTPException(403, "Доступ запрещён")

    all_field_names = [f["name"] for f in module.fields_schema]
    status_field, status_options, status_colors = _extract_status_settings(module)
    visible_fields = get_visible_fields(permissions, slug, all_field_names, user.is_superuser)
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)

    per_page = 25
    offset = (page - 1) * per_page

    # Base query
    stmt = select(DynamicRecord).where(DynamicRecord.module_slug == slug)

    # Search filter
    if search:
        stmt = stmt.where(DynamicRecord.data.cast(str).ilike(f"%{search}%"))

    if sort not in SORT_OPTIONS:
        sort = "newest"

    # Count
    count_stmt = select(func.count()).select_from(
        stmt.subquery()
    )
    total = (await db.execute(count_stmt)).scalar() or 0

    # Paginated data
    if sort == "oldest":
        stmt = stmt.order_by(DynamicRecord.row_index.asc())
    elif sort == "updated_desc":
        stmt = stmt.order_by(DynamicRecord.updated_at.desc(), DynamicRecord.row_index.desc())
    elif sort == "updated_asc":
        stmt = stmt.order_by(DynamicRecord.updated_at.asc(), DynamicRecord.row_index.asc())
    else:
        stmt = stmt.order_by(DynamicRecord.row_index.desc())

    stmt = stmt.offset(offset).limit(per_page)
    records = (await db.execute(stmt)).scalars().all()

    total_pages = max(1, (total + per_page - 1) // per_page)
    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    ctx = {
        "request": request,
        "user": user,
        "module": module,
        "modules": modules,
        "records": records,
        "visible_fields": visible_fields,
        "editable_fields": editable_fields,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "search": search,
        "sort": sort,
        "status_field": status_field,
        "status_options": status_options,
        "status_colors": status_colors,
        "row_bg_for_status": lambda value: _row_bg_for_status(value, status_colors),
    }

    # HTMX partial
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/module_table.html", ctx)

    return templates.TemplateResponse("module_list.html", ctx)


@router.get("/modules/{slug}/record/{record_id}", response_class=HTMLResponse)
async def record_detail(
    request: Request,
    slug: str,
    record_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)

    permissions = get_user_permissions(user)
    if not check_module_visible(permissions, slug, user.is_superuser):
        raise HTTPException(403)

    result = await db.execute(
        select(DynamicRecord).where(DynamicRecord.id == record_id, DynamicRecord.module_slug == slug)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404)

    all_field_names = [f["name"] for f in module.fields_schema]
    status_field, status_options, _ = _extract_status_settings(module)
    visible_fields = get_visible_fields(permissions, slug, all_field_names, user.is_superuser)
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)

    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    return templates.TemplateResponse("record_edit_modal.html", {
        "request": request,
        "user": user,
        "module": module,
        "modules": modules,
        "record": record,
        "visible_fields": visible_fields,
        "editable_fields": editable_fields,
        "status_field": status_field,
        "status_options": status_options,
    })


@router.post("/modules/{slug}/record/{record_id}", response_class=HTMLResponse)
async def record_update(
    request: Request,
    slug: str,
    record_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)

    permissions = get_user_permissions(user)
    all_field_names = [f["name"] for f in module.fields_schema]
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)

    result = await db.execute(
        select(DynamicRecord).where(DynamicRecord.id == record_id, DynamicRecord.module_slug == slug)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404)

    form_data = await request.form()
    new_data = dict(record.data)
    for field in editable_fields:
        if field in form_data:
            new_data[field] = form_data[field]

    record.data = new_data
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    # Try push to Google Sheets
    try:
        from app.sync_service import push_single_record
        await push_single_record(db, module, record)
    except Exception:
        pass

    if request.headers.get("HX-Request"):
        return HTMLResponse('<div class="alert alert-success">Сохранено</div>', status_code=200)

    return HTMLResponse(status_code=200, headers={"HX-Redirect": f"/modules/{slug}"})


@router.post("/modules/{slug}/record/{record_id}/field")
async def record_update_single_field(
    slug: str,
    record_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)

    permissions = get_user_permissions(user)
    all_field_names = [f["name"] for f in module.fields_schema]
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)

    if field not in all_field_names:
        raise HTTPException(400, "Поле не найдено")
    if field not in editable_fields:
        raise HTTPException(403, "Поле недоступно для редактирования")

    status_field, _, status_colors = _extract_status_settings(module)
    if status_field and field != status_field:
        raise HTTPException(400, "Inline-обновление разрешено только для поля статуса")

    result = await db.execute(
        select(DynamicRecord).where(DynamicRecord.id == record_id, DynamicRecord.module_slug == slug)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404)

    new_data = dict(record.data)
    new_data[field] = value
    record.data = new_data
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    # Keep Google Sheets in sync on inline status changes as well.
    try:
        from app.sync_service import push_single_record
        await push_single_record(db, module, record)
    except Exception:
        pass

    return JSONResponse({
        "ok": True,
        "row_bg": _row_bg_for_status(value, status_colors),
    })


@router.post("/modules/{slug}/record/new", response_class=HTMLResponse)
async def record_create(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)

    permissions = get_user_permissions(user)
    all_field_names = [f["name"] for f in module.fields_schema]
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)

    if not editable_fields:
        raise HTTPException(403)

    # Get max row_index
    max_row = (await db.execute(
        select(func.max(DynamicRecord.row_index)).where(DynamicRecord.module_slug == slug)
    )).scalar() or 1
    next_row = max_row + 1

    form_data = await request.form()
    data = {}
    for field in all_field_names:
        data[field] = form_data.get(field, "")

    record = DynamicRecord(
        module_slug=slug,
        row_index=next_row,
        data=data,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.commit()

    if request.headers.get("HX-Request"):
        return HTMLResponse(status_code=200, headers={"HX-Redirect": f"/modules/{slug}"})

    return HTMLResponse(status_code=302, headers={"Location": f"/modules/{slug}"})


@router.get("/modules/{slug}/new", response_class=HTMLResponse)
async def record_new_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)

    permissions = get_user_permissions(user)
    all_field_names = [f["name"] for f in module.fields_schema]
    status_field, status_options, _ = _extract_status_settings(module)
    visible_fields = get_visible_fields(permissions, slug, all_field_names, user.is_superuser)
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)
    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    return templates.TemplateResponse("record_new_modal.html", {
        "request": request,
        "user": user,
        "module": module,
        "modules": modules,
        "visible_fields": visible_fields,
        "editable_fields": editable_fields,
        "status_field": status_field,
        "status_options": status_options,
    })


@router.delete("/modules/{slug}/record/{record_id}")
async def record_delete(
    slug: str,
    record_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user.is_superuser:
        raise HTTPException(403)

    await db.execute(
        sa_delete(DynamicRecord).where(
            DynamicRecord.id == record_id, DynamicRecord.module_slug == slug
        )
    )
    await db.commit()
    return HTMLResponse(status_code=200, headers={"HX-Redirect": f"/modules/{slug}"})

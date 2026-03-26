import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
import re

from app.config import settings
from app.database import create_tables, async_session
from app.models import User, Role, DynamicRecord, ModuleConfig
from app.auth import get_current_user, hash_password, filter_visible_modules_for_user
from app.schema_loader import seed_modules, get_all_modules
from app.routes_auth import router as auth_router
from app.routes_modules import router as modules_router
from app.routes_admin import router as admin_router
from app.routes_calculator import router as calculator_router
from app.routes_logistics import router as logistics_router
from app.database import get_db
from api.orders import router as orders_api_router
from api.catalog import router as catalog_api_router
from api.chats import router as chats_api_router
from api.mobile import router as mobile_api_router
from api.executors import router as executors_api_router
from realtime.ws_router import router as ws_router

# Import normalized models package so Base.metadata includes new tables.
import models  # noqa: F401

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _find_field_by_candidates(field_names: list[str], candidates: list[str]) -> str | None:
    lowered_map = {str(name).strip().lower(): name for name in field_names if str(name).strip()}
    for candidate in candidates:
        key = str(candidate).strip().lower()
        if key in lowered_map:
            return lowered_map[key]
    for name in field_names:
        lowered = str(name).strip().lower()
        if any(str(candidate).strip().lower() in lowered for candidate in candidates):
            return name
    return None


def _status_key(value: str) -> str:
    return str(value or "").strip().lower()


def _is_completed_status(value: str) -> bool:
    return _status_key(value) in {
        "выдан",
        "отменен",
        "завершен",
        "закрыт",
        "выдан клиенту",
        "completed",
        "closed",
        "cancelled",
        "canceled",
    }


def _parse_money(value) -> float:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0.0
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned.count(".") > 1:
        first_dot = cleaned.find(".")
        cleaned = cleaned[:first_dot + 1] + cleaned[first_dot + 1 :].replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _build_reception_role_permissions_from_modules(modules: list[ModuleConfig]) -> dict:
    permissions: dict = {}

    order_edit_keywords = (
        "статус",
        "клиент",
        "телефон",
        "устройство",
        "неисправ",
        "мастер",
        "дедлайн",
        "выдач",
        "прием",
        "примеч",
        "comment",
        "status",
        "client",
        "phone",
        "device",
    )
    client_edit_keywords = (
        "фио",
        "название",
        "телефон",
        "примеч",
        "name",
        "phone",
        "note",
    )

    for mod in modules:
        fields = [f.get("name", "") for f in (mod.fields_schema or []) if isinstance(f, dict)]
        slug = str(mod.slug or "")

        if slug == "orders":
            editable = [
                field_name
                for field_name in fields
                if any(keyword in str(field_name).lower() for keyword in order_edit_keywords)
            ]
            permissions[slug] = {
                "visible": True,
                "fields_visible": list(fields),
                "fields_editable": editable or list(fields),
            }
            continue

        if slug == "clients":
            editable = [
                field_name
                for field_name in fields
                if any(keyword in str(field_name).lower() for keyword in client_edit_keywords)
            ]
            permissions[slug] = {
                "visible": True,
                "fields_visible": list(fields),
                "fields_editable": editable,
            }
            continue

        if slug == "warranty":
            editable = [
                field_name
                for field_name in fields
                if "статус" in str(field_name).lower() or "status" in str(field_name).lower()
            ]
            permissions[slug] = {
                "visible": True,
                "fields_visible": list(fields),
                "fields_editable": editable,
            }
            continue

        permissions[slug] = {
            "visible": False,
            "fields_visible": [],
            "fields_editable": [],
        }

    permissions["courier"] = {"cabinet": False}
    permissions["logistics"] = {"manage": True}
    permissions["v2"] = {"services": {"manage": False}}

    return permissions


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Creating database tables...")
    await create_tables()

    # Seed modules from schema.json
    async with async_session() as db:
        await seed_modules(db)

        # Create default superuser if not exists
        result = await db.execute(select(User).where(User.email == settings.FIRST_SUPERUSER_EMAIL))
        existing_superuser = result.scalar_one_or_none()
        if not existing_superuser:
            default_username = str(settings.FIRST_SUPERUSER_EMAIL).split("@", 1)[0] or "admin"
            su = User(
                username=default_username,
                email=settings.FIRST_SUPERUSER_EMAIL,
                password_hash=hash_password(settings.FIRST_SUPERUSER_PASSWORD),
                is_superuser=True,
                is_active=True,
            )
            db.add(su)
            await db.commit()
            logger.info(f"Superuser created: {settings.FIRST_SUPERUSER_EMAIL}")
        elif not str(existing_superuser.username or "").strip():
            existing_superuser.username = str(settings.FIRST_SUPERUSER_EMAIL).split("@", 1)[0] or "admin"
            await db.commit()

        # Create default role if none exist
        role_count = (await db.execute(select(func.count()).select_from(Role))).scalar()
        if role_count == 0:
            role = Role(name="Менеджер", description="Базовая роль", permissions={})
            db.add(role)
            await db.commit()

        courier_role = (await db.execute(select(Role).where(Role.name == "Курьер"))).scalar_one_or_none()
        if not courier_role:
            db.add(
                Role(
                    name="Курьер",
                    description="Доступ в кабинет курьера и к перевозкам",
                    permissions={
                        "courier": {"cabinet": True},
                        "logistics": {"manage": False},
                        "v2_services_manage": False,
                    },
                )
            )
            await db.commit()

        reception_role = (await db.execute(select(Role).where(Role.name == "Пункт приема заказов"))).scalar_one_or_none()
        if not reception_role:
            all_modules = (await db.execute(select(ModuleConfig).order_by(ModuleConfig.sort_order))).scalars().all()
            db.add(
                Role(
                    name="Пункт приема заказов",
                    description="Приемка, выдача и отправка заказов",
                    permissions=_build_reception_role_permissions_from_modules(all_modules),
                )
            )
            await db.commit()

    logger.info("Application started")
    yield
    # Shutdown
    logger.info("Application stopped")


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

# Routers
app.include_router(auth_router)
app.include_router(modules_router)
app.include_router(admin_router)
app.include_router(calculator_router)
app.include_router(logistics_router)
app.include_router(orders_api_router)
app.include_router(catalog_api_router)
app.include_router(chats_api_router)
app.include_router(mobile_api_router)
app.include_router(executors_api_router)
app.include_router(ws_router)


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    # Collect stats per module
    module_stats = []
    module_by_slug = {mod.slug: mod for mod in modules}
    for mod in modules:
        count = (await db.execute(
            select(func.count()).where(DynamicRecord.module_slug == mod.slug)
        )).scalar() or 0
        module_stats.append({"module": mod, "count": count})

    orders_records = []
    orders_module = module_by_slug.get("orders")
    if orders_module:
        orders_records = (
            await db.execute(
                select(DynamicRecord)
                .where(DynamicRecord.module_slug == "orders")
                .order_by(DynamicRecord.updated_at.desc(), DynamicRecord.row_index.desc())
            )
        ).scalars().all()

    order_field_names = [f.get("name", "") for f in ((orders_module.fields_schema if orders_module else []) or []) if isinstance(f, dict)]
    order_no_field = _find_field_by_candidates(order_field_names, ["№ заказа", "номер заказа", "номер", "order_no"])
    order_client_field = _find_field_by_candidates(order_field_names, ["Клиент", "фио", "client", "name"])
    order_status_field = _find_field_by_candidates(order_field_names, ["Статус", "статус заказа", "status"])

    open_orders_count = 0
    completed_orders_count = 0
    ready_orders_count = 0
    status_breakdown: dict[str, int] = {}

    for record in orders_records:
        row_data = dict(record.data or {})
        status_value = str(row_data.get(order_status_field or "", "")).strip() if order_status_field else ""
        key = status_value or "Без статуса"
        status_breakdown[key] = status_breakdown.get(key, 0) + 1

        if _is_completed_status(status_value):
            completed_orders_count += 1
        else:
            open_orders_count += 1
            if _status_key(status_value) in {"готов", "ожидает выдачи", "едет на выдачу"}:
                ready_orders_count += 1

    recent_orders = []
    for record in orders_records[:8]:
        row_data = dict(record.data or {})
        recent_orders.append({
            "id": record.id,
            "order_no": str(row_data.get(order_no_field or "", "") or "-").strip() or "-",
            "client": str(row_data.get(order_client_field or "", "") or "").strip() or "-",
            "status": str(row_data.get(order_status_field or "", "") or "").strip() or "Без статуса",
            "updated_at": record.updated_at,
        })

    finance_total_profit = 0.0
    finance_module = module_by_slug.get("finance")
    if finance_module:
        finance_field_names = [f.get("name", "") for f in (finance_module.fields_schema or []) if isinstance(f, dict)]
        profit_field = _find_field_by_candidates(finance_field_names, ["Чистая прибыль", "прибыль", "profit"])
        if profit_field:
            finance_records = (
                await db.execute(select(DynamicRecord).where(DynamicRecord.module_slug == "finance"))
            ).scalars().all()
            for record in finance_records:
                finance_total_profit += _parse_money((record.data or {}).get(profit_field, 0))

    supplies_total_cost = 0.0
    supplies_module = module_by_slug.get("supplies")
    if supplies_module:
        supplies_field_names = [f.get("name", "") for f in (supplies_module.fields_schema or []) if isinstance(f, dict)]
        supplies_cost_field = _find_field_by_candidates(supplies_field_names, ["Стоимость", "себестоимость", "cost"])
        if supplies_cost_field:
            supplies_records = (
                await db.execute(select(DynamicRecord).where(DynamicRecord.module_slug == "supplies"))
            ).scalars().all()
            for record in supplies_records:
                supplies_total_cost += _parse_money((record.data or {}).get(supplies_cost_field, 0))

    warranty_open_count = 0
    warranty_module = module_by_slug.get("warranty")
    if warranty_module:
        warranty_field_names = [f.get("name", "") for f in (warranty_module.fields_schema or []) if isinstance(f, dict)]
        warranty_status_field = _find_field_by_candidates(warranty_field_names, ["Статус", "status"])
        warranty_records = (
            await db.execute(select(DynamicRecord).where(DynamicRecord.module_slug == "warranty"))
        ).scalars().all()
        for record in warranty_records:
            status_value = str((record.data or {}).get(warranty_status_field or "", "")).strip() if warranty_status_field else ""
            if not _is_completed_status(status_value):
                warranty_open_count += 1

    top_statuses = sorted(status_breakdown.items(), key=lambda item: item[1], reverse=True)[:6]

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "modules": modules,
        "module_stats": module_stats,
        "open_orders_count": open_orders_count,
        "completed_orders_count": completed_orders_count,
        "ready_orders_count": ready_orders_count,
        "warranty_open_count": warranty_open_count,
        "finance_total_profit": finance_total_profit,
        "supplies_total_cost": supplies_total_cost,
        "recent_orders": recent_orders,
        "top_statuses": top_statuses,
    })



import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func

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
        if not result.scalar_one_or_none():
            su = User(
                email=settings.FIRST_SUPERUSER_EMAIL,
                password_hash=hash_password(settings.FIRST_SUPERUSER_PASSWORD),
                is_superuser=True,
                is_active=True,
            )
            db.add(su)
            await db.commit()
            logger.info(f"Superuser created: {settings.FIRST_SUPERUSER_EMAIL}")

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
    for mod in modules:
        count = (await db.execute(
            select(func.count()).where(DynamicRecord.module_slug == mod.slug)
        )).scalar() or 0
        module_stats.append({"module": mod, "count": count})

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "modules": modules,
        "module_stats": module_stats,
    })



from datetime import datetime, timedelta, timezone
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import User, Role
from app.config import settings
import re

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                            headers={"Location": "/login"})
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                            headers={"Location": "/login"})
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                            headers={"Location": "/login"})
    result = await db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.id == int(user_id))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                            headers={"Location": "/login"})
    return user


async def get_current_user_optional(request: Request, db: AsyncSession = Depends(get_db)) -> Optional[User]:
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if payload is None:
        return None
    user_id = payload.get("sub")
    if user_id is None:
        return None
    result = await db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.id == int(user_id))
    )
    return result.scalar_one_or_none()


def require_superuser(user: User):
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser access required")


def get_user_permissions(user: User) -> dict:
    """Return permissions dict for user's role, or full access for superuser."""
    if user.is_superuser:
        return {}  # empty means full access
    if user.role and user.role.permissions:
        return user.role.permissions
    return {}


def check_module_visible(permissions: dict, module_slug: str, is_superuser: bool) -> bool:
    if is_superuser:
        return True
    if not permissions:
        return False
    mod_perms = permissions.get(module_slug, {})
    return mod_perms.get("visible", False)


def get_visible_fields(permissions: dict, module_slug: str, all_fields: list, is_superuser: bool) -> list:
    if is_superuser:
        return all_fields
    if not permissions:
        return []
    mod_perms = permissions.get(module_slug, {})
    visible = mod_perms.get("fields_visible", [])
    return [f for f in all_fields if f in visible]


def get_editable_fields(permissions: dict, module_slug: str, all_fields: list, is_superuser: bool) -> list:
    if is_superuser:
        return all_fields
    if not permissions:
        return []
    mod_perms = permissions.get(module_slug, {})
    editable = mod_perms.get("fields_editable", [])
    return [f for f in all_fields if f in editable]


def filter_visible_modules_for_user(modules: list, user: User) -> list:
    """Return only modules visible for current user according to role permissions."""
    if user.is_superuser:
        return modules
    permissions = get_user_permissions(user)
    return [m for m in modules if check_module_visible(permissions, m.slug, user.is_superuser)]


def get_user_specializations(user: User) -> list[str]:
    """Parse partner specialization list from user profile text."""
    raw = (user.specialization or "").strip()
    if not raw:
        return []
    items = [s.strip() for s in re.split(r"[,;\n]", raw) if s.strip()]
    unique: list[str] = []
    for item in items:
        if item.lower() not in [v.lower() for v in unique]:
            unique.append(item)
    return unique


def can_manage_services(user: User) -> bool:
    """
    Access policy for V2 services catalog management.

    Defaults to allow for authenticated users unless role permissions explicitly deny it.
    Supported permission keys:
    - permissions["v2"]["services"]["manage"] -> bool
    - permissions["v2_services_manage"] -> bool (legacy shortcut)
    """
    if user.is_superuser:
        return True

    permissions = get_user_permissions(user)
    if not permissions:
        return True

    if "v2_services_manage" in permissions:
        return bool(permissions.get("v2_services_manage"))

    v2 = permissions.get("v2")
    if isinstance(v2, dict):
        services = v2.get("services")
        if isinstance(services, dict) and "manage" in services:
            return bool(services.get("manage"))

    return True


def get_services_access_scope(user: User) -> dict:
    """
    Return additive V2 services access scope for UI/API filtering.

    Supported permission keys:
    - permissions["v2"]["services"]["partner_mode"] -> bool
    - permissions["v2"]["services"]["location"] -> str
    - permissions["v2"]["services"]["locations"] -> list[str]
    - permissions["v2"]["services"]["hide_own_price"] -> bool
    - permissions["v2_partner_mode"] -> bool
    - permissions["v2_partner_location"] -> str
    - permissions["v2_partner_locations"] -> list[str]
    - permissions["v2_hide_own_price"] -> bool
    """
    if user.is_superuser:
        return {
            "partner_mode": False,
            "allowed_points": [],
            "hide_own_price": False,
        }

    permissions = get_user_permissions(user)
    v2_services = {}
    if isinstance(permissions.get("v2"), dict) and isinstance(permissions.get("v2").get("services"), dict):
        v2_services = permissions.get("v2").get("services")

    partner_mode = bool(v2_services.get("partner_mode") or permissions.get("v2_partner_mode"))

    allowed_points: list[str] = []

    locations_list = v2_services.get("locations")
    if isinstance(locations_list, list):
        allowed_points.extend([str(value).strip() for value in locations_list if str(value).strip()])

    legacy_locations = permissions.get("v2_partner_locations")
    if isinstance(legacy_locations, list):
        allowed_points.extend([str(value).strip() for value in legacy_locations if str(value).strip()])

    location_single = str(v2_services.get("location") or permissions.get("v2_partner_location") or "").strip()
    if location_single:
        allowed_points.append(location_single)

    if partner_mode and not allowed_points:
        # Fallback for existing partner accounts that keep point names in specialization.
        allowed_points = get_user_specializations(user)

    dedup = []
    seen = set()
    for point_name in allowed_points:
        key = point_name.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(point_name)

    hide_own_price = bool(v2_services.get("hide_own_price") or permissions.get("v2_hide_own_price"))
    if partner_mode:
        hide_own_price = True if "hide_own_price" not in v2_services and "v2_hide_own_price" not in permissions else hide_own_price

    return {
        "partner_mode": partner_mode,
        "allowed_points": dedup,
        "hide_own_price": hide_own_price,
    }

"""
Routes for authentication pages and API.
"""
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User, Role
from app.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user_optional
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_current_user_optional)):
    if user:
        return RedirectResponse("/", status_code=302)
    registration_disabled = str(request.query_params.get("registration", "")).strip().lower() == "disabled"
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "info": "Регистрация отключена. Логин и пароль выдает администратор." if registration_disabled else None,
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    login: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    identifier = str(login or email or "").strip().lower()
    if not identifier:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Введите логин или email", "info": None}
        )

    result = await db.execute(
        select(User).where(
            or_(
                func.lower(func.coalesce(User.username, "")) == identifier,
                func.lower(func.coalesce(User.email, "")) == identifier,
            )
        )
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный логин/email или пароль", "info": None}
        )
    if not user.is_active:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Аккаунт деактивирован", "info": None}
        )
    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, user=Depends(get_current_user_optional)):
    if user:
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/login?registration=disabled", status_code=302)


@router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    return RedirectResponse("/login?registration=disabled", status_code=302)

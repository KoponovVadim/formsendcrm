"""
Routes for authentication pages and API.
"""
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
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
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный email или пароль"}
        )
    if not user.is_active:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Аккаунт деактивирован"}
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
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if password != password2:
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "Пароли не совпадают"}
        )
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "Email уже зарегистрирован"}
        )
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    await db.commit()
    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response

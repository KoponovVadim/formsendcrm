from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from models.crm import Client
from repositories.crm_repository import CRMRepository
from services.order_service import OrderService

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


@router.post("")
async def create_order(payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    service = OrderService(db)
    try:
        order = await service.create_order(payload, actor_user_id=user.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": order.id, "order_no": order.order_no, "status": order.status}


@router.get("/clients/search")
async def search_clients(
    q: str = "",
    phone: str = "",
    limit: int = 8,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    text = str(phone or q or "").strip()
    if len(text) < 2:
        return []

    safe_limit = max(1, min(int(limit or 8), 25))
    like = f"%{text}%"

    clients = (
        await db.execute(
            select(Client)
            .where(or_(Client.phone.ilike(like), Client.name.ilike(like)))
            .order_by(Client.updated_at.desc())
            .limit(safe_limit)
        )
    ).scalars().all()

    return [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "email": c.email,
        }
        for c in clients
    ]


@router.get("/{order_id}")
async def get_order(order_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    repo = CRMRepository(db)
    order = await repo.get_order(order_id)
    if not order:
        raise HTTPException(404, "Order not found")

    return {
        "id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "priority": order.priority,
        "total_amount": float(order.total_amount or 0),
        "client": {
            "id": order.client.id,
            "name": order.client.name,
            "phone": order.client.phone,
            "email": order.client.email,
        },
        "items": [
            {
                "id": item.id,
                "service_id": item.service_id,
                "title": item.title,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price or 0),
                "line_total": float(item.line_total or 0),
                "calculator_payload": item.calculator_payload,
                "calculator_breakdown": item.calculator_breakdown,
            }
            for item in order.items
        ],
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "executor_id": task.executor_id,
                "assignment_score": task.assignment_score,
            }
            for task in order.tasks
        ],
    }

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from integrations.connectors.dummy import DummyConnector
from integrations.connectors.registry import registry
from models.omnichannel import Conversation, Message
from services.ai_service import ai_lead_service
from services.chat_service import ChatService

router = APIRouter(prefix="/api/v1/chats", tags=["chats"])
registry.register("custom", DummyConnector())
registry.register("telegram", DummyConnector())
registry.register("vk", DummyConnector())
registry.register("avito", DummyConnector())
registry.register("whatsapp", DummyConnector())


@router.get("/inbox")
async def inbox(limit: int = 50, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    stmt = select(Conversation).order_by(Conversation.last_message_at.desc().nullslast()).limit(limit)
    conversations = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": c.id,
            "channel_id": c.channel_id,
            "status": c.status,
            "external_thread_id": c.external_thread_id,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            "assigned_executor_id": c.assigned_executor_id,
        }
        for c in conversations
    ]


@router.post("/incoming")
async def incoming(payload: dict, db: AsyncSession = Depends(get_db)):
    service = ChatService(db)
    try:
        msg = await service.receive_incoming(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"message_id": msg.id, "conversation_id": msg.conversation_id, "ai": msg.ai_classification}


@router.post("/{conversation_id}/send")
async def send_message(conversation_id: int, payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text is required")
    service = ChatService(db)
    try:
        msg = await service.send_message(conversation_id, text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"message_id": msg.id, "direction": msg.direction}


@router.get("/{conversation_id}/messages")
async def conversation_messages(conversation_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
    messages = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": m.id,
            "direction": m.direction,
            "sender_name": m.sender_name,
            "text": m.text,
            "ai_classification": m.ai_classification,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]


@router.get("/{conversation_id}/suggest-order")
async def suggest_order(conversation_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.direction == "in")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    msg = (await db.execute(stmt)).scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "Incoming message not found")

    prediction = ai_lead_service.classify_message(msg.text)
    return {
        "conversation_id": conversation_id,
        "message_id": msg.id,
        "prediction": prediction,
        "proposal": {
            "create_order": prediction.get("intent") == "create_order",
            "source_channel": "chat",
            "priority": 1,
        },
    }

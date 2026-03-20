from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.events import event_bus

router = APIRouter()


@router.websocket("/ws/updates")
async def ws_updates(websocket: WebSocket):
    await websocket.accept()
    event_bus.subscribe("orders", websocket)
    event_bus.subscribe("chats", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_bus.unsubscribe("orders", websocket)
        event_bus.unsubscribe("chats", websocket)


@router.websocket("/ws/chats/{conversation_id}")
async def ws_chat_conversation(websocket: WebSocket, conversation_id: int):
    await websocket.accept()
    topic = f"chat:{conversation_id}"
    event_bus.subscribe(topic, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_bus.unsubscribe(topic, websocket)

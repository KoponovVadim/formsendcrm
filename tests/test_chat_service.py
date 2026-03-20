from sqlalchemy import select

from integrations.connectors.dummy import DummyConnector
from integrations.connectors.registry import registry
from models.omnichannel import Channel, Conversation, Message
from services.chat_service import ChatService


async def test_chat_service_receive_and_send_message_flow(db_session):
    registry.register("custom", DummyConnector())
    service = ChatService(db_session)

    incoming_message = await service.receive_incoming(
        {
            "channel_type": "custom",
            "external_thread_id": "thread-1",
            "sender_name": "Alice",
            "text": "Хочу оформить заказ, какая цена?",
            "attachments": [
                {
                    "file_name": "photo.jpg",
                    "file_url": "https://example.org/photo.jpg",
                    "mime_type": "image/jpeg",
                    "size_bytes": 100,
                }
            ],
        }
    )

    assert incoming_message.direction == "in"
    assert incoming_message.ai_classification.get("intent") == "create_order"

    channel = (await db_session.execute(select(Channel).where(Channel.type == "custom"))).scalar_one()
    conversation = (
        await db_session.execute(
            select(Conversation).where(
                Conversation.channel_id == channel.id,
                Conversation.external_thread_id == "thread-1",
            )
        )
    ).scalar_one()

    outgoing_message = await service.send_message(conversation.id, "Принято, создаю заказ")
    assert outgoing_message.direction == "out"

    messages = (
        await db_session.execute(select(Message).where(Message.conversation_id == conversation.id))
    ).scalars().all()
    assert len(messages) == 2


async def test_chat_service_incoming_is_idempotent_for_same_payload(db_session):
    registry.register("custom", DummyConnector())
    service = ChatService(db_session)

    payload = {
        "channel_type": "custom",
        "external_thread_id": "thread-dup-1",
        "sender_name": "Alice",
        "text": "Повтор вебхука",
    }

    first = await service.receive_incoming(payload)
    second = await service.receive_incoming(payload)

    assert first.id == second.id

    all_messages = (
        await db_session.execute(select(Message).where(Message.conversation_id == first.conversation_id))
    ).scalars().all()
    assert len(all_messages) == 1

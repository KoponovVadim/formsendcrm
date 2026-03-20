from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import event_bus
from integrations.connectors.registry import registry
from models.crm import Client
from models.omnichannel import Attachment, Channel, Conversation, Message
from services.ai_service import ai_lead_service
from services.audit_service import write_audit


class ChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create_channel(self, channel_type: str) -> Channel:
        channel = (
            await self.db.execute(select(Channel).where(Channel.type == channel_type))
        ).scalar_one_or_none()
        if channel:
            return channel

        channel = Channel(type=channel_type, name=channel_type.capitalize(), status="active", settings={})
        self.db.add(channel)
        await self.db.flush()
        return channel

    async def receive_incoming(self, payload: dict) -> Message:
        connector = registry.get(str(payload.get("channel_type", "custom")))
        incoming = await connector.parse_incoming(payload)
        dedup_key = self._dedup_key(incoming.channel_type, incoming.external_thread_id, incoming.sender_name, incoming.text, incoming.attachments)

        channel = await self.get_or_create_channel(incoming.channel_type)

        conversation = (
            await self.db.execute(
                select(Conversation).where(
                    Conversation.channel_id == channel.id,
                    Conversation.external_thread_id == incoming.external_thread_id,
                )
            )
        ).scalar_one_or_none()

        if not conversation:
            client = Client(name=incoming.sender_name, source=incoming.channel_type)
            self.db.add(client)
            await self.db.flush()
            conversation = Conversation(
                channel_id=channel.id,
                client_id=client.id,
                external_thread_id=incoming.external_thread_id,
                status="open",
            )
            self.db.add(conversation)
            await self.db.flush()

        existing_message = (
            await self.db.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "in",
                    Message.dedup_key == dedup_key,
                )
            )
        ).scalar_one_or_none()
        if existing_message:
            return existing_message

        ai_meta = ai_lead_service.classify_message(incoming.text)

        msg = Message(
            conversation_id=conversation.id,
            direction="in",
            sender_name=incoming.sender_name,
            text=incoming.text,
            dedup_key=dedup_key,
            payload={"raw": payload, "dedup_key": dedup_key},
            ai_classification=ai_meta,
        )
        self.db.add(msg)
        await self.db.flush()

        for a in incoming.attachments:
            self.db.add(
                Attachment(
                    message_id=msg.id,
                    file_name=str(a.get("file_name", "")),
                    file_url=str(a.get("file_url", "")),
                    mime_type=str(a.get("mime_type", "")),
                    size_bytes=int(a.get("size_bytes", 0) or 0),
                )
            )

        conversation.last_message_at = datetime.now(timezone.utc)

        await write_audit(
            self.db,
            entity_type="conversation",
            entity_id=conversation.id,
            action="incoming_message",
            actor_user_id=None,
            before_data={},
            after_data={"message_id": msg.id, "intent": ai_meta.get("intent")},
        )
        await self.db.commit()

        payload = {"type": "message_in", "conversation_id": conversation.id, "message_id": msg.id}
        await event_bus.publish("chats", payload)
        await event_bus.publish(f"chat:{conversation.id}", payload)
        return msg

    @staticmethod
    def _dedup_key(channel_type: str, external_thread_id: str, sender_name: str, text: str, attachments: list[dict]) -> str:
        packed = {
            "channel_type": str(channel_type or "").strip(),
            "external_thread_id": str(external_thread_id or "").strip(),
            "sender_name": str(sender_name or "").strip(),
            "text": str(text or "").strip(),
            "attachments": attachments or [],
        }
        raw = json.dumps(packed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def send_message(self, conversation_id: int, text: str) -> Message:
        conversation = (
            await self.db.execute(select(Conversation).where(Conversation.id == conversation_id))
        ).scalar_one_or_none()
        if not conversation:
            raise ValueError("Conversation not found")

        channel = (
            await self.db.execute(select(Channel).where(Channel.id == conversation.channel_id))
        ).scalar_one_or_none()
        if not channel:
            raise ValueError("Channel not found")

        connector = registry.get(channel.type)
        await connector.send_message(conversation.external_thread_id, text)

        msg = Message(
            conversation_id=conversation.id,
            direction="out",
            sender_name="crm",
            text=text,
            payload={},
            ai_classification={},
        )
        self.db.add(msg)
        conversation.last_message_at = datetime.now(timezone.utc)
        await self.db.commit()

        payload = {"type": "message_out", "conversation_id": conversation.id, "message_id": msg.id}
        await event_bus.publish("chats", payload)
        await event_bus.publish(f"chat:{conversation.id}", payload)
        return msg

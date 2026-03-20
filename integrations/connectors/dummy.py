from integrations.connectors.base import ChannelConnector, IncomingMessage


class DummyConnector(ChannelConnector):
    """Fallback connector for local development and unsupported channels."""

    async def send_message(self, destination: str, text: str) -> dict:
        return {"ok": True, "destination": destination, "text": text}

    async def parse_incoming(self, payload: dict) -> IncomingMessage:
        return IncomingMessage(
            channel_type=str(payload.get("channel_type", "custom")),
            external_thread_id=str(payload.get("external_thread_id", "unknown")),
            sender_name=str(payload.get("sender_name", "unknown")),
            text=str(payload.get("text", "")),
            attachments=payload.get("attachments", []) or [],
        )

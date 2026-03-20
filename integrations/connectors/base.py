from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class IncomingMessage:
    channel_type: str
    external_thread_id: str
    sender_name: str
    text: str
    attachments: list[dict]


class ChannelConnector(ABC):
    """Transport adapter interface for omnichannel integrations."""

    @abstractmethod
    async def send_message(self, destination: str, text: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def parse_incoming(self, payload: dict) -> IncomingMessage:
        raise NotImplementedError

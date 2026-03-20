from integrations.connectors.base import ChannelConnector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, ChannelConnector] = {}

    def register(self, channel_type: str, connector: ChannelConnector) -> None:
        self._connectors[channel_type] = connector

    def get(self, channel_type: str) -> ChannelConnector:
        connector = self._connectors.get(channel_type)
        if not connector:
            raise ValueError(f"Connector not configured for channel '{channel_type}'")
        return connector


registry = ConnectorRegistry()

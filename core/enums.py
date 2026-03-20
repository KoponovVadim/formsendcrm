from enum import Enum


class ServiceCategory(str, Enum):
    REPAIR = "repair"
    WEB = "web"
    PRINT = "print"
    MARKETING = "marketing"


class ClientStage(str, Enum):
    NEW = "new"
    QUALIFIED = "qualified"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"


class OrderStatus(str, Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    DONE = "done"
    CANCELED = "canceled"


class TaskStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELED = "canceled"


class ConversationStatus(str, Enum):
    OPEN = "open"
    PENDING = "pending"
    CLOSED = "closed"


class ChannelType(str, Enum):
    TELEGRAM = "telegram"
    VK = "vk"
    AVITO = "avito"
    WHATSAPP = "whatsapp"
    CUSTOM = "custom"

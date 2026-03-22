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
    ACCEPTED = "Принят"
    AWAITING_COURIER = "Ожидает курьера"
    IN_TRANSIT_TO_MAIN = "В пути в ЦО"
    IN_REPAIR = "В ремонте"
    READY_FOR_DISPATCH = "Готов к отправке"
    IN_TRANSIT_TO_POINT = "В пути в точку выдачи"
    READY_FOR_PICKUP = "Готов к выдаче"
    ISSUED = "Выдан"
    CANCELED = "Отменен"


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

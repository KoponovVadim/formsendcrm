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
    NEW = "Новый"
    AWAITING_DELIVERY_TO_REPAIR = "Ожидает доставки в ремонт"
    IN_TRANSIT_TO_REPAIR = "Едет в точку ремонта"
    ARRIVED_REPAIR_POINT = "Приехал в точку ремонта"
    IN_REPAIR = "В ремонте"
    READY = "Готов"
    IN_TRANSIT_TO_PICKUP = "Едет на выдачу"
    AWAITING_PICKUP = "Ожидает выдачи"
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

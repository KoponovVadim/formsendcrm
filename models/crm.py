from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    before_data: Mapped[dict] = mapped_column(JSON, default=dict)
    after_data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str] = mapped_column(String(50), default="", index=True)
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    stage: Mapped[str] = mapped_column(String(30), default="new", index=True)
    source: Mapped[str] = mapped_column(String(100), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    orders: Mapped[list["Order"]] = relationship(back_populates="client")


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, index=True)

    executors: Mapped[list["Executor"]] = relationship(back_populates="department")
    tasks: Mapped[list["Task"]] = relationship(back_populates="department")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    base_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    calculator_schema: Mapped[dict] = mapped_column(JSON, default=dict)

    order_items: Mapped[list["OrderItem"]] = relationship(back_populates="service")
    location_prices: Mapped[list["LocationPrice"]] = relationship(back_populates="service", cascade="all, delete-orphan")


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    prices: Mapped[list["LocationPrice"]] = relationship(back_populates="location", cascade="all, delete-orphan")
    executors: Mapped[list["Executor"]] = relationship(back_populates="location")
    orders: Mapped[list["Order"]] = relationship(back_populates="location")


class LocationPrice(Base):
    __tablename__ = "location_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    location: Mapped[Location] = relationship(back_populates="prices")
    service: Mapped[Service] = relationship(back_populates="location_prices")


class Executor(Base):
    __tablename__ = "executors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    max_active_tasks: Mapped[int] = mapped_column(Integer, default=10)
    current_active_tasks: Mapped[int] = mapped_column(Integer, default=0, index=True)

    department: Mapped[Department | None] = relationship(back_populates="executors")
    location: Mapped[Location | None] = relationship(back_populates="executors")
    skills: Mapped[list["ExecutorSkill"]] = relationship(back_populates="executor", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="executor")


class ExecutorSkill(Base):
    __tablename__ = "executor_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    executor_id: Mapped[int] = mapped_column(ForeignKey("executors.id"), index=True)
    service_category: Mapped[str] = mapped_column(String(30), index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)

    executor: Mapped[Executor] = relationship(back_populates="skills")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    source_channel: Mapped[str] = mapped_column(String(60), default="manual", index=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), index=True)

    client: Mapped[Client] = relationship(back_populates="orders")
    location: Mapped[Location | None] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    price_client: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    price_partner: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    line_total: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    calculator_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    calculator_breakdown: Mapped[list[dict]] = mapped_column(JSON, default=list)

    order: Mapped[Order] = relationship(back_populates="items")
    service: Mapped[Service | None] = relationship(back_populates="order_items")
    tasks: Mapped[list["Task"]] = relationship(back_populates="order_item")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    order_item_id: Mapped[int | None] = mapped_column(ForeignKey("order_items.id"), nullable=True, index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    executor_id: Mapped[int | None] = mapped_column(ForeignKey("executors.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    assignment_score: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), index=True)

    order: Mapped[Order] = relationship(back_populates="tasks")
    order_item: Mapped[OrderItem | None] = relationship(back_populates="tasks")
    department: Mapped[Department | None] = relationship(back_populates="tasks")
    executor: Mapped[Executor | None] = relationship(back_populates="tasks")


Index("ix_tasks_executor_status", Task.executor_id, Task.status)
Index("ix_orders_client_status", Order.client_id, Order.status)
Index("ix_order_items_order_status", OrderItem.order_id, OrderItem.status)
Index("ix_executors_active_load", Executor.is_active, Executor.current_active_tasks)

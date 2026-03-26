from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, JSON, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(String(255), default="")
    permissions = Column(JSON, default=dict)
    # permissions schema:
    # {
    #   "module_slug": {
    #     "visible": true/false,
    #     "fields_visible": ["field1", "field2"],
    #     "fields_editable": ["field1"]
    #   }
    # }
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=True, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)
    point_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    specialization = Column(String(255), default="")
    is_superuser = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    role = relationship("Role", back_populates="users")


class ModuleConfig(Base):
    __tablename__ = "module_configs"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(100), unique=True, nullable=False)
    sheet_name = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    icon = Column(String(50), default="bi-table")
    enabled = Column(Boolean, default=True)
    fields_schema = Column(JSON, default=list)
    sort_order = Column(Integer, default=0)


class DynamicRecord(Base):
    __tablename__ = "dynamic_records"

    id = Column(Integer, primary_key=True, index=True)
    module_slug = Column(String(100), nullable=False, index=True)
    row_index = Column(Integer, nullable=False)
    data = Column(JSON, default=dict)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # unique per module+row
    )


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    module_slug = Column(String(100), nullable=False)
    direction = Column(String(10), nullable=False)  # 'pull' or 'push'
    status = Column(String(20), nullable=False)  # 'success', 'error'
    records_affected = Column(Integer, default=0)
    message = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

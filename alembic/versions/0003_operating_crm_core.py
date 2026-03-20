"""Operating CRM core and omnichannel

Revision ID: 0003_operating_crm_core
Revises: 0002_add_user_specialization
Create Date: 2026-03-20 10:00:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003_operating_crm_core"
down_revision = "0002_add_user_specialization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("before_data", sa.JSON(), nullable=True),
        sa.Column("after_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
    )
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("stage", sa.String(length=30), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_clients_name", "clients", ["name"])
    op.create_index("ix_clients_phone", "clients", ["phone"])
    op.create_index("ix_clients_email", "clients", ["email"])
    op.create_index("ix_clients_stage", "clients", ["stage"])

    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.UniqueConstraint("name", name="uq_departments_name"),
        sa.UniqueConstraint("code", name="uq_departments_code"),
    )
    op.create_index("ix_departments_name", "departments", ["name"])
    op.create_index("ix_departments_code", "departments", ["code"])

    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(length=150), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("base_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("calculator_schema", sa.JSON(), nullable=True),
        sa.UniqueConstraint("slug", name="uq_services_slug"),
    )
    op.create_index("ix_services_slug", "services", ["slug"])
    op.create_index("ix_services_name", "services", ["name"])
    op.create_index("ix_services_category", "services", ["category"])
    op.create_index("ix_services_is_active", "services", ["is_active"])

    op.create_table(
        "executors",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("max_active_tasks", sa.Integer(), nullable=True),
        sa.Column("current_active_tasks", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
    )
    op.create_index("ix_executors_user_id", "executors", ["user_id"])
    op.create_index("ix_executors_department_id", "executors", ["department_id"])
    op.create_index("ix_executors_name", "executors", ["name"])
    op.create_index("ix_executors_is_active", "executors", ["is_active"])
    op.create_index("ix_executors_current_active_tasks", "executors", ["current_active_tasks"])
    op.create_index("ix_executors_active_load", "executors", ["is_active", "current_active_tasks"])

    op.create_table(
        "executor_skills",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("executor_id", sa.Integer(), nullable=False),
        sa.Column("service_category", sa.String(length=30), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["executor_id"], ["executors.id"]),
    )
    op.create_index("ix_executor_skills_executor_id", "executor_skills", ["executor_id"])
    op.create_index("ix_executor_skills_service_category", "executor_skills", ["service_category"])

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("order_no", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("source_channel", sa.String(length=60), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.UniqueConstraint("order_no", name="uq_orders_order_no"),
    )
    op.create_index("ix_orders_order_no", "orders", ["order_no"])
    op.create_index("ix_orders_client_id", "orders", ["client_id"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_priority", "orders", ["priority"])
    op.create_index("ix_orders_source_channel", "orders", ["source_channel"])
    op.create_index("ix_orders_created_by_user_id", "orders", ["created_by_user_id"])
    op.create_index("ix_orders_created_at", "orders", ["created_at"])
    op.create_index("ix_orders_updated_at", "orders", ["updated_at"])
    op.create_index("ix_orders_client_status", "orders", ["client_id", "status"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("calculator_payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_service_id", "order_items", ["service_id"])
    op.create_index("ix_order_items_status", "order_items", ["status"])
    op.create_index("ix_order_items_order_status", "order_items", ["order_id", "status"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("executor_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("assignment_score", sa.Integer(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.ForeignKeyConstraint(["executor_id"], ["executors.id"]),
    )
    op.create_index("ix_tasks_order_id", "tasks", ["order_id"])
    op.create_index("ix_tasks_order_item_id", "tasks", ["order_item_id"])
    op.create_index("ix_tasks_department_id", "tasks", ["department_id"])
    op.create_index("ix_tasks_executor_id", "tasks", ["executor_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_priority", "tasks", ["priority"])
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_index("ix_tasks_updated_at", "tasks", ["updated_at"])
    op.create_index("ix_tasks_executor_status", "tasks", ["executor_id", "status"])

    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=True),
    )
    op.create_index("ix_channels_type", "channels", ["type"])
    op.create_index("ix_channels_name", "channels", ["name"])
    op.create_index("ix_channels_status", "channels", ["status"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("external_thread_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("assigned_executor_id", sa.Integer(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["assigned_executor_id"], ["executors.id"]),
    )
    op.create_index("ix_conversations_channel_id", "conversations", ["channel_id"])
    op.create_index("ix_conversations_client_id", "conversations", ["client_id"])
    op.create_index("ix_conversations_external_thread_id", "conversations", ["external_thread_id"])
    op.create_index("ix_conversations_status", "conversations", ["status"])
    op.create_index("ix_conversations_assigned_executor_id", "conversations", ["assigned_executor_id"])
    op.create_index("ix_conversations_last_message_at", "conversations", ["last_message_at"])
    op.create_index(
        "ix_conversations_channel_thread",
        "conversations",
        ["channel_id", "external_thread_id"],
        unique=True,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("ai_classification", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_direction", "messages", ["direction"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])

    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_url", sa.String(length=500), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
    )
    op.create_index("ix_attachments_message_id", "attachments", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_message_id", table_name="attachments")
    op.drop_table("attachments")

    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_index("ix_messages_created_at", table_name="messages")
    op.drop_index("ix_messages_direction", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_conversations_channel_thread", table_name="conversations")
    op.drop_index("ix_conversations_last_message_at", table_name="conversations")
    op.drop_index("ix_conversations_assigned_executor_id", table_name="conversations")
    op.drop_index("ix_conversations_status", table_name="conversations")
    op.drop_index("ix_conversations_external_thread_id", table_name="conversations")
    op.drop_index("ix_conversations_client_id", table_name="conversations")
    op.drop_index("ix_conversations_channel_id", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("ix_channels_status", table_name="channels")
    op.drop_index("ix_channels_name", table_name="channels")
    op.drop_index("ix_channels_type", table_name="channels")
    op.drop_table("channels")

    op.drop_index("ix_tasks_executor_status", table_name="tasks")
    op.drop_index("ix_tasks_updated_at", table_name="tasks")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_due_at", table_name="tasks")
    op.drop_index("ix_tasks_priority", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_executor_id", table_name="tasks")
    op.drop_index("ix_tasks_department_id", table_name="tasks")
    op.drop_index("ix_tasks_order_item_id", table_name="tasks")
    op.drop_index("ix_tasks_order_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_order_items_order_status", table_name="order_items")
    op.drop_index("ix_order_items_status", table_name="order_items")
    op.drop_index("ix_order_items_service_id", table_name="order_items")
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")

    op.drop_index("ix_orders_client_status", table_name="orders")
    op.drop_index("ix_orders_updated_at", table_name="orders")
    op.drop_index("ix_orders_created_at", table_name="orders")
    op.drop_index("ix_orders_created_by_user_id", table_name="orders")
    op.drop_index("ix_orders_source_channel", table_name="orders")
    op.drop_index("ix_orders_priority", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_client_id", table_name="orders")
    op.drop_index("ix_orders_order_no", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_executor_skills_service_category", table_name="executor_skills")
    op.drop_index("ix_executor_skills_executor_id", table_name="executor_skills")
    op.drop_table("executor_skills")

    op.drop_index("ix_executors_active_load", table_name="executors")
    op.drop_index("ix_executors_current_active_tasks", table_name="executors")
    op.drop_index("ix_executors_is_active", table_name="executors")
    op.drop_index("ix_executors_name", table_name="executors")
    op.drop_index("ix_executors_department_id", table_name="executors")
    op.drop_index("ix_executors_user_id", table_name="executors")
    op.drop_table("executors")

    op.drop_index("ix_services_is_active", table_name="services")
    op.drop_index("ix_services_category", table_name="services")
    op.drop_index("ix_services_name", table_name="services")
    op.drop_index("ix_services_slug", table_name="services")
    op.drop_table("services")

    op.drop_index("ix_departments_code", table_name="departments")
    op.drop_index("ix_departments_name", table_name="departments")
    op.drop_table("departments")

    op.drop_index("ix_clients_stage", table_name="clients")
    op.drop_index("ix_clients_email", table_name="clients")
    op.drop_index("ix_clients_phone", table_name="clients")
    op.drop_index("ix_clients_name", table_name="clients")
    op.drop_table("clients")

    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type", table_name="audit_logs")
    op.drop_table("audit_logs")

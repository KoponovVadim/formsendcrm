from sqlalchemy.ext.asyncio import AsyncSession

from models.crm import AuditLog


async def write_audit(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    actor_user_id: int | None,
    before_data: dict | None = None,
    after_data: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_user_id=actor_user_id,
            before_data=before_data or {},
            after_data=after_data or {},
        )
    )

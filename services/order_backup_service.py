from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DynamicRecord, ModuleConfig


def _find_field_name(module: ModuleConfig, candidates: list[str]) -> str | None:
    raw_schema = module.fields_schema
    if not isinstance(raw_schema, list):
        return None
    schema = cast(list[dict[str, Any]], raw_schema)
    field_names = [str(field.get("name", "")).strip() for field in schema]
    lowered = {name.lower(): name for name in field_names if name}

    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        if key in lowered:
            return lowered[key]

    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        for field_name in field_names:
            if key in str(field_name).lower():
                return field_name

    return None


async def _get_module_by_slug(db: AsyncSession, slug: str) -> ModuleConfig | None:
    return (await db.execute(select(ModuleConfig).where(ModuleConfig.slug == slug))).scalar_one_or_none()


async def _get_or_create_record_by_order_no(
    db: AsyncSession,
    module_slug: str,
    order_no_field: str,
    order_no: str,
) -> DynamicRecord:
    records = (
        await db.execute(
            select(DynamicRecord)
            .where(DynamicRecord.module_slug == module_slug)
            .order_by(DynamicRecord.row_index.asc())
        )
    ).scalars().all()

    for record in records:
        if str((record.data or {}).get(order_no_field, "")).strip() == str(order_no).strip():
            return record

    existing_rows = (await db.execute(select(DynamicRecord).where(DynamicRecord.module_slug == module_slug))).scalars().all()
    next_row = max([int(getattr(row, "row_index", 1) or 1) for row in existing_rows], default=1) + 1

    record = DynamicRecord(
        module_slug=module_slug,
        row_index=next_row,
        data={order_no_field: str(order_no)},
        updated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.flush()
    return record


async def mirror_order_to_dynamic_modules(
    db: AsyncSession,
    *,
    order_no: str,
    accepted_at: datetime | None,
    client_name: str,
    client_phone: str,
    device_name: str,
    issue_text: str,
    master_name: str,
    status: str,
    total_amount: Decimal | float | int = 0,
    warranty_until: str = "",
) -> None:
    orders_module = await _get_module_by_slug(db, "orders")
    clients_module = await _get_module_by_slug(db, "clients")
    finance_module = await _get_module_by_slug(db, "finance")

    records_to_push: list[tuple[ModuleConfig, DynamicRecord]] = []

    if orders_module:
        order_no_field = _find_field_name(orders_module, ["№ заказа", "номер", "заказ"])
        if order_no_field:
            record = await _get_or_create_record_by_order_no(db, "orders", order_no_field, order_no)
            payload = dict(cast(dict[str, Any], record.data or {}))

            accepted_field = _find_field_name(orders_module, ["Дата приёма", "дата приема"])
            client_field = _find_field_name(orders_module, ["Клиент"])
            device_field = _find_field_name(orders_module, ["Устройство"])
            issue_field = _find_field_name(orders_module, ["Неисправность", "проблема", "описание"])
            master_field = _find_field_name(orders_module, ["Мастер", "партнер", "партнёр"])
            warranty_field = _find_field_name(orders_module, ["Гарантия до"])
            status_field = _find_field_name(orders_module, ["Статус"])

            if accepted_field:
                payload[accepted_field] = (accepted_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
            if client_field:
                payload[client_field] = client_name
            if device_field:
                payload[device_field] = device_name
            if issue_field:
                payload[issue_field] = issue_text
            if master_field:
                payload[master_field] = master_name
            if warranty_field:
                payload[warranty_field] = warranty_until
            if status_field:
                payload[status_field] = status

            record.data = payload  # type: ignore[assignment]
            record.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            records_to_push.append((orders_module, record))

    if clients_module:
        order_no_field = _find_field_name(clients_module, ["№ заказа", "номер", "заказ"])
        client_name_field = _find_field_name(clients_module, ["ФИО название", "клиент", "фио"])
        phone_field = _find_field_name(clients_module, ["Телефон"])
        note_field = _find_field_name(clients_module, ["Примечание", "комментарий"])

        if order_no_field:
            record = await _get_or_create_record_by_order_no(db, "clients", order_no_field, order_no)
            payload = dict(cast(dict[str, Any], record.data or {}))
            if client_name_field:
                payload[client_name_field] = client_name
            if phone_field:
                payload[phone_field] = client_phone
            if note_field and issue_text:
                payload[note_field] = issue_text
            record.data = payload  # type: ignore[assignment]
            record.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            records_to_push.append((clients_module, record))

    if finance_module:
        order_no_field = _find_field_name(finance_module, ["№ заказа", "номер", "заказ"])
        if order_no_field:
            record = await _get_or_create_record_by_order_no(db, "finance", order_no_field, order_no)
            payload = dict(cast(dict[str, Any], record.data or {}))

            issued_at_field = _find_field_name(finance_module, ["Дата выдачи", "дата"])
            client_price_field = _find_field_name(finance_module, ["Цена для клиента", "цена", "сумма"])
            status_order_payment_field = _find_field_name(finance_module, ["Статус оплаты заказа", "оплаты заказа"])
            status_staff_payment_field = _find_field_name(finance_module, ["Статус оплаты сотруднику", "оплаты сотруднику"])

            if issued_at_field and issued_at_field not in payload:
                payload[issued_at_field] = ""
            if client_price_field:
                payload[client_price_field] = str(total_amount)
            if status_order_payment_field and status_order_payment_field not in payload:
                payload[status_order_payment_field] = ""
            if status_staff_payment_field and status_staff_payment_field not in payload:
                payload[status_staff_payment_field] = ""

            record.data = payload  # type: ignore[assignment]
            record.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            records_to_push.append((finance_module, record))

    if records_to_push:
        await db.commit()

        try:
            from app.sync_service import push_single_record

            for module, record in records_to_push:
                try:
                    await push_single_record(db, module, record)
                except Exception:
                    # Fallback to full module push for row append scenarios.
                    from app.sync_service import push_module  # type: ignore[attr-defined]

                    await push_module(db, module)
        except Exception:
            # Never fail order flow because of backup integration.
            return


async def remove_order_from_dynamic_modules(db: AsyncSession, *, order_no: str) -> int:
    target_order_no = str(order_no or "").strip()
    if not target_order_no:
        return 0

    removed = 0
    for module_slug in ("orders", "clients", "finance"):
        module = await _get_module_by_slug(db, module_slug)
        if not module:
            continue

        order_no_field = _find_field_name(module, ["№ заказа", "номер", "заказ"])
        if not order_no_field:
            continue

        records = (
            await db.execute(
                select(DynamicRecord)
                .where(DynamicRecord.module_slug == module_slug)
                .order_by(DynamicRecord.row_index.asc())
            )
        ).scalars().all()

        for record in records:
            value = str((record.data or {}).get(order_no_field, "")).strip()
            if value == target_order_no:
                await db.delete(record)
                removed += 1

    return removed

"""
Sync service: bidirectional sync between Google Sheets and local database.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import DynamicRecord, ModuleConfig, SyncLog
from models.crm import Order, SystemSetting
from app import sheets_adapter
from app.config import settings

logger = logging.getLogger(__name__)

ORDER_NUMBER_FIELD_CANDIDATES = (
    "№ заказа",
    "номер заказа",
    "номер",
    "order_no",
    "order number",
)


def _find_order_number_field(fields_schema: list | None) -> str | None:
    field_names = [str(f.get("name", "")).strip() for f in (fields_schema or []) if isinstance(f, dict)]
    lowered_map = {name.lower(): name for name in field_names if name}

    for candidate in ORDER_NUMBER_FIELD_CANDIDATES:
        if candidate in lowered_map:
            return lowered_map[candidate]

    for name in field_names:
        lowered = name.lower()
        if "заказ" in lowered and ("№" in lowered or "номер" in lowered):
            return name
        if lowered in {"order", "order id", "order_no"}:
            return name

    return None


async def _generate_next_order_no(db: AsyncSession, reserved_numbers: set[str]) -> str:
    key = "orders_next_sequence"
    setting = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.key == key).with_for_update()
        )
    ).scalar_one_or_none()

    if setting and str(setting.value or "").strip().isdigit():
        next_sequence = int(str(setting.value).strip())
    else:
        order_numbers = (await db.execute(select(Order.order_no).where(Order.order_no.like("JX-%")))).scalars().all()
        dynamic_numbers = (
            await db.execute(select(DynamicRecord.data).where(DynamicRecord.module_slug == "orders"))
        ).scalars().all()

        max_sequence = 0
        for raw in order_numbers:
            value = str(raw or "").strip()
            if value.startswith("JX-") and value[3:].isdigit():
                max_sequence = max(max_sequence, int(value[3:]))

        for row_data in dynamic_numbers:
            if not isinstance(row_data, dict):
                continue
            for value in row_data.values():
                text = str(value or "").strip()
                if text.startswith("JX-") and text[3:].isdigit():
                    max_sequence = max(max_sequence, int(text[3:]))

        next_sequence = max_sequence + 1

    while True:
        candidate = f"JX-{next_sequence:08d}"
        if candidate not in reserved_numbers:
            break
        next_sequence += 1

    if setting:
        setting.value = str(next_sequence + 1)
    else:
        db.add(SystemSetting(key=key, value=str(next_sequence + 1)))

    await db.flush()
    reserved_numbers.add(candidate)
    return candidate


def _normalize_row_data_by_schema(data: dict | None, fields_schema: list | None) -> dict:
    source = data if isinstance(data, dict) else {}
    field_names = [str(f.get("name", "")).strip() for f in (fields_schema or []) if isinstance(f, dict)]
    if not field_names:
        return dict(source)
    normalized: dict[str, str] = {}
    for field_name in field_names:
        normalized[field_name] = str(source.get(field_name, "")) if source.get(field_name) is not None else ""
    return normalized


async def pull_module(db: AsyncSession, module: ModuleConfig) -> dict:
    """Pull data from Google Sheets into local database."""
    result = {"status": "success", "records_affected": 0, "message": ""}
    if not settings.GOOGLE_SHEETS_IMPORT_ENABLED:
        result["message"] = "Google Sheets pull skipped: PostgreSQL is the primary storage."
        await _log_sync(db, module.slug, "pull", "success", 0, result["message"])
        return result

    try:
        rows = sheets_adapter.get_worksheet_data(module.sheet_name)
        if not rows:
            result["message"] = "No data returned from Google Sheets"
            await _log_sync(db, module.slug, "pull", "success", 0, result["message"])
            return result

        if module.slug == "orders":
            order_number_field = _find_order_number_field(module.fields_schema)
            if order_number_field:
                reserved_numbers: set[str] = set()
                for row in rows:
                    value = str((row or {}).get(order_number_field, "") or "").strip()
                    if value:
                        reserved_numbers.add(value)

                for row in rows:
                    current_value = str((row or {}).get(order_number_field, "") or "").strip()
                    if current_value:
                        continue
                    row[order_number_field] = await _generate_next_order_no(db, reserved_numbers)

        headers = [f["name"] for f in module.fields_schema]

        # Delete existing records for this module
        await db.execute(
            delete(DynamicRecord).where(DynamicRecord.module_slug == module.slug)
        )

        # Insert new records
        for idx, row in enumerate(rows, start=2):
            data = {}
            for h in headers:
                data[h] = str(row.get(h, "")) if row.get(h) is not None else ""
            record = DynamicRecord(
                module_slug=module.slug,
                row_index=idx,
                data=data,
                updated_at=datetime.now(timezone.utc),
            )
            db.add(record)

        await db.commit()
        result["records_affected"] = len(rows)
        result["message"] = f"Pulled {len(rows)} records"
        await _log_sync(db, module.slug, "pull", "success", len(rows), result["message"])

    except Exception as e:
        logger.exception(f"Pull failed for {module.slug}")
        result["status"] = "error"
        result["message"] = str(e)
        await _log_sync(db, module.slug, "pull", "error", 0, str(e))

    return result


async def push_module(db: AsyncSession, module: ModuleConfig) -> dict:
    """Push local database records to Google Sheets."""
    result = {"status": "success", "records_affected": 0, "message": ""}
    try:
        stmt = select(DynamicRecord).where(
            DynamicRecord.module_slug == module.slug
        ).order_by(DynamicRecord.row_index)
        records = (await db.execute(stmt)).scalars().all()

        if not records:
            result["message"] = "No local records to push"
            await _log_sync(db, module.slug, "push", "success", 0, result["message"])
            return result

        rows_data = [_normalize_row_data_by_schema(rec.data, module.fields_schema) for rec in records]
        sheets_adapter.update_worksheet_data(module.sheet_name, rows_data)

        result["records_affected"] = len(rows_data)
        result["message"] = f"Pushed {len(rows_data)} records"
        await _log_sync(db, module.slug, "push", "success", len(rows_data), result["message"])

    except Exception as e:
        logger.exception(f"Push failed for {module.slug}")
        result["status"] = "error"
        result["message"] = str(e)
        await _log_sync(db, module.slug, "push", "error", 0, str(e))

    return result


async def push_single_record(db: AsyncSession, module: ModuleConfig, record: DynamicRecord):
    """Push a single updated record back to Google Sheets."""
    try:
        row_data = _normalize_row_data_by_schema(record.data, module.fields_schema)
        sheets_adapter.update_single_row(
            module.sheet_name, record.row_index, row_data
        )
    except Exception as e:
        logger.warning(f"Failed to push single record to Sheets: {e}")


async def pull_all(db: AsyncSession):
    """Pull all enabled modules from Google Sheets."""
    stmt = select(ModuleConfig).where(ModuleConfig.enabled == True)
    modules = (await db.execute(stmt)).scalars().all()
    results = {}
    for mod in modules:
        results[mod.slug] = await pull_module(db, mod)
    return results


async def push_all(db: AsyncSession):
    """Push all enabled modules to Google Sheets."""
    stmt = select(ModuleConfig).where(ModuleConfig.enabled == True)
    modules = (await db.execute(stmt)).scalars().all()
    results = {}
    for mod in modules:
        results[mod.slug] = await push_module(db, mod)
    return results


async def _log_sync(db: AsyncSession, module_slug: str, direction: str,
                     status: str, records: int, message: str):
    log = SyncLog(
        module_slug=module_slug,
        direction=direction,
        status=status,
        records_affected=records,
        message=message,
    )
    db.add(log)
    await db.commit()

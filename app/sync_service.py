"""
Sync service: bidirectional sync between Google Sheets and local database.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import DynamicRecord, ModuleConfig, SyncLog
from app import sheets_adapter
from app.config import settings

logger = logging.getLogger(__name__)


async def pull_module(db: AsyncSession, module: ModuleConfig) -> dict:
    """Pull data from Google Sheets into local database."""
    result = {"status": "success", "records_affected": 0, "message": ""}
    if not settings.GOOGLE_SHEETS_IMPORT_ENABLED:
        result["status"] = "error"
        result["message"] = "Google Sheets pull is disabled. PostgreSQL is the primary storage."
        await _log_sync(db, module.slug, "pull", "error", 0, result["message"])
        return result

    try:
        rows = sheets_adapter.get_worksheet_data(module.sheet_name)
        if not rows:
            result["message"] = "No data returned from Google Sheets"
            await _log_sync(db, module.slug, "pull", "success", 0, result["message"])
            return result

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

        rows_data = [rec.data for rec in records]
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
        sheets_adapter.update_single_row(
            module.sheet_name, record.row_index, record.data
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

"""
Sync service: bidirectional sync between Google Sheets and local database.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import DynamicRecord, ModuleConfig, SyncLog
from app import sheets_adapter
from app.config import settings

logger = logging.getLogger(__name__)

SYNC_DISABLED_MODULES = {"analytics"}


def _is_sync_disabled(module_slug: str) -> bool:
    return str(module_slug or "").strip().lower() in SYNC_DISABLED_MODULES


def _normalize_header_key(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("ё", "е").replace("№", "номер")
    normalized = re.sub(r"[\s_\-]+", "", normalized)
    normalized = re.sub(r"[^0-9a-zа-я]", "", normalized)
    return normalized


def _is_date_field(field_schema: dict | None) -> bool:
    field_type = str((field_schema or {}).get("type", "")).strip().lower()
    return field_type in {"date", "datetime", "datetime-local", "timestamp"}


def _normalize_date_value(value) -> str:
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.date().isoformat()

    text = str(value).strip()
    if not text:
        return ""

    # Google Sheets can return serial date numbers for unformatted cells.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if float(value) <= 0:
                return ""
            origin = datetime(1899, 12, 30)
            converted = origin + timedelta(days=float(value))
            return converted.date().isoformat()
        except Exception:
            return ""

    if re.match(r"^\d+(\.\d+)?$", text):
        try:
            serial = float(text)
            if serial > 0:
                origin = datetime(1899, 12, 30)
                converted = origin + timedelta(days=serial)
                return converted.date().isoformat()
        except ValueError:
            pass

    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text

    if "T" in text:
        iso_candidate = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(iso_candidate).date().isoformat()
        except ValueError:
            pass

    if " " in text and re.match(r"^\d{4}-\d{2}-\d{2}\s", text):
        return text.split(" ", 1)[0]

    normalized = text.replace("\\", "/")
    formats = (
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    )
    for fmt in formats:
        for candidate in (text, normalized):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue

    return ""


def _coerce_field_value(value, field_schema: dict | None) -> tuple[str, bool]:
    if _is_date_field(field_schema):
        normalized = _normalize_date_value(value)
        invalid_date = bool(str(value or "").strip()) and not normalized
        return normalized, invalid_date
    return (str(value).strip() if value is not None else ""), False


def _build_header_mapping(rows: list[dict], expected_headers: list[str]) -> dict[str, str]:
    source_headers: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for raw_key in row.keys():
            key = str(raw_key or "")
            if key and key not in source_headers:
                source_headers.append(key)

    normalized_source: dict[str, str] = {}
    for source_header in source_headers:
        normalized_key = _normalize_header_key(source_header)
        if normalized_key and normalized_key not in normalized_source:
            normalized_source[normalized_key] = source_header

    mapping: dict[str, str] = {}
    for expected in expected_headers:
        normalized_expected = _normalize_header_key(expected)

        if normalized_expected in normalized_source:
            mapping[expected] = normalized_source[normalized_expected]
            continue

        if expected in source_headers:
            mapping[expected] = expected
            continue

        candidates: list[tuple[int, int, str]] = []
        for source_header in source_headers:
            normalized_source_header = _normalize_header_key(source_header)
            if not normalized_expected or not normalized_source_header:
                continue
            if normalized_expected in normalized_source_header or normalized_source_header in normalized_expected:
                score = abs(len(normalized_source_header) - len(normalized_expected))
                candidates.append((score, -len(normalized_source_header), source_header))

        if candidates:
            candidates.sort()
            mapping[expected] = candidates[0][2]

    return mapping


async def pull_module(db: AsyncSession, module: ModuleConfig) -> dict:
    """Pull data from Google Sheets into local database."""
    result = {"status": "success", "records_affected": 0, "message": ""}
    if _is_sync_disabled(module.slug):
        result["message"] = "Sync disabled for this module"
        await _log_sync(db, module.slug, "pull", "success", 0, result["message"])
        return result
    if not settings.GOOGLE_SHEETS_IMPORT_ENABLED:
        result["status"] = "error"
        result["message"] = "Google Sheets pull is disabled. PostgreSQL is the primary storage."
        await _log_sync(db, module.slug, "pull", "error", 0, result["message"])
        return result

    try:
        logger.info(f"Starting pull for module '{module.slug}' from sheet '{module.sheet_name}'")
        source_rows = sheets_adapter.get_worksheet_data(module.sheet_name)
        if not source_rows:
            logger.info(f"No rows in worksheet '{module.sheet_name}' for module '{module.slug}'; local records will be cleared")

        rows = [row for row in source_rows if isinstance(row, dict)]
        if source_rows and not rows:
            logger.warning(f"Worksheet '{module.sheet_name}' returned non-dict rows only; treating as empty dataset")

        headers = [
            str(field.get("name", "")).strip()
            for field in (module.fields_schema or [])
            if isinstance(field, dict) and str(field.get("name", "")).strip()
        ]
        field_schema_by_name = {
            str(field.get("name", "")).strip(): field
            for field in (module.fields_schema or [])
            if isinstance(field, dict) and str(field.get("name", "")).strip()
        }
        header_mapping = _build_header_mapping(rows, headers)
        logger.debug(f"Headers for {module.slug}: {headers}")
        logger.debug(f"Header mapping: {header_mapping}")

        prepared_rows: list[dict[str, str]] = []
        skipped_blank_rows = 0
        invalid_date_cells = 0
        for row in rows:
            data: dict[str, str] = {}
            has_values = False
            for header in headers:
                source_header = header_mapping.get(header, header)
                raw_value = row.get(source_header)
                normalized_value, is_invalid_date = _coerce_field_value(raw_value, field_schema_by_name.get(header))
                if is_invalid_date:
                    invalid_date_cells += 1
                if normalized_value:
                    has_values = True
                data[header] = normalized_value

            if not has_values:
                skipped_blank_rows += 1
                continue

            prepared_rows.append(data)

        logger.info(f"Prepared {len(prepared_rows)} rows from {len(rows)} total rows (skipped {skipped_blank_rows} blank rows)")

        # Delete existing records for this module
        await db.execute(
            delete(DynamicRecord).where(DynamicRecord.module_slug == module.slug)
        )

        # Insert new records
        for idx, data in enumerate(prepared_rows, start=2):
            record = DynamicRecord(
                module_slug=module.slug,
                row_index=idx,
                data=data,
                updated_at=datetime.now(timezone.utc),
            )
            db.add(record)

        await db.commit()
        result["records_affected"] = len(prepared_rows)

        message_parts = [f"Pulled {len(prepared_rows)} records"]
        if skipped_blank_rows:
            message_parts.append(f"skipped {skipped_blank_rows} blank rows")
        if invalid_date_cells:
            message_parts.append(f"normalized with {invalid_date_cells} invalid date values")
        result["message"] = "; ".join(message_parts)
        logger.info(f"Pull completed for {module.slug}: {result['message']}")

        await _log_sync(db, module.slug, "pull", "success", len(prepared_rows), result["message"])

    except Exception as e:
        logger.exception(f"Pull failed for {module.slug}: {type(e).__name__}: {str(e)}")
        await db.rollback()
        result["status"] = "error"
        result["message"] = f"Pull failed: {str(e)}"
        await _log_sync(db, module.slug, "pull", "error", 0, str(e))

    return result


async def push_module(db: AsyncSession, module: ModuleConfig) -> dict:
    """Push local database records to Google Sheets."""
    result = {"status": "success", "records_affected": 0, "message": ""}
    if _is_sync_disabled(module.slug):
        result["message"] = "Sync disabled for this module"
        await _log_sync(db, module.slug, "push", "success", 0, result["message"])
        return result
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
        if _is_sync_disabled(mod.slug):
            results[mod.slug] = {"status": "success", "records_affected": 0, "message": "Sync disabled for this module"}
            continue
        results[mod.slug] = await pull_module(db, mod)
    return results


async def push_all(db: AsyncSession):
    """Push all enabled modules to Google Sheets."""
    stmt = select(ModuleConfig).where(ModuleConfig.enabled == True)
    modules = (await db.execute(stmt)).scalars().all()
    results = {}
    for mod in modules:
        if _is_sync_disabled(mod.slug):
            results[mod.slug] = {"status": "success", "records_affected": 0, "message": "Sync disabled for this module"}
            continue
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

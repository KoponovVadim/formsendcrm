"""
Dynamic module routes – list, detail, create, update, delete for any module.
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, delete as sa_delete, case, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
import re

from app.database import get_db
from app.models import DynamicRecord, ModuleConfig
from app.auth import (
    get_current_user, get_user_permissions, check_module_visible,
    get_visible_fields, get_editable_fields, filter_visible_modules_for_user, get_user_specializations,
)
from app.schema_loader import get_all_modules, get_module_by_slug
from models.crm import Order as CRMOrder

router = APIRouter()
templates = Jinja2Templates(directory="templates")

DEFAULT_STATUS_OPTIONS = [
    "Новый",
    "Ожидает доставки в ремонт",
    "Едет в точку ремонта",
    "Приехал в точку ремонта",
    "В ремонте",
    "Готов",
    "Едет на выдачу",
    "Ожидает выдачи",
    "Выдан",
    "Отменен",
]

DEFAULT_STATUS_COLORS = {
    "Новый": "#93c5fd",
    "Ожидает доставки в ремонт": "#fbbf24",
    "Едет в точку ремонта": "#60a5fa",
    "Приехал в точку ремонта": "#38bdf8",
    "В ремонте": "#f59e0b",
    "Готов": "#22c55e",
    "Едет на выдачу": "#0ea5e9",
    "Ожидает выдачи": "#34d399",
    "Выдан": "#60a5fa",
    "Отменен": "#fecaca",
}

SORT_OPTIONS = {"newest", "oldest", "updated_desc", "updated_asc"}

PRICE_FIELD_KEYWORDS = ("цена", "стоим", "сумм", "price", "cost", "total")
DATE_FIELD_KEYWORDS = ("дата", "date", "deadline", "дедлайн", "гарант", "warranty")

ORDER_MAIN_FIELD_CANDIDATES = [
    ["№ заказа", "номер заказа", "заказ"],
    ["Дата приёма", "дата приема", "принят", "дата"],
    ["Клиент", "контакт", "фио"],
    ["Устройство", "девайс", "модель"],
    ["Неисправность", "проблем", "полом"],
    ["Мастер", "исполнитель", "партнер", "партнёр"],
    ["Статус"],
]

READONLY_DERIVED_MODULE_SLUGS = {"finance", "analytics"}
SYNC_HIDDEN_MODULE_SLUGS = {"analytics"}


def _find_field_name_by_candidates(field_names: list[str], candidates: list[str]) -> str | None:
    lowered = {str(name).strip().lower(): name for name in field_names if str(name).strip()}

    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        if key in lowered:
            return lowered[key]

    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        for field_name in field_names:
            if key in str(field_name).strip().lower():
                return field_name

    return None


def _parse_float(value: str | None) -> float:
    raw = str(value or "").strip().replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _field_schema_map(module: ModuleConfig) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for field in (module.fields_schema or []):
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", "")).strip()
        if not name:
            continue
        mapping[name] = field
    return mapping


def _is_price_like_field(field_name: str) -> bool:
    lowered = str(field_name or "").strip().lower()
    if not lowered:
        return False
    return any(keyword in lowered for keyword in PRICE_FIELD_KEYWORDS)


def _filter_module_visible_fields(slug: str, field_names: list[str]) -> list[str]:
    return list(field_names)


def _split_order_fields_for_compact_table(field_names: list[str]) -> tuple[list[str], list[str]]:
    if not field_names:
        return [], []

    remaining = list(field_names)
    selected_main: list[str] = []

    for candidates in ORDER_MAIN_FIELD_CANDIDATES:
        match = _find_field_name_by_candidates(remaining, candidates)
        if not match:
            continue
        selected_main.append(match)
        remaining = [field_name for field_name in remaining if field_name != match]

    if len(selected_main) < 6:
        for field_name in remaining:
            if len(selected_main) >= 6:
                break
            selected_main.append(field_name)

    selected_main_set = set(selected_main)
    main_fields = [field_name for field_name in field_names if field_name in selected_main_set]
    detail_fields = [field_name for field_name in field_names if field_name not in selected_main_set]
    return main_fields, detail_fields


def _is_date_field(field_name: str, field_schema: dict | None) -> bool:
    field_type = str((field_schema or {}).get("type", "")).strip().lower()
    if field_type in {"date", "datetime", "datetime-local", "timestamp"}:
        return True

    lowered = str(field_name or "").strip().lower()
    if not lowered:
        return False
    return any(keyword in lowered for keyword in DATE_FIELD_KEYWORDS)


def _resolve_field_input_types(module: ModuleConfig, field_names: list[str]) -> dict[str, str]:
    schema_by_name = _field_schema_map(module)
    return {
        field_name: "date" if _is_date_field(field_name, schema_by_name.get(field_name)) else "text"
        for field_name in field_names
    }


def _to_date_input_value(value) -> str:
    if isinstance(value, date):
        return value.isoformat()

    raw = str(value or "").strip()
    if not raw:
        return ""

    candidate = raw
    if "T" in candidate:
        candidate = candidate.split("T", 1)[0]
    if " " in candidate and re.match(r"^\d{4}-\d{2}-\d{2}\s", raw):
        candidate = candidate.split(" ", 1)[0]

    if re.match(r"^\d{4}-\d{2}-\d{2}$", candidate):
        return candidate

    date_formats = [
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y.%m.%d",
        "%Y/%m/%d",
        "%d.%m.%y",
    ]
    for fmt in date_formats:
        for source in (raw, candidate):
            try:
                return datetime.strptime(source, fmt).date().isoformat()
            except ValueError:
                continue

    return ""


def _is_order_derived_module_readonly(slug: str) -> bool:
    return str(slug or "").strip().lower() in READONLY_DERIVED_MODULE_SLUGS


def _is_sync_hidden_for_module(slug: str) -> bool:
    return str(slug or "").strip().lower() in SYNC_HIDDEN_MODULE_SLUGS


def _module_field_names(module: ModuleConfig | None) -> list[str]:
    if not module:
        return []
    return [
        str(field.get("name", "")).strip()
        for field in (module.fields_schema or [])
        if isinstance(field, dict) and str(field.get("name", "")).strip()
    ]


def _normalize_order_no(value: str) -> str:
    return str(value or "").strip().upper()


def _format_amount(value: float) -> str:
    try:
        numeric = round(float(value or 0), 2)
    except (TypeError, ValueError):
        return "0"
    if float(numeric).is_integer():
        return str(int(numeric))
    return f"{numeric:.2f}"


def _extract_price_from_row_data(data: dict[str, Any]) -> str:
    if not data:
        return ""

    for field_name, field_value in data.items():
        if not _is_price_like_field(field_name):
            continue

        raw = str(field_value or "").strip()
        if not raw:
            continue

        if any(ch.isdigit() for ch in raw):
            return _format_amount(_parse_float(raw))

        return raw

    return ""


async def _build_order_total_amount_by_record_id(
    db: AsyncSession,
    records: list[Any],
    order_no_field: str | None,
) -> dict[int, str]:
    if not records or not order_no_field:
        return {}

    order_no_by_record_id: dict[int, str] = {}
    for record in records:
        record_id = int(getattr(record, "id", 0) or 0)
        if not record_id:
            continue
        data = dict(getattr(record, "data", {}) or {})
        order_no = _normalize_order_no(str(data.get(order_no_field, "")))
        if order_no:
            order_no_by_record_id[record_id] = order_no

    if not order_no_by_record_id:
        return {}

    order_nos = sorted(set(order_no_by_record_id.values()))
    rows = (
        await db.execute(
            select(CRMOrder.order_no, CRMOrder.total_amount).where(func.upper(CRMOrder.order_no).in_(order_nos))
        )
    ).all()
    amount_by_order_no = {
        _normalize_order_no(str(order_no or "")): _format_amount(float(total_amount or 0))
        for order_no, total_amount in rows
        if str(order_no or "").strip()
    }

    return {
        record_id: amount_by_order_no.get(order_no, "")
        for record_id, order_no in order_no_by_record_id.items()
    }


def _record_matches_search(data: dict[str, Any], search: str) -> bool:
    needle = str(search or "").strip().lower()
    if not needle:
        return True
    haystack = " ".join(str(value or "") for value in data.values()).lower()
    return needle in haystack


def _record_updated_at(record: Any) -> datetime:
    value = getattr(record, "updated_at", None)
    if isinstance(value, datetime):
        return value
    return datetime.now(timezone.utc)


def _record_row_index(record: Any) -> int:
    try:
        return int(getattr(record, "row_index", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _sort_in_memory_records(records: list[Any], sort: str) -> list[Any]:
    if sort == "oldest":
        return sorted(records, key=lambda rec: (_record_row_index(rec), _record_updated_at(rec)))
    if sort == "updated_desc":
        return sorted(records, key=lambda rec: (_record_updated_at(rec), _record_row_index(rec)), reverse=True)
    if sort == "updated_asc":
        return sorted(records, key=lambda rec: (_record_updated_at(rec), _record_row_index(rec)))
    return sorted(records, key=lambda rec: (_record_row_index(rec), _record_updated_at(rec)), reverse=True)


def _parse_date_value(value) -> date | None:
    iso = _to_date_input_value(value)
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_orders_period_field(field_names: list[str]) -> str | None:
    return _find_field_name_by_candidates(
        field_names,
        ["Дата приёма", "дата приема", "Дата выдачи", "выдачи", "дата"],
    )


def _record_matches_period(
    record: Any,
    date_field: str | None,
    period_from: date | None,
    period_to: date | None,
) -> bool:
    if not date_field or (not period_from and not period_to):
        return True

    data = dict(getattr(record, "data", {}) or {})
    record_date = _parse_date_value(data.get(date_field, ""))
    if not record_date:
        return False

    if period_from and record_date < period_from:
        return False
    if period_to and record_date > period_to:
        return False
    return True


async def _build_crm_orders_index(db: AsyncSession) -> dict[str, dict[str, Any]]:
    rows = (
        await db.execute(
            select(CRMOrder.order_no, CRMOrder.total_amount, CRMOrder.created_at, CRMOrder.updated_at)
        )
    ).all()
    result: dict[str, dict[str, Any]] = {}
    for order_no, total_amount, created_at, updated_at in rows:
        key = _normalize_order_no(str(order_no or ""))
        if not key:
            continue
        result[key] = {
            "total_amount": float(total_amount or 0),
            "created_at": created_at,
            "updated_at": updated_at,
        }
    return result


async def _build_finance_legacy_index(db: AsyncSession) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    finance_module = await get_module_by_slug(db, "finance")
    finance_fields = _module_field_names(finance_module)
    order_no_field = _find_field_name_by_candidates(finance_fields, ["№ заказа", "номер заказа", "номер", "заказ"])
    client_price_field = _find_field_name_by_candidates(finance_fields, ["Цена для клиента", "цена", "сумма"])
    salary_field = _find_field_name_by_candidates(finance_fields, ["Зарплата мастера", "зарплата"])
    status_order_payment_field = _find_field_name_by_candidates(finance_fields, ["Статус оплаты заказа", "оплаты заказа"])
    status_staff_payment_field = _find_field_name_by_candidates(finance_fields, ["Статус оплаты сотруднику", "оплаты сотруднику"])

    metadata = {
        "order_no_field": order_no_field or "",
        "client_price_field": client_price_field or "",
        "salary_field": salary_field or "",
        "status_order_payment_field": status_order_payment_field or "",
        "status_staff_payment_field": status_staff_payment_field or "",
    }

    if not order_no_field:
        return {}, metadata

    rows = (
        await db.execute(
            select(DynamicRecord)
            .where(DynamicRecord.module_slug == "finance")
            .order_by(DynamicRecord.row_index.desc())
        )
    ).scalars().all()

    result: dict[str, dict[str, Any]] = {}
    for record in rows:
        data = dict(record.data or {})
        order_no = str(data.get(order_no_field, "")).strip()
        if not order_no:
            continue
        key = _normalize_order_no(order_no)
        if key not in result:
            result[key] = data

    return result, metadata


async def _build_supplies_cost_by_order_no(db: AsyncSession) -> dict[str, float]:
    supplies_module = await get_module_by_slug(db, "supplies")
    supplies_fields = _module_field_names(supplies_module)
    if not supplies_fields:
        return {}

    order_ref_field = _find_field_name_by_candidates(supplies_fields, ["№ заказа", "номер заказа", "заказ"])
    origin_field = _find_field_name_by_candidates(supplies_fields, ["Происхождение", "источник", "origin"])
    cost_field = _find_field_name_by_candidates(supplies_fields, ["Стоимость", "цена", "себестоимость"])
    if not cost_field:
        return {}

    rows = (
        await db.execute(
            select(DynamicRecord)
            .where(DynamicRecord.module_slug == "supplies")
            .order_by(DynamicRecord.row_index.desc())
        )
    ).scalars().all()

    order_re = re.compile(r"заказ\s*[№:#-]*\s*([\w\-/]+)", re.IGNORECASE)
    result: dict[str, float] = {}
    for record in rows:
        data = dict(record.data or {})
        order_no = str(data.get(order_ref_field or "", "")).strip() if order_ref_field else ""

        if not order_no and origin_field:
            origin_text = str(data.get(origin_field, "")).strip()
            found = order_re.search(origin_text)
            if found:
                order_no = str(found.group(1) or "").strip()

        if not order_no:
            continue

        key = _normalize_order_no(order_no)
        result[key] = result.get(key, 0.0) + _parse_float(str(data.get(cost_field, "0")))

    return result


async def _build_finance_records_from_orders(db: AsyncSession, module: ModuleConfig) -> list[SimpleNamespace]:
    target_fields = _module_field_names(module)
    orders_module = await get_module_by_slug(db, "orders")
    order_fields = _module_field_names(orders_module)
    if not order_fields:
        return []

    order_no_source_field = _find_field_name_by_candidates(order_fields, ["№ заказа", "номер заказа", "номер", "заказ"])
    order_issued_source_field = _find_field_name_by_candidates(order_fields, ["Дата выдачи", "выдачи"])
    order_accepted_source_field = _find_field_name_by_candidates(order_fields, ["Дата приёма", "дата приема", "дата"])
    if not order_no_source_field:
        return []

    finance_order_no_field = _find_field_name_by_candidates(target_fields, ["№ заказа", "номер заказа", "номер", "заказ"])
    finance_issued_field = _find_field_name_by_candidates(target_fields, ["Дата выдачи", "выдачи", "дата"])
    finance_client_price_field = _find_field_name_by_candidates(target_fields, ["Цена для клиента", "цена", "сумма"])
    finance_work_cost_field = _find_field_name_by_candidates(target_fields, ["Стоимость работы", "работы"])
    finance_parts_cost_field = _find_field_name_by_candidates(target_fields, ["Запчасти себестоимость", "запчаст", "себестоимость"])
    finance_salary_field = _find_field_name_by_candidates(target_fields, ["Зарплата мастера", "зарплата"])
    finance_net_field = _find_field_name_by_candidates(target_fields, ["Чистая прибыль", "прибыль"])
    finance_order_payment_field = _find_field_name_by_candidates(target_fields, ["Статус оплаты заказа", "оплаты заказа"])
    finance_staff_payment_field = _find_field_name_by_candidates(target_fields, ["Статус оплаты сотруднику", "оплаты сотруднику"])

    supplies_cost_by_order = await _build_supplies_cost_by_order_no(db)
    crm_orders = await _build_crm_orders_index(db)
    finance_legacy_index, finance_legacy_meta = await _build_finance_legacy_index(db)

    order_records = (
        await db.execute(
            select(DynamicRecord)
            .where(DynamicRecord.module_slug == "orders")
            .order_by(DynamicRecord.row_index.desc())
        )
    ).scalars().all()

    derived_rows: list[SimpleNamespace] = []
    for source in order_records:
        source_data = dict(source.data or {})
        order_no = str(source_data.get(order_no_source_field, "")).strip()
        if not order_no:
            continue

        key = _normalize_order_no(order_no)
        crm_row = crm_orders.get(key, {})
        legacy_row = finance_legacy_index.get(key, {})

        total_amount = float(crm_row.get("total_amount", 0.0) or 0.0)
        if not total_amount and finance_legacy_meta.get("client_price_field"):
            total_amount = _parse_float(str(legacy_row.get(finance_legacy_meta["client_price_field"], "0")))

        supplies_cost = float(supplies_cost_by_order.get(key, 0.0) or 0.0)
        salary_field_name = finance_legacy_meta.get("salary_field") or finance_salary_field or ""
        salary_amount = _parse_float(str(legacy_row.get(salary_field_name, "0"))) if salary_field_name else 0.0
        work_cost = max(total_amount - supplies_cost, 0.0)
        net_profit = total_amount - supplies_cost - salary_amount

        issued_at = ""
        if order_issued_source_field:
            issued_at = str(source_data.get(order_issued_source_field, "")).strip()
        if not issued_at and order_accepted_source_field:
            issued_at = str(source_data.get(order_accepted_source_field, "")).strip()
        if not issued_at and isinstance(crm_row.get("created_at"), datetime):
            issued_at = str(crm_row["created_at"].date().isoformat())

        row_data = {field_name: str(legacy_row.get(field_name, "")) for field_name in target_fields}
        if finance_order_no_field:
            row_data[finance_order_no_field] = order_no
        if finance_issued_field:
            row_data[finance_issued_field] = issued_at
        if finance_client_price_field:
            row_data[finance_client_price_field] = _format_amount(total_amount)
        if finance_work_cost_field:
            row_data[finance_work_cost_field] = _format_amount(work_cost)
        if finance_parts_cost_field:
            row_data[finance_parts_cost_field] = _format_amount(supplies_cost)
        if finance_salary_field:
            row_data[finance_salary_field] = _format_amount(salary_amount)
        if finance_net_field:
            row_data[finance_net_field] = _format_amount(net_profit)

        if finance_order_payment_field and finance_legacy_meta.get("status_order_payment_field"):
            row_data[finance_order_payment_field] = str(
                legacy_row.get(finance_legacy_meta["status_order_payment_field"], "")
            )
        if finance_staff_payment_field and finance_legacy_meta.get("status_staff_payment_field"):
            row_data[finance_staff_payment_field] = str(
                legacy_row.get(finance_legacy_meta["status_staff_payment_field"], "")
            )

        updated_at = source.updated_at if isinstance(source.updated_at, datetime) else crm_row.get("updated_at")
        if not isinstance(updated_at, datetime):
            updated_at = datetime.now(timezone.utc)

        source_id = int(getattr(source, "id", 0) or 0)
        source_row_index = int(getattr(source, "row_index", len(derived_rows) + 1) or (len(derived_rows) + 1))
        derived_rows.append(
            SimpleNamespace(
                id=-source_id if source_id else -(len(derived_rows) + 1),
                row_index=source_row_index,
                data=row_data,
                updated_at=updated_at,
            )
        )

    return derived_rows


async def _build_analytics_records_from_orders(db: AsyncSession, module: ModuleConfig) -> list[SimpleNamespace]:
    target_fields = _module_field_names(module)
    orders_module = await get_module_by_slug(db, "orders")
    order_fields = _module_field_names(orders_module)
    if not order_fields:
        return []

    order_no_source_field = _find_field_name_by_candidates(order_fields, ["№ заказа", "номер заказа", "номер", "заказ"])
    order_issued_source_field = _find_field_name_by_candidates(order_fields, ["Дата выдачи", "выдачи"])
    order_accepted_source_field = _find_field_name_by_candidates(order_fields, ["Дата приёма", "дата приема", "дата"])
    if not order_no_source_field:
        return []

    month_field = _find_field_name_by_candidates(target_fields, ["Месяц", "month"])
    revenue_field = _find_field_name_by_candidates(target_fields, ["Выручка", "доход"])
    parts_cost_field = _find_field_name_by_candidates(target_fields, ["Затраты (запчасти)", "запчаст"])
    supplies_cost_field = _find_field_name_by_candidates(target_fields, ["Затраты (расходники)", "расход"])
    salary_field = _find_field_name_by_candidates(target_fields, ["Выплаты (зарплата)", "зарплат"])
    net_field = _find_field_name_by_candidates(target_fields, ["Чистая прибыль", "прибыль"])

    supplies_cost_by_order = await _build_supplies_cost_by_order_no(db)
    crm_orders = await _build_crm_orders_index(db)
    finance_legacy_index, finance_legacy_meta = await _build_finance_legacy_index(db)

    order_records = (
        await db.execute(
            select(DynamicRecord)
            .where(DynamicRecord.module_slug == "orders")
            .order_by(DynamicRecord.row_index.desc())
        )
    ).scalars().all()

    monthly: dict[str, dict[str, float]] = {}
    for source in order_records:
        source_data = dict(source.data or {})
        order_no = str(source_data.get(order_no_source_field, "")).strip()
        if not order_no:
            continue

        key = _normalize_order_no(order_no)
        crm_row = crm_orders.get(key, {})
        legacy_row = finance_legacy_index.get(key, {})

        total_amount = float(crm_row.get("total_amount", 0.0) or 0.0)
        if not total_amount and finance_legacy_meta.get("client_price_field"):
            total_amount = _parse_float(str(legacy_row.get(finance_legacy_meta["client_price_field"], "0")))

        supplies_cost = float(supplies_cost_by_order.get(key, 0.0) or 0.0)
        salary_amount = 0.0
        if finance_legacy_meta.get("salary_field"):
            salary_amount = _parse_float(str(legacy_row.get(finance_legacy_meta["salary_field"], "0")))

        order_date = None
        if order_issued_source_field:
            order_date = _parse_date_value(source_data.get(order_issued_source_field, ""))
        if not order_date and order_accepted_source_field:
            order_date = _parse_date_value(source_data.get(order_accepted_source_field, ""))
        if not order_date and isinstance(crm_row.get("created_at"), datetime):
            order_date = crm_row["created_at"].date()
        if not order_date:
            continue

        month_key = f"{order_date.year:04d}-{order_date.month:02d}"
        bucket = monthly.setdefault(
            month_key,
            {
                "revenue": 0.0,
                "parts_cost": 0.0,
                "supplies_cost": 0.0,
                "salary": 0.0,
            },
        )
        bucket["revenue"] += total_amount
        bucket["supplies_cost"] += supplies_cost
        bucket["salary"] += salary_amount

    derived_rows: list[SimpleNamespace] = []
    for index, month_key in enumerate(sorted(monthly.keys(), reverse=True), start=1):
        bucket = monthly[month_key]
        net_profit = bucket["revenue"] - bucket["parts_cost"] - bucket["supplies_cost"] - bucket["salary"]

        row_data = {field_name: "" for field_name in target_fields}
        if month_field:
            row_data[month_field] = month_key
        if revenue_field:
            row_data[revenue_field] = _format_amount(bucket["revenue"])
        if parts_cost_field:
            row_data[parts_cost_field] = _format_amount(bucket["parts_cost"])
        if supplies_cost_field:
            row_data[supplies_cost_field] = _format_amount(bucket["supplies_cost"])
        if salary_field:
            row_data[salary_field] = _format_amount(bucket["salary"])
        if net_field:
            row_data[net_field] = _format_amount(net_profit)

        derived_rows.append(
            SimpleNamespace(
                id=-(100000 + index),
                row_index=index,
                data=row_data,
                updated_at=datetime.now(timezone.utc),
            )
        )

    return derived_rows


async def _build_derived_records_from_orders(db: AsyncSession, module: ModuleConfig) -> list[SimpleNamespace]:
    if module.slug == "finance":
        return await _build_finance_records_from_orders(db, module)
    if module.slug == "analytics":
        return await _build_analytics_records_from_orders(db, module)
    return []


async def _get_order_supplies_context(
    db: AsyncSession,
    order_record: DynamicRecord,
    orders_module: ModuleConfig | None,
    supplies_module: ModuleConfig | None,
) -> dict:
    order_supplies: list[dict] = []
    order_supplies_total = 0.0
    order_no = ""
    supplies_fields: dict[str, str] = {}

    if not orders_module or not supplies_module:
        return {
            "order_no": order_no,
            "order_supplies": order_supplies,
            "order_supplies_total": order_supplies_total,
            "supplies_fields": supplies_fields,
        }

    order_field_names = [f.get("name", "") for f in (orders_module.fields_schema or []) if isinstance(f, dict)]
    order_field = _find_field_name_by_candidates(order_field_names, ["№ заказа", "номер", "заказ"])
    order_no = str((order_record.data or {}).get(order_field or "", "")).strip() if order_field else ""
    if not order_no:
        return {
            "order_no": order_no,
            "order_supplies": order_supplies,
            "order_supplies_total": order_supplies_total,
            "supplies_fields": supplies_fields,
        }

    supplies_field_names = [f.get("name", "") for f in (supplies_module.fields_schema or []) if isinstance(f, dict)]
    idx_field = _find_field_name_by_candidates(supplies_field_names, ["№ п/п", "номер", "№"])
    date_field = _find_field_name_by_candidates(supplies_field_names, ["Дата покупки", "дата"])
    name_field = _find_field_name_by_candidates(supplies_field_names, ["Наименование расходника", "наименование", "расходник"])
    origin_field = _find_field_name_by_candidates(supplies_field_names, ["Происхождение", "источник", "origin"])
    cost_field = _find_field_name_by_candidates(supplies_field_names, ["Стоимость", "цена", "себестоимость"])
    order_ref_field = _find_field_name_by_candidates(supplies_field_names, ["№ заказа", "номер заказа", "заказ"])

    supplies_fields = {
        "idx": idx_field or "",
        "date": date_field or "",
        "name": name_field or "",
        "origin": origin_field or "",
        "cost": cost_field or "",
        "order_ref": order_ref_field or "",
    }

    supplies_records = (
        await db.execute(
            select(DynamicRecord)
            .where(DynamicRecord.module_slug == "supplies")
            .order_by(DynamicRecord.row_index.desc())
        )
    ).scalars().all()

    for supply_record in supplies_records:
        data = dict(supply_record.data or {})
        origin_text = str(data.get(origin_field or "", "")).strip().lower() if origin_field else ""
        order_ref_text = str(data.get(order_ref_field or "", "")).strip() if order_ref_field else ""
        if order_ref_text != order_no and (not origin_text or order_no.lower() not in origin_text):
            continue

        cost_value = _parse_float(str(data.get(cost_field or "", "0")) if cost_field else "0")
        order_supplies_total += cost_value
        order_supplies.append(
            {
                "row_index": supply_record.row_index,
                "date": str(data.get(date_field or "", "")).strip() if date_field else "",
                "name": str(data.get(name_field or "", "")).strip() if name_field else "",
                "origin": str(data.get(origin_field or "", "")).strip() if origin_field else "",
                "cost": cost_value,
            }
        )

    return {
        "order_no": order_no,
        "order_supplies": order_supplies,
        "order_supplies_total": order_supplies_total,
        "supplies_fields": supplies_fields,
    }


def _status_key(value: str) -> str:
    return (value or "").strip().lower()


def _get_status_field(all_fields: list[str]) -> str | None:
    exact_candidates = {"статус", "статус заказа", "status", "order status"}
    for field in all_fields:
        if field.strip().lower() in exact_candidates:
            return field
    for field in all_fields:
        lowered = field.strip().lower()
        if "статус" in lowered and "оплат" not in lowered:
            return field
    return None


def _normalize_hex_color(value: str) -> str:
    if not value:
        return ""
    raw = value.strip()
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if not re.match(r"^#[0-9a-fA-F]{6}$", raw):
        return ""
    return raw.lower()


def _hex_to_rgba(hex_color: str, alpha: float = 0.16) -> str:
    h = _normalize_hex_color(hex_color)
    if not h:
        return ""
    r = int(h[1:3], 16)
    g = int(h[3:5], 16)
    b = int(h[5:7], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _get_partner_field(all_fields: list[str]) -> str | None:
    exact = {"мастер", "партнер", "партнёр", "specialist", "partner"}
    for field in all_fields:
        if field.strip().lower() in exact:
            return field
    for field in all_fields:
        lowered = field.strip().lower()
        if "мастер" in lowered or "партнер" in lowered or "партн" in lowered:
            return field
    return None


def _get_specialization_field(all_fields: list[str]) -> str | None:
    exact = {"устройство", "тип устройства", "категория", "специализация", "device", "category"}
    for field in all_fields:
        if field.strip().lower() in exact:
            return field
    for field in all_fields:
        lowered = field.strip().lower()
        if "устрой" in lowered or "категор" in lowered or "специал" in lowered:
            return field
    return None


def _status_eq(value: str, expected: str) -> bool:
    return _status_key(value) == _status_key(expected)


def _extract_status_settings(module: ModuleConfig) -> tuple[str | None, list[str], dict[str, str]]:
    all_field_names = [f.get("name", "") for f in (module.fields_schema or []) if isinstance(f, dict)]
    detected_status_field = _get_status_field(all_field_names)
    status_field_obj = None

    for field in (module.fields_schema or []):
        if not isinstance(field, dict):
            continue
        if field.get("name") == detected_status_field:
            status_field_obj = field
            break

    stored_options = status_field_obj.get("status_options") if status_field_obj else None
    options = [str(v).strip() for v in (stored_options or []) if str(v).strip()]
    if not options:
        options = list(DEFAULT_STATUS_OPTIONS)

    status_colors = {k: v for k, v in DEFAULT_STATUS_COLORS.items() if k in options}
    stored_colors = status_field_obj.get("status_colors") if status_field_obj else None
    if isinstance(stored_colors, dict):
        for status_name, color in stored_colors.items():
            if status_name not in options:
                continue
            normalized = _normalize_hex_color(str(color))
            if normalized:
                status_colors[str(status_name)] = normalized

    return detected_status_field, options, status_colors


def _extract_specialization_settings(module: ModuleConfig) -> tuple[str | None, list[str]]:
    field_names = [f.get("name", "") for f in (module.fields_schema or []) if isinstance(f, dict)]
    selected_field = _get_specialization_field(field_names)
    field_obj = None

    for field in (module.fields_schema or []):
        if not isinstance(field, dict):
            continue
        if field.get("specialization_options"):
            selected_field = field.get("name")
            field_obj = field
            break
        if field.get("name") == selected_field:
            field_obj = field

    options = []
    if field_obj and isinstance(field_obj.get("specialization_options"), list):
        options = [str(v).strip() for v in field_obj.get("specialization_options", []) if str(v).strip()]

    return selected_field, options


def _row_bg_for_status(status_value: str, status_colors: dict[str, str]) -> str:
    key = _status_key(status_value)
    for status_name, color in status_colors.items():
        if _status_key(status_name) == key:
            return _hex_to_rgba(color, 0.28)
    return ""


@router.get("/modules/{slug}", response_class=HTMLResponse)
async def module_list(
    request: Request,
    slug: str,
    page: int = 1,
    search: str = "",
    sort: str = "newest",
    date_from: str = "",
    date_to: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404, "Модуль не найден")

    permissions = get_user_permissions(user)
    if not check_module_visible(permissions, slug, user.is_superuser):
        raise HTTPException(403, "Доступ запрещён")

    all_field_names = [f["name"] for f in module.fields_schema]
    status_field, status_options, status_colors = _extract_status_settings(module)
    specialization_field, _ = _extract_specialization_settings(module)
    user_specializations = get_user_specializations(user)
    visible_fields = get_visible_fields(permissions, slug, all_field_names, user.is_superuser)
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)
    visible_fields = _filter_module_visible_fields(slug, visible_fields)
    editable_fields = [field_name for field_name in editable_fields if field_name in visible_fields]
    is_module_readonly = _is_order_derived_module_readonly(slug)
    if is_module_readonly:
        editable_fields = []

    order_main_fields: list[str] = []
    order_detail_fields: list[str] = []
    virtual_order_price_field = ""
    order_total_amount_by_record_id: dict[int, str] = {}
    if slug == "orders":
        order_main_fields, order_detail_fields = _split_order_fields_for_compact_table(visible_fields)
        virtual_order_price_field = "Цена"

    period_from = _parse_date_value(date_from) if slug == "orders" else None
    period_to = _parse_date_value(date_to) if slug == "orders" else None
    if period_from and period_to and period_from > period_to:
        period_from, period_to = period_to, period_from
    period_field = _resolve_orders_period_field(all_field_names) if slug == "orders" else None
    has_period_filter = bool(period_from or period_to)

    per_page = 25
    offset = (page - 1) * per_page

    if sort not in SORT_OPTIONS:
        sort = "newest"

    if is_module_readonly:
        in_memory_records = await _build_derived_records_from_orders(db, module)
        if search:
            in_memory_records = [
                record
                for record in in_memory_records
                if _record_matches_search(dict(getattr(record, "data", {}) or {}), search)
            ]
        if has_period_filter:
            in_memory_records = [
                record
                for record in in_memory_records
                if _record_matches_period(record, period_field, period_from, period_to)
            ]
        total = len(in_memory_records)
        records = _sort_in_memory_records(in_memory_records, sort)[offset: offset + per_page]
    else:
        # Base query
        stmt = select(DynamicRecord).where(DynamicRecord.module_slug == slug)

        # Search filter
        if search:
            stmt = stmt.where(DynamicRecord.data.cast(str).ilike(f"%{search}%"))

        # Partner specialization filter: show only own partner orders.
        if not user.is_superuser and specialization_field and user_specializations:
            lowered_specializations = [s.lower() for s in user_specializations]
            partner_expr = func.lower(func.coalesce(cast(DynamicRecord.data[specialization_field], String), ""))
            stmt = stmt.where(
                or_(*[partner_expr.like(f"%{spec}%") for spec in lowered_specializations])
            )

        partner_issued_priority = case(
            (func.lower(func.coalesce(cast(DynamicRecord.data["__partner_issued__"], String), "false")).like("%true%"), 1),
            else_=0,
        ).desc()

        # Paginated data
        if sort == "oldest":
            stmt = stmt.order_by(partner_issued_priority, DynamicRecord.row_index.asc())
        elif sort == "updated_desc":
            stmt = stmt.order_by(partner_issued_priority, DynamicRecord.updated_at.desc(), DynamicRecord.row_index.desc())
        elif sort == "updated_asc":
            stmt = stmt.order_by(partner_issued_priority, DynamicRecord.updated_at.asc(), DynamicRecord.row_index.asc())
        else:
            stmt = stmt.order_by(partner_issued_priority, DynamicRecord.row_index.desc())

        if has_period_filter:
            all_records = (await db.execute(stmt)).scalars().all()
            filtered_records = [
                record
                for record in all_records
                if _record_matches_period(record, period_field, period_from, period_to)
            ]
            total = len(filtered_records)
            records = filtered_records[offset: offset + per_page]
        else:
            # Count
            count_stmt = select(func.count()).select_from(
                stmt.subquery()
            )
            total = (await db.execute(count_stmt)).scalar() or 0

            stmt = stmt.offset(offset).limit(per_page)
            records = (await db.execute(stmt)).scalars().all()

    if slug == "orders" and virtual_order_price_field:
        order_no_field = _find_field_name_by_candidates(all_field_names, ["№ заказа", "номер заказа", "номер", "заказ", "order_no"])
        order_total_amount_by_record_id = await _build_order_total_amount_by_record_id(db, list(records), order_no_field)

        for record in records:
            record_id = int(getattr(record, "id", 0) or 0)
            if not record_id or order_total_amount_by_record_id.get(record_id):
                continue
            fallback_price = _extract_price_from_row_data(dict(getattr(record, "data", {}) or {}))
            if fallback_price:
                order_total_amount_by_record_id[record_id] = fallback_price

    total_pages = max(1, (total + per_page - 1) // per_page)
    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    allow_sync_buttons = bool(user.is_superuser and not _is_sync_hidden_for_module(slug))
    allow_add_button = not is_module_readonly

    ctx = {
        "request": request,
        "user": user,
        "module": module,
        "modules": modules,
        "records": records,
        "visible_fields": visible_fields,
        "editable_fields": editable_fields,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "search": search,
        "sort": sort,
        "date_from": period_from.isoformat() if period_from else "",
        "date_to": period_to.isoformat() if period_to else "",
        "status_field": status_field,
        "status_options": status_options,
        "status_colors": status_colors,
        "row_bg_for_status": lambda value: _row_bg_for_status(value, status_colors),
        "partner_issued_field": "__partner_issued__",
        "order_main_fields": order_main_fields,
        "order_detail_fields": order_detail_fields,
        "virtual_order_price_field": virtual_order_price_field,
        "order_total_amount_by_record_id": order_total_amount_by_record_id,
        "is_module_readonly": is_module_readonly,
        "allow_sync_buttons": allow_sync_buttons,
        "allow_add_button": allow_add_button,
        "show_partner_issued_column": slug == "orders",
    }

    # HTMX partial
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/module_table.html", ctx)

    return templates.TemplateResponse("module_list.html", ctx)


@router.get("/modules/{slug}/record/{record_id}", response_class=HTMLResponse)
async def record_detail(
    request: Request,
    slug: str,
    record_id: int,
    panel: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)

    permissions = get_user_permissions(user)
    if not check_module_visible(permissions, slug, user.is_superuser):
        raise HTTPException(403)
    if _is_order_derived_module_readonly(slug):
        raise HTTPException(403, "Модуль доступен только для просмотра")

    result = await db.execute(
        select(DynamicRecord).where(DynamicRecord.id == record_id, DynamicRecord.module_slug == slug)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404)

    all_field_names = [f["name"] for f in module.fields_schema]
    status_field, status_options, _ = _extract_status_settings(module)
    visible_fields = get_visible_fields(permissions, slug, all_field_names, user.is_superuser)
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)
    visible_fields = _filter_module_visible_fields(slug, visible_fields)
    editable_fields = [field_name for field_name in editable_fields if field_name in visible_fields]
    field_input_types = _resolve_field_input_types(module, visible_fields)

    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    order_supplies: list[dict] = []
    order_supplies_total = 0.0
    order_no = ""
    supplies_fields: dict[str, str] = {}

    if slug == "orders":
        supplies_module = await get_module_by_slug(db, "supplies")
        supplies_ctx = await _get_order_supplies_context(db, record, module, supplies_module)
        order_no = supplies_ctx["order_no"]
        order_supplies = supplies_ctx["order_supplies"]
        order_supplies_total = supplies_ctx["order_supplies_total"]
        supplies_fields = supplies_ctx["supplies_fields"]

    template_name = "record_edit_drawer.html" if str(panel or "").strip().lower() == "drawer" else "record_edit_modal.html"

    return templates.TemplateResponse(template_name, {
        "request": request,
        "user": user,
        "module": module,
        "modules": modules,
        "record": record,
        "visible_fields": visible_fields,
        "editable_fields": editable_fields,
        "status_field": status_field,
        "status_options": status_options,
        "field_input_types": field_input_types,
        "to_date_input_value": _to_date_input_value,
        "order_no": order_no,
        "order_supplies": order_supplies,
        "order_supplies_total": order_supplies_total,
        "supplies_fields": supplies_fields,
    })


@router.post("/modules/{slug}/record/{record_id}", response_class=HTMLResponse)
async def record_update(
    request: Request,
    slug: str,
    record_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)
    if _is_order_derived_module_readonly(slug):
        raise HTTPException(403, "Модуль доступен только для просмотра")

    permissions = get_user_permissions(user)
    all_field_names = [f["name"] for f in module.fields_schema]
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)
    status_field, _, _ = _extract_status_settings(module)

    result = await db.execute(
        select(DynamicRecord).where(DynamicRecord.id == record_id, DynamicRecord.module_slug == slug)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404)

    form_data = await request.form()
    new_data = dict(record.data)
    for field in editable_fields:
        if field in form_data:
            new_data[field] = form_data[field]

    # Once client handoff is confirmed via status, remove temporary partner-issued priority.
    if status_field and status_field in new_data and _status_eq(str(new_data.get(status_field, "")), "Выдан"):
        new_data["__partner_issued__"] = False

    record.data = new_data
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    # Try push to Google Sheets
    try:
        from app.sync_service import push_single_record
        await push_single_record(db, module, record)
    except Exception:
        pass

    if request.headers.get("HX-Request"):
        return HTMLResponse('<div class="alert alert-success">Сохранено</div>', status_code=200)

    return HTMLResponse(status_code=200, headers={"HX-Redirect": f"/modules/{slug}"})


@router.post("/modules/orders/record/{record_id}/supplies", response_class=HTMLResponse)
async def add_supply_to_order(
    request: Request,
    record_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    orders_module = await get_module_by_slug(db, "orders")
    supplies_module = await get_module_by_slug(db, "supplies")
    if not orders_module or not supplies_module:
        raise HTTPException(404, "Модуль не найден")

    permissions = get_user_permissions(user)
    supplies_field_names = [f.get("name", "") for f in (supplies_module.fields_schema or []) if isinstance(f, dict)]
    supplies_editable = get_editable_fields(permissions, "supplies", supplies_field_names, user.is_superuser)
    if not user.is_superuser and not supplies_editable:
        raise HTTPException(403, "Нет доступа к редактированию расходников")

    order_result = await db.execute(
        select(DynamicRecord).where(DynamicRecord.id == record_id, DynamicRecord.module_slug == "orders")
    )
    order_record = order_result.scalar_one_or_none()
    if not order_record:
        raise HTTPException(404, "Заказ не найден")

    order_field_names = [f.get("name", "") for f in (orders_module.fields_schema or []) if isinstance(f, dict)]
    order_number_field = _find_field_name_by_candidates(order_field_names, ["№ заказа", "номер", "заказ"])
    order_no = str((order_record.data or {}).get(order_number_field or "", "")).strip()

    supplies_ctx = await _get_order_supplies_context(db, order_record, orders_module, supplies_module)
    base_partial_ctx = {
        "request": request,
        "record": order_record,
        "module": orders_module,
        **supplies_ctx,
    }

    if not order_no:
        return templates.TemplateResponse(
            "partials/order_supplies_section.html",
            {
                **base_partial_ctx,
                "supply_add_error": "Не найден номер заказа для привязки расходника",
                "supply_add_success": "",
            },
            status_code=400,
        )

    idx_field = _find_field_name_by_candidates(supplies_field_names, ["№ п/п", "номер", "№"])
    date_field = _find_field_name_by_candidates(supplies_field_names, ["Дата покупки", "дата"])
    name_field = _find_field_name_by_candidates(supplies_field_names, ["Наименование расходника", "наименование", "расходник"])
    origin_field = _find_field_name_by_candidates(supplies_field_names, ["Происхождение", "источник", "origin"])
    cost_field = _find_field_name_by_candidates(supplies_field_names, ["Стоимость", "цена", "себестоимость"])
    order_ref_field = _find_field_name_by_candidates(supplies_field_names, ["№ заказа", "номер заказа", "заказ"])

    form = await request.form()
    supply_name = str(form.get("supply_name", "")).strip()
    supply_cost = str(form.get("supply_cost", "")).strip()
    supply_date = str(form.get("supply_date", "")).strip() or date.today().isoformat()
    supply_origin = str(form.get("supply_origin", "")).strip() or f"Заказ {order_no}"

    if not supply_name:
        return templates.TemplateResponse(
            "partials/order_supplies_section.html",
            {
                **base_partial_ctx,
                "supply_add_error": "Укажите наименование расходника",
                "supply_add_success": "",
            },
            status_code=400,
        )

    max_row = (
        await db.execute(select(func.max(DynamicRecord.row_index)).where(DynamicRecord.module_slug == "supplies"))
    ).scalar() or 1
    next_row = max_row + 1

    data: dict[str, str] = {name: "" for name in supplies_field_names}
    if idx_field:
        data[idx_field] = str(next_row)
    if date_field:
        data[date_field] = supply_date
    if name_field:
        data[name_field] = supply_name
    if origin_field:
        data[origin_field] = supply_origin
    if cost_field:
        data[cost_field] = supply_cost
    if order_ref_field:
        data[order_ref_field] = order_no

    supply_record = DynamicRecord(
        module_slug="supplies",
        row_index=next_row,
        data=data,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(supply_record)
    await db.commit()

    try:
        from app.sync_service import push_single_record
        await push_single_record(db, supplies_module, supply_record)
    except Exception:
        pass

    refreshed_supplies_ctx = await _get_order_supplies_context(db, order_record, orders_module, supplies_module)

    return templates.TemplateResponse(
        "partials/order_supplies_section.html",
        {
            "request": request,
            "record": order_record,
            "module": orders_module,
            **refreshed_supplies_ctx,
            "supply_add_error": "",
            "supply_add_success": f"Расходник добавлен и привязан к заказу {order_no}",
        },
        status_code=200,
    )


@router.post("/modules/{slug}/record/{record_id}/field")
async def record_update_single_field(
    slug: str,
    record_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)
    if _is_order_derived_module_readonly(slug):
        raise HTTPException(403, "Модуль доступен только для просмотра")

    permissions = get_user_permissions(user)
    all_field_names = [f["name"] for f in module.fields_schema]
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)

    status_field, _, status_colors = _extract_status_settings(module)
    allowed_meta_fields = {"__partner_issued__"}

    if field not in all_field_names and field not in allowed_meta_fields:
        raise HTTPException(400, "Поле не найдено")
    if field not in editable_fields and field not in allowed_meta_fields:
        raise HTTPException(403, "Поле недоступно для редактирования")

    if status_field and field != status_field and field not in allowed_meta_fields:
        raise HTTPException(400, "Inline-обновление разрешено только для статуса и флага выдачи")

    result = await db.execute(
        select(DynamicRecord).where(DynamicRecord.id == record_id, DynamicRecord.module_slug == slug)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404)

    new_data = dict(record.data)
    if field == "__partner_issued__":
        if not status_field:
            raise HTTPException(400, "Статусное поле не настроено")
        current_status = str(new_data.get(status_field, ""))
        if not (_status_eq(current_status, "Готов") or _status_eq(current_status, "Выдан")):
            raise HTTPException(400, "Флаг выдачи доступен только для статусов 'Готов' и 'Выдан'")
        new_data[field] = str(value).strip().lower() in {"1", "true", "on", "yes"}
    else:
        new_data[field] = value
        if status_field and field == status_field and _status_eq(str(value), "Выдан"):
            new_data["__partner_issued__"] = False
    record.data = new_data
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    # Keep Google Sheets in sync on inline status changes as well.
    try:
        from app.sync_service import push_single_record
        await push_single_record(db, module, record)
    except Exception:
        pass

    return JSONResponse({
        "ok": True,
        "row_bg": _row_bg_for_status(str(new_data.get(status_field, "")), status_colors) if status_field else "",
    })


@router.post("/modules/{slug}/record/new", response_class=HTMLResponse)
async def record_create(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)
    if _is_order_derived_module_readonly(slug):
        raise HTTPException(403, "Модуль доступен только для просмотра")

    permissions = get_user_permissions(user)
    all_field_names = [f["name"] for f in module.fields_schema]
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)

    if not editable_fields:
        raise HTTPException(403)

    # Get max row_index
    max_row = (await db.execute(
        select(func.max(DynamicRecord.row_index)).where(DynamicRecord.module_slug == slug)
    )).scalar() or 1
    next_row = max_row + 1

    form_data = await request.form()
    data = {}
    for field in all_field_names:
        data[field] = form_data.get(field, "")

    record = DynamicRecord(
        module_slug=slug,
        row_index=next_row,
        data=data,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.commit()

    try:
        from app.sync_service import push_single_record, push_module
        await push_single_record(db, module, record)
    except Exception:
        try:
            # Fallback for row append/update mismatches.
            from app.sync_service import push_module
            await push_module(db, module)
        except Exception:
            pass

    if request.headers.get("HX-Request"):
        return HTMLResponse(status_code=200, headers={"HX-Redirect": f"/modules/{slug}"})

    return HTMLResponse(status_code=302, headers={"Location": f"/modules/{slug}"})


@router.get("/modules/{slug}/new", response_class=HTMLResponse)
async def record_new_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    module = await get_module_by_slug(db, slug)
    if not module:
        raise HTTPException(404)
    if _is_order_derived_module_readonly(slug):
        raise HTTPException(403, "Модуль доступен только для просмотра")

    permissions = get_user_permissions(user)
    all_field_names = [f["name"] for f in module.fields_schema]
    status_field, status_options, _ = _extract_status_settings(module)
    visible_fields = get_visible_fields(permissions, slug, all_field_names, user.is_superuser)
    editable_fields = get_editable_fields(permissions, slug, all_field_names, user.is_superuser)
    visible_fields = _filter_module_visible_fields(slug, visible_fields)
    editable_fields = [field_name for field_name in editable_fields if field_name in visible_fields]
    field_input_types = _resolve_field_input_types(module, visible_fields)
    modules = await get_all_modules(db)
    modules = filter_visible_modules_for_user(modules, user)

    return templates.TemplateResponse("record_new_modal.html", {
        "request": request,
        "user": user,
        "module": module,
        "modules": modules,
        "visible_fields": visible_fields,
        "editable_fields": editable_fields,
        "status_field": status_field,
        "status_options": status_options,
        "field_input_types": field_input_types,
    })


@router.delete("/modules/{slug}/record/{record_id}")
async def record_delete(
    slug: str,
    record_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user.is_superuser:
        raise HTTPException(403)
    if _is_order_derived_module_readonly(slug):
        raise HTTPException(403, "Модуль доступен только для просмотра")

    await db.execute(
        sa_delete(DynamicRecord).where(
            DynamicRecord.id == record_id, DynamicRecord.module_slug == slug
        )
    )
    await db.commit()
    return HTMLResponse(status_code=200, headers={"HX-Redirect": f"/modules/{slug}"})

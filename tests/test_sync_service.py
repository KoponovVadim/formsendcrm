import pytest
from sqlalchemy import select
from typing import Any, cast

from _pytest.monkeypatch import MonkeyPatch

from app import sync_service
from app.models import DynamicRecord, ModuleConfig


@pytest.mark.asyncio
async def test_pull_module_replaces_records_skips_blank_rows_and_normalizes_dates(
    db_session: Any,
    monkeypatch: MonkeyPatch,
):
    module = ModuleConfig(
        slug="orders",
        sheet_name="Заказы",
        display_name="Заказы",
        icon="bi-clipboard-check",
        enabled=True,
        fields_schema=[
            {"name": "№ заказа", "type": "TEXT"},
            {"name": "Дата приёма", "type": "DATE"},
            {"name": "Клиент", "type": "TEXT"},
        ],
        sort_order=0,
    )
    db_session.add(module)
    db_session.add(
        DynamicRecord(
            module_slug="orders",
            row_index=2,
            data={"№ заказа": "OLD-1", "Дата приёма": "2026-01-01", "Клиент": "Old"},
        )
    )
    await db_session.commit()

    monkeypatch.setattr(sync_service.settings, "GOOGLE_SHEETS_IMPORT_ENABLED", True)

    def _fake_get_data(sheet_name: str):
        assert sheet_name == "Заказы"
        return [
            {" № заказа ": "ORD-1", "Дата приема": "04.04.2026", "Клиент ": "Alice"},
            {" № заказа ": "", "Дата приема": "", "Клиент ": ""},
            {" № заказа ": "ORD-2", "Дата приема": "not-a-date", "Клиент ": "Bob"},
        ]

    monkeypatch.setattr(sync_service.sheets_adapter, "get_worksheet_data", _fake_get_data)

    pull_module = getattr(sync_service, "pull_module")
    result = cast(dict[str, Any], await pull_module(db_session, module))

    assert result["status"] == "success"
    assert result["records_affected"] == 2

    records_result = await db_session.execute(
        select(DynamicRecord)
        .where(DynamicRecord.module_slug == "orders")
        .order_by(DynamicRecord.row_index.asc())
    )
    records = cast(list[Any], records_result.scalars().all())

    assert len(records) == 2
    assert records[0].row_index == 2
    assert records[0].data.get("№ заказа") == "ORD-1"
    assert records[0].data.get("Дата приёма") == "2026-04-04"
    assert records[1].row_index == 3
    assert records[1].data.get("№ заказа") == "ORD-2"
    assert records[1].data.get("Дата приёма") == ""


@pytest.mark.asyncio
async def test_pull_module_clears_local_rows_when_sheet_is_empty(
    db_session: Any,
    monkeypatch: MonkeyPatch,
):
    module = ModuleConfig(
        slug="orders",
        sheet_name="Заказы",
        display_name="Заказы",
        icon="bi-clipboard-check",
        enabled=True,
        fields_schema=[
            {"name": "№ заказа", "type": "TEXT"},
            {"name": "Дата приёма", "type": "DATE"},
        ],
        sort_order=0,
    )
    db_session.add(module)
    db_session.add(
        DynamicRecord(
            module_slug="orders",
            row_index=2,
            data={"№ заказа": "OLD-1", "Дата приёма": "2026-01-01"},
        )
    )
    await db_session.commit()

    monkeypatch.setattr(sync_service.settings, "GOOGLE_SHEETS_IMPORT_ENABLED", True)

    def _empty_sheet(_sheet: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(sync_service.sheets_adapter, "get_worksheet_data", _empty_sheet)

    pull_module = getattr(sync_service, "pull_module")
    result = cast(dict[str, Any], await pull_module(db_session, module))

    assert result["status"] == "success"
    assert result["records_affected"] == 0

    records = cast(
        list[DynamicRecord],
        (await db_session.execute(select(DynamicRecord).where(DynamicRecord.module_slug == "orders"))).scalars().all(),
    )
    assert records == []

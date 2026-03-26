"""
Google Sheets adapter using gspread.
Credentials are loaded from GOOGLE_CREDENTIALS_JSON env var (path to JSON file or JSON string).
"""
import json
import os
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_client: Optional[object] = None


def _column_label(index_1_based: int) -> str:
    """Convert 1-based column index to Excel letters (1->A, 27->AA)."""
    if index_1_based <= 0:
        return "A"
    value = index_1_based
    letters = []
    while value > 0:
        value, rem = divmod(value - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def _get_client():
    global _client
    if _client is not None:
        return _client

    if not GSPREAD_AVAILABLE:
        logger.warning("gspread not installed, Google Sheets sync disabled")
        return None

    creds_env = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_env:
        logger.warning("GOOGLE_CREDENTIALS_JSON not set, Google Sheets sync disabled")
        return None

    # Try as file path first, then as raw JSON
    if os.path.isfile(creds_env):
        credentials = Credentials.from_service_account_file(creds_env, scopes=SCOPES)
    else:
        try:
            info = json.loads(creds_env)
            credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
        except json.JSONDecodeError:
            logger.error("GOOGLE_CREDENTIALS_JSON is neither a valid file path nor valid JSON")
            return None

    _client = gspread.authorize(credentials)
    return _client


def _get_spreadsheet():
    client = _get_client()
    if client is None:
        return None
    spreadsheet_id = os.getenv("SPREADSHEET_ID", "")
    if not spreadsheet_id:
        logger.warning("SPREADSHEET_ID not set")
        return None
    return client.open_by_key(spreadsheet_id)


def _retry(func, retries=3, delay=2):
    """Simple retry for API rate limiting."""
    for attempt in range(retries):
        try:
            return func()
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"API call failed (attempt {attempt+1}): {e}, retrying in {delay}s")
                time.sleep(delay)
                delay *= 2
            else:
                raise


def get_all_sheet_names() -> list[str]:
    ss = _get_spreadsheet()
    if ss is None:
        return []
    worksheets = _retry(lambda: ss.worksheets())
    return [ws.title for ws in worksheets]


def get_headers(sheet_name: str) -> list[str]:
    ss = _get_spreadsheet()
    if ss is None:
        return []
    ws = _retry(lambda: ss.worksheet(sheet_name))
    row = _retry(lambda: ws.row_values(1))
    return row


def get_worksheet_data(sheet_name: str) -> list[dict]:
    """Fetch all rows from a worksheet as list of dicts."""
    ss = _get_spreadsheet()
    if ss is None:
        return []
    ws = _retry(lambda: ss.worksheet(sheet_name))
    records = _retry(lambda: ws.get_all_records())
    return records


def update_worksheet_data(sheet_name: str, records: list[dict], headers: list[str] | None = None):
    """Overwrite worksheet with records, preserving and extending headers when needed."""
    ss = _get_spreadsheet()
    if ss is None:
        return
    ws = _retry(lambda: ss.worksheet(sheet_name))

    sheet_headers = [str(h).strip() for h in _retry(lambda: ws.row_values(1)) if str(h).strip()]
    desired_headers = [str(h).strip() for h in (headers or []) if str(h).strip()]

    if not desired_headers:
        desired_headers = list(sheet_headers)

    for rec in records:
        if not isinstance(rec, dict):
            continue
        for key in rec.keys():
            normalized_key = str(key).strip()
            if normalized_key and normalized_key not in desired_headers:
                desired_headers.append(normalized_key)

    if not desired_headers:
        return

    max_col = _column_label(len(desired_headers))

    # Resize and write header row to keep column order deterministic.
    _retry(lambda: ws.resize(rows=max(1, len(records) + 1), cols=max(1, len(desired_headers))))
    _retry(lambda: ws.update(f"A1:{max_col}1", [desired_headers]))

    if not records:
        return

    rows = []
    for rec in records:
        src = rec if isinstance(rec, dict) else {}
        row = [str(src.get(h, "")) for h in desired_headers]
        rows.append(row)

    cell_range = f"A2:{max_col}{len(records) + 1}"
    _retry(lambda: ws.update(cell_range, rows))


def update_single_row(sheet_name: str, row_index: int, data: dict, headers: list[str] | None = None):
    """Update a single row (1-based, row 1 = header), extending headers safely."""
    ss = _get_spreadsheet()
    if ss is None:
        return
    ws = _retry(lambda: ss.worksheet(sheet_name))
    sheet_headers = [str(h).strip() for h in _retry(lambda: ws.row_values(1)) if str(h).strip()]
    desired_headers = [str(h).strip() for h in (headers or []) if str(h).strip()]
    if not desired_headers:
        desired_headers = list(sheet_headers)

    source = data if isinstance(data, dict) else {}
    for key in source.keys():
        normalized_key = str(key).strip()
        if normalized_key and normalized_key not in desired_headers:
            desired_headers.append(normalized_key)

    if not desired_headers:
        return

    max_col = _column_label(len(desired_headers))
    _retry(lambda: ws.resize(rows=max(row_index, 1), cols=max(1, len(desired_headers))))
    _retry(lambda: ws.update(f"A1:{max_col}1", [desired_headers]))

    row_values = [str(source.get(h, "")) for h in desired_headers]
    cell_range = f"A{row_index}:{max_col}{row_index}"
    _retry(lambda: ws.update(cell_range, [row_values]))

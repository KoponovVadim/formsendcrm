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


def update_worksheet_data(sheet_name: str, records: list[dict]):
    """Overwrite worksheet with records (preserving headers)."""
    ss = _get_spreadsheet()
    if ss is None:
        return
    ws = _retry(lambda: ss.worksheet(sheet_name))
    headers = _retry(lambda: ws.row_values(1))
    if not headers:
        return

    # Clear data rows (keep header row)
    _retry(lambda: ws.resize(rows=1))
    _retry(lambda: ws.resize(rows=len(records) + 1))

    if not records:
        return

    rows = []
    for rec in records:
        row = [str(rec.get(h, "")) for h in headers]
        rows.append(row)

    cell_range = f"A2:{chr(64 + len(headers))}{len(records) + 1}"
    _retry(lambda: ws.update(cell_range, rows))


def update_single_row(sheet_name: str, row_index: int, data: dict):
    """Update a single row (1-based, row 1 = header)."""
    ss = _get_spreadsheet()
    if ss is None:
        return
    ws = _retry(lambda: ss.worksheet(sheet_name))
    headers = _retry(lambda: ws.row_values(1))
    row_values = [str(data.get(h, "")) for h in headers]
    cell_range = f"A{row_index}:{chr(64 + len(headers))}{row_index}"
    _retry(lambda: ws.update(cell_range, [row_values]))

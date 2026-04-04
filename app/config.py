import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root even if service is started from another cwd (common on VDS/systemd).
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv()


class Settings:
    PROJECT_NAME: str = "CRM System"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///./crm.db"
    )

    GOOGLE_CREDENTIALS_JSON: str = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    SPREADSHEET_ID: str = os.getenv("SPREADSHEET_ID", "")
    GOOGLE_SHEETS_IMPORT_ENABLED: bool = os.getenv("GOOGLE_SHEETS_IMPORT_ENABLED", "true").lower() == "true"
    FIREBASE_CREDENTIALS_JSON: str = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

    FIRST_SUPERUSER_EMAIL: str = os.getenv("FIRST_SUPERUSER_EMAIL", "admin@admin.com")
    FIRST_SUPERUSER_PASSWORD: str = os.getenv("FIRST_SUPERUSER_PASSWORD", "admin")


settings = Settings()

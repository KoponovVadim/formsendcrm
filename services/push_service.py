import json
import logging
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, messaging

from app.config import settings

logger = logging.getLogger(__name__)


class PushService:
    """Firebase push facade. Works as no-op when Firebase is not configured."""

    def __init__(self) -> None:
        self.enabled = False
        self._firebase_app = None
        self._init_error = "firebase_not_configured"

        raw_credentials = str(getattr(settings, "FIREBASE_CREDENTIALS_JSON", "") or "").strip()
        if not raw_credentials:
            return

        try:
            cred = self._build_credentials(raw_credentials)
            app_name = "formsendcrm-push"

            try:
                self._firebase_app = firebase_admin.get_app(app_name)
            except ValueError:
                self._firebase_app = firebase_admin.initialize_app(cred, name=app_name)

            self.enabled = True
            self._init_error = ""
        except Exception as exc:
            self._init_error = "firebase_init_failed"
            logger.exception("Firebase init failed: %s", exc)

    def _build_credentials(self, raw_credentials: str):
        # Supports either inline JSON string or a file path.
        if raw_credentials.startswith("{"):
            return credentials.Certificate(json.loads(raw_credentials))

        path = Path(raw_credentials)
        if not path.is_absolute():
            path = Path.cwd() / path

        return credentials.Certificate(str(path))

    async def notify(self, token: str, title: str, body: str, data: dict | None = None) -> dict:
        token = (token or "").strip()
        if not token:
            return {"ok": False, "reason": "invalid_token"}

        if not self.enabled:
            logger.info("Push skipped (Firebase not configured): %s", title)
            return {"ok": False, "reason": self._init_error or "firebase_not_configured"}

        payload_data = {str(k): str(v) for k, v in (data or {}).items()}

        try:
            message = messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
                data=payload_data,
            )
            message_id = messaging.send(message, app=self._firebase_app)
            return {"ok": True, "message_id": message_id}
        except Exception as exc:
            logger.exception("Push send failed: %s", exc)
            return {"ok": False, "reason": "send_failed", "error": str(exc)}


push_service = PushService()

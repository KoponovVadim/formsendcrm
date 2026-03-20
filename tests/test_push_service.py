from services.push_service import PushService


async def test_push_service_returns_invalid_token_on_empty_token():
    svc = PushService()
    result = await svc.notify("", "Title", "Body", {"k": "v"})

    assert result["ok"] is False
    assert result["reason"] == "invalid_token"


async def test_push_service_returns_not_configured_without_firebase_credentials():
    svc = PushService()
    result = await svc.notify("token123", "Title", "Body", {"k": "v"})

    assert result["ok"] is False
    assert result["reason"] in {"firebase_not_configured", "firebase_init_failed"}

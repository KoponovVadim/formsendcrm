from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/mobile", tags=["mobile"])


@router.get("/deeplink/chat/{chat_id}")
async def mobile_chat_deeplink(chat_id: int):
    return RedirectResponse(url=f"/chat/{chat_id}", status_code=302)


@router.get("/deeplink/order/{order_id}")
async def mobile_order_deeplink(order_id: int):
    return RedirectResponse(url=f"/order/{order_id}", status_code=302)


@router.get("/bootstrap")
async def mobile_bootstrap(base_url: str = ""):
    return {
        "platform": "capacitor",
        "webview_url": base_url,
        "deeplinks": ["/chat/{id}", "/order/{id}"],
        "ux": {
            "quick_chat": True,
            "new_order_fab": True,
            "reduced_clicks": True,
        },
    }

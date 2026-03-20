from core.enums import ClientStage, ServiceCategory


class AILeadService:
    """Simple deterministic AI layer placeholder. Replace with LLM provider when needed."""

    def classify_message(self, text: str) -> dict:
        low = (text or "").lower()
        category = ServiceCategory.REPAIR.value
        stage = ClientStage.NEW.value

        if any(word in low for word in ["лендинг", "сайт", "frontend", "backend"]):
            category = ServiceCategory.WEB.value
        elif any(word in low for word in ["печать", "визитк", "баннер"]):
            category = ServiceCategory.PRINT.value
        elif any(word in low for word in ["лиды", "реклама", "таргет", "маркетинг"]):
            category = ServiceCategory.MARKETING.value

        if any(word in low for word in ["срок", "цена", "оплата", "оформить"]):
            stage = ClientStage.QUALIFIED.value
        if any(word in low for word in ["заказ", "договор", "оплачу", "беру"]):
            stage = ClientStage.WON.value

        return {
            "service_category": category,
            "client_stage": stage,
            "intent": "create_order" if stage in {ClientStage.QUALIFIED.value, ClientStage.WON.value} else "info",
        }


ai_lead_service = AILeadService()

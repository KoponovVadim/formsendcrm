from services.pricing_service import calculate_total_with_breakdown


def test_pricing_service_mixed_fields_breakdown():
    schema = {
        "fields": [
            {"name": "hours", "label": "Часы", "type": "number", "coefficient": 100},
            {"name": "urgent", "label": "Срочно", "type": "boolean", "true_price": 50},
            {
                "name": "tier",
                "label": "Тариф",
                "type": "select",
                "options": [
                    {"value": "base", "price": 0, "multiplier": 1},
                    {"value": "pro", "price": 20, "multiplier": 1.5},
                ],
            },
        ]
    }

    total, breakdown = calculate_total_with_breakdown(
        100,
        schema,
        {"hours": 2, "urgent": True, "tier": "pro"},
    )

    assert round(total, 2) == 555.00
    assert breakdown[0]["type"] == "base_price"
    assert any(item["type"] == "add" for item in breakdown)
    assert any(item["type"] == "boolean" for item in breakdown)
    assert any(item["type"] == "select" for item in breakdown)


def test_pricing_service_minimum_and_maximum_limits():
    schema_min = {"minimum_total": 300, "fields": []}
    total_min, breakdown_min = calculate_total_with_breakdown(100, schema_min, {})
    assert total_min == 300
    assert breakdown_min[-1]["type"] == "minimum_total"

    schema_max = {"maximum_total": 150, "fields": []}
    total_max, breakdown_max = calculate_total_with_breakdown(500, schema_max, {})
    assert total_max == 150
    assert breakdown_max[-1]["type"] == "maximum_total"


def test_pricing_service_commission_fixed_mode():
    schema = {
        "fields": [{"name": "hours", "type": "number", "coefficient": 50}],
        "commission": {"enabled": True, "mode": "fixed", "value": 120},
    }

    total, breakdown = calculate_total_with_breakdown(100, schema, {"hours": 2})

    assert total == 320
    assert breakdown[-1]["type"] == "commission"
    assert breakdown[-1]["mode"] == "fixed"
    assert breakdown[-1]["delta"] == 120


def test_pricing_service_commission_percent_mode():
    schema = {
        "fields": [{"name": "hours", "type": "number", "coefficient": 50}],
        "commission": {"enabled": True, "mode": "percent", "value": 10},
    }

    total, breakdown = calculate_total_with_breakdown(100, schema, {"hours": 2})

    # Partner subtotal: 100 + 2*50 = 200; commission 10% => 20; total => 220.
    assert total == 220
    assert breakdown[-1]["type"] == "commission"
    assert breakdown[-1]["mode"] == "percent"
    assert breakdown[-1]["delta"] == 20


def test_pricing_service_supports_simplified_manager_schema():
    schema = {
        "fields": [
            {"name": "hours", "label": "Часы", "type": "number", "price": 100},
            {"name": "urgent", "label": "Срочно", "type": "boolean", "price": 50},
            {
                "name": "tier",
                "label": "Тариф",
                "type": "select",
                "choices": {
                    "base": 0,
                    "pro": 200,
                },
            },
        ]
    }

    total, breakdown = calculate_total_with_breakdown(100, schema, {"hours": 2, "urgent": True, "tier": "pro"})

    assert total == 550
    assert any(item["type"] == "add" and item.get("field") == "hours" for item in breakdown)
    assert any(item["type"] == "boolean" and item.get("field") == "urgent" for item in breakdown)
    assert any(item["type"] == "select" and item.get("field") == "tier" for item in breakdown)


def test_pricing_service_uses_point_specific_base_price():
    schema = {
        "point_prices": {
            "Точка А": 1500,
            "Точка Б": 2300,
        },
        "fields": [
            {"name": "hours", "type": "number", "coefficient": 100},
        ],
    }

    total, breakdown = calculate_total_with_breakdown(1000, schema, {"__point": "Точка Б", "hours": 1})

    assert total == 2400
    assert breakdown[0]["type"] == "base_price"
    assert breakdown[0]["value"] == 2300
    assert breakdown[0]["point"] == "Точка Б"

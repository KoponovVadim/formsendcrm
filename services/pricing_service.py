from __future__ import annotations


def as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _normalize_select_options(field: dict) -> list[dict]:
    raw_options = field.get("options")
    if isinstance(raw_options, list):
        return raw_options

    raw_choices = field.get("choices")
    options: list[dict] = []

    if isinstance(raw_choices, dict):
        for key, value in raw_choices.items():
            options.append({"value": str(key), "price": as_float(value, 0.0)})
        return options

    if isinstance(raw_choices, list):
        for item in raw_choices:
            if isinstance(item, dict):
                option_value = item.get("value", item.get("name", ""))
                options.append(
                    {
                        "value": str(option_value),
                        "price": as_float(item.get("price", 0), 0.0),
                        "multiplier": as_float(item.get("multiplier", 1), 1.0),
                        "label": item.get("label", item.get("name", option_value)),
                    }
                )
            else:
                options.append({"value": str(item), "price": 0.0})
        return options

    return []


def _normalize_field(field: dict) -> dict:
    normalized = dict(field)
    field_type = str(normalized.get("type", normalized.get("kind", "number"))).strip().lower()
    normalized["type"] = field_type

    if "label" not in normalized and "name" in normalized:
        normalized["label"] = normalized.get("name")

    if field_type in {"number", "int", "float"}:
        if "coefficient" not in normalized and "price" in normalized:
            normalized["coefficient"] = normalized.get("price")
        normalized.setdefault("coefficient", 0)
        normalized.setdefault("offset", 0)
        normalized.setdefault("operation", "add")

    elif field_type in {"boolean", "bool", "checkbox"}:
        if "true_price" not in normalized and "price" in normalized:
            normalized["true_price"] = normalized.get("price")
        normalized.setdefault("true_price", 0)
        normalized.setdefault("false_price", 0)

    elif field_type in {"select", "enum", "choice"}:
        normalized["options"] = _normalize_select_options(normalized)

    return normalized


def _normalize_schema(schema: dict | None) -> dict:
    if not isinstance(schema, dict):
        return {"fields": []}

    fields = schema.get("fields", [])
    if not isinstance(fields, list):
        fields = []

    normalized_fields: list[dict] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        normalized_fields.append(_normalize_field(field))

    normalized_schema = dict(schema)
    normalized_schema["fields"] = normalized_fields
    return normalized_schema


def _extract_selected_point(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in ("__point", "point", "location", "branch"):
        value = payload.get(key)
        if value is None:
            continue
        point = str(value).strip()
        if point:
            return point
    return ""


def _resolve_base_price_for_point(base_price: float, schema: dict, payload: dict | None) -> tuple[float, str]:
    selected_point = _extract_selected_point(payload)
    if not selected_point:
        return float(base_price or 0), ""

    point_prices = schema.get("point_prices")
    if not isinstance(point_prices, dict):
        return float(base_price or 0), selected_point

    if selected_point not in point_prices:
        return float(base_price or 0), selected_point

    return as_float(point_prices.get(selected_point), float(base_price or 0)), selected_point


def _apply_commission(total: float, schema: dict | None, breakdown: list[dict]) -> float:
    commission = (schema or {}).get("commission")
    if not isinstance(commission, dict):
        return total

    enabled = as_bool(commission.get("enabled", True))
    if not enabled:
        return total

    mode = str(commission.get("mode", "fixed")).strip().lower()
    raw_value = as_float(commission.get("value", 0), 0.0)
    label = str(commission.get("label", "Commission"))

    delta = 0.0
    if mode == "percent":
        # Commission percent is applied to the already computed partner subtotal.
        delta = total * (raw_value / 100.0)
    else:
        # Default mode is fixed amount in service currency.
        mode = "fixed"
        delta = raw_value

    total += delta
    breakdown.append(
        {
            "type": "commission",
            "label": label,
            "mode": mode,
            "value": raw_value,
            "delta": delta,
            "total_after": total,
        }
    )
    return total


def calculate_total_with_breakdown(base_price: float, schema: dict | None, payload: dict | None) -> tuple[float, list[dict]]:
    schema = _normalize_schema(schema)
    resolved_base_price, selected_point = _resolve_base_price_for_point(base_price, schema, payload)
    total = float(resolved_base_price or 0)
    breakdown: list[dict] = [
        {
            "type": "base_price",
            "label": "Base price",
            "value": float(resolved_base_price or 0),
            "delta": float(resolved_base_price or 0),
            "point": selected_point,
        }
    ]

    schema_fields = (schema or {}).get("fields", [])
    data = payload or {}

    for field in schema_fields:
        if not isinstance(field, dict):
            continue

        name = str(field.get("name", "")).strip()
        if not name:
            continue

        field_type = str(field.get("type", "number")).strip().lower()
        operation = str(field.get("operation", "add")).strip().lower()
        raw_value = data.get(name, field.get("default", 0))

        if field_type in {"number", "int", "float"}:
            value = as_float(raw_value, 0.0)
            coefficient = as_float(field.get("coefficient", 0), 0.0)
            offset = as_float(field.get("offset", 0), 0.0)
            delta = value * coefficient + offset

            if operation == "multiply":
                factor = max(delta, 0)
                total *= factor
                breakdown.append(
                    {
                        "type": "multiply",
                        "field": name,
                        "label": field.get("label", name),
                        "value": value,
                        "coefficient": coefficient,
                        "offset": offset,
                        "factor": factor,
                        "total_after": total,
                    }
                )
            else:
                total += delta
                breakdown.append(
                    {
                        "type": "add",
                        "field": name,
                        "label": field.get("label", name),
                        "value": value,
                        "coefficient": coefficient,
                        "offset": offset,
                        "delta": delta,
                        "total_after": total,
                    }
                )

        elif field_type in {"boolean", "bool", "checkbox"}:
            checked = as_bool(raw_value)
            if checked:
                delta = as_float(field.get("true_price", field.get("coefficient", 0)), 0.0)
                total += delta
            else:
                delta = as_float(field.get("false_price", 0), 0.0)
                total += delta

            breakdown.append(
                {
                    "type": "boolean",
                    "field": name,
                    "label": field.get("label", name),
                    "value": checked,
                    "delta": delta,
                    "total_after": total,
                }
            )

        elif field_type in {"select", "enum", "choice"}:
            selected_value = str(raw_value)
            for option in field.get("options", []):
                if not isinstance(option, dict):
                    continue

                option_value = str(option.get("value", option.get("name", "")))
                if option_value != selected_value:
                    continue

                price_delta = as_float(option.get("price", 0), 0.0)
                total += price_delta
                multiplier = max(as_float(option.get("multiplier", 1), 1.0), 0)
                total *= multiplier
                breakdown.append(
                    {
                        "type": "select",
                        "field": name,
                        "label": field.get("label", name),
                        "value": selected_value,
                        "option_label": option.get("label", option.get("name", selected_value)),
                        "price_delta": price_delta,
                        "multiplier": multiplier,
                        "total_after": total,
                    }
                )
                break

    minimum_total = (schema or {}).get("minimum_total")
    maximum_total = (schema or {}).get("maximum_total")
    if minimum_total is not None:
        min_value = as_float(minimum_total, total)
        if total < min_value:
            total = min_value
            breakdown.append({"type": "minimum_total", "value": min_value, "total_after": total})
    if maximum_total is not None:
        max_value = as_float(maximum_total, total)
        if total > max_value:
            total = max_value
            breakdown.append({"type": "maximum_total", "value": max_value, "total_after": total})

    total = _apply_commission(total, schema, breakdown)

    return total, breakdown

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
    total = float(base_price or 0)
    breakdown: list[dict] = [
        {
            "type": "base_price",
            "label": "Base price",
            "value": float(base_price or 0),
            "delta": float(base_price or 0),
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

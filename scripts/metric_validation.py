"""Validate daily metric responses without changing the original payload."""

import math
from datetime import UTC, date, datetime, timedelta

SCALAR_METRICS = (
    "averageMinutesPerPlayer",
    "peakCCU",
    "favorites",
    "minutesPlayed",
    "recommendations",
    "plays",
    "uniquePlayers",
)


def validate_metrics(payload: object, start: date, end: date) -> dict:
    expected = {
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days)
    }
    errors = []
    coverage = {}
    if not isinstance(payload, dict):
        return {
            "status": "validation_failed",
            "errors": ["Expected a JSON object."],
            "metrics": {},
        }
    for metric in (*SCALAR_METRICS, "retention"):
        fields = ("d1", "d7") if metric == "retention" else ("value",)
        seen = set()
        counts = {
            field: {"numeric_days": [], "null_days": [], "missing_value_days": []}
            for field in fields
        }
        rows = payload.get(metric)
        present = metric in payload
        if present and not isinstance(rows, list):
            errors.append(f"{metric}: expected an array.")
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            label = f"{metric}[{index}]"
            if not isinstance(row, dict):
                errors.append(f"{label}: expected an object.")
                continue
            try:
                timestamp = datetime.fromisoformat(row["timestamp"])
                if timestamp.utcoffset() != timedelta(0):
                    raise ValueError("Timestamp must explicitly use UTC.")
                if timestamp.timetz().replace(tzinfo=None).isoformat() != "00:00:00":
                    raise ValueError("Daily timestamp must be midnight UTC.")
                day = timestamp.astimezone(UTC).date().isoformat()
                if day not in expected:
                    raise ValueError("Timestamp is outside the requested period.")
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(f"{label}: invalid timestamp ({exc}).")
                continue
            if day in seen:
                errors.append(f"{label}: duplicate timestamp for {day}.")
                continue
            seen.add(day)
            for field in fields:
                if field not in row:
                    counts[field]["missing_value_days"].append(day)
                    continue
                value = row[field]
                if value is None:
                    counts[field]["null_days"].append(day)
                elif (
                    type(value) not in (int, float)
                    or (type(value) is float and not math.isfinite(value))
                    or value < 0
                ):
                    errors.append(
                        f"{label}.{field}: expected a finite nonnegative number or null."
                    )
                else:
                    counts[field]["numeric_days"].append(day)
        for field in fields:
            name = f"retention.{field}" if metric == "retention" else metric
            coverage[name] = {
                "field_present": present,
                "expected_days": len(expected),
                "observed_days": sorted(seen),
                "missing_bucket_days": sorted(expected - seen),
                **{key: sorted(value) for key, value in counts[field].items()},
            }
    incomplete = any(
        m["missing_bucket_days"] or m["missing_value_days"] for m in coverage.values()
    )
    return {
        "status": "validation_failed"
        if errors
        else "incomplete"
        if incomplete
        else "ok",
        "errors": errors,
        "unexpected_metrics": sorted(set(payload) - {*SCALAR_METRICS, "retention"}),
        "metrics": coverage,
    }

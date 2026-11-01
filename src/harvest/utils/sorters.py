from datetime import datetime, timezone


def parse_timestamp(value):
    """Accept existing naive UTC values and new ISO 8601 offsets."""
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def date_sort(obj, key, reverse=False):
    return sorted(obj, key=lambda item: parse_timestamp(item[key]), reverse=reverse)

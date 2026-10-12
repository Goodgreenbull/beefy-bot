"""Public diagnostics without provider URLs, credentials, or chat identifiers."""

from datetime import datetime, timezone
import re


def log_feed_error(name, error, credentials=()):
    message = str(error)
    for credential in credentials:
        if credential:
            message = message.replace(credential, "[redacted]")
    message = re.sub(r"https?://\S+", "[provider URL]", message)
    message = re.sub(r"[A-Za-z0-9_:/+\-]{32,}", "[redacted]", message)
    print(f"Scanner feed {name}: {type(error).__name__}: {message[:220]}", flush=True)


def error_category(error):
    text = str(error).lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    if "timeout" in text:
        return "timeout"
    if "chat not found" in text:
        return "destination_not_found"
    if "forbidden" in text or "blocked" in text:
        return "permission_denied"
    return "upstream_error"


def public_diagnostics(status, interval_seconds, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(status.get("last_cycle_at") or "")
        age = max(0, (now - last).total_seconds())
    except (ValueError, TypeError):
        age = None
    feeds = status.get("feeds", [])
    errors = {item["feed_name"]: error_category(item["last_error"])
              for item in feeds if item.get("last_error")}
    telegram = [item for item in feeds if item.get("feed_name", "").startswith("telegram-")]
    return {
        "scanner_cycle_fresh": age is not None and age <= max(180, interval_seconds * 3),
        "cycle_age_seconds": round(age) if age is not None else None,
        "feed_errors": errors,
        "telegram_delivery": ("error" if any(item.get("last_error") for item in telegram)
                              else "healthy" if any(item.get("last_success_at") for item in telegram)
                              else "pending"),
        "telegram_last_success_at": max((item["last_success_at"] for item in telegram
                                          if item.get("last_success_at")), default=None),
    }

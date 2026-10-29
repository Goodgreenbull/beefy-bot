"""Conservative delivery trial; preserves the legacy score for comparison.

These are explicit trial gates, not weights fitted to a winner dataset.
"""

from dataclasses import replace
from datetime import datetime, timezone
import math
from urllib.parse import urlsplit

from .models import MarketSnapshot, ScoreResult


POLICY_VERSION = "evidence-trial-v1"
MAX_DATA_AGE_SECONDS = 300


def _age(value, now):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            return None
        return (now - stamp).total_seconds()
    except (TypeError, ValueError):
        return None


def _fresh(value, now):
    age = _age(value, now)
    return age is not None and -15 <= age <= MAX_DATA_AGE_SECONDS


def _clean_tax(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value < 5)


def _cited_authors(snapshot, now):
    """Count distinct, recent exact-token X citations; raw link counts do not count."""
    authors = set()
    post_ids = set()
    rows = snapshot.raw.get("social_evidence") or []
    if not isinstance(rows, list):
        return authors
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("tokenAddress", "")).lower() != snapshot.token_address:
            continue
        try:
            url = urlsplit(str(row.get("url", "")))
        except ValueError:
            continue
        parts = url.path.strip("/").split("/")
        if (url.scheme != "https" or url.hostname not in {"x.com", "twitter.com"}
                or len(parts) != 3 or parts[1] != "status" or not parts[2].isdigit()
                or not _fresh(row.get("publishedAt"), now)):
            continue
        if parts[2] not in post_ids:
            authors.add(parts[0].lower())
            post_ids.add(parts[2])
    return authors


def apply_quality_trial(snapshot: MarketSnapshot, history: list[MarketSnapshot],
                        result: ScoreResult, now=None) -> ScoreResult:
    now = now or datetime.now(timezone.utc)
    reasons = []
    raw = snapshot.raw
    security = raw.get("security") or {}
    wallet = raw.get("verified_wallet_flow") or {}
    wallet_support = bool(
        wallet.get("buys", 0) >= 1 and wallet.get("sells", 0) == 0
        and wallet.get("paid_swap_verified") is True
        and _fresh(wallet.get("captured_at"), now)
        and _fresh(wallet.get("swap_observed_at"), now)
    )
    cited_authors = _cited_authors(snapshot, now)
    social_support = len(cited_authors) >= 2
    trades = snapshot.buys_5m + snapshot.sells_5m
    buy_share = snapshot.buys_5m / trades if trades > 0 else 0
    if not result.eligible:
        reasons.append("legacy safety/score gates did not qualify")
    if result.signal not in {"SCOUT", "ACTION", "A+"}:
        reasons.append("attention-only tier remains in research")
    if not _fresh(snapshot.captured_at, now):
        reasons.append("market observation is stale or undated")
    if (not snapshot.flow_checked
            or not _fresh(raw.get("flow_captured_at", snapshot.captured_at), now)):
        reasons.append("fresh unique-wallet flow is unconfirmed")
    if (snapshot.unique_buyers_5m < 5 or snapshot.buys_5m <= 0
            or snapshot.unique_buyers_5m > snapshot.buys_5m
            or snapshot.unique_buyers_5m / snapshot.buys_5m < 0.5):
        reasons.append("buyer diversity is insufficient for this trial")
    if buy_share < 0.60:
        reasons.append("buy demand is not dominant")
    if not (security.get("checked") and security.get("admin_checks_complete")
            and security.get("simulation_checked") and security.get("sell_simulation_success")
            and _clean_tax(security.get("buy_tax"))
            and _clean_tax(security.get("sell_tax"))
            and not security.get("cannot_buy") and not security.get("cannot_sell")
            and not security.get("is_honeypot")):
        reasons.append("independent contract and sellability checks are incomplete")
    if snapshot.deployer_sells_15m or wallet.get("sells", 0) > 0:
        reasons.append("deployer or monitored-wallet selling is present")
    if not (wallet_support or social_support):
        reasons.append("no verified wallet entry or two distinct cited exact-CA authors")
    # A fresh verified wallet entry is allowed to qualify without waiting for a
    # second market snapshot. Ordinary social/momentum promotion must persist.
    persistent = any(
        previous.flow_checked and previous.unique_buyers_5m >= 5
        and 120 <= (_age(previous.captured_at, now) or 0) <= 900
        and previous.buys_5m > 0
        and previous.buys_5m / max(1, previous.buys_5m + previous.sells_5m) >= 0.60
        and snapshot.liquidity_usd >= previous.liquidity_usd * 0.80
        for previous in history[:3]
    )
    if not wallet_support and not persistent:
        reasons.append("ordinary momentum has not persisted across observations")
    allowed = not reasons
    raw["quality_trial"] = {
        "version": POLICY_VERSION, "evaluated_at": now.isoformat(),
        "baseline_eligible": result.eligible, "baseline_signal": result.signal,
        "baseline_score": result.score, "main_eligible": allowed,
        "reasons": reasons, "verified_wallet_support": wallet_support,
        "cited_authors": len(cited_authors), "persistent_demand": persistent,
    }
    return replace(result, eligible=allowed, blockers=[*result.blockers, *reasons])

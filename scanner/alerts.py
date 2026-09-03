from __future__ import annotations

import html
from datetime import datetime, timezone

from .models import Candidate, MarketSnapshot, ScoreResult
from .targeting import structural_target


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}m"
    if value >= 1_000:
        return f"${value / 1_000:.1f}k"
    return f"${value:,.0f}"


def _multiple(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") + "x"


def _setup_summary(snapshot: MarketSnapshot, result: ScoreResult) -> str:
    trades = snapshot.buys_5m + snapshot.sells_5m
    buy_ratio = snapshot.buys_5m / trades if trades else 0.0
    evidence = [
        f"{snapshot.unique_buyers_5m} unique buyers/5m" if snapshot.flow_checked else None,
        f"{buy_ratio:.0%} buy share",
        (
            f"{snapshot.smart_wallet_buys} proven-wallet entries"
            if snapshot.smart_wallet_buys
            else None
        ),
        (
            f"{snapshot.exact_ca_mentions_5m} exact-CA mentions/5m"
            if snapshot.exact_ca_mentions_5m
            else None
        ),
    ]
    chosen = [item for item in evidence if item][:3]
    if chosen:
        return " · ".join(chosen)
    return " · ".join(result.drivers[:2]) or "measured flow inflection"


def format_alert(candidate: Candidate, snapshot: MarketSnapshot, result: ScoreResult) -> str:
    origin = candidate.launch_at or candidate.discovered_at
    age_minutes = max(0, int((datetime.now(timezone.utc) - origin).total_seconds() / 60))
    age = f"{age_minutes}m" if age_minutes < 120 else f"{age_minutes / 60:.1f}h"
    alert_market_cap = snapshot.market_cap_usd or snapshot.fdv_usd
    first_market_cap = candidate.metadata.get("first_detected_market_cap_usd")
    target_multiple = result.target_multiple or structural_target(candidate, snapshot, result)
    target_market_cap = alert_market_cap * target_multiple if alert_market_cap else None
    target_text = f" (~{_money(target_market_cap)} MC)" if target_market_cap else ""
    confidence = html.escape(result.target_confidence or "LOW")
    symbol = html.escape(candidate.symbol or "UNKNOWN")
    name = html.escape(candidate.name or symbol)
    chain = html.escape(candidate.chain.upper())
    source = html.escape(candidate.source.split(",")[0])
    summary = html.escape(_setup_summary(snapshot, result))
    if result.signal == "A+":
        verdict = f"🅰️ <b>Beefy A+ · {_multiple(target_multiple)} model upside{target_text}</b> [{confidence}]"
    elif result.signal == "ACTION":
        verdict = f"🎯 <b>Beefy ACTION · {_multiple(target_multiple)} model upside{target_text}</b> [{confidence}]"
    else:
        verdict = f"🔭 <b>Beefy SCOUT · {_multiple(target_multiple)} model upside{target_text}</b> [{confidence}]"

    security = snapshot.raw.get("security") if isinstance(snapshot.raw, dict) else {}
    security = security if isinstance(security, dict) else {}
    sell_proxy = bool(
        security.get("sell_simulation_success")
        and float(security.get("sell_tax") or 0.0) < 5
        and snapshot.liquidity_usd >= 3_000
    )
    providers = "+".join(str(item) for item in security.get("providers", [])) or "free checks"
    holder_text = str(snapshot.holder_count) if snapshot.holder_count else "n/a"
    gmgn = snapshot.raw.get("gmgn") if isinstance(snapshot.raw, dict) else {}
    gmgn = gmgn if isinstance(gmgn, dict) else {}
    tagged_wallet_text = ""
    if gmgn:
        tagged_wallet_text = (
            f" · GMGN tagged {int(gmgn.get('smart_count') or 0)} smart/"
            f"{int(gmgn.get('kol_count') or 0)} KOL"
        )
    lines = [
        f"🚨 <b>{chain} · {html.escape(result.signal)} · {result.score:.0f}/100 · {html.escape(result.stage)}</b>",
        f"<b>{name} (${symbol})</b>",
        verdict,
        f"Why now: {summary}",
        "",
        f"MC first {_money(first_market_cap)} · alert {_money(alert_market_cap)} · Liq {_money(snapshot.liquidity_usd)}",
        f"Age {age} · {snapshot.buys_5m}B/{snapshot.sells_5m}S · net wallets {snapshot.net_new_wallets_5m:+d}/5m",
        f"Holders {holder_text}{tagged_wallet_text} · exact-CA social {snapshot.exact_ca_mentions_5m}/5m · via {source}",
        (
            f"£20 sellability proxy PASS ({providers}) · tax "
            f"{float(security.get('buy_tax') or 0):.1f}%/{float(security.get('sell_tax') or 0):.1f}%"
            if sell_proxy
            else f"£20 sellability proxy unconfirmed ({providers})"
        ),
    ]
    if result.signal == "SCOUT" and result.upgrade_trigger:
        lines.extend(["", f"<b>Upgrade trigger:</b> {html.escape(result.upgrade_trigger)}"])
    lines.extend(
        [
            "",
            f"<b>Invalidation:</b> {html.escape(result.invalidation)}",
            f"<b>CA:</b> <code>{html.escape(candidate.token_address)}</code>",
        ]
    )
    chart = candidate.chart_url or snapshot.raw.get("url")
    if chart:
        lines.append(f'<a href="{html.escape(str(chart), quote=True)}">Open chart</a>')
    lines.extend(
        [
            "",
            f"Model: {html.escape(result.target_basis)}",
            "Target starts at alert MC—not first-detected MC · not a promise · no auto-trading",
        ]
    )
    return "\n".join(lines)

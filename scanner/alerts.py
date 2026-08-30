from __future__ import annotations

import html
from datetime import datetime, timezone

from .models import Candidate, MarketSnapshot, ScoreResult


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}m"
    if value >= 1_000:
        return f"${value / 1_000:.1f}k"
    return f"${value:,.0f}"


def format_alert(candidate: Candidate, snapshot: MarketSnapshot, result: ScoreResult) -> str:
    origin = candidate.launch_at or candidate.discovered_at
    age_minutes = max(0, int((datetime.now(timezone.utc) - origin).total_seconds() / 60))
    age = f"{age_minutes}m" if age_minutes < 120 else f"{age_minutes / 60:.1f}h"
    market_cap = snapshot.market_cap_usd or snapshot.fdv_usd
    churn = snapshot.volume_5m_usd / snapshot.liquidity_usd if snapshot.liquidity_usd else 0.0
    symbol = html.escape(candidate.symbol or "UNKNOWN")
    name = html.escape(candidate.name or symbol)
    chain = html.escape(candidate.chain.upper())
    source = html.escape(candidate.source.split(",")[0])
    drivers = "; ".join(html.escape(item) for item in result.drivers[:3]) or "awaiting more confirmation"
    if result.signal == "STRONG WATCH":
        verdict = "🐂 <b>Beefy Call: BUY</b>"
        analysis = f"Strong qualifying flow: {drivers}."
    else:
        verdict = "👀 <b>Beefy Verdict: WATCH FOR NOW</b>"
        analysis = f"Promising early flow, but conviction is not high enough yet: {drivers}."
    address = html.escape(candidate.token_address)
    chart = candidate.chart_url or snapshot.raw.get("url")
    security = snapshot.raw.get("security") if isinstance(snapshot.raw, dict) else {}
    security = security if isinstance(security, dict) else {}
    providers = "+".join(str(item) for item in security.get("providers", [])) or "free checks"
    safety = (
        f"Safety checked ({providers}) · sell tax {float(security.get('sell_tax') or 0):.1f}%"
        if security.get("checked")
        else "Safety check unavailable"
    )

    lines = [
        f"🚨 <b>{chain} · {html.escape(result.stage)} · {result.score:.0f}/100</b>",
        f"<b>{name} (${symbol})</b>",
        verdict,
        analysis,
        "",
        f"Age {age} · MC {_money(market_cap)} · Liq {_money(snapshot.liquidity_usd)}",
        f"5m vol {_money(snapshot.volume_5m_usd)} ({churn:.2f}x liq) · {snapshot.buys_5m}B/{snapshot.sells_5m}S",
        f"5m {snapshot.price_change_5m:+.1f}% · 1h {snapshot.price_change_1h:+.1f}% · via {source}",
        html.escape(safety),
        "",
        f"<b>Invalidation:</b> {html.escape(result.invalidation)}",
        f"<b>CA:</b> <code>{address}</code>",
    ]
    if chart:
        lines.append(f'<a href="{html.escape(str(chart), quote=True)}">Open chart</a>')
    lines.extend(["", "High-risk microcap · alerts only · no auto-trading · DYOR"])
    return "\n".join(lines)

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


def _setup_summary(candidate: Candidate, snapshot: MarketSnapshot, result: ScoreResult) -> str:
    trades = snapshot.buys_5m + snapshot.sells_5m
    buy_ratio = snapshot.buys_5m / trades if trades else 0.0
    source_names = set(candidate.source.split(","))
    verified_launch = bool(
        source_names
        & {
            "bankr",
            "flaunch",
            "clanker",
            "baseline",
            "o1-b20",
            "o1-robinhood",
            "o1-robinhood-stocks",
            "pools-fun",
            "basestonk",
        }
    )
    if result.signal == "STRONG WATCH":
        if result.stage == "REAWAKENING":
            return f"volume has reaccelerated with {buy_ratio:.0%} buyer control"
        if snapshot.smart_wallet_buys > snapshot.smart_wallet_sells:
            return f"smart-wallet support and {buy_ratio:.0%} buyer control"
        if verified_launch:
            return f"verified launch with {buy_ratio:.0%} buyer control"
        return f"liquidity and flow qualify with {buy_ratio:.0%} buyer control"

    if snapshot.price_change_5m >= 20:
        return "dip-entry potential; wait for price to hold after a pullback"
    if snapshot.liquidity_usd < 8_000:
        return f"buyer flow is building, but liquidity is only {_money(snapshot.liquidity_usd)}"
    if trades < 18:
        return f"buyers control {buy_ratio:.0%}, but only {trades} trades landed in 5m"
    if buy_ratio < 0.60:
        return f"buy pressure is {buy_ratio:.0%} and not yet decisive"
    if snapshot.social_links < 2:
        return f"buyers control {buy_ratio:.0%}, but project evidence remains limited"
    if result.stage == "REAWAKENING":
        return f"volume is reawakening with {buy_ratio:.0%} buyer control; await continuation"
    return f"buyers control {buy_ratio:.0%}; wait for another volume and price hold"


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
    target_multiple = result.target_multiple or structural_target(candidate, snapshot, result)
    target_market_cap = market_cap * target_multiple if market_cap else None
    target_text = f" (~{_money(target_market_cap)} MC)" if target_market_cap else ""
    target_label = _multiple(target_multiple)
    confidence = html.escape(result.target_confidence or "LOW")
    summary = html.escape(_setup_summary(candidate, snapshot, result))
    if result.signal == "STRONG WATCH":
        verdict = (
            f"🐂 <b>Beefy Call: BUY · {target_label} model upside"
            f"{target_text}</b> [{confidence}] — {summary}."
        )
    else:
        verdict = (
            f"👀 <b>Beefy Verdict: WATCH · {target_label} model upside"
            f"{target_text}</b> [{confidence}] — {summary}."
        )
    address = html.escape(candidate.token_address)
    chart = candidate.chart_url or snapshot.raw.get("url")
    security = snapshot.raw.get("security") if isinstance(snapshot.raw, dict) else {}
    security = security if isinstance(security, dict) else {}
    providers = "+".join(str(item) for item in security.get("providers", [])) or "free checks"
    safety = (
        f"Safety checked ({providers}) · tax "
        f"{float(security.get('buy_tax') or 0):.1f}%/{float(security.get('sell_tax') or 0):.1f}%"
        if security.get("checked")
        else "Safety check unavailable"
    )

    lines = [
        f"🚨 <b>{chain} · {html.escape(result.stage)} · {result.score:.0f}/100</b>",
        f"<b>{name} (${symbol})</b>",
        verdict,
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
    lines.extend(
        [
            "",
            f"Model: {html.escape(result.target_basis)}",
            "From alert price · not a promise · high-risk · no auto-trading · DYOR",
        ]
    )
    return "\n".join(lines)

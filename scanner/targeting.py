from __future__ import annotations

from .models import Candidate, MarketSnapshot, ScoreResult


def structural_target(
    candidate: Candidate,
    snapshot: MarketSnapshot,
    result: ScoreResult,
) -> float:
    """Estimate uncapped upside from valuation, liquidity, flow, and quality.

    This runs only after the independent safety/quality gate. A large output
    therefore cannot make an otherwise ineligible token alert.
    """
    del candidate  # Reserved for platform-specific priors once outcomes support them.
    trades = snapshot.buys_5m + snapshot.sells_5m
    buy_ratio = snapshot.buys_5m / trades if trades else 0.0
    quality = max(0.0, min(1.0, (result.score - 70.0) / 30.0))
    market_cap = snapshot.market_cap_usd or snapshot.fdv_usd
    if not market_cap or market_cap <= 0 or snapshot.liquidity_usd <= 0:
        return 1.2
    liquidity = max(1.0, snapshot.liquidity_usd)

    # Use a conservative effective valuation when a feed reports market cap
    # below the capital already sitting in the pool.
    effective_market_cap = max(float(market_cap or 0.0), liquidity * 1.25)
    liquidity_power = 4.0 + quality * 18.0
    if result.signal == "STRONG WATCH":
        liquidity_power += 3.0
    if buy_ratio >= 0.70:
        liquidity_power += 2.0
    if snapshot.smart_wallet_buys > snapshot.smart_wallet_sells:
        liquidity_power += 2.0
    if snapshot.social_links >= 2:
        liquidity_power += 1.0
    if result.stage == "REAWAKENING":
        liquidity_power += 2.0

    volume_power = 8.0 + quality * 12.0
    projected_market_cap = max(
        effective_market_cap * 1.20,
        liquidity * liquidity_power,
        snapshot.volume_5m_usd * volume_power,
        snapshot.volume_1h_usd * (2.0 + quality * 3.0),
    )
    if snapshot.price_change_5m >= 35:
        projected_market_cap = max(effective_market_cap * 1.20, projected_market_cap * 0.80)
    return round(max(1.20, projected_market_cap / effective_market_cap), 1)


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = max(0.0, min(1.0, quantile)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def combine_target(
    structural: float,
    historical_mfe_pct: list[float],
) -> tuple[float, str]:
    """Blend live structure with the 70th-percentile move of comparable calls."""
    samples = len(historical_mfe_pct)
    if samples < 5:
        return structural, "MEDIUM" if structural >= 8.0 else "LOW"

    empirical = max(1.0, 1.0 + percentile(historical_mfe_pct, 0.70) / 100.0)
    weight = 0.35 if samples < 8 else (0.55 if samples < 20 else 0.70)
    agreement = min(structural, empirical) / max(structural, empirical)
    if agreement >= 0.67:
        combined = max(structural, empirical)
    else:
        combined = structural * (1.0 - weight) + empirical * weight
    confidence = "HIGH" if samples >= 20 and agreement >= 0.67 else (
        "MEDIUM" if samples >= 8 else "LOW"
    )
    return round(max(1.20, combined), 1), confidence

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from .config import ScannerConfig
from .models import Candidate, MarketSnapshot, ScoreResult


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


class SignalScorer:
    """Transparent first-leg score. It deliberately emits watch signals, never orders."""

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config

    def score(
        self,
        candidate: Candidate,
        snapshot: MarketSnapshot,
        history: list[MarketSnapshot],
        now: datetime | None = None,
    ) -> ScoreResult:
        now = now or datetime.now(timezone.utc)
        origin = candidate.launch_at or candidate.discovered_at
        age_minutes = max(0.0, (now - origin).total_seconds() / 60)
        market_cap = snapshot.market_cap_usd or snapshot.fdv_usd
        txns_5m = snapshot.buys_5m + snapshot.sells_5m
        buy_ratio = snapshot.buys_5m / txns_5m if txns_5m else 0.0
        churn = snapshot.volume_5m_usd / snapshot.liquidity_usd if snapshot.liquidity_usd > 0 else 0.0

        previous_volumes = [item.volume_5m_usd for item in history[:5] if item.volume_5m_usd > 0]
        baseline_volume = mean(previous_volumes) if previous_volumes else 0.0
        acceleration = snapshot.volume_5m_usd / baseline_volume if baseline_volume > 0 else 1.0

        components: dict[str, float] = {}
        if age_minutes <= 15:
            components["freshness"] = 18.0
        elif age_minutes <= 60:
            components["freshness"] = 14.0
        elif age_minutes <= 180:
            components["freshness"] = 9.0
        elif age_minutes <= 360:
            components["freshness"] = 5.0
        else:
            components["freshness"] = 1.0

        components["churn"] = _clamp(churn * 40.0, 0.0, 22.0)
        components["transaction_velocity"] = _clamp(txns_5m * 0.75, 0.0, 15.0)
        components["buy_pressure"] = _clamp((buy_ratio - 0.45) * 50.0, 0.0, 15.0)
        components["volume_acceleration"] = _clamp((acceleration - 0.8) * 10.0, 0.0, 15.0)

        if -15 <= snapshot.price_change_5m < 0:
            components["price_confirmation"] = 3.0
        elif 0 <= snapshot.price_change_5m <= 20:
            components["price_confirmation"] = 10.0
        elif snapshot.price_change_5m <= 45:
            components["price_confirmation"] = 8.0
        elif snapshot.price_change_5m <= self.config.late_price_change_5m:
            components["price_confirmation"] = 3.0
        else:
            components["price_confirmation"] = 0.0

        components["social"] = _clamp(
            snapshot.social_links * 1.5 + snapshot.boost_score * 0.5 + snapshot.social_velocity * 2.0,
            0.0,
            8.0,
        )
        components["smart_wallet"] = _clamp(
            snapshot.smart_wallet_buys * 5.0
            - snapshot.smart_wallet_sells * 3.0
            + max(0.0, snapshot.smart_wallet_net_usd) / 1_000.0,
            0.0,
            15.0,
        )

        if not history and candidate.source.split(",")[0] in {"bankr", "flaunch"}:
            components["direct_launch"] = 5.0

        blockers: list[str] = []
        anti_late_penalty = 0.0
        if snapshot.liquidity_usd < self.config.min_liquidity_usd:
            blockers.append(f"liquidity below ${self.config.min_liquidity_usd:,.0f}")
        if txns_5m < 4:
            blockers.append("too few 5m trades")
        if market_cap and market_cap > self.config.max_market_cap_usd:
            blockers.append("market cap beyond first-leg range")
            anti_late_penalty += 35.0
        if snapshot.price_change_5m >= self.config.late_price_change_5m:
            blockers.append("5m move already extended")
            anti_late_penalty += 35.0
        if snapshot.price_change_1h >= self.config.late_price_change_1h:
            blockers.append("1h move already extended")
            anti_late_penalty += 35.0
        if txns_5m >= 8 and buy_ratio < 0.42:
            blockers.append("sell pressure dominates")
            anti_late_penalty += 25.0
        if history:
            recent_peak = max((item.price_usd or 0.0) for item in history[:8])
            drawdown = 1 - ((snapshot.price_usd or 0.0) / recent_peak) if recent_peak > 0 else 0.0
            if drawdown >= 0.30 and buy_ratio < 0.50:
                blockers.append("post-peak distribution")
                anti_late_penalty += 30.0
            if acceleration < 0.55 and snapshot.price_change_5m < 0:
                blockers.append("volume and price are fading")
                anti_late_penalty += 20.0

        reawakening = age_minutes > 360 and acceleration >= 1.5 and buy_ratio >= 0.55
        stage = "REAWAKENING" if reawakening else ("IGNITION" if age_minutes <= 360 else "WATCH")
        if reawakening:
            components["reawakening"] = _clamp((acceleration - 1.0) * 6.0, 0.0, 10.0)

        risk_penalty = 0.0
        if market_cap and snapshot.liquidity_usd and market_cap / snapshot.liquidity_usd > 80:
            blockers.append("thin liquidity versus valuation")
            risk_penalty += 10.0
        raw_score = sum(components.values()) - anti_late_penalty - risk_penalty
        final_score = round(_clamp(raw_score), 1)

        hard_late = anti_late_penalty >= 35.0
        basic_quality = snapshot.liquidity_usd >= self.config.min_liquidity_usd and txns_5m >= 4
        eligible = final_score >= self.config.min_alert_score and basic_quality and not hard_late
        if eligible and final_score >= self.config.strong_alert_score:
            signal = "STRONG WATCH"
        elif eligible:
            signal = "EARLY WATCH"
        elif hard_late:
            signal = "AVOID LATE"
        else:
            signal = "MONITOR"

        ranked = sorted(components.items(), key=lambda item: item[1], reverse=True)
        labels = {
            "freshness": f"fresh ({age_minutes:.0f}m)",
            "churn": f"5m churn {churn:.2f}x liquidity",
            "transaction_velocity": f"{txns_5m} trades/5m",
            "buy_pressure": f"{buy_ratio:.0%} buys",
            "volume_acceleration": f"volume acceleration {acceleration:.1f}x",
            "social": f"social score {components['social']:.0f}/8",
            "smart_wallet": f"smart-wallet flow +{snapshot.smart_wallet_buys}/-{snapshot.smart_wallet_sells}",
            "direct_launch": f"direct {candidate.source.split(',')[0]} launch",
            "reawakening": f"reawakening acceleration {acceleration:.1f}x",
            "price_confirmation": f"5m price {snapshot.price_change_5m:+.1f}%",
        }
        drivers = [labels[name] for name, value in ranked if value >= 3.0][:4]
        invalidation = "5m buys below 45%, liquidity falls 25%, or price loses the pre-alert base"
        return ScoreResult(
            score=final_score,
            stage=stage,
            signal=signal,
            eligible=eligible,
            anti_late_penalty=anti_late_penalty,
            components={key: round(value, 1) for key, value in components.items()},
            drivers=drivers,
            blockers=blockers,
            invalidation=invalidation,
        )

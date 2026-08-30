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
        min_alert_score: float | None = None,
        strong_alert_score: float | None = None,
    ) -> ScoreResult:
        now = now or datetime.now(timezone.utc)
        min_alert_score = self.config.min_alert_score if min_alert_score is None else min_alert_score
        strong_alert_score = self.config.strong_alert_score if strong_alert_score is None else strong_alert_score
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

        components["churn"] = _clamp(churn * 32.0, 0.0, 18.0)
        components["transaction_velocity"] = _clamp(txns_5m * 0.65, 0.0, 14.0)
        components["buy_pressure"] = _clamp((buy_ratio - 0.48) * 42.0, 0.0, 12.0)
        components["volume_acceleration"] = _clamp((acceleration - 0.8) * 8.0, 0.0, 12.0)

        if -15 <= snapshot.price_change_5m < 0:
            components["price_confirmation"] = 3.0
        elif 0 <= snapshot.price_change_5m <= 20:
            components["price_confirmation"] = 8.0
        elif snapshot.price_change_5m <= 45:
            components["price_confirmation"] = 6.0
        elif snapshot.price_change_5m <= self.config.late_price_change_5m:
            components["price_confirmation"] = 3.0
        else:
            components["price_confirmation"] = 0.0

        components["social"] = _clamp(
            snapshot.social_links * 2.5 + snapshot.boost_score * 0.35 + snapshot.social_velocity * 2.0,
            0.0,
            12.0,
        )
        components["smart_wallet"] = _clamp(
            snapshot.smart_wallet_buys * 5.0
            - snapshot.smart_wallet_sells * 3.0
            + max(0.0, snapshot.smart_wallet_net_usd) / 1_000.0,
            0.0,
            15.0,
        )

        source_names = set(candidate.source.split(","))
        verified_sources = {
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
        trusted_launch = bool(source_names & verified_sources)
        if trusted_launch:
            components["direct_launch"] = 5.0

        security = snapshot.raw.get("security") if isinstance(snapshot.raw, dict) else None
        security = security if isinstance(security, dict) else {}
        identity = candidate.metadata.get("identity_risk")
        identity = identity if isinstance(identity, dict) else {}

        blockers: list[str] = []
        anti_late_penalty = 0.0
        if snapshot.liquidity_usd < self.config.min_liquidity_usd:
            blockers.append(f"liquidity below ${self.config.min_liquidity_usd:,.0f}")
        if txns_5m < 6:
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

        risk_penalty = float(identity.get("copycat_penalty") or 0.0)
        if risk_penalty:
            blockers.append(str(identity.get("reason") or "copycat identity overlap"))
        hard_risk = False
        safety_complete = bool(security.get("admin_checks_complete")) and (
            candidate.chain != "base"
            or bool(security.get("simulation_checked"))
            or trusted_launch
        )
        if not security.get("checked"):
            blockers.append("contract safety not confirmed yet")
        else:
            if not safety_complete:
                blockers.append("contract safety response incomplete")
            if security.get("is_honeypot"):
                blockers.append("honeypot simulation failed")
                hard_risk = True
            if security.get("cannot_sell") or security.get("cannot_buy"):
                blockers.append("buy/sell restriction detected")
                hard_risk = True
            if security.get("owner_change_balance"):
                blockers.append("owner can change holder balances")
                hard_risk = True
            if security.get("fake_token"):
                blockers.append("fake-token association detected")
                hard_risk = True
            if security.get("selfdestruct"):
                blockers.append("contract can self-destruct")
                hard_risk = True
            buy_tax = float(security.get("buy_tax") or 0.0)
            if buy_tax >= 20:
                blockers.append(f"buy tax {buy_tax:.0f}%")
                hard_risk = True
            elif buy_tax >= 5:
                blockers.append(f"elevated buy tax {buy_tax:.0f}%")
                risk_penalty += min(10.0, buy_tax * 0.5)
            sell_tax = float(security.get("sell_tax") or 0.0)
            if sell_tax >= 20:
                blockers.append(f"sell tax {sell_tax:.0f}%")
                hard_risk = True
            elif sell_tax >= 5:
                blockers.append(f"elevated sell tax {sell_tax:.0f}%")
                risk_penalty += min(10.0, sell_tax * 0.5)
            if int(security.get("risk_level") or 0) >= 80:
                blockers.append("very high contract risk")
                hard_risk = True
            elif int(security.get("risk_level") or 0) >= 60:
                blockers.append("high contract-risk flags")
                risk_penalty += 10.0
            elif int(security.get("risk_level") or 0) >= 20:
                risk_penalty += min(6.0, int(security.get("risk_level") or 0) / 10.0)
            for field, label, penalty in (
                ("hidden_owner", "hidden owner", 10.0),
                ("blacklist_function", "blacklist function", 10.0),
                ("transfer_pausable", "transfers can be paused", 8.0),
                ("mintable", "supply can be minted", 8.0),
                ("proxy", "upgradeable proxy", 5.0),
                ("can_take_back_ownership", "ownership can be reclaimed", 12.0),
                ("slippage_modifiable", "tax/slippage can be changed", 10.0),
                ("personal_slippage_modifiable", "wallet-specific tax can be changed", 12.0),
                ("trading_cooldown", "trading cooldown controls", 5.0),
            ):
                if security.get(field):
                    blockers.append(label)
                    risk_penalty += penalty
            if security.get("open_source") is False:
                blockers.append("contract source not verified")
                hard_risk = True
            owner_percent = float(security.get("owner_percent") or 0.0)
            if owner_percent > 10:
                blockers.append(f"owner holds {owner_percent:.0f}%")
                risk_penalty += min(15.0, (owner_percent - 10.0) * 0.5)
            creator_percent = float(security.get("creator_percent") or 0.0)
            if creator_percent > 10:
                blockers.append(f"creator holds {creator_percent:.0f}%")
                risk_penalty += min(18.0, (creator_percent - 10.0) * 0.6)
                if creator_percent > 25:
                    hard_risk = True
            creator_honeypots = int(security.get("creator_honeypot_count") or 0)
            if creator_honeypots:
                blockers.append(f"creator linked to {creator_honeypots} honeypot(s)")
                risk_penalty += min(35.0, 25.0 + creator_honeypots * 5.0)
            concentration = float(security.get("top_unlocked_eoa_percent") or 0.0)
            if concentration > 35:
                blockers.append(f"top unlocked wallets hold {concentration:.0f}%")
                risk_penalty += min(12.0, (concentration - 35.0) * 0.35)
            lp_unlocked = security.get("lp_unlocked_percent")
            if (
                isinstance(lp_unlocked, (int, float))
                and float(lp_unlocked) > 50
                and not trusted_launch
            ):
                blockers.append(f"{float(lp_unlocked):.0f}% of LP appears unlocked")
                hard_risk = True
            if safety_complete and not hard_risk and risk_penalty < 8:
                components["contract_safety"] = 5.0

        if market_cap and snapshot.liquidity_usd and market_cap / snapshot.liquidity_usd > 60:
            blockers.append("thin liquidity versus valuation")
            risk_penalty += 12.0
        raw_score = sum(components.values()) - anti_late_penalty - risk_penalty
        final_score = round(_clamp(raw_score), 1)

        hard_late = anti_late_penalty >= 35.0
        has_price = snapshot.price_usd is not None and snapshot.price_usd > 0
        if not has_price:
            blockers.append("reliable USD price unavailable")
        basic_quality = (
            has_price
            and snapshot.liquidity_usd >= self.config.min_liquidity_usd
            and txns_5m >= 6
        )
        evidence_quality = snapshot.social_links >= 1 or snapshot.smart_wallet_buys >= 1
        if not evidence_quality:
            blockers.append("no project/social or proven smart-wallet evidence")
        eligible = (
            final_score >= min_alert_score
            and basic_quality
            and evidence_quality
            and safety_complete
            and not hard_late
            and not hard_risk
        )
        strong_quality = (
            txns_5m >= 18
            and buy_ratio >= 0.60
            and snapshot.liquidity_usd >= max(self.config.min_liquidity_usd, 8_000.0)
            and market_cap is not None
            and snapshot.price_change_5m <= 45
            and snapshot.social_links >= 2
            and not any(
                security.get(field)
                for field in (
                    "hidden_owner",
                    "blacklist_function",
                    "transfer_pausable",
                    "mintable",
                    "proxy",
                    "can_take_back_ownership",
                    "slippage_modifiable",
                    "personal_slippage_modifiable",
                    "trading_cooldown",
                )
            )
            and float(security.get("creator_percent") or 0.0) <= 10
            and int(security.get("creator_honeypot_count") or 0) == 0
            and float(security.get("buy_tax") or 0.0) < 5
            and float(security.get("sell_tax") or 0.0) < 5
            and float(identity.get("copycat_penalty") or 0.0) == 0
        )
        if eligible and final_score >= strong_alert_score and strong_quality:
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
            "contract_safety": "contract checks clean",
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

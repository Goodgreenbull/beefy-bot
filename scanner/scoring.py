from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from .config import ScannerConfig
from .models import Candidate, MarketSnapshot, ScoreResult


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator > 0 else default


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


class SignalScorer:
    """Inflection-first scoring. Outputs alerts and never places an order."""

    VERIFIED_SOURCES = {
        "bankr", "flaunch", "baseline", "o1-b20",
        "o1-robinhood", "o1-robinhood-stocks", "pons-v1", "pons-v2",
        "pools-fun", "basestonk",
    }

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
        scout_alert_score: float | None = None,
    ) -> ScoreResult:
        now = now or datetime.now(timezone.utc)
        action_score = self.config.min_alert_score if min_alert_score is None else min_alert_score
        a_plus_score = self.config.strong_alert_score if strong_alert_score is None else strong_alert_score
        scout_score = self.config.scout_alert_score if scout_alert_score is None else scout_alert_score
        origin = candidate.launch_at or candidate.discovered_at
        age_minutes = max(0.0, (now - origin).total_seconds() / 60.0)
        market_cap = snapshot.market_cap_usd or snapshot.fdv_usd
        txns_5m = snapshot.buys_5m + snapshot.sells_5m
        buy_ratio = _ratio(snapshot.buys_5m, txns_5m)
        churn = _ratio(snapshot.volume_5m_usd, snapshot.liquidity_usd)

        prior = history[:5]
        prior_buys = [item.buys_5m for item in prior if item.buys_5m > 0]
        prior_buy_ratios = [
            _ratio(item.buys_5m, item.buys_5m + item.sells_5m)
            for item in prior if item.buys_5m + item.sells_5m > 0
        ]
        prior_volumes = [item.volume_5m_usd for item in prior if item.volume_5m_usd > 0]
        mean_prior_buys = mean(prior_buys) if prior_buys else 0.0
        mean_prior_volume = mean(prior_volumes) if prior_volumes else 0.0
        buyer_acceleration = _ratio(snapshot.buys_5m, mean_prior_buys, 1.0)
        volume_acceleration = _ratio(snapshot.volume_5m_usd, mean_prior_volume, 1.0)
        prior_buy_ratio = mean(prior_buy_ratios) if prior_buy_ratios else buy_ratio
        balance_improvement = buy_ratio - prior_buy_ratio

        older_unique_buyers = max(0, snapshot.unique_buyers_15m - snapshot.unique_buyers_5m)
        unique_buyer_acceleration = _ratio(
            snapshot.unique_buyers_5m,
            older_unique_buyers / 2.0,
            1.0 if snapshot.flow_checked else 0.0,
        )
        holder_values = [item.holder_count for item in prior if item.holder_count]
        holder_change = snapshot.holder_count - holder_values[-1] if snapshot.holder_count and holder_values else 0
        holder_growth_pct = _ratio(holder_change * 100.0, holder_values[-1] if holder_values else 0)

        prices = [float(item.price_usd) for item in history[:8] if item.price_usd and item.price_usd > 0]
        current_price = float(snapshot.price_usd or 0.0)
        local_base = min(prices + ([current_price] if current_price > 0 else []), default=0.0)
        local_multiple = _ratio(current_price, local_base, 1.0)
        dipped_and_absorbed = bool(
            prices and current_price >= min(prices) * 1.05 and buy_ratio >= 0.60
            and -12 <= snapshot.price_change_5m <= 25
        )

        older_mentions = max(0, snapshot.exact_ca_mentions_15m - snapshot.exact_ca_mentions_5m)
        exact_ca_acceleration = _ratio(snapshot.exact_ca_mentions_5m, older_mentions / 2.0)
        source_names = set(candidate.source.split(","))
        trusted_launch = bool(source_names & self.VERIFIED_SOURCES)
        gmgn_evidence = bool(candidate.metadata.get("gmgn_evidence"))
        platform_provenance = bool(
            trusted_launch
            and (
                candidate.metadata.get("verified_platform_event")
                or candidate.metadata.get("verified_platform_api")
                or candidate.metadata.get("platform_terms_verified")
            )
        )
        security = snapshot.raw.get("security") if isinstance(snapshot.raw, dict) else None
        security = security if isinstance(security, dict) else {}
        identity = candidate.metadata.get("identity_risk")
        identity = identity if isinstance(identity, dict) else {}
        deployer_reputation = candidate.metadata.get("deployer_reputation")
        deployer_reputation = deployer_reputation if isinstance(deployer_reputation, dict) else {}
        creator_activity = _clamp(float(snapshot.raw.get("creator_activity_score") or 0.0), 0.0, 1.0)
        gmgn = snapshot.raw.get("gmgn") if isinstance(snapshot.raw, dict) else {}
        gmgn = gmgn if isinstance(gmgn, dict) else {}
        attention_rank = _integer(gmgn.get("attention_rank"))
        recent_signal_types = {
            _integer(value) for value in (gmgn.get("recent_signal_types") or [])
        }
        recent_smart_attention = bool(recent_signal_types & {12, 20})
        recent_platform_attention = bool(recent_signal_types & {13, 19})
        prior_attention_rank = 0
        for item in prior:
            item_gmgn = item.raw.get("gmgn") if isinstance(item.raw, dict) else {}
            item_gmgn = item_gmgn if isinstance(item_gmgn, dict) else {}
            item_rank = _integer(item_gmgn.get("attention_rank"))
            if item_rank:
                prior_attention_rank = item_rank
                break
        attention_rank_gain = (
            max(0, prior_attention_rank - attention_rank)
            if attention_rank and prior_attention_rank
            else 0
        )
        live_attention = bool(
            (0 < attention_rank <= 30)
            or attention_rank_gain >= 5
            or recent_smart_attention
            or recent_platform_attention
        )

        components: dict[str, float] = {}
        if age_minutes <= 15:
            components["freshness"] = 8.0
        elif age_minutes <= 60:
            components["freshness"] = 6.0
        elif age_minutes <= 180:
            components["freshness"] = 4.0
        elif age_minutes <= 360:
            components["freshness"] = 2.0
        else:
            components["freshness"] = 0.5
        components["direct_discovery"] = 6.0 if trusted_launch else (5.0 if gmgn_evidence else 2.0)
        components["creator_narrative"] = _clamp(
            (2.0 if candidate.deployer else 0.0) + snapshot.creator_reputation * 4.0
            + min(3.0, snapshot.social_links * 1.0)
            + snapshot.narrative_score * 3.0 + creator_activity * 3.0, 0.0, 12.0,
        )
        components["buyer_velocity"] = _clamp(
            max(0.0, buyer_acceleration - 0.9) * 8.0
            + max(0.0, unique_buyer_acceleration - 0.9) * 7.0
            + min(3.0, snapshot.unique_buyers_5m * 0.35), 0.0, 18.0,
        )
        components["holder_velocity"] = _clamp(
            max(0, snapshot.net_new_wallets_5m) + max(0.0, holder_growth_pct) * 0.8,
            0.0, 10.0,
        )
        components["balance_absorption"] = _clamp(
            max(0.0, buy_ratio - 0.45) * 32.0 + max(0.0, balance_improvement) * 18.0
            + (4.0 if dipped_and_absorbed else 0.0), 0.0, 14.0,
        )
        components["smart_wallet"] = _clamp(
            snapshot.smart_wallet_buys * 5.0 - snapshot.smart_wallet_sells * 4.0
            + max(0.0, snapshot.smart_wallet_net_usd) / 1_500.0, 0.0, 12.0,
        )
        components["exact_ca_social"] = _clamp(
            snapshot.exact_ca_mentions_5m * 1.5 + snapshot.credible_social_mentions_5m * 2.5
            + max(0.0, exact_ca_acceleration - 1.0) * 2.0
            + max(0.0, snapshot.social_velocity) * 0.5, 0.0, 10.0,
        )
        components["live_attention"] = _clamp(
            (
                8.0
                if 0 < attention_rank <= 5
                else 6.0
                if attention_rank <= 15 and attention_rank
                else 4.0
                if attention_rank <= 30 and attention_rank
                else 0.0
            )
            + min(2.0, attention_rank_gain / 5.0)
            + (4.0 if recent_smart_attention else 0.0)
            + (3.0 if recent_platform_attention else 0.0),
            0.0,
            10.0,
        )
        sell_simulation = bool(security.get("sell_simulation_success"))
        clean_tax = float(security.get("buy_tax") or 0.0) < 5 and float(security.get("sell_tax") or 0.0) < 5
        components["market_quality"] = _clamp(
            min(3.0, snapshot.liquidity_usd / 4_000.0)
            + (4.0 if sell_simulation and clean_tax else 0.0)
            + (3.0 if 0.10 <= churn <= 1.25 else (1.0 if churn < 2.0 else 0.0)),
            0.0, 10.0,
        )

        buyer_inflecting = bool(
            buyer_acceleration >= 1.20
            or (snapshot.flow_checked and snapshot.unique_buyers_5m >= 5 and unique_buyer_acceleration >= 1.20)
        )
        holder_inflecting = snapshot.net_new_wallets_5m >= 3 or holder_growth_pct >= 2.0
        balance_healthy = buy_ratio >= 0.60 and balance_improvement >= -0.03
        smart_confirmed = snapshot.smart_wallet_buys >= 2 and snapshot.smart_wallet_buys > snapshot.smart_wallet_sells
        social_inflecting = bool(
            snapshot.exact_ca_mentions_5m >= 2
            and (exact_ca_acceleration >= 1.20 or snapshot.credible_social_mentions_5m >= 1)
        )
        creator_or_narrative = bool(
            snapshot.creator_reputation >= 0.50 or snapshot.narrative_score >= 0.60
            or creator_activity >= 0.60
            or (trusted_launch and candidate.deployer and snapshot.social_links >= 3)
            or (candidate.deployer and trusted_launch and int(deployer_reputation.get("samples") or 0) >= 3)
        )
        sellable_20_proxy = bool(
            sell_simulation and clean_tax and not security.get("cannot_sell")
            and snapshot.liquidity_usd >= self.config.min_liquidity_usd
        )
        independent_signals = sum((
            buyer_inflecting, holder_inflecting, balance_healthy, dipped_and_absorbed,
            smart_confirmed, social_inflecting, creator_or_narrative, sellable_20_proxy,
            live_attention,
        ))

        blockers: list[str] = []
        risk_penalty = float(identity.get("copycat_penalty") or 0.0)
        anti_late_penalty = 0.0
        hard_risk = False
        if risk_penalty:
            blockers.append(str(identity.get("reason") or "copycat identity overlap"))
            if risk_penalty >= 20:
                hard_risk = True
        if _integer(identity.get("exact_both")) >= 1:
            hard_risk = True
        if _integer(identity.get("serial_deployer_launches")) >= 5:
            blockers.append("deployer is mass-launching recent tokens")
            hard_risk = True
        if identity.get("blocked_theme"):
            hard_risk = True
        gmgn_creator_tokens = _integer(gmgn.get("creator_token_count"), 0)
        if gmgn_creator_tokens >= 10:
            blockers.append(f"creator linked to {gmgn_creator_tokens} token launches")
            risk_penalty += min(16.0, 5.0 + (gmgn_creator_tokens - 10) * 0.25)
        if not candidate.deployer:
            blockers.append("creator/deployer not identified")
            risk_penalty += 4.0
        elif (
            int(deployer_reputation.get("samples") or 0) >= 3
            and float(deployer_reputation.get("score") or 0.0) < 0.25
        ):
            blockers.append("deployer has a weak completed-launch record")
            risk_penalty += 10.0
        if snapshot.liquidity_usd < self.config.min_liquidity_usd:
            blockers.append(f"liquidity below ${self.config.min_liquidity_usd:,.0f}")
        if txns_5m < 6:
            blockers.append("too few 5m trades")
        if market_cap and market_cap > self.config.max_market_cap_usd:
            blockers.append("market cap beyond first-leg range")
            anti_late_penalty += 35.0
        if local_multiple >= 2.0:
            blockers.append(f"already {local_multiple:.1f}x from its measured local base")
            anti_late_penalty += 22.0 if local_multiple < 3.0 else 40.0
        if snapshot.price_change_5m >= 60:
            blockers.append("vertical 5m price expansion")
            anti_late_penalty += 22.0
        if snapshot.price_change_5m >= self.config.late_price_change_5m:
            anti_late_penalty += 20.0
        if snapshot.price_change_1h >= 150:
            blockers.append("1h move already extended")
            anti_late_penalty += 22.0
        if snapshot.price_change_1h >= self.config.late_price_change_1h:
            anti_late_penalty += 20.0
        if txns_5m >= 8 and buy_ratio < 0.45:
            blockers.append("sell pressure dominates")
            anti_late_penalty += 25.0
        if churn >= 1.25 and not buyer_inflecting and not holder_inflecting:
            blockers.append("high churn without buyer/holder growth")
            risk_penalty += 16.0
        if snapshot.flow_checked and snapshot.buys_5m >= 15 and _ratio(snapshot.unique_buyers_5m, snapshot.buys_5m) < 0.25:
            blockers.append("bot/wash-flow pattern: few unique buyers")
            risk_penalty += 22.0
        if snapshot.deployer_sells_15m > 0:
            blockers.append("deployer sold into the pool in the last 15m")
            hard_risk = True
        if history:
            recent_peak = max((item.price_usd or 0.0) for item in history[:8])
            drawdown = 1 - _ratio(current_price, recent_peak, 1.0) if recent_peak > 0 else 0.0
            if drawdown >= 0.30 and buy_ratio < 0.55:
                blockers.append("post-peak distribution")
                anti_late_penalty += 30.0
            if volume_acceleration < 0.55 and snapshot.price_change_5m < 0:
                blockers.append("volume and price are fading")
                anti_late_penalty += 20.0

        contract_screen_complete = bool(security.get("admin_checks_complete"))
        platform_template_screen = bool(
            platform_provenance
            and security.get("checked")
            and security.get("platform_template") in {"pons-v2", "pools-fun", "zora"}
            and security.get("open_source") is not False
            and float(security.get("buy_tax") or 0.0) < 5
            and float(security.get("sell_tax") or 0.0) < 5
            and not security.get("cannot_sell")
            and not security.get("cannot_buy")
        )
        safety_complete = (
            contract_screen_complete
            and (
                candidate.chain != "base"
                or bool(security.get("simulation_checked"))
                or trusted_launch
            )
        ) or platform_provenance
        if not security.get("checked"):
            blockers.append(
                "platform provenance only; independent contract screen pending"
                if platform_provenance else "contract safety not confirmed yet"
            )
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
            sell_tax = float(security.get("sell_tax") or 0.0)
            if buy_tax >= 20 or sell_tax >= 20:
                blockers.append(f"dangerous tax {buy_tax:.0f}%/{sell_tax:.0f}%")
                hard_risk = True
            elif buy_tax >= 5 or sell_tax >= 5:
                blockers.append(f"elevated tax {buy_tax:.0f}%/{sell_tax:.0f}%")
                risk_penalty += min(12.0, max(buy_tax, sell_tax) * 0.6)
            risk_level = int(security.get("risk_level") or 0)
            if risk_level >= 80:
                blockers.append("very high contract risk")
                hard_risk = True
            elif risk_level >= 60:
                blockers.append("high contract-risk flags")
                risk_penalty += 12.0
            elif risk_level >= 20:
                risk_penalty += min(6.0, risk_level / 10.0)
            for field, label, penalty in (
                ("hidden_owner", "hidden owner", 10.0),
                ("blacklist_function", "blacklist function", 10.0),
                ("transfer_pausable", "transfers can be paused", 8.0),
                ("mintable", "supply can be minted", 8.0),
                ("proxy", "upgradeable proxy", 5.0),
                ("can_take_back_ownership", "ownership can be reclaimed", 12.0),
                ("slippage_modifiable", "tax/slippage can be changed", 10.0),
                ("personal_slippage_modifiable", "wallet tax can be changed", 12.0),
                ("trading_cooldown", "trading cooldown controls", 5.0),
            ):
                if security.get(field):
                    blockers.append(label)
                    risk_penalty += penalty
            if security.get("open_source") is False:
                blockers.append("contract source not verified")
                hard_risk = True
            owner_percent = float(security.get("owner_percent") or 0.0)
            creator_percent = float(security.get("creator_percent") or 0.0)
            concentration = float(security.get("top_unlocked_eoa_percent") or 0.0)
            if owner_percent > 10:
                blockers.append(f"owner holds {owner_percent:.0f}%")
                risk_penalty += min(15.0, (owner_percent - 10.0) * 0.5)
            if creator_percent > 10:
                blockers.append(f"creator holds {creator_percent:.0f}%")
                risk_penalty += min(18.0, (creator_percent - 10.0) * 0.6)
                hard_risk = hard_risk or creator_percent > 25
            creator_honeypots = int(security.get("creator_honeypot_count") or 0)
            if creator_honeypots:
                blockers.append(f"creator linked to {creator_honeypots} honeypot(s)")
                risk_penalty += min(35.0, 25.0 + creator_honeypots * 5.0)
            if concentration > 35:
                blockers.append(f"top unlocked wallets hold {concentration:.0f}%")
                risk_penalty += min(14.0, (concentration - 35.0) * 0.4)
            lp_unlocked = security.get("lp_unlocked_percent")
            if isinstance(lp_unlocked, (int, float)) and float(lp_unlocked) > 50 and not trusted_launch:
                blockers.append(f"{float(lp_unlocked):.0f}% of LP appears unlocked")
                hard_risk = True
        if market_cap and snapshot.liquidity_usd and market_cap / snapshot.liquidity_usd > 60:
            blockers.append("thin liquidity versus valuation")
            risk_penalty += 12.0

        final_score = round(_clamp(sum(components.values()) - anti_late_penalty - risk_penalty), 1)
        if platform_provenance and not contract_screen_complete:
            # A verified launch source is enough for a tightly-scoped SCOUT, but
            # never let provenance substitute for the independent ACTION screen.
            final_score = min(final_score, max(0.0, action_score - 0.1))
        hard_late = bool(
            anti_late_penalty >= 35.0
            or local_multiple >= 2.0
            or snapshot.price_change_5m >= 60
            or snapshot.price_change_1h >= 150
        )
        has_price = snapshot.price_usd is not None and snapshot.price_usd > 0
        if not has_price:
            blockers.append("reliable USD price unavailable")
        basic_quality = has_price and snapshot.liquidity_usd >= self.config.min_liquidity_usd and txns_5m >= 6
        action_project_evidence = bool(
            snapshot.social_links >= 2 or snapshot.exact_ca_mentions_5m >= 1
            or snapshot.smart_wallet_buys >= 1 or creator_or_narrative
        )
        project_evidence = action_project_evidence or bool(
            platform_provenance and candidate.deployer
        )
        if project_evidence and not action_project_evidence:
            risk_penalty += 4.0
            final_score = round(_clamp(final_score - 4.0), 1)
            blockers.append("launchpad provenance only; independent project identity is still thin")
        if not project_evidence:
            blockers.append("no project/social or proven smart-wallet evidence")
        inflection_confirmed = buyer_inflecting or holder_inflecting or dipped_and_absorbed
        if not inflection_confirmed:
            blockers.append("buyer/holder inflection not confirmed yet")

        missing_upgrade_gates = int(not (contract_screen_complete or platform_template_screen)) + int(
            not action_project_evidence
        )
        exceptional_flow = bool(
            platform_provenance
            and snapshot.flow_checked
            and snapshot.unique_buyers_5m >= 6
            and snapshot.net_new_wallets_5m >= 4
            and snapshot.buys_5m >= 6
            and buy_ratio >= 0.65
            and unique_buyer_acceleration >= 1.15
            and snapshot.social_links >= 1
            and local_multiple < 1.80
            and snapshot.price_change_5m < 45
        )
        upgrade_trigger: str | None = None
        if not contract_screen_complete:
            upgrade_trigger = "the independent contract screen confirms no dangerous admin controls"
        elif not action_project_evidence:
            upgrade_trigger = "a verified product/social profile or credible exact-CA mention appears"
        elif snapshot.smart_wallet_buys == 1 and snapshot.smart_wallet_sells == 0:
            upgrade_trigger = "a second proven smart/KOL wallet enters without a smart-wallet sell"
        elif not snapshot.flow_checked or snapshot.unique_buyers_5m < 5:
            upgrade_trigger = "5m unique buyers reach 5 while buy share remains at least 60%"
        elif snapshot.exact_ca_mentions_5m < 2:
            upgrade_trigger = "two credible exact-CA mentions land within 5m and keep accelerating"
        elif not holder_inflecting:
            upgrade_trigger = "net new wallets reach +3 in 5m or holder count rises 2%"
        elif buy_ratio < 0.60:
            upgrade_trigger = "5m buy share reaches 60% without a liquidity drop"
        elif not sellable_20_proxy:
            upgrade_trigger = "the free sell simulation confirms clean sellability with tax below 5%"

        pulse_confirmations = sum(
            (
                buyer_inflecting,
                holder_inflecting,
                balance_healthy,
                dipped_and_absorbed,
                creator_or_narrative,
                snapshot.social_links >= 2,
                recent_smart_attention,
                recent_platform_attention,
            )
        )
        pulse_safety = bool(
            security.get("checked")
            and security.get("open_source") is not False
            and float(security.get("buy_tax") or 0.0) < 5
            and float(security.get("sell_tax") or 0.0) < 5
            and float(security.get("top_unlocked_eoa_percent") or 0.0) <= 35
            and int(security.get("risk_level") or 0) < 60
        )
        if not live_attention:
            pulse_trigger = "GMGN 1m activity or a fresh smart/KOL/platform signal appears"
        elif pulse_confirmations < 2:
            pulse_trigger = "a second independent confirmation joins the live attention spike"
        elif not buyer_inflecting and not holder_inflecting:
            pulse_trigger = "5m buyer or holder growth starts accelerating"
        elif not action_project_evidence:
            pulse_trigger = "a verified product/social profile or proven wallet entry appears"
        elif not contract_screen_complete:
            pulse_trigger = "the independent contract screen confirms no dangerous admin controls"
        else:
            pulse_trigger = upgrade_trigger or "the score reaches the 60/100 SCOUT quality floor"

        common_gate = basic_quality and project_evidence and safety_complete and not hard_late and not hard_risk
        action_eligible = (
            common_gate and contract_screen_complete and action_project_evidence
            and final_score >= action_score and inflection_confirmed and independent_signals >= 3
        )
        a_plus_quality = (
            action_eligible and final_score >= a_plus_score and independent_signals >= 5
            and sellable_20_proxy and smart_confirmed
            and txns_5m >= 18 and buy_ratio >= 0.60 and snapshot.liquidity_usd >= 8_000
            and local_multiple < 2.0 and snapshot.deployer_sells_15m == 0
        )
        standard_scout = (
            common_gate and scout_score <= final_score < action_score
            and independent_signals >= 2 and missing_upgrade_gates <= 1
            and upgrade_trigger is not None
        )
        exceptional_scout = (
            common_gate
            and self.config.exceptional_scout_score <= final_score < action_score
            and exceptional_flow
            and independent_signals >= 3
            and missing_upgrade_gates <= 1
            and upgrade_trigger is not None
        )
        scout_eligible = standard_scout or exceptional_scout
        pulse_eligible = bool(
            basic_quality
            and pulse_safety
            and not hard_late
            and not hard_risk
            and live_attention
            and pulse_confirmations >= 2
            and final_score >= self.config.pulse_alert_score
            and final_score < scout_score
            and txns_5m >= 8
            and buy_ratio >= 0.55
            and local_multiple < 1.90
            and snapshot.price_change_5m < 45
            and (
                candidate.deployer
                or snapshot.social_links >= 1
                or recent_smart_attention
                or recent_platform_attention
            )
        )
        eligible = a_plus_quality or action_eligible or scout_eligible or pulse_eligible

        reawakening = age_minutes > 360 and (buyer_acceleration >= 1.5 or volume_acceleration >= 1.5) and buy_ratio >= 0.55
        stage = "REAWAKENING" if reawakening else ("IGNITION" if age_minutes <= 360 else "WATCH")
        if a_plus_quality:
            signal = "A+"
        elif action_eligible:
            signal = "ACTION"
        elif scout_eligible:
            signal = "SCOUT"
        elif pulse_eligible:
            signal = "PULSE"
        elif hard_late:
            signal = "AVOID LATE"
        else:
            signal = "MONITOR"

        labels = {
            "freshness": f"fresh ({age_minutes:.0f}m)",
            "direct_discovery": (
                f"GMGN-qualified {candidate.metadata.get('gmgn_launchpad', 'market')} discovery"
                if gmgn_evidence and not trusted_launch
                else f"direct {candidate.source.split(',')[0]} discovery"
            ),
            "creator_narrative": "creator/narrative evidence",
            "buyer_velocity": f"buyer velocity {buyer_acceleration:.1f}x",
            "holder_velocity": f"net new wallets +{snapshot.net_new_wallets_5m}/5m",
            "balance_absorption": f"{buy_ratio:.0%} buys with dip absorption",
            "smart_wallet": f"proven wallets +{snapshot.smart_wallet_buys}/-{snapshot.smart_wallet_sells}",
            "exact_ca_social": f"exact-CA mentions {snapshot.exact_ca_mentions_5m}/5m",
            "live_attention": (
                f"GMGN 1m activity #{attention_rank}"
                if attention_rank
                else "fresh GMGN smart/KOL/platform attention"
            ),
            "market_quality": "liquidity and sellability quality",
        }
        ranked = sorted(components.items(), key=lambda item: item[1], reverse=True)
        drivers = [labels[name] for name, value in ranked if value >= 3.0][:5]
        invalidation = (
            "5m buy share below 50%, unique buyers stop accelerating, liquidity falls 20%, "
            "or deployer/smart-wallet selling appears"
        )
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
            upgrade_trigger=(pulse_trigger if signal == "PULSE" else upgrade_trigger)
            if signal in {"PULSE", "SCOUT"}
            else None,
        )

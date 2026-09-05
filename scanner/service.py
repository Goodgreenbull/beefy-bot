from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

import aiohttp

from .config import ScannerConfig
from .feeds import (
    BankrLaunchFeed,
    BaselineLaunchFeed,
    DexScreenerProfilesFeed,
    DexScreenerEnricher,
    FactoryLaunchFeed,
    FlaunchFeed,
    GeckoTerminalNewPoolsFeed,
    GMGNReadOnlyFeed,
    OnchainFlowEnricher,
    PonsV1Enricher,
    PonsV2Enricher,
    PoolsFunLaunchFeed,
    RobinhoodMarketEnricher,
    RpcPairFeed,
    SignalOverlay,
    SmartWalletMonitor,
    TokenRiskEnricher,
    _integer,
    _number,
    _timestamp,
    platform_factory_specs,
)
from .models import Candidate, MarketSnapshot, ScoreResult
from .scoring import SignalScorer
from .state import SQLiteState


AlertCallback = Callable[[Candidate, MarketSnapshot, ScoreResult], Awaitable[None]]
ProtectionCallback = Callable[[Candidate, MarketSnapshot, dict], Awaitable[None]]


class ScannerService:
    def __init__(
        self,
        config: ScannerConfig,
        state: SQLiteState,
        alert_callback: AlertCallback | None = None,
        protection_callback: ProtectionCallback | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.alert_callback = alert_callback
        self.protection_callback = protection_callback
        self.session: aiohttp.ClientSession | None = None
        self.lock = asyncio.Lock()
        self.enricher = DexScreenerEnricher(config)
        self.robinhood_market_enricher = RobinhoodMarketEnricher(config)
        self.pons_v1_enricher = PonsV1Enricher(config)
        self.pons_v2_enricher = PonsV2Enricher(config)
        self.risk_enricher = TokenRiskEnricher(config)
        self.flow_enricher = OnchainFlowEnricher(config)
        self.scorer = SignalScorer(config)
        self.overlay = SignalOverlay(config.overlay_url)
        self.smart_wallet_monitor = SmartWalletMonitor(config)
        self.feeds = [
            BankrLaunchFeed(config),
            PoolsFunLaunchFeed(),
            FlaunchFeed(config),
            BaselineLaunchFeed(),
            GMGNReadOnlyFeed(config),
            DexScreenerProfilesFeed(),
        ]
        self.feeds.extend(GeckoTerminalNewPoolsFeed(network) for network in config.gecko_networks)
        self.feeds.extend(
            [
                FactoryLaunchFeed(
                    "base", config.base_rpc_url, platform_factory_specs(config, "base"), config
                ),
                FactoryLaunchFeed(
                    "robinhood",
                    config.robinhood_rpc_url,
                    platform_factory_specs(config, "robinhood"),
                    config,
                ),
                RpcPairFeed("base", config.base_rpc_url, config),
                RpcPairFeed("robinhood", config.robinhood_rpc_url, config),
            ]
        )
        self.last_status: dict = {
            "running": False,
            "last_cycle_at": None,
            "duration_seconds": 0.0,
            "discovered": 0,
            "enriched": 0,
            "alerts": 0,
            "pulses": 0,
            "protects": 0,
            "outcomes": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=self.config.http_timeout_seconds)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "BeefyBot-FirstLegScanner/3.0"},
            )

    async def stop(self) -> None:
        if self.session:
            await self.session.close()
            self.session = None
        self.state.close()

    async def _run_feed(self, feed) -> list[Candidate]:
        assert self.session is not None
        try:
            items = await feed.discover(self.session, self.state)
            self.state.mark_feed_success(feed.name, len(items))
            return items
        except Exception as error:
            self.state.mark_feed_error(feed.name, error)
            return []

    async def _enrich(
        self, candidate: Candidate, allow_robinhood_fallback: bool = False
    ) -> tuple[Candidate, MarketSnapshot | None, bool]:
        assert self.session is not None
        if "pons-v1" in set(candidate.source.split(",")):
            try:
                snapshot = await self.pons_v1_enricher.enrich(self.session, candidate)
                if snapshot is not None:
                    self.state.mark_feed_success("pons-v1-market", 1)
                    return candidate, snapshot, True
            except Exception as error:
                self.state.mark_feed_error("pons-v1-market", error)
        if "pons-v2" in set(candidate.source.split(",")):
            try:
                snapshot = await self.pons_v2_enricher.enrich(self.session, candidate)
                if snapshot is not None:
                    self.state.mark_feed_success("pons-v2-market", 1)
                    return candidate, snapshot, True
            except Exception as error:
                self.state.mark_feed_error("pons-v2-market", error)
        try:
            snapshot = await self.enricher.enrich(self.session, candidate)
            if snapshot is not None:
                return candidate, snapshot, True
        except Exception as error:
            self.state.mark_feed_error("dexscreener", error)
        if allow_robinhood_fallback:
            try:
                snapshot = await self.robinhood_market_enricher.enrich(
                    self.session, candidate
                )
                if snapshot is not None:
                    self.state.mark_feed_success("hooderscan", 1)
                    return candidate, snapshot, True
            except Exception as error:
                self.state.mark_feed_error("hooderscan", error)
        return candidate, None, True

    def _balanced_active_candidates(self) -> list[Candidate]:
        limit = self.config.active_candidate_limit
        pool = self.state.list_active_candidates(
            self.config.active_max_age_hours, max(limit, limit * 3)
        )
        if len(pool) <= limit:
            return pool
        reserve = max(1, limit // 3)
        per_lane_limit = max(4, limit // 6)
        selected: list[Candidate] = []
        selected_keys: set[str] = set()
        lane_counts: dict[str, int] = {}

        def source_lane(candidate: Candidate) -> str:
            sources = set(candidate.source.split(","))
            if "gmgn" in sources:
                return f"gmgn:{candidate.metadata.get('gmgn_launchpad', 'unknown')}"
            for lane in (
                "pons-v2",
                "pons-v1",
                "bankr",
                "o1-b20",
                "o1-robinhood",
                "o1-robinhood-stocks",
                "baseline",
                "flaunch",
            ):
                if lane in sources:
                    return lane
            return candidate.source.split(",")[0]

        for chain in ("base", "robinhood"):
            lanes: dict[str, list[Candidate]] = {}
            for candidate in (item for item in pool if item.chain == chain):
                lanes.setdefault(source_lane(candidate), []).append(candidate)
            for lane, rows in lanes.items():
                unseen = deque(item for item in rows if not item.metadata.get("_has_score"))
                rechecks = deque(item for item in rows if item.metadata.get("_has_score"))
                interleaved: list[Candidate] = []
                while unseen or rechecks:
                    if unseen:
                        interleaved.append(unseen.popleft())
                    if rechecks:
                        interleaved.append(rechecks.popleft())
                lanes[lane] = interleaved
            chain_selected = 0
            while lanes and chain_selected < reserve:
                for lane in list(lanes):
                    lane_key = f"{chain}:{lane}"
                    if lane_counts.get(lane_key, 0) >= per_lane_limit:
                        del lanes[lane]
                        continue
                    candidate = lanes[lane].pop(0)
                    selected.append(candidate)
                    selected_keys.add(candidate.key)
                    lane_counts[lane_key] = lane_counts.get(lane_key, 0) + 1
                    chain_selected += 1
                    if not lanes[lane]:
                        del lanes[lane]
                    if chain_selected >= reserve:
                        break
        remaining_lanes: dict[str, deque[Candidate]] = {}
        for candidate in pool:
            if candidate.key not in selected_keys:
                lane = f"{candidate.chain}:{source_lane(candidate)}"
                remaining_lanes.setdefault(lane, deque()).append(candidate)
        while remaining_lanes and len(selected) < limit:
            for lane in list(remaining_lanes):
                if lane_counts.get(lane, 0) >= per_lane_limit:
                    del remaining_lanes[lane]
                    continue
                candidate = remaining_lanes[lane].popleft()
                selected.append(candidate)
                selected_keys.add(candidate.key)
                lane_counts[lane] = lane_counts.get(lane, 0) + 1
                if not remaining_lanes[lane]:
                    del remaining_lanes[lane]
                if len(selected) >= limit:
                    break
        return selected

    def _robinhood_market_keys(self, candidates: list[Candidate]) -> set[str]:
        rows = [candidate for candidate in candidates if candidate.chain == "robinhood"]
        limit = min(self.config.max_robinhood_market_checks_per_cycle, len(rows))
        if not limit:
            return set()
        fresh_count = max(1, limit // 2)
        chosen = rows[:fresh_count]
        remaining = rows[fresh_count:]
        if remaining and len(chosen) < limit:
            cursor_key = "hooderscan:rotation"
            offset = _integer(self.state.get_cursor(cursor_key), 0) % len(remaining)
            rotated = remaining[offset:] + remaining[:offset]
            take = min(limit - len(chosen), len(rotated))
            chosen.extend(rotated[:take])
            self.state.set_cursor(cursor_key, str((offset + take) % len(remaining)))
        return {candidate.key for candidate in chosen}

    @staticmethod
    def _apply_external_signals(
        snapshot: MarketSnapshot,
        overlay: dict | None,
        wallet_signal: dict | None,
    ) -> None:
        overlay = overlay or {}
        wallet_signal = wallet_signal or {}
        snapshot.social_velocity = _number(
            overlay.get("socialVelocity", overlay.get("social_velocity", snapshot.social_velocity)),
            snapshot.social_velocity,
        )
        snapshot.social_links = max(
            snapshot.social_links,
            _integer(overlay.get("socialLinks", overlay.get("social_links", snapshot.social_links))),
        )
        snapshot.smart_wallet_buys += _integer(
            overlay.get("smartWalletBuys", overlay.get("smart_wallet_buys", 0))
        ) + _integer(wallet_signal.get("smart_wallet_buys", 0))
        snapshot.smart_wallet_sells += _integer(
            overlay.get("smartWalletSells", overlay.get("smart_wallet_sells", 0))
        ) + _integer(wallet_signal.get("smart_wallet_sells", 0))
        snapshot.smart_wallet_net_usd += _number(
            overlay.get("smartWalletNetUsd", overlay.get("smart_wallet_net_usd", 0))
        ) + _number(wallet_signal.get("smart_wallet_net_usd", 0))
        snapshot.exact_ca_mentions_5m = _integer(
            overlay.get("exactCaMentions5m", overlay.get("exact_ca_mentions_5m", 0))
        )
        snapshot.exact_ca_mentions_15m = _integer(
            overlay.get("exactCaMentions15m", overlay.get("exact_ca_mentions_15m", 0))
        )
        snapshot.credible_social_mentions_5m = _integer(
            overlay.get(
                "credibleSocialMentions5m",
                overlay.get("credible_social_mentions_5m", 0),
            )
        )
        snapshot.creator_reputation = max(
            snapshot.creator_reputation,
            min(1.0, max(0.0, _number(overlay.get("creatorReputation", 0)))),
        )
        snapshot.narrative_score = max(
            snapshot.narrative_score,
            min(1.0, max(0.0, _number(overlay.get("narrativeScore", 0)))),
        )
        snapshot.raw["creator_activity_score"] = min(
            1.0,
            max(
                0.0,
                _number(
                    overlay.get(
                        "creatorActivityScore",
                        overlay.get("creator_activity_score", 0),
                    )
                ),
            ),
        )
        snapshot.deployer_sells_15m += _integer(
            overlay.get("deployerSells15m", overlay.get("deployer_sells_15m", 0))
        )

    @staticmethod
    def _apply_gmgn_evidence(candidate: Candidate, snapshot: MarketSnapshot) -> None:
        market = candidate.metadata.get("gmgn_market")
        if not isinstance(market, dict):
            return
        if not snapshot.price_usd:
            snapshot.price_usd = _number(market.get("price_usd"), 0.0) or None
        if not snapshot.liquidity_usd:
            snapshot.liquidity_usd = _number(market.get("liquidity_usd"))
        if not snapshot.market_cap_usd:
            snapshot.market_cap_usd = _number(market.get("market_cap_usd"), 0.0) or None
        if not snapshot.volume_5m_usd:
            snapshot.volume_5m_usd = _number(market.get("volume_5m_usd"))
        if snapshot.buys_5m + snapshot.sells_5m == 0:
            snapshot.buys_5m = _integer(market.get("buys_5m"))
            snapshot.sells_5m = _integer(market.get("sells_5m"))
        snapshot.holder_count = max(
            snapshot.holder_count or 0,
            _integer(market.get("holder_count")),
        ) or None
        snapshot.social_links = max(
            snapshot.social_links,
            _integer(candidate.metadata.get("profile_social_links")),
        )
        # GMGN signal events are useful attention evidence, but they do not
        # identify distinct wallets. Keep them out of the proven-wallet count;
        # only pool-confirmed addresses collected by Beefy may fill that field.
        gmgn_security = market.get("security")
        gmgn_security = gmgn_security if isinstance(gmgn_security, dict) else {}
        existing = snapshot.raw.get("security")
        existing = dict(existing) if isinstance(existing, dict) else {}
        providers = list(
            dict.fromkeys(
                [str(item) for item in existing.get("providers", [])]
                + [str(item) for item in gmgn_security.get("providers", [])]
            )
        )
        merged = {**gmgn_security, **existing}
        merged["providers"] = providers
        for field in (
            "checked",
            "admin_checks_complete",
            "simulation_checked",
            "sell_simulation_success",
            "is_honeypot",
            "cannot_buy",
            "cannot_sell",
            "fake_token",
        ):
            merged[field] = bool(existing.get(field) or gmgn_security.get(field))
        for field in (
            "buy_tax",
            "sell_tax",
            "top_unlocked_eoa_percent",
            "creator_percent",
            "risk_level",
        ):
            values = [
                _number(value)
                for value in (existing.get(field), gmgn_security.get(field))
                if value not in (None, "")
            ]
            if values:
                merged[field] = max(values)
        open_source_values = [
            value
            for value in (existing.get("open_source"), gmgn_security.get("open_source"))
            if value is not None
        ]
        if open_source_values:
            merged["open_source"] = all(bool(value) for value in open_source_values)
        snapshot.raw["security"] = merged
        snapshot.raw["gmgn"] = {
            "launchpad": candidate.metadata.get("gmgn_launchpad"),
            "routes": candidate.metadata.get("gmgn_routes", []),
            "smart_count": candidate.metadata.get("gmgn_smart_count", 0),
            "kol_count": candidate.metadata.get("gmgn_kol_count", 0),
            "recent_signal_types": candidate.metadata.get("gmgn_recent_signal_types", []),
            "recent_smart_signals": candidate.metadata.get("gmgn_recent_smart_signals", 0),
            "recent_platform_signals": candidate.metadata.get("gmgn_recent_platform_signals", 0),
            "attention_rank": candidate.metadata.get("gmgn_attention_rank"),
            "attention_source": candidate.metadata.get("gmgn_attention_source"),
            "creator_token_count": candidate.metadata.get("gmgn_creator_token_count", 0),
        }

    @staticmethod
    def _apply_flow_signals(snapshot: MarketSnapshot, flow: dict | None) -> None:
        flow = flow or {}
        for field in (
            "unique_buyers_5m",
            "unique_buyers_15m",
            "unique_sellers_5m",
            "unique_sellers_15m",
            "net_new_wallets_5m",
            "net_new_wallets_15m",
            "deployer_sells_15m",
        ):
            setattr(snapshot, field, _integer(flow.get(field), getattr(snapshot, field)))
        if snapshot.buys_5m + snapshot.sells_5m == 0:
            snapshot.buys_5m = _integer(flow.get("buy_events_5m"), 0)
            snapshot.sells_5m = _integer(flow.get("sell_events_5m"), 0)
        if snapshot.buys_1h + snapshot.sells_1h == 0:
            snapshot.buys_1h = _integer(flow.get("buy_events_15m"), 0)
            snapshot.sells_1h = _integer(flow.get("sell_events_15m"), 0)
        snapshot.flow_checked = bool(flow.get("flow_checked", snapshot.flow_checked))

    async def run_cycle(self) -> dict:
        if self.lock.locked():
            return self.status()
        await self.start()
        assert self.session is not None
        started = time.monotonic()
        async with self.lock:
            self.last_status["running"] = True
            completed_warmups = _integer(self.state.get_cursor("scanner:warmup_cycles"), 0)
            warmup_complete = completed_warmups >= self.config.warmup_cycles
            discovered_lists = await asyncio.gather(*(self._run_feed(feed) for feed in self.feeds))
            discovered = [candidate for rows in discovered_lists for candidate in rows]
            for candidate in discovered:
                if not warmup_complete:
                    candidate.metadata["bootstrap_candidate"] = True
                self.state.upsert_candidate(candidate)

            candidates = self._balanced_active_candidates()
            outcome_candidates = self.state.list_outcome_candidates(self.config.outcome_candidate_limit)
            outcome_keys = {candidate.key for candidate in outcome_candidates}
            candidates = list({candidate.key: candidate for candidate in candidates + outcome_candidates}.values())
            overlay_task = asyncio.create_task(self.overlay.fetch(self.session))
            wallet_task = asyncio.create_task(
                self.smart_wallet_monitor.collect(self.session, self.state, candidates)
            )
            robinhood_market_keys = self._robinhood_market_keys(candidates)
            enriched = await asyncio.gather(
                *(
                    self._enrich(candidate, candidate.key in robinhood_market_keys)
                    for candidate in candidates
                )
            )
            try:
                overlays = await overlay_task
                self.state.mark_feed_success("signal-overlay", len(overlays))
            except Exception as error:
                overlays = {}
                self.state.mark_feed_error("signal-overlay", error)
            try:
                wallet_signals = await wallet_task
                self.state.mark_feed_success("smart-wallets", len(wallet_signals))
            except Exception as error:
                wallet_signals = {}
                self.state.mark_feed_error("smart-wallets", error)

            direct_sources = {
                "bankr",
                "flaunch",
                "baseline",
                "o1-b20",
                "o1-robinhood",
                "o1-robinhood-stocks",
                "pons-v1",
                "pons-v2",
                "pools-fun",
                "basestonk",
                "gmgn",
            }
            flow_candidates = [
                (candidate, snapshot)
                for candidate, snapshot, _ in enriched
                if snapshot is not None
                and not snapshot.flow_checked
                and not self.state.blocked_identity_theme(candidate)
                and len(snapshot.pair_address or candidate.pair_address or "") == 42
                and snapshot.price_usd
                and snapshot.liquidity_usd >= self.config.min_liquidity_usd
            ]

            def flow_priority(item: tuple[Candidate, MarketSnapshot]) -> float:
                candidate, snapshot = item
                external = overlays.get(candidate.key) or {}
                txns_5m = snapshot.buys_5m + snapshot.sells_5m
                return (
                    int(bool(set(candidate.source.split(",")) & direct_sources)) * 6.0
                    + min(12.0, txns_5m / 10.0)
                    + min(8.0, snapshot.volume_5m_usd / 2_000.0)
                    + min(6.0, snapshot.social_links * 1.5)
                    + _integer(external.get("exactCaMentions5m"), 0) * 3.0
                    + _integer(external.get("credibleSocialMentions5m"), 0) * 5.0
                    + _number(external.get("creatorActivityScore"), 0.0) * 5.0
                    + min(8.0, _number(candidate.metadata.get("gmgn_priority")) / 10.0)
                )

            ranked_flow = sorted(flow_candidates, key=flow_priority, reverse=True)
            direct_ranked = [
                item
                for item in ranked_flow
                if set(item[0].source.split(",")) & direct_sources
            ]
            limit = self.config.max_flow_checks_per_cycle
            direct_reserve = min(len(direct_ranked), max(1, limit // 2))
            flow_targets = direct_ranked[:direct_reserve]
            selected_flow_keys = {item[0].key for item in flow_targets}
            flow_targets.extend(
                item
                for item in ranked_flow
                if item[0].key not in selected_flow_keys
            )
            flow_targets = flow_targets[:limit]
            flow_results = await asyncio.gather(
                *(
                    self.flow_enricher.enrich(self.session, candidate, snapshot)
                    for candidate, snapshot in flow_targets
                ),
                return_exceptions=True,
            )
            flow_signals: dict[str, dict] = {}
            flow_errors = [item for item in flow_results if isinstance(item, Exception)]
            for (candidate, _), flow in zip(flow_targets, flow_results):
                if isinstance(flow, dict):
                    flow_signals[candidate.key] = flow
            if flow_signals or not flow_targets:
                self.state.mark_feed_success("onchain-flow", len(flow_signals))
            elif flow_errors:
                self.state.mark_feed_error("onchain-flow", flow_errors[0])

            thresholds = self.state.calibrated_thresholds(self.config)
            enriched_count = 0
            alert_count = 0
            pulse_count = 0
            protect_count = 0
            outcome_count = 0
            prepared: list[tuple[Candidate, MarketSnapshot, list[MarketSnapshot], ScoreResult]] = []
            for candidate, snapshot, request_succeeded in enriched:
                if snapshot is None:
                    if request_succeeded and candidate.key in outcome_keys:
                        captured_at = datetime.now(timezone.utc)
                        outcome_count += self.state.record_missing_market(
                            candidate.key,
                            captured_at,
                            self.config.outcome_missing_confirmations,
                        )
                        if (
                            self.protection_callback
                            and protect_count < self.config.max_protect_alerts_per_cycle
                            and self.state.market_missing_confirmed(
                                candidate.key, self.config.outcome_missing_confirmations
                            )
                        ):
                            missing_snapshot = MarketSnapshot(
                                chain=candidate.chain,
                                token_address=candidate.token_address,
                                captured_at=captured_at,
                                price_usd=0.0,
                                liquidity_usd=0.0,
                                source="confirmed-market-disappearance",
                            )
                            protection = self.state.protection_needed(
                                candidate.key, missing_snapshot
                            )
                            if protection:
                                protection["reasons"] = [
                                    "market disappeared after repeated successful checks"
                                ] + list(protection.get("reasons") or [])
                                try:
                                    await self.protection_callback(
                                        candidate, missing_snapshot, protection
                                    )
                                    self.state.record_protection(candidate.key, protection)
                                    self.state.mark_feed_success("telegram-protect", 1)
                                    protect_count += 1
                                except Exception as error:
                                    self.state.mark_feed_error("telegram-protect", error)
                    continue
                if candidate.key in outcome_keys:
                    self.state.reset_market_failures(candidate.key)
                enriched_count += 1
                if snapshot.raw.get("url"):
                    candidate.chart_url = str(snapshot.raw["url"])
                if snapshot.raw.get("name"):
                    candidate.name = str(snapshot.raw["name"])
                if snapshot.raw.get("symbol"):
                    candidate.symbol = str(snapshot.raw["symbol"])
                pair_created_at = _timestamp(snapshot.raw.get("pair_created_at"))
                if pair_created_at and candidate.launch_at is None:
                    candidate.launch_at = pair_created_at
                if snapshot.pair_address:
                    candidate.pair_address = snapshot.pair_address
                snapshot.social_links = max(
                    snapshot.social_links,
                    _integer(candidate.metadata.get("profile_social_links"), 0),
                )
                self.state.upsert_candidate(candidate)
                self._apply_external_signals(
                    snapshot,
                    overlays.get(candidate.key),
                    wallet_signals.get(candidate.key),
                )
                self._apply_gmgn_evidence(candidate, snapshot)
                self._apply_flow_signals(snapshot, flow_signals.get(candidate.key))
                candidate.metadata["identity_risk"] = self.state.identity_risk(candidate)
                candidate.metadata["deployer_reputation"] = self.state.deployer_reputation(candidate)
                snapshot.creator_reputation = max(
                    snapshot.creator_reputation,
                    _number(candidate.metadata["deployer_reputation"].get("score"), 0.0),
                )
                self.state.upsert_candidate(candidate)
                history = self.state.recent_snapshots(candidate.key)
                cached_risk = self.state.get_security_profile(
                    candidate.key, self.config.security_cache_minutes
                )
                if cached_risk:
                    snapshot.raw["security"] = cached_risk.to_record()
                    snapshot.holder_count = cached_risk.holder_count
                pre_result = self.scorer.score(
                    candidate,
                    snapshot,
                    history,
                    min_alert_score=thresholds["watch"],
                    strong_alert_score=thresholds["buy"],
                    scout_alert_score=thresholds["scout"],
                )
                prepared.append((candidate, snapshot, history, pre_result))

            risk_targets = [
                item for item in sorted(prepared, key=lambda item: item[3].score, reverse=True)
                if (
                    not (item[1].raw.get("security") or {}).get("admin_checks_complete")
                    or (
                        item[0].chain == "base"
                        and not (item[1].raw.get("security") or {}).get("simulation_checked")
                        and not any(
                            source in {
                                "bankr",
                                "flaunch",
                                "baseline",
                                "o1-b20",
                                "o1-robinhood",
                                "o1-robinhood-stocks",
                                "pons-v1",
                                "pons-v2",
                                "pools-fun",
                                "basestonk",
                            }
                            for source in item[0].source.split(",")
                        )
                    )
                )
                and item[3].score >= self.config.security_check_min_score
            ][: self.config.max_security_checks_per_cycle]
            checked_profiles = await asyncio.gather(
                *(self.risk_enricher.check(self.session, item[0]) for item in risk_targets),
                return_exceptions=True,
            )
            for item, profile in zip(risk_targets, checked_profiles):
                candidate, snapshot, _, _ = item
                if isinstance(profile, Exception):
                    self.state.mark_feed_error("token-safety", profile)
                    continue
                snapshot.raw["security"] = profile.to_record()
                snapshot.holder_count = profile.holder_count
                if profile.checked:
                    self.state.upsert_security_profile(profile)
                    self.state.mark_feed_success("token-safety", 1)

            finalised: list[tuple[Candidate, MarketSnapshot, ScoreResult]] = []
            for candidate, snapshot, history, _ in prepared:
                result = self.scorer.score(
                    candidate,
                    snapshot,
                    history,
                    min_alert_score=thresholds["watch"],
                    strong_alert_score=thresholds["buy"],
                    scout_alert_score=thresholds["scout"],
                )
                self.state.add_snapshot(candidate.key, snapshot)
                candidate.metadata["first_detected_market_cap_usd"] = (
                    self.state.first_detected_market_cap(candidate.key)
                )
                outcome_count += self.state.update_alert_outcomes(candidate.key, snapshot)
                self.state.update_score(candidate.key, result)
                protection = self.state.protection_needed(candidate.key, snapshot)
                if (
                    protection
                    and self.protection_callback
                    and protect_count < self.config.max_protect_alerts_per_cycle
                ):
                    try:
                        await self.protection_callback(candidate, snapshot, protection)
                        self.state.record_protection(candidate.key, protection)
                        self.state.mark_feed_success("telegram-protect", 1)
                        protect_count += 1
                    except Exception as error:
                        self.state.mark_feed_error("telegram-protect", error)
                finalised.append((candidate, snapshot, result))

            tier_priority = {"A+": 4, "ACTION": 3, "SCOUT": 2, "PULSE": 1}
            finalised.sort(
                key=lambda item: (tier_priority.get(item[2].signal, 0), item[2].score),
                reverse=True,
            )
            for candidate, snapshot, result in finalised:
                if (
                    warmup_complete
                    and result.eligible
                    and (
                        not candidate.metadata.get("bootstrap_candidate")
                        or result.stage == "REAWAKENING"
                    )
                    and self.alert_callback
                    and alert_count < self.config.max_alerts_per_cycle
                    and (
                        result.signal != "PULSE"
                        or pulse_count < self.config.max_pulse_alerts_per_cycle
                    )
                    and self.state.alert_allowed(
                        candidate.key,
                        result,
                        self.config.alert_cooldown_minutes,
                        self.config.alert_score_upgrade,
                        self.config.token_realert_hours,
                    )
                ):
                    try:
                        if result.signal != "PULSE":
                            try:
                                self.state.apply_target_estimate(candidate, snapshot, result)
                                self.state.mark_feed_success("target-model", 1)
                            except Exception as error:
                                self.state.mark_feed_error("target-model", error)
                        await self.alert_callback(candidate, snapshot, result)
                        self.state.mark_feed_success("telegram-alerts", 1)
                        alert_id = self.state.record_alert(candidate.key, result, snapshot)
                        if result.signal != "PULSE":
                            try:
                                early_buyers = await self.smart_wallet_monitor.observe_early_buyers(
                                    self.session, candidate, snapshot
                                )
                                self.state.record_alert_wallets(
                                    alert_id, candidate.chain, early_buyers
                                )
                                self.state.mark_feed_success("wallet-curation", len(early_buyers))
                            except Exception as error:
                                self.state.mark_feed_error("wallet-curation", error)
                        alert_count += 1
                        pulse_count += int(result.signal == "PULSE")
                    except Exception as error:
                        self.state.mark_feed_error("telegram-alerts", error)

            if not warmup_complete:
                self.state.set_cursor("scanner:warmup_cycles", str(completed_warmups + 1))

            self.last_status = {
                "running": False,
                "last_cycle_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": round(time.monotonic() - started, 2),
                "discovered": len(discovered),
                "enriched": enriched_count,
                "alerts": alert_count,
                "pulses": pulse_count,
                "protects": protect_count,
                "outcomes": outcome_count,
                "gmgn_candidates": sum(
                    "gmgn" in candidate.source.split(",") for candidate in discovered
                ),
                "watch_threshold": thresholds["watch"],
                "buy_threshold": thresholds["buy"],
                "scout_threshold": thresholds["scout"],
                "exceptional_scout_threshold": self.config.exceptional_scout_score,
                "calibration_samples": thresholds["samples"],
                "calibrated": thresholds["calibrated"],
                "errors": sum(1 for item in self.state.health() if item.get("last_error")),
            }
            last_prune = _integer(self.state.get_cursor("maintenance:last_prune"), 0)
            now_epoch = int(time.time())
            if now_epoch - last_prune >= 3_600:
                self.state.prune()
                self.state.set_cursor("maintenance:last_prune", str(now_epoch))
            return self.status()

    def status(self) -> dict:
        wallet_report = self.state.smart_wallet_report()
        wallet_report["qualified"] = len(
            self.state.curated_smart_wallets(
                self.config.smart_wallet_min_observations,
                self.config.smart_wallet_min_win_rate,
                self.config.smart_wallet_min_average_return,
            )
        )
        return {
            **self.last_status,
            **self.state.stats(),
            "outcome_report": self.state.outcome_report(),
            "smart_wallet_report": wallet_report,
            "near_misses": self.state.near_misses(),
            "screening_report": self.state.screening_report(),
            "feeds": self.state.health(),
        }

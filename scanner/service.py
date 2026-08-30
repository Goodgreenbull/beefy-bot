from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import aiohttp

from .config import ScannerConfig
from .feeds import (
    BankrLaunchFeed,
    BaselineLaunchFeed,
    ClankerLaunchFeed,
    DexScreenerProfilesFeed,
    DexScreenerEnricher,
    FactoryLaunchFeed,
    FlaunchFeed,
    GeckoTerminalNewPoolsFeed,
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


class ScannerService:
    def __init__(
        self,
        config: ScannerConfig,
        state: SQLiteState,
        alert_callback: AlertCallback | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.alert_callback = alert_callback
        self.session: aiohttp.ClientSession | None = None
        self.lock = asyncio.Lock()
        self.enricher = DexScreenerEnricher(config)
        self.risk_enricher = TokenRiskEnricher(config)
        self.scorer = SignalScorer(config)
        self.overlay = SignalOverlay(config.overlay_url)
        self.smart_wallet_monitor = SmartWalletMonitor(config)
        self.feeds = [
            BankrLaunchFeed(config),
            FlaunchFeed(config),
            ClankerLaunchFeed(),
            BaselineLaunchFeed(),
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

    async def _enrich(self, candidate: Candidate) -> tuple[Candidate, MarketSnapshot | None]:
        assert self.session is not None
        try:
            snapshot = await self.enricher.enrich(self.session, candidate)
            return candidate, snapshot
        except Exception as error:
            self.state.mark_feed_error("dexscreener", error)
            return candidate, None

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
                self.state.upsert_candidate(candidate)

            candidates = self.state.list_active_candidates(
                self.config.active_max_age_hours, self.config.active_candidate_limit
            )
            outcome_candidates = self.state.list_outcome_candidates(self.config.outcome_candidate_limit)
            candidates = list({candidate.key: candidate for candidate in candidates + outcome_candidates}.values())
            overlay_task = asyncio.create_task(self.overlay.fetch(self.session))
            wallet_task = asyncio.create_task(
                self.smart_wallet_monitor.collect(self.session, self.state, candidates)
            )
            enriched = await asyncio.gather(*(self._enrich(candidate) for candidate in candidates))
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

            thresholds = self.state.calibrated_thresholds(self.config)
            enriched_count = 0
            alert_count = 0
            outcome_count = 0
            prepared: list[tuple[Candidate, MarketSnapshot, list[MarketSnapshot], ScoreResult]] = []
            for candidate, snapshot in enriched:
                if snapshot is None:
                    continue
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
                candidate.metadata["identity_risk"] = self.state.identity_risk(candidate)
                self.state.upsert_candidate(candidate)
                history = self.state.recent_snapshots(candidate.key)
                cached_risk = self.state.get_security_profile(
                    candidate.key, self.config.security_cache_minutes
                )
                if cached_risk:
                    snapshot.raw["security"] = cached_risk.to_record()
                pre_result = self.scorer.score(
                    candidate,
                    snapshot,
                    history,
                    min_alert_score=thresholds["watch"],
                    strong_alert_score=thresholds["buy"],
                )
                prepared.append((candidate, snapshot, history, pre_result))

            risk_targets = [
                item for item in sorted(prepared, key=lambda item: item[3].score, reverse=True)
                if not (item[1].raw.get("security") or {}).get("checked")
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
                if profile.checked:
                    self.state.upsert_security_profile(profile)
                    self.state.mark_feed_success("token-safety", 1)

            for candidate, snapshot, history, _ in prepared:
                result = self.scorer.score(
                    candidate,
                    snapshot,
                    history,
                    min_alert_score=thresholds["watch"],
                    strong_alert_score=thresholds["buy"],
                )
                self.state.add_snapshot(candidate.key, snapshot)
                outcome_count += self.state.update_alert_outcomes(candidate.key, snapshot)
                self.state.update_score(candidate.key, result)
                if (
                    warmup_complete
                    and result.eligible
                    and self.alert_callback
                    and alert_count < self.config.max_alerts_per_cycle
                    and self.state.alert_allowed(
                        candidate.key,
                        result,
                        self.config.alert_cooldown_minutes,
                        self.config.alert_score_upgrade,
                    )
                ):
                    try:
                        await self.alert_callback(candidate, snapshot, result)
                        self.state.mark_feed_success("telegram-alerts", 1)
                        alert_id = self.state.record_alert(candidate.key, result, snapshot)
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
                "outcomes": outcome_count,
                "watch_threshold": thresholds["watch"],
                "buy_threshold": thresholds["buy"],
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
            "feeds": self.state.health(),
        }

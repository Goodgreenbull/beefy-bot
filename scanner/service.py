from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import aiohttp

from .config import ScannerConfig
from .feeds import (
    BankrLaunchFeed,
    DexScreenerEnricher,
    FlaunchFeed,
    GeckoTerminalNewPoolsFeed,
    RpcPairFeed,
    SignalOverlay,
    SmartWalletMonitor,
    _integer,
    _number,
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
        self.scorer = SignalScorer(config)
        self.overlay = SignalOverlay(config.overlay_url)
        self.smart_wallet_monitor = SmartWalletMonitor(config)
        self.feeds = [BankrLaunchFeed(config), FlaunchFeed(config)]
        self.feeds.extend(GeckoTerminalNewPoolsFeed(network) for network in config.gecko_networks)
        self.feeds.extend(
            [
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
            discovered_lists = await asyncio.gather(*(self._run_feed(feed) for feed in self.feeds))
            discovered = [candidate for rows in discovered_lists for candidate in rows]
            for candidate in discovered:
                self.state.upsert_candidate(candidate)

            candidates = self.state.list_active_candidates(
                self.config.active_max_age_hours, self.config.active_candidate_limit
            )
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

            enriched_count = 0
            alert_count = 0
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
                if snapshot.pair_address:
                    candidate.pair_address = snapshot.pair_address
                self.state.upsert_candidate(candidate)
                self._apply_external_signals(
                    snapshot,
                    overlays.get(candidate.key),
                    wallet_signals.get(candidate.key),
                )
                history = self.state.recent_snapshots(candidate.key)
                result = self.scorer.score(candidate, snapshot, history)
                self.state.add_snapshot(candidate.key, snapshot)
                self.state.update_score(candidate.key, result)
                if (
                    result.eligible
                    and self.alert_callback
                    and self.state.alert_allowed(
                        candidate.key,
                        result,
                        self.config.alert_cooldown_minutes,
                        self.config.alert_score_upgrade,
                    )
                ):
                    try:
                        await self.alert_callback(candidate, snapshot, result)
                        self.state.record_alert(candidate.key, result)
                        alert_count += 1
                    except Exception as error:
                        self.state.mark_feed_error("telegram-alerts", error)

            self.last_status = {
                "running": False,
                "last_cycle_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": round(time.monotonic() - started, 2),
                "discovered": len(discovered),
                "enriched": enriched_count,
                "alerts": alert_count,
                "errors": sum(1 for item in self.state.health() if item.get("last_error")),
            }
            last_prune = _integer(self.state.get_cursor("maintenance:last_prune"), 0)
            now_epoch = int(time.time())
            if now_epoch - last_prune >= 3_600:
                self.state.prune()
                self.state.set_cursor("maintenance:last_prune", str(now_epoch))
            return self.status()

    def status(self) -> dict:
        return {**self.last_status, **self.state.stats(), "feeds": self.state.health()}

"""Read-only smoke check for Beefy's free public scanner dependencies."""

from __future__ import annotations

import asyncio

import aiohttp

from scanner.config import ScannerConfig
from scanner.feeds import (
    BaselineLaunchFeed,
    ClankerLaunchFeed,
    FactoryLaunchFeed,
    TokenRiskEnricher,
    platform_factory_specs,
)
from scanner.models import Candidate
from scanner.state import SQLiteState


async def main() -> int:
    config = ScannerConfig(
        rpc_lookback_blocks=200,
        rpc_max_block_span=200,
        http_timeout_seconds=20,
    )
    state = SQLiteState(":memory:")
    failed = False
    recent_candidates: dict[str, Candidate] = {}
    timeout = aiohttp.ClientTimeout(total=config.http_timeout_seconds)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "BeefyBot-validation/3.0"},
        ) as session:
            feeds = [
                BaselineLaunchFeed(),
                ClankerLaunchFeed(),
                FactoryLaunchFeed(
                    "base",
                    config.base_rpc_url,
                    platform_factory_specs(config, "base"),
                    config,
                ),
                FactoryLaunchFeed(
                    "robinhood",
                    config.robinhood_rpc_url,
                    platform_factory_specs(config, "robinhood"),
                    config,
                ),
            ]
            for feed in feeds:
                try:
                    rows = await feed.discover(session, state)
                    if rows and (
                        rows[0].chain not in recent_candidates
                        or feed.name.startswith("platform-launches")
                    ):
                        recent_candidates[rows[0].chain] = rows[0]
                    detail = rows[0].source if rows else "healthy; no event in lookback"
                    print(f"{feed.name}: {len(rows)} ({detail})")
                except Exception as error:  # smoke script should report every dependency
                    failed = True
                    print(f"{feed.name}: ERROR {type(error).__name__}: {str(error)[:160]}")

            for chain, candidate in recent_candidates.items():
                profile = await TokenRiskEnricher(config).check(
                    session,
                    candidate,
                )
                print(
                    f"token-safety:{chain}:{candidate.token_address}: "
                    f"checked={profile.checked} providers={','.join(profile.providers) or 'none'} "
                    f"admin={profile.admin_checks_complete} "
                    f"simulation={profile.simulation_checked} honeypot={profile.is_honeypot} "
                    f"error={profile.error or 'none'}"
                )
                failed = failed or not profile.admin_checks_complete
                if chain == "base" and not candidate.source.startswith("o1-"):
                    failed = failed or not profile.simulation_checked
    finally:
        state.close()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

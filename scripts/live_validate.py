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
                    detail = rows[0].source if rows else "healthy; no event in lookback"
                    print(f"{feed.name}: {len(rows)} ({detail})")
                except Exception as error:  # smoke script should report every dependency
                    failed = True
                    print(f"{feed.name}: ERROR {type(error).__name__}: {str(error)[:160]}")

            safety_checks = (
                ("base", "0x4200000000000000000000000000000000000006"),
                ("robinhood", "0x0bd7d308f8e1639fab988df18a8011f41eacad73"),
            )
            for chain, address in safety_checks:
                profile = await TokenRiskEnricher(config).check(
                    session,
                    Candidate(chain=chain, token_address=address, source="validation"),
                )
                print(
                    f"token-safety:{chain}: "
                    f"checked={profile.checked} providers={','.join(profile.providers) or 'none'} "
                    f"honeypot={profile.is_honeypot}"
                )
                failed = failed or not profile.checked
    finally:
        state.close()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

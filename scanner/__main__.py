from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace

from .config import ScannerConfig
from .service import ScannerService
from .state import SQLiteState


async def _once(state_path: str | None, limit: int | None) -> None:
    config = ScannerConfig.from_env()
    if state_path:
        config = replace(config, state_db=state_path)
    if limit:
        config = replace(config, active_candidate_limit=max(1, limit))
    state = SQLiteState(config.state_db)
    service = ScannerService(config, state, alert_callback=None)
    try:
        status = await service.run_cycle()
        print(json.dumps(status, indent=2))
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one alerts-disabled Beefy scanner cycle")
    parser.add_argument("--state", help="SQLite path for the dry run")
    parser.add_argument("--limit", type=int, help="Maximum candidates to enrich")
    args = parser.parse_args()
    asyncio.run(_once(args.state, args.limit))


if __name__ == "__main__":
    main()

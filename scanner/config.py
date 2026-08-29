from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


BASE_QUOTES = {
    "0x4200000000000000000000000000000000000006",  # WETH
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
}


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in os.getenv(name, default).split(",") if part.strip())


def _json_map(name: str) -> dict:
    try:
        parsed = json.loads(os.getenv(name, "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


@dataclass(slots=True)
class ScannerConfig:
    enabled: bool = True
    interval_seconds: int = 300
    state_db: str = "scanner_state.sqlite3"
    alert_chat_id: str | None = None
    min_alert_score: float = 68.0
    strong_alert_score: float = 80.0
    alert_cooldown_minutes: int = 45
    alert_score_upgrade: float = 10.0
    active_candidate_limit: int = 50
    active_max_age_hours: int = 720
    max_alerts_per_cycle: int = 3
    warmup_cycles: int = 1
    min_liquidity_usd: float = 3_000.0
    max_market_cap_usd: float = 5_000_000.0
    late_price_change_5m: float = 90.0
    late_price_change_1h: float = 250.0
    bankr_url: str = "https://api.bankr.bot/token-launches"
    flaunch_url: str = "https://data.flayerlabs.xyz/tokens"
    gecko_networks: tuple[str, ...] = ("base",)
    base_rpc_url: str = "https://mainnet.base.org"
    robinhood_rpc_url: str = "https://rpc.mainnet.chain.robinhood.com"
    rpc_lookback_blocks: int = 1_800
    rpc_max_block_span: int = 1_800
    http_timeout_seconds: int = 12
    dex_concurrency: int = 6
    smart_wallets: tuple[str, ...] = ()
    overlay_url: str | None = None
    quote_tokens: dict[str, set[str]] = field(default_factory=lambda: {"base": set(BASE_QUOTES)})

    @classmethod
    def from_env(cls) -> "ScannerConfig":
        interval = max(60, min(600, _int("SCANNER_INTERVAL_SECONDS", 300)))
        raw_quotes = _json_map("SCANNER_QUOTE_TOKENS_JSON")
        quote_tokens: dict[str, set[str]] = {"base": set(BASE_QUOTES)}
        for chain, addresses in raw_quotes.items():
            if isinstance(addresses, list):
                quote_tokens[str(chain).lower()] = {str(address).lower() for address in addresses}

        return cls(
            enabled=_bool("SCANNER_ENABLED", True),
            interval_seconds=interval,
            state_db=os.getenv("SCANNER_STATE_DB", "scanner_state.sqlite3"),
            alert_chat_id=(
                os.getenv("SIGNAL_TELEGRAM_CHAT_ID")
                or os.getenv("ADMIN_CHAT_ID")
                or os.getenv("TELEGRAM_GROUP_ID")
            ),
            min_alert_score=_float("SCANNER_MIN_ALERT_SCORE", 68.0),
            strong_alert_score=_float("SCANNER_STRONG_ALERT_SCORE", 80.0),
            alert_cooldown_minutes=_int("SCANNER_ALERT_COOLDOWN_MINUTES", 45),
            alert_score_upgrade=_float("SCANNER_ALERT_SCORE_UPGRADE", 10.0),
            active_candidate_limit=_int("SCANNER_ACTIVE_LIMIT", 50),
            active_max_age_hours=_int("SCANNER_ACTIVE_MAX_AGE_HOURS", 720),
            max_alerts_per_cycle=max(1, _int("SCANNER_MAX_ALERTS_PER_CYCLE", 3)),
            warmup_cycles=max(0, _int("SCANNER_WARMUP_CYCLES", 1)),
            min_liquidity_usd=_float("SCANNER_MIN_LIQUIDITY_USD", 3_000.0),
            max_market_cap_usd=_float("SCANNER_MAX_MARKET_CAP_USD", 5_000_000.0),
            late_price_change_5m=_float("SCANNER_LATE_5M_PCT", 90.0),
            late_price_change_1h=_float("SCANNER_LATE_1H_PCT", 250.0),
            bankr_url=os.getenv("BANKR_LAUNCH_URL", "https://api.bankr.bot/token-launches"),
            flaunch_url=os.getenv("FLAUNCH_TOKEN_URL", "https://data.flayerlabs.xyz/tokens"),
            gecko_networks=_csv("GECKOTERMINAL_NETWORKS", "base"),
            base_rpc_url=os.getenv("BASE_RPC_URL", "https://mainnet.base.org"),
            robinhood_rpc_url=os.getenv("ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com"),
            rpc_lookback_blocks=_int("SCANNER_RPC_LOOKBACK_BLOCKS", 1_800),
            rpc_max_block_span=_int("SCANNER_RPC_MAX_BLOCK_SPAN", 1_800),
            http_timeout_seconds=_int("SCANNER_HTTP_TIMEOUT_SECONDS", 12),
            dex_concurrency=_int("SCANNER_DEX_CONCURRENCY", 6),
            smart_wallets=_csv("SCANNER_SMART_WALLETS"),
            overlay_url=os.getenv("SCANNER_SIGNAL_OVERLAY_URL"),
            quote_tokens=quote_tokens,
        )

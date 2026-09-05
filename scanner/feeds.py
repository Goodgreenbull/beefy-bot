from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, TYPE_CHECKING
import uuid

import aiohttp

from .config import ScannerConfig
from .models import Candidate, MarketSnapshot, SecurityProfile, normalise_address, utc_now

if TYPE_CHECKING:
    from .state import SQLiteState


PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
O1_LAUNCHED_TOPIC = "0x207384e895174175cc774fe7f7457b37c382f27ebf53d37d5257b862f80eaf9c"
PONS_V1_LAUNCHED_TOPIC = "0xdb51ea9ad51ab453a65a4cb7e60c3cb378c9501bb002609f8f97778fb6c4235a"
PONS_V2_LAUNCHED_TOPIC = "0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607"
PONS_V2_BUY_TOPIC = "0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455"
PONS_V2_SELL_TOPIC = "0x8113d738abdcb6b38357e9d53a54a7157861a09031b453651f0fe7fe151f59df"
ROBINHOOD_V4_POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"

O1_FACTORIES = {
    "base": {
        "0x1176122eb77ad6a2339322cda7c4d7ea9bfa63dc": "o1-b20",
    },
    "robinhood": {
        "0x411f21283d3e492bc395027329e08f9f4f560ba5": "o1-robinhood",
        "0xe64ac4113848bbc1a6dde1a6d1da96720a36f297": "o1-robinhood-stocks",
    },
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _percent(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    number = _number(value, float("nan"))
    if number != number:
        return default
    return number * 100.0 if abs(number) <= 1.0 else number


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _address_from_topic(topic: str | None) -> str:
    topic = topic or ""
    return normalise_address("0x" + topic[-40:]) if len(topic) >= 40 else ""


def _address_from_word(word: str | None) -> str:
    word = word or ""
    return normalise_address("0x" + word[-40:]) if len(word) >= 40 else ""


def _abi_words(value: str | None) -> list[int]:
    raw = (value or "0x").removeprefix("0x")
    return [int(raw[index : index + 64], 16) for index in range(0, len(raw), 64) if len(raw[index : index + 64]) == 64]


def _decode_abi_string(value: str | None, head_index: int = 0) -> str:
    try:
        raw = bytes.fromhex((value or "0x").removeprefix("0x"))
        head = head_index * 32
        offset = int.from_bytes(raw[head : head + 32], "big")
        length = int.from_bytes(raw[offset : offset + 32], "big")
        return raw[offset + 32 : offset + 32 + length].decode("utf-8", errors="replace").strip()
    except (ValueError, IndexError):
        return ""


def _decode_abi_strings(value: str | None, count: int) -> list[str]:
    return [_decode_abi_string(value, index) for index in range(count)]


async def _get_json(session: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
    host = url.split("/", 3)[2] if "://" in url else url
    for attempt in range(3):
        async with session.get(url, **kwargs) as response:
            if response.status == 429:
                retry_header = getattr(response, "headers", {}).get("Retry-After")
                try:
                    retry_after = float(retry_header)
                except (TypeError, ValueError):
                    retry_after = 0.75 * (2 ** attempt)
            elif response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} from {host}")
            else:
                return await response.json(content_type=None)
        await asyncio.sleep(min(4.0, max(0.5, retry_after)))
    raise RuntimeError(f"rate limited by {host} after retries")


class BankrLaunchFeed:
    name = "bankr"

    def __init__(self, config: ScannerConfig) -> None:
        self.url = config.bankr_url

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        data = await _get_json(session, self.url)
        launches = data.get("launches", []) if isinstance(data, dict) else []
        candidates: list[Candidate] = []
        for launch in launches:
            address = launch.get("tokenAddress")
            chain = str(launch.get("chain", "")).lower()
            if not address or chain not in {"base", "robinhood"} or launch.get("status") not in (None, "deployed"):
                continue
            deployer = launch.get("deployer") or {}
            social_links = sum(
                bool(launch.get(field)) for field in ("tweetUrl", "websiteUrl")
            )
            candidates.append(
                Candidate(
                    chain=chain,
                    token_address=address,
                    pair_address=(
                        ROBINHOOD_V4_POOL_MANAGER if chain == "robinhood" else None
                    ),
                    source=self.name,
                    launch_at=_timestamp(launch.get("timestamp") or launch.get("createdAt")),
                    name=launch.get("tokenName"),
                    symbol=launch.get("tokenSymbol"),
                    deployer=deployer.get("walletAddress") if isinstance(deployer, dict) else None,
                    metadata={
                        "activity_id": launch.get("activityId"),
                        "pool_id": launch.get("poolId"),
                        "launch_type": launch.get("launchType"),
                        "profile_social_links": social_links,
                        "tweet_url": launch.get("tweetUrl"),
                        "website_url": launch.get("websiteUrl"),
                        "verified_platform_api": True,
                    },
                )
            )
        return candidates


class PoolsFunLaunchFeed:
    """Read pools.fun launches from the public feed used by its own frontend."""

    name = "pools-fun"
    url = "https://api.bankr.bot/discover"

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        payload = await _get_json(
            session,
            self.url,
            params={
                "platform": "poolsfun",
                "sortBy": "deployedAt",
                "order": "desc",
                "limit": "50",
            },
        )
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        candidates: list[Candidate] = []
        for row in rows:
            if not isinstance(row, dict) or str(row.get("chain", "")).lower() != "robinhood":
                continue
            address = normalise_address(row.get("tokenAddress"))
            if len(address) != 42 or str(row.get("platform", "")).lower() != "poolsfun":
                continue
            social_links = sum(
                bool(row.get(field))
                for field in (
                    "deployerXUsername",
                    "feeRecipientXUsername",
                    "tweetUrl",
                    "websiteUrl",
                )
            )
            pair = normalise_address(row.get("poolId"))
            candidates.append(
                Candidate(
                    chain="robinhood",
                    token_address=address,
                    pair_address=pair if len(pair) == 42 else None,
                    source=self.name,
                    launch_at=_timestamp(row.get("deployedAt")),
                    name=row.get("name"),
                    symbol=row.get("symbol"),
                    deployer=row.get("deployerAddress"),
                    chart_url=f"https://pools.fun/token/{address}",
                    metadata={
                        "verified_platform_api": True,
                        "platform_terms_verified": True,
                        "profile_social_links": social_links,
                        "deployer_x_username": row.get("deployerXUsername"),
                        "fee_recipient_x_username": row.get("feeRecipientXUsername"),
                        "tweet_url": row.get("tweetUrl"),
                        "website_url": row.get("websiteUrl"),
                        "paired_asset": row.get("pairedAsset"),
                        "platform_market": {
                            "market_cap_usd": row.get("marketCapUsd"),
                            "volume_5m_usd": row.get("vol5m"),
                            "volume_1h_usd": row.get("vol1h"),
                            "volume_24h_usd": row.get("vol24h"),
                            "tx_count_24h": row.get("txCount24h"),
                            "last_trade_at": row.get("lastTradeAt"),
                        },
                    },
                )
            )
        return candidates


class ZoraExploreFeed:
    """One rate-limited official Zora explore request per cycle for Base App coins."""

    name = "zora"
    url = "https://api-sdk.zora.engineering/explore"
    list_types = ("NEW", "LAST_TRADED_UNIQUE")

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        cursor_key = "zora:explore_lane"
        lane_index = _integer(state.get_cursor(cursor_key), 0) % len(self.list_types)
        list_type = self.list_types[lane_index]
        payload = await _get_json(
            session,
            self.url,
            params={"listType": list_type, "count": "30"},
        )
        state.set_cursor(cursor_key, str((lane_index + 1) % len(self.list_types)))
        explore = payload.get("exploreList", {}) if isinstance(payload, dict) else {}
        edges = explore.get("edges", []) if isinstance(explore, dict) else []
        candidates: list[Candidate] = []
        for edge in edges:
            row = edge.get("node", {}) if isinstance(edge, dict) else {}
            profile = row.get("creatorProfile") or {}
            if (
                not isinstance(row, dict)
                or _integer(row.get("chainId"), 0) != 8453
                or row.get("platformBlocked")
                or (isinstance(profile, dict) and profile.get("platformBlocked"))
            ):
                continue
            address = normalise_address(row.get("address"))
            if len(address) != 42:
                continue
            social_accounts = (
                (profile.get("socialAccounts") or {}) if isinstance(profile, dict) else {}
            )
            social_links = (
                sum(bool(value) for value in social_accounts.values())
                if isinstance(social_accounts, dict)
                else 0
            )
            pool_key = row.get("uniswapV4PoolKey") or {}
            candidates.append(
                Candidate(
                    chain="base",
                    token_address=address,
                    source=self.name,
                    launch_at=_timestamp(row.get("createdAt")),
                    name=row.get("name"),
                    symbol=row.get("symbol"),
                    deployer=row.get("creatorAddress"),
                    metadata={
                        "verified_platform_api": True,
                        "platform_terms_verified": True,
                        "profile_social_links": social_links,
                        "creator_handle": profile.get("handle") if isinstance(profile, dict) else None,
                        "creator_social_accounts": social_accounts,
                        "coin_type": row.get("coinType"),
                        "description": row.get("description"),
                        "unique_holders": row.get("uniqueHolders"),
                        "zora_list_type": list_type,
                        "zora_pool_key": pool_key,
                    },
                )
            )
        return candidates


class GMGNReadOnlyFeed:
    """Quality-filtered Base and Robinhood discovery from GMGN's public API.

    This client has an intentionally tiny, read-only surface. It never accepts a
    wallet address and cannot reach quote, swap, order, portfolio, or signing
    endpoints. GMGN augments discovery and evidence; Beefy's own scorer remains
    the only component that can produce a Telegram verdict.
    """

    name = "gmgn"
    host = "https://openapi.gmgn.ai"
    allowed_paths = {
        "/v1/market/rank",
        "/v1/market/token_signal",
        "/v1/trenches",
    }
    stock_symbols = {
        "aapl",
        "amzn",
        "goog",
        "googl",
        "gme",
        "meta",
        "msft",
        "mstr",
        "nvda",
        "pltr",
        "qqq",
        "spcx",
        "tsla",
    }
    stock_names = {
        "amazon",
        "apple",
        "gamestop",
        "google",
        "meta platforms",
        "microstrategy",
        "microsoft",
        "nvidia",
        "palantir",
        "spacex",
        "tesla",
    }
    quote_address_types = {
        "base": [11, 3, 12, 13, 0],
        "robinhood": [11, 20, 24, 12, 0],
    }

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self.api_key = config.gmgn_api_key
        self.max_items = config.gmgn_candidate_limit
        self.max_age_hours = config.gmgn_max_age_hours
        self.base_platforms = list(config.gmgn_base_platforms)
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._cooldown_until = 0.0

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        value = payload
        for _ in range(4):
            if not isinstance(value, dict) or "code" not in value:
                break
            if str(value.get("code")) != "0":
                message = value.get("message") or value.get("error") or "GMGN API error"
                raise RuntimeError(str(message))
            if "data" not in value:
                break
            value = value["data"]
        return value

    async def _request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        *,
        query: dict[str, Any],
        body: dict[str, Any] | None = None,
    ) -> Any:
        if path not in self.allowed_paths:
            raise RuntimeError(f"refusing non-read-only GMGN path: {path}")
        if not self.api_key:
            raise RuntimeError("GMGN read-only API key is not configured")
        now_epoch = datetime.now(timezone.utc).timestamp()
        if now_epoch < self._cooldown_until:
            retry_at = datetime.fromtimestamp(self._cooldown_until, tz=timezone.utc)
            raise RuntimeError(
                f"GMGN rate limited; retry after {retry_at.isoformat()}"
            )
        params = {
            **query,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
            "client_id": str(uuid.uuid4()).lower(),
        }
        headers = {
            "X-APIKEY": self.api_key,
            "Content-Type": "application/json",
            # GMGN explicitly identifies supported OpenAPI clients by this UA
            # family; generic/custom agents can be rejected by Cloudflare.
            "User-Agent": "gmgn-cli/1.5.6",
        }
        async with self._request_lock:
            elapsed = asyncio.get_running_loop().time() - self._last_request_at
            if elapsed < 0.08:
                await asyncio.sleep(0.08 - elapsed)
            request = session.post if method == "POST" else session.get
            kwargs: dict[str, Any] = {"params": params, "headers": headers}
            if body is not None:
                kwargs["json"] = body
            async with request(f"{self.host}{path}", **kwargs) as response:
                self._last_request_at = asyncio.get_running_loop().time()
                if response.status == 429:
                    response_headers = getattr(response, "headers", {})
                    reset_header = response_headers.get("X-RateLimit-Reset")
                    retry_header = response_headers.get("Retry-After")
                    reset_at = 0.0
                    try:
                        reset_at = float(reset_header)
                    except (TypeError, ValueError):
                        pass
                    try:
                        payload = await response.json(content_type=None)
                    except Exception:
                        payload = {}
                    if isinstance(payload, dict):
                        try:
                            reset_at = max(reset_at, float(payload.get("reset_at") or 0))
                        except (TypeError, ValueError):
                            pass
                    try:
                        retry_after = float(retry_header)
                    except (TypeError, ValueError):
                        retry_after = 0.0
                    reset_at = max(
                        reset_at,
                        datetime.now(timezone.utc).timestamp() + retry_after,
                        datetime.now(timezone.utc).timestamp() + 300.0,
                    )
                    self._cooldown_until = reset_at
                    retry_at = datetime.fromtimestamp(reset_at, tz=timezone.utc)
                    raise RuntimeError(
                        f"GMGN rate limited; retry after {retry_at.isoformat()}"
                    )
                if response.status in {401, 403}:
                    self._cooldown_until = datetime.now(timezone.utc).timestamp() + 300.0
                    retry_at = datetime.fromtimestamp(self._cooldown_until, tz=timezone.utc)
                    raise RuntimeError(
                        f"GMGN temporarily unavailable (HTTP {response.status}); "
                        f"retry after {retry_at.isoformat()}"
                    )
                if response.status >= 400:
                    raise RuntimeError(f"GMGN {path} HTTP {response.status}")
                payload = await response.json(content_type=None)
                if isinstance(payload, dict) and str(payload.get("code")) == "429":
                    try:
                        reset_at = float(payload.get("reset_at") or 0)
                    except (TypeError, ValueError):
                        reset_at = 0.0
                    self._cooldown_until = max(
                        reset_at,
                        datetime.now(timezone.utc).timestamp() + 300.0,
                    )
                    retry_at = datetime.fromtimestamp(self._cooldown_until, tz=timezone.utc)
                    raise RuntimeError(
                        f"GMGN rate limited; retry after {retry_at.isoformat()}"
                    )
                return self._unwrap(payload)

    def _trenches_body(
        self, chain: str, platforms: list[str] | None = None
    ) -> dict[str, Any]:
        section: dict[str, Any] = {
            "filters": ["offchain", "onchain"],
            "launchpad_platform_v2": True,
            "limit": 40,
            "quote_address_type": self.quote_address_types[chain],
        }
        if platforms:
            section["launchpad_platform"] = platforms
        return {
            "version": "v2",
            "new_creation": dict(section),
            "near_completion": dict(section),
            "completed": dict(section),
        }

    @staticmethod
    def _walk_token_rows(
        value: Any, inherited_chain: str | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if isinstance(value, dict):
            row_chain = str(value.get("chain") or inherited_chain or "").lower()
            address = normalise_address(value.get("address") or value.get("token_address"))
            if len(address) == 42:
                row = dict(value)
                if row_chain:
                    row.setdefault("chain", row_chain)
                rows.append(row)
            for nested in value.values():
                rows.extend(GMGNReadOnlyFeed._walk_token_rows(nested, row_chain or inherited_chain))
        elif isinstance(value, list):
            for nested in value:
                rows.extend(GMGNReadOnlyFeed._walk_token_rows(nested, inherited_chain))
        return rows

    @staticmethod
    def _flatten_row(row: dict[str, Any]) -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        for key in ("token", "cur_data", "data"):
            nested = row.get(key)
            if isinstance(nested, dict):
                flattened.update(
                    {nested_key: nested_value for nested_key, nested_value in nested.items() if not isinstance(nested_value, (dict, list))}
                )
        flattened.update(
            {key: value for key, value in row.items() if not isinstance(value, (dict, list))}
        )
        return flattened

    @staticmethod
    def _merge_row(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
        cumulative_fields = {
            "holder_count",
            "smart_degen_count",
            "renowned_count",
        }
        for key, value in incoming.items():
            if value in (None, "", [], {}):
                continue
            if key not in existing or existing[key] in (None, "", 0):
                existing[key] = value
            # Rank is queried before launch and signal routes, so its live market
            # values win. Only genuinely cumulative counts are safe to maximise;
            # mixing the largest price/MC/flow values across windows would make
            # Beefy judge a price that was never available at alert time.
            elif key in cumulative_fields and _number(value) > _number(existing[key]):
                existing[key] = value

    def _blocked_theme(self, name: str, symbol: str, launchpad: str) -> bool:
        identity = f"{name} {symbol}".lower()
        compact = "".join(character for character in identity if character.isalnum())
        symbol_key = "".join(character for character in symbol.lower() if character.isalnum())
        if "spacex" in compact or symbol_key in self.stock_symbols:
            return True
        if any(re.search(rf"\b{re.escape(stock_name)}\b", identity) for stock_name in self.stock_names):
            return True
        if launchpad in {"pool_robinhood_stock_amm", "o1_rwa"}:
            return True
        if (
            symbol_key in {"usd", "usdc", "usdt", "busd", "usde", "usds"}
            or symbol_key.startswith("usd")
            or any(term in identity for term in ("stablecoin", "stable coin", "us dollar"))
        ):
            return True
        if symbol_key in {"oil", "usoil", "wti", "crude"} or any(
            term in identity for term in ("us oil", "crude oil", "west texas intermediate")
        ):
            return True
        return False

    @staticmethod
    def _social_links(row: dict[str, Any]) -> int:
        return sum(bool(row.get(key)) for key in ("twitter_username", "twitter", "website", "telegram"))

    def _candidate(
        self,
        row: dict[str, Any],
        chain_hint: str,
        signal_events: list[tuple[int, int]],
        route_names: set[str],
    ) -> Candidate | None:
        chain = str(row.get("chain") or chain_hint).lower()
        address = normalise_address(row.get("address") or row.get("token_address"))
        if chain not in {"base", "robinhood"} or len(address) != 42:
            return None
        name = str(row.get("name") or "").strip()
        symbol = str(row.get("symbol") or "").strip()
        launchpad = str(row.get("launchpad_platform") or row.get("launchpad") or "unknown").lower()
        if self._blocked_theme(name, symbol, launchpad):
            return None

        market_cap = _number(row.get("market_cap") or row.get("marketcap") or row.get("mc"))
        liquidity = _number(row.get("liquidity") or row.get("liquidity_usd"))
        price = _number(row.get("price") or row.get("price_usd"))
        buys = _integer(row.get("buys"))
        sells = _integer(row.get("sells"))
        swaps = _integer(row.get("swaps"), buys + sells)
        holder_count = _integer(row.get("holder_count"))
        smart_count = _integer(row.get("smart_degen_count"))
        kol_count = _integer(row.get("renowned_count"))
        top10_rate = _number(row.get("top_10_holder_rate"))
        rug_ratio = _number(row.get("rug_ratio"))
        buy_tax = _percent(row.get("buy_tax"), 0.0) or 0.0
        sell_tax = _percent(row.get("sell_tax"), 0.0) or 0.0
        launch_at = _timestamp(
            row.get("creation_timestamp")
            or row.get("open_timestamp")
            or row.get("created_at")
            or row.get("create_time")
        )
        age_hours = (
            (datetime.now(timezone.utc) - launch_at).total_seconds() / 3600.0
            if launch_at
            else None
        )
        social_links = self._social_links(row)
        total_trades = swaps or buys + sells
        buy_ratio = buys / (buys + sells) if buys + sells else 0.0
        credible_activity = bool(
            smart_count + kol_count >= 2
            or (total_trades >= 10 and buy_ratio >= 0.50)
            or social_links >= 2
            or signal_events
        )
        if (
            market_cap < 3_000
            or market_cap > self.config.max_market_cap_usd
            or liquidity < self.config.min_liquidity_usd
            or (age_hours is not None and (age_hours < 0 or age_hours > self.max_age_hours))
            or (age_hours is None and not signal_events)
            or not credible_activity
            or _truthy(row.get("is_honeypot"))
            or _truthy(row.get("is_wash_trading"))
            or rug_ratio > 0.30
            or top10_rate > 0.50
            or buy_tax >= 5.0
            or sell_tax >= 5.0
        ):
            return None

        now_epoch = int(datetime.now(timezone.utc).timestamp())
        recent_events = []
        for signal_type, trigger_at in signal_events:
            normalised_trigger = trigger_at // 1000 if trigger_at > 10_000_000_000 else trigger_at
            if signal_type in {12, 13, 19, 20} and 0 <= now_epoch - normalised_trigger <= 900:
                recent_events.append((signal_type, normalised_trigger))
        recent_signal_types = sorted({signal_type for signal_type, _ in recent_events})
        recent_smart_signals = sum(
            signal_type in {12, 20} for signal_type, _ in set(recent_events)
        )
        recent_platform_signals = sum(
            signal_type in {13, 19} for signal_type, _ in set(recent_events)
        )
        attention_rank = _integer(row.get("_gmgn_attention_rank"))
        security = {
            "checked": any(
                key in row
                for key in (
                    "is_honeypot",
                    "is_open_source",
                    "is_renounced",
                    "top_10_holder_rate",
                    "rug_ratio",
                )
            ),
            "admin_checks_complete": all(
                key in row for key in ("is_open_source", "is_renounced", "buy_tax", "sell_tax")
            ),
            "simulation_checked": False,
            "sell_simulation_success": False,
            "providers": ["gmgn"],
            "is_honeypot": _truthy(row.get("is_honeypot")),
            "cannot_buy": False,
            "cannot_sell": False,
            "open_source": None if row.get("is_open_source") is None else _truthy(row.get("is_open_source")),
            "buy_tax": buy_tax,
            "sell_tax": sell_tax,
            "top_unlocked_eoa_percent": top10_rate * 100.0,
            "creator_percent": max(
                _number(row.get("dev_team_hold_rate")),
                _number(row.get("creator_balance_rate")),
            ) * 100.0,
            "holder_count": holder_count or None,
            "risk_level": round(rug_ratio * 100.0),
            "risk_label": "gmgn rug-risk estimate",
            "flags": [
                flag
                for flag, active in (
                    ("wash-trading", _truthy(row.get("is_wash_trading"))),
                    ("high-concentration", top10_rate > 0.35),
                )
                if active
            ],
        }
        priority = (
            (20.0 if age_hours is not None and age_hours <= 1 else 10.0)
            + min(20.0, max(0.0, buy_ratio - 0.45) * 50.0)
            + min(20.0, smart_count + kol_count)
            + min(15.0, total_trades / 10.0)
            + min(10.0, liquidity / 10_000.0)
            + len(recent_signal_types) * 5.0
            + (
                12.0
                if 0 < attention_rank <= 10
                else (7.0 if attention_rank <= 30 else 0.0)
            )
        )
        return Candidate(
            chain=chain,
            token_address=address,
            source=self.name,
            launch_at=launch_at,
            name=name or None,
            symbol=symbol or None,
            deployer=row.get("creator") or row.get("deployer"),
            chart_url=f"https://gmgn.ai/{chain}/token/{address}",
            metadata={
                "gmgn_evidence": True,
                "gmgn_launchpad": launchpad,
                "gmgn_routes": sorted(route_names),
                "gmgn_priority": round(priority, 2),
                "gmgn_smart_count": smart_count,
                "gmgn_kol_count": kol_count,
                "gmgn_recent_signal_types": recent_signal_types,
                "gmgn_recent_smart_signals": recent_smart_signals,
                "gmgn_recent_platform_signals": recent_platform_signals,
                "gmgn_attention_rank": attention_rank or None,
                "gmgn_attention_source": "1m-activity" if attention_rank else None,
                "gmgn_creator_token_count": _integer(row.get("twitter_create_token_count")),
                "profile_social_links": social_links,
                "gmgn_market": {
                    "price_usd": price or None,
                    "liquidity_usd": liquidity,
                    "market_cap_usd": market_cap,
                    "volume_5m_usd": _number(row.get("volume")) if route_names & {"rank", "attention"} else 0.0,
                    "buys_5m": buys if route_names & {"rank", "attention"} else 0,
                    "sells_5m": sells if route_names & {"rank", "attention"} else 0,
                    "holder_count": holder_count or None,
                    "price_change_5m": _number(row.get("price_change_percent5m")),
                    "price_change_1h": _number(row.get("price_change_percent1h")),
                    "price_change_24h": _number(row.get("price_change_percent24h")),
                    "security": security,
                },
            },
        )

    async def discover(
        self, session: aiohttp.ClientSession, state: "SQLiteState"
    ) -> list[Candidate]:
        if not self.config.gmgn_enabled:
            return []
        day_ago = int((datetime.now(timezone.utc) - timedelta(hours=self.max_age_hours)).timestamp())
        calls = [
            (
                "gmgn-base-rank",
                "base",
                "rank",
                "GET",
                "/v1/market/rank",
                {"chain": "base", "interval": "5m", "limit": 80, "order_by": "swaps", "direction": "desc"},
                None,
            ),
            (
                "gmgn-robinhood-rank",
                "robinhood",
                "rank",
                "GET",
                "/v1/market/rank",
                {"chain": "robinhood", "interval": "5m", "limit": 80, "order_by": "swaps", "direction": "desc"},
                None,
            ),
            (
                "gmgn-base-launches",
                "base",
                "trenches",
                "POST",
                "/v1/trenches",
                {"chain": "base"},
                self._trenches_body("base", self.base_platforms),
            ),
            (
                "gmgn-robinhood-launches",
                "robinhood",
                "trenches",
                "POST",
                "/v1/trenches",
                {"chain": "robinhood"},
                self._trenches_body("robinhood"),
            ),
            (
                "gmgn-robinhood-signals",
                "robinhood",
                "signals",
                "POST",
                "/v1/market/token_signal",
                {},
                {
                    "chain": "robinhood",
                    "groups": [
                        {
                            "signal_type": [12, 13, 19, 20],
                            "mc_min": 3_000,
                            "mc_max": self.config.max_market_cap_usd,
                            "min_create_or_open_ts": str(day_ago),
                        }
                    ],
                },
            ),
            (
                "gmgn-base-attention",
                "base",
                "attention",
                "GET",
                "/v1/market/rank",
                {"chain": "base", "interval": "1m", "limit": 40, "order_by": "swaps", "direction": "desc"},
                None,
            ),
            (
                "gmgn-robinhood-attention",
                "robinhood",
                "attention",
                "GET",
                "/v1/market/rank",
                {"chain": "robinhood", "interval": "1m", "limit": 40, "order_by": "swaps", "direction": "desc"},
                None,
            ),
        ]
        aggregated: dict[str, dict[str, Any]] = {}
        successful_routes = 0
        first_error: Exception | None = None
        for health_name, chain, route, method, path, query, body in calls:
            try:
                payload = await self._request(
                    session, method, path, query=query, body=body
                )
                rows = self._walk_token_rows(payload)
                state.mark_feed_success(health_name, len(rows))
                successful_routes += 1
            except Exception as error:
                state.mark_feed_error(health_name, error)
                first_error = first_error or error
                if any(
                    phrase in str(error).lower()
                    for phrase in ("rate limited", "temporarily unavailable")
                ):
                    break
                continue
            for position, raw in enumerate(rows, start=1):
                row = self._flatten_row(raw)
                row_chain = str(row.get("chain") or chain).lower()
                if row_chain not in {"base", "robinhood"}:
                    continue
                address = normalise_address(row.get("address") or row.get("token_address"))
                if len(address) != 42:
                    continue
                key = f"{row_chain}:{address}"
                bucket = aggregated.setdefault(
                    key,
                    {"row": {"chain": row_chain, "address": address}, "events": [], "routes": set()},
                )
                self._merge_row(bucket["row"], row)
                bucket["routes"].add(route)
                if route == "attention":
                    attention_rank = _integer(row.get("rank"), position)
                    previous_rank = _integer(bucket["row"].get("_gmgn_attention_rank"))
                    if attention_rank and (not previous_rank or attention_rank < previous_rank):
                        bucket["row"]["_gmgn_attention_rank"] = attention_rank
                signal_type = _integer(row.get("signal_type"))
                trigger_at = _integer(row.get("trigger_at"))
                if signal_type and trigger_at:
                    bucket["events"].append((signal_type, trigger_at))
        if not successful_routes and first_error:
            raise first_error

        candidates = [
            candidate
            for bucket in aggregated.values()
            if (
                candidate := self._candidate(
                    bucket["row"],
                    str(bucket["row"].get("chain")),
                    bucket["events"],
                    bucket["routes"],
                )
            )
        ]
        candidates.sort(
            key=lambda candidate: (
                _number(candidate.metadata.get("gmgn_priority")),
                candidate.launch_at or candidate.discovered_at,
            ),
            reverse=True,
        )
        per_lane_limit = max(4, self.max_items // 6)
        lane_counts: dict[str, int] = defaultdict(int)
        selected: list[Candidate] = []
        for candidate in candidates:
            lane = f"{candidate.chain}:{candidate.metadata.get('gmgn_launchpad', 'unknown')}"
            if lane_counts[lane] >= per_lane_limit:
                continue
            lane_counts[lane] += 1
            selected.append(candidate)
            if len(selected) >= self.max_items:
                break
        return selected


class FlaunchFeed:
    name = "flaunch"

    def __init__(self, config: ScannerConfig) -> None:
        self.url = config.flaunch_url

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        cursor = _integer(state.get_cursor("flaunch_order_id"), 0)
        separator = "&" if "?" in self.url else "?"
        data = await _get_json(session, f"{self.url}{separator}limit=250&orderId={cursor}")
        rows = data if isinstance(data, list) else data.get("tokens", data.get("data", []))
        if not isinstance(rows, list):
            return []

        candidates: list[Candidate] = []
        max_cursor = cursor
        for row in rows:
            if not isinstance(row, dict):
                continue
            address = row.get("tokenAddress") or row.get("address") or row.get("memecoin")
            if not address:
                continue
            order_id = _integer(row.get("orderId"), 0)
            max_cursor = max(max_cursor, order_id)
            candidates.append(
                Candidate(
                    chain="base",
                    token_address=address,
                    pair_address=row.get("poolAddress") or row.get("pool"),
                    source=self.name,
                    launch_at=_timestamp(row.get("launchTime") or row.get("createdAt") or row.get("timestamp")),
                    name=row.get("name") or row.get("tokenName"),
                    symbol=row.get("symbol") or row.get("tokenSymbol"),
                    deployer=row.get("creator") or row.get("owner"),
                    metadata={"order_id": order_id},
                )
            )
        if max_cursor > cursor:
            state.set_cursor("flaunch_order_id", str(max_cursor))
        return candidates


class ClankerLaunchFeed:
    """Use Clanker's public, no-auth token index instead of waiting for DEX indexing."""

    name = "clanker"

    def __init__(self, url: str = "https://www.clanker.world/api/tokens") -> None:
        self.url = url

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        payload = await _get_json(
            session,
            self.url,
            params={
                "chainId": "8453",
                "sort": "desc",
                "limit": "20",
                "includeUser": "true",
                "includeMarket": "true",
            },
        )
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        candidates: list[Candidate] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            address = row.get("contract_address") or row.get("contractAddress") or row.get("address")
            if not address:
                continue
            market = row.get("market") or row.get("market_data") or {}
            user = row.get("user") or row.get("deployer") or {}
            candidates.append(
                Candidate(
                    chain="base",
                    token_address=address,
                    pair_address=row.get("pair_address") or row.get("pairAddress"),
                    source=self.name,
                    launch_at=_timestamp(
                        row.get("created_at")
                        or row.get("createdAt")
                        or row.get("deployed_at")
                        or row.get("timestamp")
                    ),
                    name=row.get("name"),
                    symbol=row.get("symbol"),
                    deployer=(
                        row.get("msg_sender")
                        or row.get("deployer_address")
                        or (user.get("address") if isinstance(user, dict) else None)
                    ),
                    metadata={"clanker_market": market},
                )
            )
        return candidates


class BaselineLaunchFeed:
    """Read Baseline's public CoinGecko adapter for active Base bTokens."""

    name = "baseline"

    def __init__(self, base_url: str = "https://api.baseline.markets/v1/coingecko/base") -> None:
        self.base_url = base_url.rstrip("/")

    async def _metadata(
        self, session: aiohttp.ClientSession, token_address: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        asset_url = f"{self.base_url}/asset"
        pair_url = f"{self.base_url}/pair"
        asset_result, pair_result = await asyncio.gather(
            _get_json(session, asset_url, params={"id": token_address}),
            _get_json(session, pair_url, params={"id": token_address}),
            return_exceptions=True,
        )
        asset = asset_result.get("asset", {}) if isinstance(asset_result, dict) else {}
        pair = pair_result.get("pair", {}) if isinstance(pair_result, dict) else {}
        return asset, pair

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        payload = await _get_json(session, f"{self.base_url}/tickers")
        rows = payload if isinstance(payload, list) else []
        metadata = await asyncio.gather(
            *(self._metadata(session, str(row.get("base_currency", ""))) for row in rows if row.get("base_currency"))
        )
        candidates: list[Candidate] = []
        metadata_index = 0
        for row in rows:
            address = row.get("base_currency") if isinstance(row, dict) else None
            if not address:
                continue
            asset, pair = metadata[metadata_index]
            metadata_index += 1
            candidates.append(
                Candidate(
                    chain="base",
                    token_address=address,
                    source=self.name,
                    launch_at=_timestamp(pair.get("createdAtBlockTimestamp")),
                    name=asset.get("name"),
                    symbol=asset.get("symbol"),
                    deployer=pair.get("creator"),
                    chart_url=f"https://app.baseline.markets/token/{address}",
                    metadata={
                        "baseline_market": row,
                        "baseline_pool_id": row.get("pool_id"),
                        "baseline_pair": pair,
                    },
                )
            )
        return candidates


class DexScreenerProfilesFeed:
    """Broaden discovery to profiled tokens from otherwise non-standard launchers."""

    name = "dexscreener-profiles"
    url = "https://api.dexscreener.com/token-profiles/latest/v1"

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        payload = await _get_json(session, self.url)
        rows = payload if isinstance(payload, list) else []
        chain_aliases = {
            "base": "base",
            "robinhood": "robinhood",
            "robinhood-chain": "robinhood",
            "robinhood_chain": "robinhood",
            "robinhoodchain": "robinhood",
        }
        candidates: list[Candidate] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            chain = chain_aliases.get(str(row.get("chainId", "")).lower())
            address = row.get("tokenAddress")
            if not chain or not address:
                continue
            links = row.get("links") or []
            candidates.append(
                Candidate(
                    chain=chain,
                    token_address=address,
                    source=self.name,
                    chart_url=row.get("url"),
                    metadata={
                        "profile_description": row.get("description"),
                        "profile_social_links": len(links),
                    },
                )
            )
        return candidates


@dataclass(slots=True)
class FactorySpec:
    source: str
    address: str
    topic: str = O1_LAUNCHED_TOPIC
    token_topic_index: int = 1
    creator_topic_index: int | None = 3
    pool_topic_index: int | None = 2
    pool_data_index: int | None = None
    quote_data_index: int | None = 0
    pool_topic_is_address: bool = False
    platform_terms_verified: bool = False

    def __post_init__(self) -> None:
        self.address = normalise_address(self.address)
        self.topic = self.topic.lower()


class FactoryLaunchFeed:
    """Read platform-specific launch events directly from verified factory contracts."""

    def __init__(
        self,
        chain: str,
        rpc_url: str,
        specs: list[FactorySpec],
        config: ScannerConfig,
    ) -> None:
        self.chain = chain
        self.rpc_url = rpc_url
        self.specs = specs
        self.config = config
        self.name = f"platform-launches:{chain}"

    async def _rpc(self, session: aiohttp.ClientSession, method: str, params: list[Any]) -> Any:
        async with session.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        ) as response:
            if response.status >= 400:
                raise RuntimeError(f"platform RPC HTTP {response.status} on {self.chain}")
            body = await response.json(content_type=None)
        if body.get("error"):
            raise RuntimeError(body["error"].get("message", "platform RPC error"))
        return body.get("result")

    async def _block_times(
        self, session: aiohttp.ClientSession, block_numbers: set[int]
    ) -> dict[int, datetime]:
        if not block_numbers:
            return {}
        requests = [
            {"jsonrpc": "2.0", "id": number, "method": "eth_getBlockByNumber", "params": [hex(number), False]}
            for number in block_numbers
        ]
        async with session.post(self.rpc_url, json=requests) as response:
            rows = await response.json(content_type=None)
        return {
            int(row["id"]): datetime.fromtimestamp(int(row["result"]["timestamp"], 16), timezone.utc)
            for row in rows if isinstance(rows, list)
            if row.get("result", {}).get("timestamp")
        }

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        if not self.specs:
            return []
        latest = int(await self._rpc(session, "eth_blockNumber", []), 16)
        cursor_key = f"platform_launches_block:{self.chain}"
        stored = state.get_cursor(cursor_key)
        start = int(stored) + 1 if stored is not None else latest - self.config.rpc_lookback_blocks + 1
        start = max(0, start, latest - self.config.rpc_max_block_span + 1)
        if start > latest:
            return []

        logs = await self._rpc(
            session,
            "eth_getLogs",
            [{
                "fromBlock": hex(start),
                "toBlock": hex(latest),
                "address": [spec.address for spec in self.specs],
                "topics": [[spec.topic for spec in self.specs]],
            }],
        )
        spec_by_address = {spec.address: spec for spec in self.specs}
        block_numbers = {int(log["blockNumber"], 16) for log in logs or [] if log.get("blockNumber")}
        block_times = await self._block_times(session, block_numbers)
        candidates: list[Candidate] = []
        for log in logs or []:
            spec = spec_by_address.get(normalise_address(log.get("address")))
            topics = log.get("topics") or []
            if not spec or not topics or topics[0].lower() != spec.topic:
                continue
            if len(topics) <= spec.token_topic_index:
                continue
            token = _address_from_topic(topics[spec.token_topic_index])
            if not token:
                continue
            creator = None
            if spec.creator_topic_index is not None and len(topics) > spec.creator_topic_index:
                creator = _address_from_topic(topics[spec.creator_topic_index])
            pool_id = None
            pair_address = None
            if spec.pool_topic_index is not None and len(topics) > spec.pool_topic_index:
                pool_id = topics[spec.pool_topic_index]
                if spec.pool_topic_is_address:
                    pair_address = _address_from_topic(pool_id)
            words = [log.get("data", "0x")[i : i + 64] for i in range(2, len(log.get("data", "0x")), 64)]
            if spec.pool_data_index is not None and len(words) > spec.pool_data_index:
                pair_address = _address_from_word(words[spec.pool_data_index])
                pool_id = pair_address
            quote = (
                _address_from_word(words[spec.quote_data_index])
                if spec.quote_data_index is not None and len(words) > spec.quote_data_index
                else None
            )
            block_number = int(log.get("blockNumber", "0x0"), 16)
            candidates.append(
                Candidate(
                    chain=self.chain,
                    token_address=token,
                    pair_address=pair_address,
                    source=spec.source,
                    launch_at=block_times.get(block_number),
                    deployer=creator,
                    metadata={
                        "factory": spec.address,
                        "pool_id": pool_id,
                        "quote_token": quote,
                        "transaction_hash": log.get("transactionHash"),
                        "block_number": block_number,
                        "verified_platform_event": True,
                        "platform_terms_verified": spec.platform_terms_verified,
                    },
                )
            )
        state.set_cursor(cursor_key, str(latest))
        return candidates


def platform_factory_specs(config: ScannerConfig, chain: str) -> list[FactorySpec]:
    specs = [
        FactorySpec(source=source, address=address)
        for address, source in O1_FACTORIES.get(chain, {}).items()
    ]
    if chain == "robinhood":
        specs.extend(
            [
                FactorySpec(
                    source="pons-v1",
                    address="0xa5aab3f0c6eeadf30ef1d3eb997108e976351feb",
                    topic=PONS_V1_LAUNCHED_TOPIC,
                    creator_topic_index=2,
                    pool_topic_index=None,
                    pool_data_index=1,
                    platform_terms_verified=True,
                ),
                FactorySpec(
                    source="pons-v1",
                    address="0x0c37a24f5d23a486fa692d1500881d698b1f77a4",
                    topic=PONS_V1_LAUNCHED_TOPIC,
                    creator_topic_index=2,
                    pool_topic_index=None,
                    pool_data_index=1,
                    platform_terms_verified=True,
                ),
                FactorySpec(
                    source="pons-v2",
                    address="0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e",
                    topic=PONS_V2_LAUNCHED_TOPIC,
                    creator_topic_index=3,
                    pool_topic_index=2,
                    pool_topic_is_address=True,
                    platform_terms_verified=True,
                ),
            ]
        )
    rows = config.factory_feeds.get(chain, []) if isinstance(config.factory_feeds, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("address") or not row.get("source"):
            continue
        try:
            specs.append(
                FactorySpec(
                    source=str(row["source"]),
                    address=str(row["address"]),
                    topic=str(row.get("topic") or O1_LAUNCHED_TOPIC),
                    token_topic_index=_integer(row.get("tokenTopicIndex"), 1),
                    creator_topic_index=(
                        _integer(row.get("creatorTopicIndex"), 3)
                        if row.get("creatorTopicIndex", 3) is not None else None
                    ),
                    pool_topic_index=(
                        _integer(row.get("poolTopicIndex"), 2)
                        if row.get("poolTopicIndex", 2) is not None else None
                    ),
                    pool_data_index=(
                        _integer(row.get("poolDataIndex"), 0)
                        if row.get("poolDataIndex") is not None else None
                    ),
                    quote_data_index=(
                        _integer(row.get("quoteDataIndex"), 0)
                        if row.get("quoteDataIndex", 0) is not None else None
                    ),
                    pool_topic_is_address=_truthy(row.get("poolTopicIsAddress")),
                    platform_terms_verified=_truthy(row.get("platformTermsVerified")),
                )
            )
        except (TypeError, ValueError):
            continue
    unique: dict[tuple[str, str], FactorySpec] = {}
    for spec in specs:
        unique[(spec.address, spec.topic)] = spec
    return list(unique.values())


class GeckoTerminalNewPoolsFeed:
    def __init__(self, network: str) -> None:
        self.network = network
        self.name = f"geckoterminal:{network}"
        self.url = f"https://api.geckoterminal.com/api/v2/networks/{network}/new_pools?include=base_token,quote_token,dex"

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        payload = await _get_json(session, self.url)
        included = {
            item.get("id"): item.get("attributes", {})
            for item in payload.get("included", [])
            if isinstance(item, dict)
        }
        candidates: list[Candidate] = []
        for item in payload.get("data", []):
            attributes = item.get("attributes", {})
            relationships = item.get("relationships", {})
            base_ref = (((relationships.get("base_token") or {}).get("data")) or {}).get("id")
            dex_ref = (((relationships.get("dex") or {}).get("data")) or {}).get("id")
            base_token = included.get(base_ref, {})
            token_address = base_token.get("address")
            pair_address = attributes.get("address")
            if not token_address or not pair_address:
                continue
            candidates.append(
                Candidate(
                    chain=self.network,
                    token_address=token_address,
                    pair_address=pair_address,
                    source=self.name,
                    launch_at=_timestamp(attributes.get("pool_created_at")),
                    name=base_token.get("name"),
                    symbol=base_token.get("symbol"),
                    metadata={"dex": dex_ref, "gecko_market": attributes},
                )
            )
        return candidates


class RpcPairFeed:
    """Find standard V2/V3 events emitted by verified exchange factories."""

    def __init__(self, chain: str, rpc_url: str, config: ScannerConfig) -> None:
        self.chain = chain
        self.rpc_url = rpc_url
        self.config = config
        self.name = f"rpc-pairs:{chain}"

    async def _rpc(self, session: aiohttp.ClientSession, method: str, params: list[Any]) -> Any:
        async with session.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        ) as response:
            if response.status >= 400:
                raise RuntimeError(f"RPC HTTP {response.status} on {self.chain}")
            body = await response.json(content_type=None)
        if body.get("error"):
            raise RuntimeError(f"RPC {self.chain} {method}: {body['error'].get('message', 'unknown error')}")
        return body.get("result")

    async def _block_times(
        self, session: aiohttp.ClientSession, block_numbers: set[int]
    ) -> dict[int, datetime]:
        if not block_numbers:
            return {}
        requests = [
            {"jsonrpc": "2.0", "id": number, "method": "eth_getBlockByNumber", "params": [hex(number), False]}
            for number in block_numbers
        ]
        async with session.post(self.rpc_url, json=requests) as response:
            if response.status >= 400:
                raise RuntimeError(f"RPC block lookup HTTP {response.status} on {self.chain}")
            rows = await response.json(content_type=None)
        result: dict[int, datetime] = {}
        for row in rows if isinstance(rows, list) else []:
            block = row.get("result") or {}
            if block.get("timestamp"):
                result[int(row["id"])] = datetime.fromtimestamp(int(block["timestamp"], 16), timezone.utc)
        return result

    async def discover(self, session: aiohttp.ClientSession, state: "SQLiteState") -> list[Candidate]:
        latest_hex = await self._rpc(session, "eth_blockNumber", [])
        latest = int(latest_hex, 16)
        cursor_key = f"rpc_pairs_block:{self.chain}"
        stored = state.get_cursor(cursor_key)
        start = int(stored) + 1 if stored is not None else latest - self.config.rpc_lookback_blocks + 1
        start = max(0, start, latest - self.config.rpc_max_block_span + 1)
        if start > latest:
            return []

        factories = sorted(self.config.dex_factories.get(self.chain, set()))
        if not factories:
            state.set_cursor(cursor_key, str(latest))
            return []

        logs = await self._rpc(
            session,
            "eth_getLogs",
            [{
                "fromBlock": hex(start),
                "toBlock": hex(latest),
                "address": factories,
                "topics": [[PAIR_CREATED_TOPIC, POOL_CREATED_TOPIC]],
            }],
        )
        block_numbers = {int(log["blockNumber"], 16) for log in logs or [] if log.get("blockNumber")}
        block_times = await self._block_times(session, block_numbers)
        quote_tokens = self.config.quote_tokens.get(self.chain, set())
        candidates: list[Candidate] = []

        for log in logs or []:
            if normalise_address(log.get("address")) not in set(factories):
                continue
            topics = log.get("topics") or []
            if len(topics) < 3:
                continue
            token0 = _address_from_topic(topics[1])
            token1 = _address_from_topic(topics[2])
            words = [log.get("data", "0x")[i : i + 64] for i in range(2, len(log.get("data", "0x")), 64)]
            topic0 = topics[0].lower()
            if topic0 == PAIR_CREATED_TOPIC and words:
                pair_address = _address_from_word(words[0])
                event_kind = "v2"
            elif topic0 == POOL_CREATED_TOPIC and len(words) >= 2:
                pair_address = _address_from_word(words[1])
                event_kind = "v3"
            else:
                continue
            if token0 in quote_tokens:
                token_addresses = [token1]
            elif token1 in quote_tokens:
                token_addresses = [token0]
            else:
                # Preserve both sides until the chain's quote assets are configured.
                token_addresses = list(dict.fromkeys([token0, token1]))
            block_number = int(log.get("blockNumber", "0x0"), 16)
            for token_address in token_addresses:
                candidates.append(
                    Candidate(
                        chain=self.chain,
                        token_address=token_address,
                        pair_address=pair_address,
                        source=f"{self.name}:{event_kind}",
                        launch_at=block_times.get(block_number),
                        metadata={
                            "token0": token0,
                            "token1": token1,
                            "factory": normalise_address(log.get("address")),
                            "transaction_hash": log.get("transactionHash"),
                            "block_number": block_number,
                        },
                    )
                )
        state.set_cursor(cursor_key, str(latest))
        return candidates


class DexScreenerEnricher:
    def __init__(self, config: ScannerConfig) -> None:
        self._semaphore = asyncio.Semaphore(max(1, config.dex_concurrency))

    async def enrich(self, session: aiohttp.ClientSession, candidate: Candidate) -> MarketSnapshot | None:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{candidate.token_address}"
        async with self._semaphore:
            payload = await _get_json(session, url)
        pairs = payload.get("pairs", []) if isinstance(payload, dict) else []
        if not pairs:
            return snapshot_from_fallback(candidate)

        expected_chain_ids = {candidate.chain}
        if candidate.chain == "robinhood":
            expected_chain_ids.update({"robinhood-chain", "robinhood_chain", "robinhoodchain"})
        chain_pairs = [p for p in pairs if str(p.get("chainId", "")).lower() in expected_chain_ids]
        if candidate.pair_address:
            exact = [p for p in chain_pairs if normalise_address(p.get("pairAddress")) == candidate.pair_address]
            if exact:
                chain_pairs = exact
        if not chain_pairs:
            return snapshot_from_fallback(candidate)
        # DexScreener's token endpoint can return pools where the requested token
        # is only the quote asset. Its priceUsd/name fields describe baseToken, so
        # accepting those rows would silently score and track the wrong token.
        base_matches = [
            pair
            for pair in chain_pairs
            if normalise_address((pair.get("baseToken") or {}).get("address"))
            == candidate.token_address
        ]
        if not base_matches:
            return snapshot_from_fallback(candidate)
        pair = max(
            base_matches,
            key=lambda item: _number((item.get("liquidity") or {}).get("usd")),
        )

        info = pair.get("info") or {}
        socials = info.get("socials") or []
        websites = info.get("websites") or []
        boosts = pair.get("boosts") or {}
        txns = pair.get("txns") or {}
        volume = pair.get("volume") or {}
        change = pair.get("priceChange") or {}
        return MarketSnapshot(
            chain=candidate.chain,
            token_address=candidate.token_address,
            pair_address=pair.get("pairAddress") or candidate.pair_address,
            price_usd=_number(pair.get("priceUsd"), 0.0) or None,
            liquidity_usd=_number((pair.get("liquidity") or {}).get("usd")),
            market_cap_usd=_number(pair.get("marketCap"), 0.0) or None,
            fdv_usd=_number(pair.get("fdv"), 0.0) or None,
            volume_5m_usd=_number(volume.get("m5")),
            volume_1h_usd=_number(volume.get("h1")),
            volume_24h_usd=_number(volume.get("h24")),
            buys_5m=_integer((txns.get("m5") or {}).get("buys")),
            sells_5m=_integer((txns.get("m5") or {}).get("sells")),
            buys_1h=_integer((txns.get("h1") or {}).get("buys")),
            sells_1h=_integer((txns.get("h1") or {}).get("sells")),
            price_change_5m=_number(change.get("m5")),
            price_change_1h=_number(change.get("h1")),
            price_change_24h=_number(change.get("h24")),
            social_links=len(socials) + len(websites),
            boost_score=_number(boosts.get("active")),
            source="dexscreener",
            raw={
                "dex": pair.get("dexId"),
                "url": pair.get("url"),
                "name": (pair.get("baseToken") or {}).get("name"),
                "symbol": (pair.get("baseToken") or {}).get("symbol"),
                "price_native": pair.get("priceNative"),
                "quote_address": (pair.get("quoteToken") or {}).get("address"),
                "quote_symbol": (pair.get("quoteToken") or {}).get("symbol"),
                "pair_created_at": pair.get("pairCreatedAt"),
            },
        )


class RobinhoodMarketEnricher:
    """Free no-key fallback for Robinhood markets not yet indexed elsewhere."""

    def __init__(self, config: ScannerConfig) -> None:
        self.rpc_url = config.robinhood_rpc_url
        self._semaphore = asyncio.Semaphore(4)

    async def _supply(self, session: aiohttp.ClientSession, token: str) -> float | None:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "eth_call",
                "params": [{"to": token, "data": "0x18160ddd"}, "latest"],
            },
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [{"to": token, "data": "0x313ce567"}, "latest"],
            },
        ]
        async with session.post(self.rpc_url, json=requests) as response:
            if response.status >= 400:
                return None
            rows = await response.json(content_type=None)
        results = {
            int(row.get("id")): row.get("result")
            for row in rows if isinstance(rows, list) and isinstance(row, dict) and row.get("result")
        }
        try:
            supply = int(results[0], 16)
            decimals = int(results.get(1, "0x12"), 16)
            return supply / (10 ** decimals)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    async def enrich(
        self, session: aiohttp.ClientSession, candidate: Candidate
    ) -> MarketSnapshot | None:
        if candidate.chain != "robinhood":
            return None
        url = f"https://hooderscan.com/api/v1/token/{candidate.token_address}"
        async with self._semaphore:
            async with session.get(url) as response:
                if response.status in {404, 503}:
                    return None
                if response.status == 429:
                    raise RuntimeError("rate limited by hooderscan.com")
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status} from hooderscan.com")
                payload = await response.json(content_type=None)
        data = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or not payload.get("success")
            or not isinstance(data, dict)
            or data.get("isStale")
        ):
            return None
        price = _number(data.get("priceUsd"), 0.0) or None
        supply = await self._supply(session, candidate.token_address) if price else None
        market_cap = price * supply if price and supply else None
        return MarketSnapshot(
            chain="robinhood",
            token_address=candidate.token_address,
            pair_address=candidate.pair_address,
            price_usd=price,
            liquidity_usd=_number(data.get("liquidityUsd")),
            market_cap_usd=market_cap,
            fdv_usd=market_cap,
            volume_24h_usd=_number(data.get("volume24hUsd")),
            price_change_24h=_number(data.get("priceChange24h")),
            social_links=_integer(candidate.metadata.get("profile_social_links"), 0),
            source="hooderscan",
            raw={
                "name": data.get("name") or candidate.name,
                "symbol": data.get("symbol") or candidate.symbol,
                "market_sources": data.get("sources") or [],
                "market_updated_at": data.get("updatedAt"),
            },
        )


class PonsV2Enricher:
    """Read Pons V2 curve price, flow, holders and immutable fee terms on-chain."""

    WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
    USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    NATIVE_ETH = "0x0000000000000000000000000000000000000000"

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self.rpc_url = config.robinhood_rpc_url
        self._semaphore = asyncio.Semaphore(1)
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._price_lock = asyncio.Lock()
        self._eth_price: tuple[float, float] | None = None

    async def _batch_post(
        self, session: aiohttp.ClientSession, requests: list[dict[str, Any]], label: str
    ) -> Any:
        async with self._request_lock:
            for attempt in range(3):
                elapsed = asyncio.get_running_loop().time() - self._last_request_at
                if elapsed < 0.25:
                    await asyncio.sleep(0.25 - elapsed)
                async with session.post(self.rpc_url, json=requests) as response:
                    self._last_request_at = asyncio.get_running_loop().time()
                    if response.status == 429:
                        retry_header = getattr(response, "headers", {}).get("Retry-After")
                        try:
                            retry_after = float(retry_header)
                        except (TypeError, ValueError):
                            retry_after = 0.75 * (2 ** attempt)
                    elif response.status >= 400:
                        raise RuntimeError(f"{label} HTTP {response.status}")
                    else:
                        return await response.json(content_type=None)
                await asyncio.sleep(min(3.0, max(0.5, retry_after)))
        raise RuntimeError(f"{label} HTTP 429 after retries")

    async def _calls(
        self, session: aiohttp.ClientSession, calls: list[tuple[str, str, str]]
    ) -> dict[str, str]:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "eth_call",
                "params": [{"to": address, "data": selector}, "latest"],
            }
            for index, (_, address, selector) in enumerate(calls)
        ]
        rows = await self._batch_post(session, requests, "Pons batch RPC")
        by_id = {
            int(row.get("id")): row.get("result")
            for row in rows if isinstance(rows, list) and isinstance(row, dict) and row.get("result")
        }
        return {
            name: by_id[index]
            for index, (name, _, _) in enumerate(calls)
            if index in by_id
        }

    async def _calls_with_latest(
        self, session: aiohttp.ClientSession, calls: list[tuple[str, str, str]]
    ) -> tuple[dict[str, str], str]:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "eth_call",
                "params": [{"to": address, "data": selector}, "latest"],
            }
            for index, (_, address, selector) in enumerate(calls)
        ]
        latest_id = len(requests)
        requests.append(
            {"jsonrpc": "2.0", "id": latest_id, "method": "eth_blockNumber", "params": []}
        )
        rows = await self._batch_post(session, requests, "Pons batch RPC")
        if not isinstance(rows, list):
            raise RuntimeError("Pons batch RPC returned an invalid response")
        errors = [row.get("error", {}).get("message") for row in rows if row.get("error")]
        if errors:
            raise RuntimeError(errors[0] or "Pons batch RPC error")
        by_id = {
            int(row.get("id")): row.get("result")
            for row in rows if isinstance(row, dict) and row.get("result")
        }
        latest = by_id.get(latest_id)
        if not latest:
            raise RuntimeError("Pons latest block unavailable")
        values = {
            name: by_id[index]
            for index, (name, _, _) in enumerate(calls)
            if index in by_id
        }
        return values, latest

    async def _event_logs(
        self, session: aiohttp.ClientSession, filters: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "eth_getLogs",
                "params": [log_filter],
            }
            for index, log_filter in enumerate(filters)
        ]
        rows = await self._batch_post(session, requests, "Pons log RPC")
        if not isinstance(rows, list):
            raise RuntimeError("Pons log RPC returned an invalid response")
        errors = [row.get("error", {}).get("message") for row in rows if row.get("error")]
        if errors:
            raise RuntimeError(errors[0] or "Pons log RPC error")
        by_id = {
            int(row.get("id")): row.get("result") or []
            for row in rows if isinstance(row, dict)
        }
        return [by_id.get(index, []) for index in range(len(filters))]

    async def _eth_usd(self, session: aiohttp.ClientSession) -> float:
        async with self._price_lock:
            now = datetime.now(timezone.utc).timestamp()
            if self._eth_price and now - self._eth_price[0] < 60:
                return self._eth_price[1]
            payload = await _get_json(
                session,
                "https://coins.llama.fi/prices/current/coingecko:ethereum",
            )
            price = _number(
                ((payload.get("coins") or {}).get("coingecko:ethereum") or {}).get("price")
            )
            if price <= 0:
                raise RuntimeError("ETH/USD reference unavailable")
            self._eth_price = (now, price)
            return price

    @staticmethod
    def _event_wallets(rows: list[dict], minimum_block: int) -> set[str]:
        return {
            _address_from_topic((row.get("topics") or [None, None])[1])
            for row in rows
            if int(str(row.get("blockNumber") or "0x0"), 16) >= minimum_block
            and len(row.get("topics") or []) > 1
            and len(_address_from_topic((row.get("topics") or [None, None])[1])) == 42
        }

    async def enrich(
        self, session: aiohttp.ClientSession, candidate: Candidate
    ) -> MarketSnapshot | None:
        if "pons-v2" not in set(candidate.source.split(",")):
            return None
        curve = normalise_address(candidate.pair_address)
        quote = normalise_address(candidate.metadata.get("quote_token"))
        if len(curve) != 42 or quote not in {self.NATIVE_ETH, self.WETH, self.USDG}:
            return None

        async with self._semaphore:
            initial, eth_usd = await asyncio.gather(
                self._calls_with_latest(
                    session,
                    [
                        ("reserves", curve, "0x0902f1ac"),
                        ("real_quote", curve, "0x4f1f58fd"),
                        ("fee_bps", curve, "0x24a9d853"),
                        ("creator_tax_bps", curve, "0xc1bb8901"),
                        ("supply", candidate.token_address, "0x18160ddd"),
                        ("decimals", candidate.token_address, "0x313ce567"),
                        ("name", candidate.token_address, "0x06fdde03"),
                        ("symbol", candidate.token_address, "0x95d89b41"),
                        ("socials", candidate.token_address, "0x53cd512a"),
                    ],
                ),
                self._eth_usd(session),
            )
            values, latest_hex = initial
            reserve_words = _abi_words(values.get("reserves"))
            if len(reserve_words) < 2:
                return None
            latest = int(latest_hex, 16)
            launch_block = _integer(candidate.metadata.get("block_number"), latest)
            start = max(0, launch_block, latest - self.config.rpc_lookback_blocks + 1)
            log_filter = {
                "fromBlock": hex(start),
                "toBlock": hex(latest),
            }
            buy_logs, sell_logs, transfer_logs = await self._event_logs(
                session,
                [
                    {**log_filter, "address": curve, "topics": [PONS_V2_BUY_TOPIC]},
                    {**log_filter, "address": curve, "topics": [PONS_V2_SELL_TOPIC]},
                    {
                        **log_filter,
                        "address": candidate.token_address,
                        "topics": [TRANSFER_TOPIC],
                    },
                ],
            )

        buys = [row for row in buy_logs or [] if isinstance(row, dict)]
        sells = [row for row in sell_logs or [] if isinstance(row, dict)]
        transfers = [row for row in transfer_logs or [] if isinstance(row, dict)]
        five_start = latest - self.config.flow_5m_blocks + 1
        fifteen_start = latest - self.config.flow_15m_blocks + 1

        def recent(rows: list[dict], minimum: int) -> list[dict]:
            return [
                row for row in rows
                if int(str(row.get("blockNumber") or "0x0"), 16) >= minimum
            ]

        buys_5m = recent(buys, five_start)
        sells_5m = recent(sells, five_start)
        buys_15m = recent(buys, fifteen_start)
        sells_15m = recent(sells, fifteen_start)

        def quote_volume(rows: list[dict], word_index: int) -> int:
            total = 0
            for row in rows:
                words = _abi_words(row.get("data"))
                if len(words) > word_index:
                    total += words[word_index]
            return total

        volume_5m_quote = quote_volume(buys_5m, 0) + quote_volume(sells_5m, 1)
        volume_1h_quote = quote_volume(buys, 0) + quote_volume(sells, 1)
        buyers_5m = self._event_wallets(buys, five_start)
        buyers_15m = self._event_wallets(buys, fifteen_start)
        sellers_5m = self._event_wallets(sells, five_start)
        sellers_15m = self._event_wallets(sells, fifteen_start)

        supply_raw = (_abi_words(values.get("supply")) or [0])[0]
        decimals = (_abi_words(values.get("decimals")) or [18])[0]
        quote_reserve, token_reserve = reserve_words[:2]
        supply_tokens = supply_raw / (10 ** decimals) if supply_raw else 0.0
        price_quote = (
            (quote_reserve / token_reserve) * (10 ** (decimals - 18))
            if quote_reserve and token_reserve else 0.0
        )
        quote_usd = 1.0 if quote == self.USDG else eth_usd
        price_usd = price_quote * quote_usd
        real_quote = (_abi_words(values.get("real_quote")) or [0])[0]
        fee_bps = (_abi_words(values.get("fee_bps")) or [0])[0]
        creator_tax_bps = (_abi_words(values.get("creator_tax_bps")) or [0])[0]

        balances: dict[str, int] = defaultdict(int)
        for row in transfers:
            topics = row.get("topics") or []
            if len(topics) < 3:
                continue
            sender = _address_from_topic(topics[1])
            recipient = _address_from_topic(topics[2])
            amount = int(str(row.get("data") or "0x0"), 16)
            balances[sender] -= amount
            balances[recipient] += amount
        excluded = {
            "0x0000000000000000000000000000000000000000",
            "0x000000000000000000000000000000000000dead",
            curve,
            candidate.token_address,
            normalise_address(candidate.metadata.get("factory")),
        }
        wallet_balances = sorted(
            (amount for wallet, amount in balances.items() if amount > 0 and wallet not in excluded),
            reverse=True,
        )
        concentration = (
            sum(wallet_balances[:5]) * 100.0 / supply_raw if supply_raw else None
        )
        deployer = normalise_address(candidate.deployer)
        creator_percent = (
            max(0, balances.get(deployer, 0)) * 100.0 / supply_raw
            if supply_raw and len(deployer) == 42 else None
        )
        socials = _decode_abi_strings(values.get("socials"), 5)
        total_tax = (fee_bps + creator_tax_bps) / 100.0
        security = {
            "checked": True,
            # Curve state proves fees, balances and live sell activity, but it
            # is not a substitute for the independent admin-risk provider.
            "admin_checks_complete": False,
            "simulation_checked": False,
            "sell_simulation_success": False,
            "providers": ["pons-v2-onchain"],
            "is_honeypot": False,
            "cannot_buy": False,
            "cannot_sell": False,
            "open_source": True,
            "buy_tax": total_tax,
            "sell_tax": total_tax,
            "creator_percent": creator_percent,
            "top_unlocked_eoa_percent": concentration,
            "holder_count": len(wallet_balances),
            "concentration_checked": True,
            "platform_template": "pons-v2",
        }
        return MarketSnapshot(
            chain="robinhood",
            token_address=candidate.token_address,
            pair_address=curve,
            price_usd=price_usd or None,
            liquidity_usd=real_quote / 1e18 * quote_usd,
            market_cap_usd=price_usd * supply_tokens if price_usd and supply_tokens else None,
            fdv_usd=price_usd * supply_tokens if price_usd and supply_tokens else None,
            volume_5m_usd=volume_5m_quote / 1e18 * quote_usd,
            volume_1h_usd=volume_1h_quote / 1e18 * quote_usd,
            buys_5m=len(buys_5m),
            sells_5m=len(sells_5m),
            buys_1h=len(buys),
            sells_1h=len(sells),
            social_links=sum(bool(item) for item in socials),
            unique_buyers_5m=len(buyers_5m),
            unique_buyers_15m=len(buyers_15m),
            unique_sellers_5m=len(sellers_5m),
            unique_sellers_15m=len(sellers_15m),
            net_new_wallets_5m=len(buyers_5m - sellers_5m),
            net_new_wallets_15m=len(buyers_15m - sellers_15m),
            holder_count=len(wallet_balances),
            deployer_sells_15m=sum(
                _address_from_topic((row.get("topics") or [None, None])[1]) == deployer
                for row in sells_15m if len(row.get("topics") or []) > 1
            ),
            flow_checked=True,
            source="pons-v2-onchain",
            raw={
                "name": _decode_abi_string(values.get("name")),
                "symbol": _decode_abi_string(values.get("symbol")),
                "security": security,
                "creator_tax_bps": creator_tax_bps,
                "protocol_fee_bps": fee_bps,
                "quote_token": quote,
                "quote_usd": quote_usd,
                "socials": [item for item in socials if item],
            },
        )


class PonsV1Enricher(PonsV2Enricher):
    """Read current and legacy pons Uniswap V3 launches without an indexer."""

    async def enrich(
        self, session: aiohttp.ClientSession, candidate: Candidate
    ) -> MarketSnapshot | None:
        if "pons-v1" not in set(candidate.source.split(",")):
            return None
        pool = normalise_address(candidate.pair_address)
        quote = normalise_address(candidate.metadata.get("quote_token"))
        if len(pool) != 42 or quote != self.WETH:
            return None

        balance_call = "0x70a08231" + pool.removeprefix("0x").rjust(64, "0")
        async with self._semaphore:
            values, eth_usd = await asyncio.gather(
                self._calls(
                    session,
                    [
                        ("slot0", pool, "0x3850c7bd"),
                        ("quote_balance", self.WETH, balance_call),
                        ("supply", candidate.token_address, "0x18160ddd"),
                        ("decimals", candidate.token_address, "0x313ce567"),
                        ("name", candidate.token_address, "0x06fdde03"),
                        ("symbol", candidate.token_address, "0x95d89b41"),
                        ("socials", candidate.token_address, "0x53cd512a"),
                    ],
                ),
                self._eth_usd(session),
            )
        slot_words = _abi_words(values.get("slot0"))
        if not slot_words or slot_words[0] <= 0:
            return None
        sqrt_price_x96 = slot_words[0]
        token1_per_token0 = (sqrt_price_x96 / (2 ** 96)) ** 2
        token_is_token0 = int(candidate.token_address, 16) < int(self.WETH, 16)
        price_weth = token1_per_token0 if token_is_token0 else 1.0 / token1_per_token0

        supply_raw = (_abi_words(values.get("supply")) or [0])[0]
        decimals = (_abi_words(values.get("decimals")) or [18])[0]
        supply_tokens = supply_raw / (10 ** decimals) if supply_raw else 0.0
        quote_balance = (_abi_words(values.get("quote_balance")) or [0])[0]
        price_usd = price_weth * eth_usd
        socials = _decode_abi_strings(values.get("socials"), 5)
        market_cap = price_usd * supply_tokens if supply_tokens else None
        return MarketSnapshot(
            chain="robinhood",
            token_address=candidate.token_address,
            pair_address=pool,
            price_usd=price_usd or None,
            liquidity_usd=quote_balance / 1e18 * eth_usd * 2.0,
            market_cap_usd=market_cap,
            fdv_usd=market_cap,
            social_links=sum(bool(item) for item in socials),
            source="pons-v1-onchain",
            raw={
                "name": _decode_abi_string(values.get("name")),
                "symbol": _decode_abi_string(values.get("symbol")),
                "socials": [item for item in socials if item],
                "platform_liquidity_locked": True,
                "liquidity_estimate": "2x onchain WETH pool balance",
            },
        )


def snapshot_from_fallback(candidate: Candidate) -> MarketSnapshot | None:
    market = candidate.metadata.get("gecko_market")
    if isinstance(market, dict):
        volume = market.get("volume_usd") or {}
        txns = market.get("transactions") or {}
        change = market.get("price_change_percentage") or {}
        return MarketSnapshot(
            chain=candidate.chain,
            token_address=candidate.token_address,
            pair_address=candidate.pair_address,
            price_usd=_number(market.get("base_token_price_usd"), 0.0) or None,
            liquidity_usd=_number(market.get("reserve_in_usd")),
            market_cap_usd=_number(market.get("market_cap_usd"), 0.0) or None,
            fdv_usd=_number(market.get("fdv_usd"), 0.0) or None,
            volume_5m_usd=_number(volume.get("m5")),
            volume_1h_usd=_number(volume.get("h1")),
            volume_24h_usd=_number(volume.get("h24")),
            buys_5m=_integer((txns.get("m5") or {}).get("buys")),
            sells_5m=_integer((txns.get("m5") or {}).get("sells")),
            buys_1h=_integer((txns.get("h1") or {}).get("buys")),
            sells_1h=_integer((txns.get("h1") or {}).get("sells")),
            price_change_5m=_number(change.get("m5")),
            price_change_1h=_number(change.get("h1")),
            price_change_24h=_number(change.get("h24")),
            source="geckoterminal",
            raw={"dex": candidate.metadata.get("dex")},
        )
    gmgn = candidate.metadata.get("gmgn_market")
    if isinstance(gmgn, dict) and _number(gmgn.get("price_usd")) > 0:
        security = gmgn.get("security") if isinstance(gmgn.get("security"), dict) else {}
        return MarketSnapshot(
            chain=candidate.chain,
            token_address=candidate.token_address,
            price_usd=_number(gmgn.get("price_usd"), 0.0) or None,
            liquidity_usd=_number(gmgn.get("liquidity_usd")),
            market_cap_usd=_number(gmgn.get("market_cap_usd"), 0.0) or None,
            volume_5m_usd=_number(gmgn.get("volume_5m_usd")),
            buys_5m=_integer(gmgn.get("buys_5m")),
            sells_5m=_integer(gmgn.get("sells_5m")),
            price_change_5m=_number(gmgn.get("price_change_5m")),
            price_change_1h=_number(gmgn.get("price_change_1h")),
            price_change_24h=_number(gmgn.get("price_change_24h")),
            social_links=_integer(candidate.metadata.get("profile_social_links")),
            holder_count=_integer(gmgn.get("holder_count"), 0) or None,
            smart_wallet_buys=_integer(candidate.metadata.get("gmgn_recent_smart_signals")),
            source="gmgn",
            raw={
                "url": candidate.chart_url,
                "name": candidate.name,
                "symbol": candidate.symbol,
                "security": security,
                "gmgn": {
                    "launchpad": candidate.metadata.get("gmgn_launchpad"),
                    "routes": candidate.metadata.get("gmgn_routes", []),
                    "smart_count": candidate.metadata.get("gmgn_smart_count", 0),
                    "kol_count": candidate.metadata.get("gmgn_kol_count", 0),
                    "recent_signal_types": candidate.metadata.get("gmgn_recent_signal_types", []),
                    "creator_token_count": candidate.metadata.get("gmgn_creator_token_count", 0),
                },
            },
        )
    baseline = candidate.metadata.get("baseline_market")
    if isinstance(baseline, dict):
        return MarketSnapshot(
            chain=candidate.chain,
            token_address=candidate.token_address,
            price_usd=_number(baseline.get("last_price"), 0.0) or None,
            liquidity_usd=_number(baseline.get("liquidity_in_usd")),
            # Baseline's target_volume is denominated in the target asset, not USD.
            # Leave USD volume empty until DexScreener can supply a comparable value.
            volume_24h_usd=0.0,
            source="baseline",
            raw={
                "dex": "baseline",
                "url": candidate.chart_url,
                "name": candidate.name,
                "symbol": candidate.symbol,
            },
        )
    return None


class OnchainFlowEnricher:
    """Measure recent unique wallet flow directly from token Transfer logs."""

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self.rpc_urls = {"base": config.base_rpc_url, "robinhood": config.robinhood_rpc_url}
        self._semaphore = asyncio.Semaphore(max(1, min(config.dex_concurrency, 2)))
        self._request_locks = {url: asyncio.Lock() for url in self.rpc_urls.values()}
        self._last_request_at = {url: 0.0 for url in self.rpc_urls.values()}

    async def _post_rpc(
        self,
        session: aiohttp.ClientSession,
        url: str,
        payload: dict[str, Any] | list[dict[str, Any]],
        label: str,
    ) -> Any:
        lock = self._request_locks.setdefault(url, asyncio.Lock())
        async with lock:
            for attempt in range(3):
                elapsed = asyncio.get_running_loop().time() - self._last_request_at.get(url, 0.0)
                if elapsed < 0.20:
                    await asyncio.sleep(0.20 - elapsed)
                async with session.post(url, json=payload) as response:
                    self._last_request_at[url] = asyncio.get_running_loop().time()
                    if response.status == 429:
                        retry_header = getattr(response, "headers", {}).get("Retry-After")
                        try:
                            retry_after = float(retry_header)
                        except (TypeError, ValueError):
                            retry_after = 0.75 * (2 ** attempt)
                    elif response.status >= 400:
                        raise RuntimeError(f"{label} HTTP {response.status}")
                    else:
                        return await response.json(content_type=None)
                await asyncio.sleep(min(4.0, max(0.5, retry_after)))
        raise RuntimeError(f"{label} HTTP 429 after retries")

    async def _rpc(
        self,
        session: aiohttp.ClientSession,
        url: str,
        method: str,
        params: list[Any],
    ) -> Any:
        payload = await self._post_rpc(
            session,
            url,
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            "on-chain flow RPC",
        )
        if payload.get("error"):
            raise RuntimeError(payload["error"].get("message", "on-chain flow RPC error"))
        return payload.get("result")

    async def _transaction_senders(
        self,
        session: aiohttp.ClientSession,
        url: str,
        hashes: list[str],
    ) -> dict[str, str]:
        selected = list(dict.fromkeys(hashes))[-self.config.max_flow_transactions :]
        if not selected:
            return {}
        requests = [
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "eth_getTransactionByHash",
                "params": [tx_hash],
            }
            for index, tx_hash in enumerate(selected)
        ]
        payload = await self._post_rpc(
            session, url, requests, "on-chain flow transaction RPC"
        )
        rows = payload if isinstance(payload, list) else []
        return {
            selected[int(row["id"])]: normalise_address((row.get("result") or {}).get("from"))
            for row in rows
            if isinstance(row, dict)
            and str(row.get("id", "")).isdigit()
            and int(row["id"]) < len(selected)
            and len(normalise_address((row.get("result") or {}).get("from"))) == 42
        }

    async def enrich(
        self,
        session: aiohttp.ClientSession,
        candidate: Candidate,
        snapshot: MarketSnapshot,
    ) -> dict[str, Any]:
        pair = normalise_address(snapshot.pair_address or candidate.pair_address)
        url = self.rpc_urls.get(candidate.chain)
        if not url or len(pair) != 42:
            return {}
        async with self._semaphore:
            latest = int(await self._rpc(session, url, "eth_blockNumber", []), 16)
            start = max(0, latest - self.config.flow_15m_blocks + 1)
            pair_topic = "0x" + pair.removeprefix("0x").rjust(64, "0")
            common = {
                "fromBlock": hex(start),
                "toBlock": hex(latest),
                "address": candidate.token_address,
            }
            buys, sells = await asyncio.gather(
                self._rpc(
                    session,
                    url,
                    "eth_getLogs",
                    [{**common, "topics": [TRANSFER_TOPIC, pair_topic]}],
                ),
                self._rpc(
                    session,
                    url,
                    "eth_getLogs",
                    [{**common, "topics": [TRANSFER_TOPIC, None, pair_topic]}],
                ),
            )
            buy_logs = [row for row in buys or [] if isinstance(row, dict)]
            sell_logs = [row for row in sells or [] if isinstance(row, dict)]
            hashes = [
                str(row.get("transactionHash"))
                for row in buy_logs + sell_logs
                if row.get("transactionHash")
            ]
            senders = await self._transaction_senders(session, url, hashes)

        five_minute_start = latest - self.config.flow_5m_blocks + 1

        def wallets(rows: list[dict], minimum_block: int, fallback_topic: int) -> set[str]:
            resolved: set[str] = set()
            for row in rows:
                if int(str(row.get("blockNumber") or "0x0"), 16) < minimum_block:
                    continue
                tx_hash = str(row.get("transactionHash") or "")
                sender = senders.get(tx_hash)
                topics = row.get("topics") or []
                fallback = _address_from_topic(
                    topics[fallback_topic] if len(topics) > fallback_topic else None
                )
                wallet = sender or fallback
                if len(wallet) == 42 and wallet not in {pair, candidate.token_address}:
                    resolved.add(wallet)
            return resolved

        buyers_5m = wallets(buy_logs, five_minute_start, 2)
        buyers_15m = wallets(buy_logs, start, 2)
        sellers_5m = wallets(sell_logs, five_minute_start, 1)
        sellers_15m = wallets(sell_logs, start, 1)
        deployer = normalise_address(candidate.deployer)
        deployer_sells = sum(
            1
            for row in sell_logs
            if len(deployer) == 42
            and _address_from_topic(
                (row.get("topics") or [None, None])[1]
                if len(row.get("topics") or []) > 1
                else None
            ) == deployer
        )
        return {
            "unique_buyers_5m": len(buyers_5m),
            "unique_buyers_15m": len(buyers_15m),
            "unique_sellers_5m": len(sellers_5m),
            "unique_sellers_15m": len(sellers_15m),
            "net_new_wallets_5m": len(buyers_5m - sellers_5m),
            "net_new_wallets_15m": len(buyers_15m - sellers_15m),
            "deployer_sells_15m": deployer_sells,
            "buy_events_5m": sum(
                int(str(row.get("blockNumber") or "0x0"), 16) >= five_minute_start
                for row in buy_logs
            ),
            "sell_events_5m": sum(
                int(str(row.get("blockNumber") or "0x0"), 16) >= five_minute_start
                for row in sell_logs
            ),
            "buy_events_15m": len(buy_logs),
            "sell_events_15m": len(sell_logs),
            "flow_checked": True,
        }


class TokenRiskEnricher:
    """Combine free GoPlus contract data with Base sell simulation from Honeypot.is."""

    CHAIN_IDS = {"base": "8453", "robinhood": "4663"}

    def __init__(self, config: ScannerConfig) -> None:
        self._semaphore = asyncio.Semaphore(max(1, min(config.dex_concurrency, 4)))

    async def _goplus(self, session: aiohttp.ClientSession, candidate: Candidate) -> dict[str, Any]:
        chain_id = self.CHAIN_IDS.get(candidate.chain)
        if not chain_id:
            return {}
        payload = await _get_json(
            session,
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": candidate.token_address},
        )
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        return result.get(candidate.token_address, result.get(candidate.token_address.lower(), {})) or {}

    async def _honeypot(self, session: aiohttp.ClientSession, candidate: Candidate) -> dict[str, Any]:
        if candidate.chain != "base":
            return {}
        return await _get_json(
            session,
            "https://api.honeypot.is/v2/IsHoneypot",
            params={"address": candidate.token_address, "chainID": "8453"},
        )

    async def check(self, session: aiohttp.ClientSession, candidate: Candidate) -> SecurityProfile:
        async with self._semaphore:
            results = await asyncio.gather(
                self._goplus(session, candidate),
                self._honeypot(session, candidate),
                return_exceptions=True,
            )
        goplus = results[0] if isinstance(results[0], dict) else {}
        honeypot = results[1] if isinstance(results[1], dict) else {}
        errors = [str(item) for item in results if isinstance(item, Exception)]
        recognised_goplus_keys = {
            "is_honeypot",
            "is_open_source",
            "cannot_buy",
            "cannot_sell_all",
            "cannot_sell",
            "owner_change_balance",
            "transfer_pausable",
            "is_blacklisted",
            "is_mintable",
            "buy_tax",
            "sell_tax",
            "owner_percent",
            "holders",
            "lp_holders",
        }
        # Risk fields are conditional in GoPlus, so absence does not itself mean
        # an API failure. Require its contract/source result plus multiple known
        # fields, while rejecting arbitrary non-empty error payloads.
        goplus_usable = (
            "is_open_source" in goplus
            and len(recognised_goplus_keys.intersection(goplus)) >= 3
        )
        hp_result = honeypot.get("honeypotResult") or {}
        honeypot_usable = isinstance(hp_result.get("isHoneypot"), bool)
        providers: list[str] = []
        if goplus_usable:
            providers.append("goplus")
        if honeypot_usable:
            providers.append("honeypot.is")

        summary = honeypot.get("summary") or {}
        simulation = honeypot.get("simulationResult") or {}
        contract_code = honeypot.get("contractCode") or {}
        flags = {
            str(item.get("flag", "")).lower()
            for item in summary.get("flags", [])
            if isinstance(item, dict) and item.get("flag")
        }
        holders = goplus.get("holders") or []
        unlocked_eoa = sum(
            _percent(holder.get("percent"), 0.0) or 0.0
            for holder in holders
            if isinstance(holder, dict)
            and not _truthy(holder.get("is_contract"))
            and not _truthy(holder.get("is_locked"))
            and str(holder.get("address", "")).lower()
            not in {
                "0x0000000000000000000000000000000000000000",
                "0x000000000000000000000000000000000000dead",
            }
        )
        lp_holders = [item for item in (goplus.get("lp_holders") or []) if isinstance(item, dict)]
        burn_addresses = {
            "0x0000000000000000000000000000000000000000",
            "0x000000000000000000000000000000000000dead",
        }
        lp_locked = sum(
            _percent(holder.get("percent"), 0.0) or 0.0
            for holder in lp_holders
            if _truthy(holder.get("is_locked"))
            or str(holder.get("address", "")).lower() in burn_addresses
        )
        lp_unlocked = sum(
            _percent(holder.get("percent"), 0.0) or 0.0
            for holder in lp_holders
            if not _truthy(holder.get("is_locked"))
            and str(holder.get("address", "")).lower() not in burn_addresses
        )
        open_source: bool | None = None
        if goplus.get("is_open_source") not in (None, ""):
            open_source = _truthy(goplus.get("is_open_source"))
        elif contract_code.get("rootOpenSource") is not None:
            open_source = bool(contract_code.get("rootOpenSource"))

        return SecurityProfile(
            chain=candidate.chain,
            token_address=candidate.token_address,
            checked=bool(providers),
            admin_checks_complete=goplus_usable,
            simulation_checked=honeypot_usable,
            sell_simulation_success=bool(honeypot.get("simulationSuccess")),
            providers=tuple(providers),
            is_honeypot=_truthy(goplus.get("is_honeypot")) or bool(hp_result.get("isHoneypot")),
            cannot_buy=_truthy(goplus.get("cannot_buy")),
            cannot_sell=_truthy(goplus.get("cannot_sell"))
            or _truthy(goplus.get("cannot_sell_all")),
            hidden_owner=_truthy(goplus.get("hidden_owner")),
            owner_change_balance=_truthy(goplus.get("owner_change_balance")),
            transfer_pausable=_truthy(goplus.get("transfer_pausable")),
            blacklist_function=_truthy(goplus.get("is_blacklisted")),
            mintable=_truthy(goplus.get("is_mintable")),
            proxy=_truthy(goplus.get("is_proxy")) or bool(contract_code.get("isProxy")),
            open_source=open_source,
            buy_tax=_number(simulation.get("buyTax"), _percent(goplus.get("buy_tax"), 0.0) or 0.0),
            sell_tax=_number(simulation.get("sellTax"), _percent(goplus.get("sell_tax"), 0.0) or 0.0),
            owner_percent=_percent(goplus.get("owner_percent")),
            top_unlocked_eoa_percent=round(unlocked_eoa, 2) if holders else None,
            lp_locked_percent=round(lp_locked, 2) if lp_holders else None,
            lp_unlocked_percent=round(lp_unlocked, 2) if lp_holders else None,
            holder_count=_integer(goplus.get("holder_count"), 0) or _integer((honeypot.get("token") or {}).get("totalHolders"), 0) or None,
            max_sell_quote_amount=(
                _number((simulation.get("maxSell") or {}).get("withToken"), 0.0) or None
            ),
            fake_token=_truthy(goplus.get("fake_token")),
            creator_percent=_percent(goplus.get("creator_percent")),
            creator_honeypot_count=_integer(goplus.get("honeypot_with_same_creator"), 0),
            can_take_back_ownership=_truthy(goplus.get("can_take_back_ownership")),
            selfdestruct=_truthy(goplus.get("selfdestruct")),
            slippage_modifiable=_truthy(goplus.get("slippage_modifiable")),
            personal_slippage_modifiable=_truthy(goplus.get("personal_slippage_modifiable")),
            trading_cooldown=_truthy(goplus.get("trading_cooldown")),
            risk_level=_integer(summary.get("riskLevel"), -1) if summary.get("riskLevel") is not None else None,
            risk_label=str(summary.get("risk")) if summary.get("risk") else None,
            flags=tuple(sorted(flags)),
            error="; ".join(errors)[:300] or None,
        )


class SignalOverlay:
    """Optional social/smart-wallet feed with a small, vendor-neutral JSON contract."""

    def __init__(self, url: str | None) -> None:
        self.url = url

    async def fetch(self, session: aiohttp.ClientSession) -> dict[str, dict[str, Any]]:
        if not self.url:
            return {}
        payload = await _get_json(session, self.url)
        rows = payload.get("signals", []) if isinstance(payload, dict) else []
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            chain = str(row.get("chain", "")).lower()
            address = normalise_address(row.get("tokenAddress") or row.get("token_address"))
            if chain and address:
                result[f"{chain}:{address}"] = row
        return result


class SmartWalletMonitor:
    """Monitor manually vetted and outcome-curated wallets without paid APIs."""

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self.rpc_urls = {"base": config.base_rpc_url, "robinhood": config.robinhood_rpc_url}
        self.configured_wallets = {
            normalise_address(wallet)
            for wallet in config.smart_wallets
            if len(normalise_address(wallet)) == 42
        }

    async def _rpc(self, session: aiohttp.ClientSession, url: str, method: str, params: list[Any]) -> Any:
        async with session.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) as response:
            if response.status >= 400:
                raise RuntimeError(f"smart-wallet RPC HTTP {response.status}")
            payload = await response.json(content_type=None)
        if payload.get("error"):
            raise RuntimeError(payload["error"].get("message", "smart-wallet RPC error"))
        return payload.get("result")

    async def collect(
        self, session: aiohttp.ClientSession, state: "SQLiteState", candidates: list[Candidate]
    ) -> dict[str, dict[str, float]]:
        curated_by_chain = {
            chain: state.curated_smart_wallets(
                self.config.smart_wallet_min_observations,
                self.config.smart_wallet_min_win_rate,
                self.config.smart_wallet_min_average_return,
                chain=chain,
            )
            for chain in self.rpc_urls
        } if self.config.auto_curate_smart_wallets else {}
        if not self.configured_wallets and not any(curated_by_chain.values()):
            return {}
        grouped: dict[str, list[str]] = defaultdict(list)
        pair_by_key: dict[str, str] = {}
        for candidate in candidates:
            pair = normalise_address(candidate.pair_address)
            if candidate.chain in self.rpc_urls and len(pair) == 42:
                grouped[candidate.chain].append(candidate.token_address)
                pair_by_key[candidate.key] = pair
        buys_by_key: dict[str, set[str]] = defaultdict(set)
        sells_by_key: dict[str, set[str]] = defaultdict(set)
        for chain, addresses in grouped.items():
            wallets = self.configured_wallets | curated_by_chain.get(chain, set())
            wallet_topics = [
                "0x" + wallet.removeprefix("0x").rjust(64, "0") for wallet in wallets
            ]
            if not wallet_topics:
                continue
            url = self.rpc_urls[chain]
            latest = int(await self._rpc(session, url, "eth_blockNumber", []), 16)
            cursor_key = f"smart_wallet_block:{chain}"
            stored = state.get_cursor(cursor_key)
            start = int(stored) + 1 if stored is not None else latest - self.config.rpc_lookback_blocks + 1
            start = max(0, start, latest - self.config.rpc_max_block_span + 1)
            if start > latest:
                continue
            for index in range(0, len(set(addresses)), 20):
                token_batch = list(dict.fromkeys(addresses))[index : index + 20]
                common = {"fromBlock": hex(start), "toBlock": hex(latest), "address": token_batch}
                incoming, outgoing = await asyncio.gather(
                    self._rpc(session, url, "eth_getLogs", [{**common, "topics": [TRANSFER_TOPIC, None, wallet_topics]}]),
                    self._rpc(session, url, "eth_getLogs", [{**common, "topics": [TRANSFER_TOPIC, wallet_topics]}]),
                )
                for log in incoming or []:
                    key = f"{chain}:{normalise_address(log.get('address'))}"
                    topics = log.get("topics") or []
                    source = _address_from_topic(topics[1] if len(topics) > 1 else None)
                    recipient = _address_from_topic(topics[2] if len(topics) > 2 else None)
                    if source == pair_by_key.get(key) and recipient in wallets:
                        buys_by_key[key].add(recipient)
                for log in outgoing or []:
                    key = f"{chain}:{normalise_address(log.get('address'))}"
                    topics = log.get("topics") or []
                    sender = _address_from_topic(topics[1] if len(topics) > 1 else None)
                    recipient = _address_from_topic(topics[2] if len(topics) > 2 else None)
                    if recipient == pair_by_key.get(key) and sender in wallets:
                        sells_by_key[key].add(sender)
            state.set_cursor(cursor_key, str(latest))
        keys = set(buys_by_key) | set(sells_by_key)
        return {
            key: {
                "smart_wallet_buys": float(len(buys_by_key[key])),
                "smart_wallet_sells": float(len(sells_by_key[key])),
                "smart_wallet_net_usd": 0.0,
            }
            for key in keys
        }

    async def observe_early_buyers(
        self,
        session: aiohttp.ClientSession,
        candidate: Candidate,
        snapshot: MarketSnapshot,
    ) -> set[str]:
        """Resolve transaction senders buying from a pool near alert time.

        Transfer recipients are often routers. Transaction senders are a better
        approximation of the actual wallet and need no third-party wallet feed.
        """
        pair = normalise_address(snapshot.pair_address or candidate.pair_address)
        url = self.rpc_urls.get(candidate.chain)
        if not url or len(pair) != 42:
            return set()
        latest = int(await self._rpc(session, url, "eth_blockNumber", []), 16)
        start = max(0, latest - self.config.early_buyer_lookback_blocks + 1)
        pair_topic = "0x" + pair.removeprefix("0x").rjust(64, "0")
        logs = await self._rpc(
            session,
            url,
            "eth_getLogs",
            [{
                "fromBlock": hex(start),
                "toBlock": hex(latest),
                "address": candidate.token_address,
                "topics": [TRANSFER_TOPIC, pair_topic],
            }],
        )
        hashes = list(
            dict.fromkeys(
                log.get("transactionHash")
                for log in logs or []
                if isinstance(log, dict) and log.get("transactionHash")
            )
        )[-self.config.max_early_buyers_per_alert :]
        if not hashes:
            return set()
        requests = [
            {"jsonrpc": "2.0", "id": index, "method": "eth_getTransactionByHash", "params": [tx_hash]}
            for index, tx_hash in enumerate(hashes)
        ]
        async with session.post(url, json=requests) as response:
            if response.status >= 400:
                raise RuntimeError(f"early-buyer RPC HTTP {response.status}")
            payload = await response.json(content_type=None)
        rows = payload if isinstance(payload, list) else []
        return {
            normalise_address((row.get("result") or {}).get("from"))
            for row in rows
            if isinstance(row, dict)
            and len(normalise_address((row.get("result") or {}).get("from"))) == 42
        }

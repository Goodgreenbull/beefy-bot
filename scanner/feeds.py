from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import aiohttp

from .config import ScannerConfig
from .models import Candidate, MarketSnapshot, SecurityProfile, normalise_address, utc_now

if TYPE_CHECKING:
    from .state import SQLiteState


PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
O1_LAUNCHED_TOPIC = "0x207384e895174175cc774fe7f7457b37c382f27ebf53d37d5257b862f80eaf9c"

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


async def _get_json(session: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
    async with session.get(url, **kwargs) as response:
        if response.status == 429:
            raise RuntimeError(f"rate limited by {response.url.host}")
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} from {response.url.host}")
        return await response.json(content_type=None)


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
            if not address or chain != "base" or launch.get("status") not in (None, "deployed"):
                continue
            deployer = launch.get("deployer") or {}
            candidates.append(
                Candidate(
                    chain="base",
                    token_address=address,
                    source=self.name,
                    launch_at=_timestamp(launch.get("timestamp") or launch.get("createdAt")),
                    name=launch.get("tokenName"),
                    symbol=launch.get("tokenSymbol"),
                    deployer=deployer.get("walletAddress") if isinstance(deployer, dict) else None,
                    metadata={"activity_id": launch.get("activityId")},
                )
            )
        return candidates


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
            if spec.pool_topic_index is not None and len(topics) > spec.pool_topic_index:
                pool_id = topics[spec.pool_topic_index]
            words = [log.get("data", "0x")[i : i + 64] for i in range(2, len(log.get("data", "0x")), 64)]
            quote = _address_from_word(words[0]) if words else None
            block_number = int(log.get("blockNumber", "0x0"), 16)
            candidates.append(
                Candidate(
                    chain=self.chain,
                    token_address=token,
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
                "pair_created_at": pair.get("pairCreatedAt"),
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
        for candidate in candidates:
            if candidate.chain in self.rpc_urls:
                grouped[candidate.chain].append(candidate.token_address)
        signals: dict[str, dict[str, float]] = defaultdict(
            lambda: {"smart_wallet_buys": 0.0, "smart_wallet_sells": 0.0, "smart_wallet_net_usd": 0.0}
        )
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
                    signals[key]["smart_wallet_buys"] += 1
                for log in outgoing or []:
                    key = f"{chain}:{normalise_address(log.get('address'))}"
                    signals[key]["smart_wallet_sells"] += 1
            state.set_cursor(cursor_key, str(latest))
        return dict(signals)

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

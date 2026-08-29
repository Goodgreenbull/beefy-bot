from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import aiohttp

from .config import ScannerConfig
from .models import Candidate, MarketSnapshot, normalise_address, utc_now

if TYPE_CHECKING:
    from .state import SQLiteState


PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


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
    """Find standard V2 PairCreated and V3 PoolCreated events without guessing factories."""

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

        logs = await self._rpc(
            session,
            "eth_getLogs",
            [{"fromBlock": hex(start), "toBlock": hex(latest), "topics": [[PAIR_CREATED_TOPIC, POOL_CREATED_TOPIC]]}],
        )
        block_numbers = {int(log["blockNumber"], 16) for log in logs or [] if log.get("blockNumber")}
        block_times = await self._block_times(session, block_numbers)
        quote_tokens = self.config.quote_tokens.get(self.chain, set())
        candidates: list[Candidate] = []

        for log in logs or []:
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
            return snapshot_from_gecko(candidate)

        expected_chain_ids = {candidate.chain}
        if candidate.chain == "robinhood":
            expected_chain_ids.update({"robinhood-chain", "robinhood_chain", "robinhoodchain"})
        chain_pairs = [p for p in pairs if str(p.get("chainId", "")).lower() in expected_chain_ids]
        if candidate.pair_address:
            exact = [p for p in chain_pairs if normalise_address(p.get("pairAddress")) == candidate.pair_address]
            if exact:
                chain_pairs = exact
        if not chain_pairs:
            return snapshot_from_gecko(candidate)
        viable = chain_pairs
        pair = max(viable, key=lambda p: _number((p.get("liquidity") or {}).get("usd")))
        base_token = pair.get("baseToken") or {}
        if normalise_address(base_token.get("address")) not in {candidate.token_address, ""}:
            matching = [p for p in viable if normalise_address((p.get("baseToken") or {}).get("address")) == candidate.token_address]
            if matching:
                pair = max(matching, key=lambda p: _number((p.get("liquidity") or {}).get("usd")))

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
            },
        )


def snapshot_from_gecko(candidate: Candidate) -> MarketSnapshot | None:
    market = candidate.metadata.get("gecko_market")
    if not isinstance(market, dict):
        return None
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
    """Count ERC-20 transfers into/out of explicitly curated wallets."""

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self.rpc_urls = {"base": config.base_rpc_url, "robinhood": config.robinhood_rpc_url}
        self.wallet_topics = ["0x" + wallet.removeprefix("0x").rjust(64, "0") for wallet in config.smart_wallets]

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
        if not self.wallet_topics:
            return {}
        grouped: dict[str, list[str]] = defaultdict(list)
        for candidate in candidates:
            if candidate.chain in self.rpc_urls:
                grouped[candidate.chain].append(candidate.token_address)
        signals: dict[str, dict[str, float]] = defaultdict(
            lambda: {"smart_wallet_buys": 0.0, "smart_wallet_sells": 0.0, "smart_wallet_net_usd": 0.0}
        )
        for chain, addresses in grouped.items():
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
                    self._rpc(session, url, "eth_getLogs", [{**common, "topics": [TRANSFER_TOPIC, None, self.wallet_topics]}]),
                    self._rpc(session, url, "eth_getLogs", [{**common, "topics": [TRANSFER_TOPIC, self.wallet_topics]}]),
                )
                for log in incoming or []:
                    key = f"{chain}:{normalise_address(log.get('address'))}"
                    signals[key]["smart_wallet_buys"] += 1
                for log in outgoing or []:
                    key = f"{chain}:{normalise_address(log.get('address'))}"
                    signals[key]["smart_wallet_sells"] += 1
            state.set_cursor(cursor_key, str(latest))
        return dict(signals)

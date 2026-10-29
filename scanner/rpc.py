"""Paced read-only JSON-RPC with bounded recovery from provider throttling."""

from __future__ import annotations

import asyncio
import time
from weakref import WeakKeyDictionary

import aiohttp


_pools = WeakKeyDictionary()


class _Unavailable(RuntimeError):
    pass


def _validate(body):
    rows = body if isinstance(body, list) else [body]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise _Unavailable("RPC returned an invalid response")
    for row in rows:
        if row.get("error"):
            error = row["error"]
            message = str(error.get("message", "RPC error"))
            if error.get("code") in {-32005, -32016} or any(
                word in message.lower() for word in (
                    "rate limit", "too many requests", "overloaded", "request limit reached",
                )
            ):
                raise _Unavailable("RPC rate limited")
            raise RuntimeError(message)
        if "result" not in row:
            raise _Unavailable("RPC response missing result")
    return body


async def rpc_payload(session, url, payload, config, *, chain):
    """Share pacing across feeds; never turn a provider error into an empty scan."""
    requests = payload if isinstance(payload, list) else [payload]
    allowed = {"eth_blockNumber", "eth_chainId", "eth_call", "eth_getLogs",
               "eth_getBlockByNumber", "eth_getTransactionByHash", "eth_getTransactionReceipt"}
    if any(row.get("method") not in allowed for row in requests):
        raise ValueError("Scanner RPC transport accepts read methods only")
    # Providers count methods inside a JSON-RPC batch against their quota.
    # Bound batches before entering the shared lock; each chunk still receives
    # the normal chain verification, retry, ID validation and pacing rules.
    if isinstance(payload, list) and len(payload) > 10:
        responses = []
        for offset in range(0, len(payload), 10):
            responses.extend(await rpc_payload(
                session, url, payload[offset:offset + 10], config, chain=chain,
            ))
        return responses
    endpoints = tuple(dict.fromkeys((url, *(
        config.base_rpc_fallback_urls if chain == "base" else ()
    ))))
    pools = _pools.setdefault(session, {})
    pool = pools.setdefault(endpoints, {
        "lock": asyncio.Lock(), "last_at": 0.0, "spacing": 0.20,
        "cooldowns": {}, "verified": set(),
    })
    async with pool["lock"]:
        last_error = _Unavailable("RPC providers unavailable")
        available = [endpoint for endpoint in endpoints
                     if pool["cooldowns"].get(endpoint, 0) <= time.monotonic()]
        if not available:
            raise last_error
        for endpoint in available:
            for attempt in range(2):
                try:
                    delay = pool["spacing"] - (time.monotonic() - pool["last_at"])
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if endpoint != url and endpoint not in pool["verified"]:
                        async with session.post(endpoint, json={
                            "jsonrpc": "2.0", "id": 0, "method": "eth_chainId", "params": [],
                        }) as response:
                            pool["last_at"] = time.monotonic()
                            pool["spacing"] = 0.20
                            if response.status >= 400:
                                raise _Unavailable(f"RPC chain verification HTTP {response.status}")
                            body = _validate(await response.json(content_type=None))
                        if body.get("result") != "0x2105":
                            raise _Unavailable("RPC fallback is not Base mainnet")
                        pool["verified"].add(endpoint)
                        await asyncio.sleep(0.20)
                    async with session.post(endpoint, json=payload) as response:
                        pool["last_at"] = time.monotonic()
                        pool["spacing"] = 0.20 * len(requests)
                        # Access/quota failures belong to this provider. A
                        # separately configured, chain-verified endpoint may
                        # still serve the same read. Keep malformed requests
                        # and contract reverts outside this retry path.
                        if response.status in {401, 403, 408, 429} or response.status >= 500:
                            raise _Unavailable(f"RPC HTTP {response.status}")
                        if response.status >= 400:
                            raise RuntimeError(f"RPC HTTP {response.status}")
                        body = _validate(await response.json(content_type=None))
                    if not isinstance(payload, list) and not isinstance(body, dict):
                        raise _Unavailable("RPC returned a batch for a single request")
                    if isinstance(payload, list) and (
                        not isinstance(body, list)
                        or {row.get("id") for row in body} != {row.get("id") for row in payload}
                    ):
                        raise _Unavailable("RPC batch response missing request IDs")
                    pool["cooldowns"].pop(endpoint, None)
                    return body
                except (_Unavailable, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                    last_error = _Unavailable(f"RPC unavailable ({type(error).__name__})")
                    if isinstance(error, _Unavailable):
                        last_error = error
                    if attempt == 0:
                        await asyncio.sleep(0.5)
            pool["cooldowns"][endpoint] = time.monotonic() + 300
        raise last_error


async def rpc_call(session, url, method, params, config, *, chain):
    if method == "eth_getLogs" and params and isinstance(params[0], dict):
        query = params[0]
        addresses = query.get("address")
        try:
            start = int(query["fromBlock"], 16)
            end = int(query["toBlock"], 16)
        except (KeyError, ValueError, TypeError):
            start = end = None
        # Public gateways can reject otherwise valid address arrays or wider
        # ranges with only "invalid params". Keep the full window, query each
        # address separately, and return only after every partition succeeds.
        if isinstance(addresses, list) or (start is not None and end-start >= 300):
            if isinstance(addresses, list) and not addresses:
                raise ValueError("RPC log query has an empty address list")
            windows = [(left, min(end, left+299)) for left in range(start, end+1, 300)] \
                if start is not None and end >= start else [(None, None)]
            results = []
            for address in addresses if isinstance(addresses, list) else [addresses]:
                for left, right in windows:
                    partition = dict(query)
                    if isinstance(addresses, list):
                        partition["address"] = address
                    if left is not None:
                        partition.update(fromBlock=hex(left), toBlock=hex(right))
                    body = await rpc_payload(session, url, {
                        "jsonrpc": "2.0", "id": 1, "method": method, "params": [partition],
                    }, config, chain=chain)
                    rows = body["result"]
                    if not isinstance(rows, list):
                        raise RuntimeError("RPC log response is not a list")
                    results.extend(rows)
            return results
    body = await rpc_payload(session, url, {
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    }, config, chain=chain)
    result = body["result"]
    if method == "eth_getLogs" and not isinstance(result, list):
        raise RuntimeError("RPC log response is not a list")
    return result

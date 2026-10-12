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
                word in message.lower() for word in ("rate limit", "too many requests", "overloaded")
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
    endpoints = tuple(dict.fromkeys((url, *(
        config.base_rpc_fallback_urls if chain == "base" else ()
    ))))
    pools = _pools.setdefault(session, {})
    pool = pools.setdefault(endpoints, {
        "lock": asyncio.Lock(), "last_at": 0.0, "cooldowns": {}, "verified": set(),
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
                    delay = 0.20 - (time.monotonic() - pool["last_at"])
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if endpoint != url and endpoint not in pool["verified"]:
                        async with session.post(endpoint, json={
                            "jsonrpc": "2.0", "id": 0, "method": "eth_chainId", "params": [],
                        }) as response:
                            pool["last_at"] = time.monotonic()
                            if response.status >= 400:
                                raise _Unavailable(f"RPC chain verification HTTP {response.status}")
                            body = _validate(await response.json(content_type=None))
                        if body.get("result") != "0x2105":
                            raise _Unavailable("RPC fallback is not Base mainnet")
                        pool["verified"].add(endpoint)
                        await asyncio.sleep(0.20)
                    async with session.post(endpoint, json=payload) as response:
                        pool["last_at"] = time.monotonic()
                        if response.status == 429 or response.status >= 500:
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
    body = await rpc_payload(session, url, {
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    }, config, chain=chain)
    return body["result"]

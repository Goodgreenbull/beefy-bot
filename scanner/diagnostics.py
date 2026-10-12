"""Temporary, read-only RPC probes from the actual Render instance."""

import asyncio
from urllib.parse import urlsplit

import aiohttp

from .feeds import TRANSFER_TOPIC


async def probe_base_rpcs(wallets):
    endpoints = ("https://base.api.onfinality.io/public", "https://base.gateway.tenderly.co",
                 "https://8453.rpc.thirdweb.com", "https://mainnet.base.org")
    results = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        async def probe(url):
            host = urlsplit(url).hostname
            async def call(method, params):
                async with session.post(url, json={
                    "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
                }) as response:
                    if response.status >= 400:
                        raise RuntimeError(f"HTTP {response.status}")
                    body = await response.json(content_type=None)
                if body.get("error"):
                    raise RuntimeError(f"RPC code {body['error'].get('code', 'unknown')}")
                return body.get("result")
            try:
                chain = await call("eth_chainId", [])
                if chain != "0x2105":
                    raise RuntimeError("wrong chain")
                block = int(await call("eth_blockNumber", []), 16)-2
                logs = await call("eth_getLogs", [{
                    "fromBlock": hex(block-299), "toBlock": hex(block),
                    "topics": [TRANSFER_TOPIC, None,
                               ["0x"+wallet[2:].rjust(64, "0") for wallet in wallets]],
                }])
                if not isinstance(logs, list):
                    raise RuntimeError("invalid logs")
                results[host] = {"wallet_query": "healthy", "events": len(logs)}
            except RuntimeError as error:
                results[host] = {"wallet_query": str(error)}
            except Exception as error:
                results[host] = {"wallet_query": type(error).__name__}
            print(f"RPC recovery probe {host}: {results[host]}", flush=True)
        await asyncio.gather(*(probe(url) for url in endpoints))
    return results

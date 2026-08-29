# First-Leg Scanner Architecture

## Data path

```text
Bankr launches ─┐
Flaunch tokens ─┤
New-pool API ───┼─> normalize candidate ─> SQLite state ─> market enrichment
Base RPC logs ──┤                                      │
Robinhood RPC ──┘                                      ├─> ignition / reawakening score
                                                       ├─> anti-late gate
social metadata / optional overlay ────────────────────┤
curated smart-wallet transfers ────────────────────────┘
                                                              │
                                                     dedupe + cooldown
                                                              │
                                                     Telegram watch alert
```

Every external feed is isolated. One failing provider is recorded in `feed_health` but does not stop the remaining feeds or the next cycle.

## Direct discovery

- Bankr: polls its unauthenticated recent-launch endpoint and keeps Base deployments.
- Flaunch: advances the documented `orderId` cursor and stores it in SQLite.
- New pools: polls GeckoTerminal's per-network `new_pools` feed.
- Base and Robinhood Chain: polls standard EVM logs for Uniswap-style V2 `PairCreated` and V3 `PoolCreated` event signatures. It does not require a guessed factory allowlist. Block cursors prevent gaps during normal restarts.

Robinhood Chain is therefore discoverable even when a market-data indexer has not added a named Robinhood network. A new pair is kept in state until DexScreener or an optional overlay can supply enough market data to score it. For a production Robinhood deployment, add known quote-token addresses to `SCANNER_QUOTE_TOKENS_JSON` so the non-quote side is selected reliably.

## State and deduplication

SQLite stores only public market data:

- normalized candidates and first/launch timestamps;
- rolling snapshots used for acceleration and fading detection;
- per-feed block/order cursors;
- feed success/error timestamps;
- prior alerts and their scores.

The same token can arrive from several feeds and is merged by `chain:token_address`. An alert is suppressed during the cooldown unless its score improves materially. A later reawakening may alert after the cooldown.

## Scoring

The 0–100 score is inspectable and uses:

- freshness or reawakening persistence;
- 5-minute volume/liquidity churn;
- transaction velocity and buy pressure;
- volume acceleration versus stored snapshots;
- controlled price confirmation;
- social metadata/velocity;
- curated smart-wallet flow.

The anti-late gate rejects or penalizes candidates that are already extended, above the configured microcap ceiling, dominated by sells, drawing down from a recent peak, or fading in both price and volume. Minimum liquidity and trade-count gates must also pass.

Outputs are `MONITOR`, `EARLY WATCH`, `STRONG WATCH`, or `AVOID LATE`. None of these outputs places an order.

## Operational limits

This architecture closes the hourly/two-hour latency gap; it does not prove profitability. Scores and thresholds need forward testing with every alert and subsequent maximum favorable/adverse excursion recorded before any auto-trading discussion. Public APIs and RPCs have rate and indexing delays.

The free deployment uses a five-minute scan and two ten-minute health checks (one internal and one scheduled through GitHub Actions) to remain below Render's 15-minute idle timeout. The public repository's standard GitHub-hosted Actions do not consume paid minutes. Scheduled Actions can be delayed, which is why the internal check is retained as the primary keep-awake path.

Free Render storage remains ephemeral. After a restart the scanner restores recent candidates from launch feeds and a 1,800-block RPC lookback, warms up for one cycle, and then resumes. Alert history from before the restart is not permanent, so an unusually timed restart can still cause a repeated alert later. Permanent deduplication would require an external durable datastore.

Social quality is limited to market metadata unless `SCANNER_SIGNAL_OVERLAY_URL` is connected to an X/social listener. Smart-wallet quality depends entirely on the public wallet list supplied by the operator. These are explicit inputs, not hidden heuristics.

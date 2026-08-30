# First-Leg Scanner Architecture

## Data path

```text
launch APIs / profiles ─┐
verified factory logs ──┼─> candidate state ─> market + contract enrichment
V2/V3 pool events ──────┘                         │
                                                  ├─> ignition / reawakening score
social/project identity ──────────────────────────┤
manual + learned smart wallets ───────────────────┤
                                                  ├─> quality + anti-late gates
                                                  └─> dedupe ─> Telegram alert
                                                                       │
                                              15m / 1h / 6h / 24h outcomes
                                                                       │
                                                MFE/MAE + threshold calibration
```

Every external feed is isolated. One failing provider is recorded in `feed_health` but does not stop the remaining feeds or the next cycle.

## Direct discovery

- Bankr: polls its unauthenticated recent-launch endpoint and keeps Base deployments.
- Flaunch: advances the documented `orderId` cursor and stores it in SQLite.
- Clanker: polls its public Base token index directly.
- Baseline: polls its public Base CoinGecko adapter and asset/pair metadata.
- o1/B20: reads verified production `Launched` events from the Base and Robinhood factories, including Robinhood tokenized-stock launches.
- DexScreener profiles: adds recently profiled Base/Robinhood tokens so non-standard launches are not solely dependent on pool indexing.
- New pools: polls GeckoTerminal's per-network `new_pools` feed.
- Base and Robinhood Chain: polls standard EVM logs for Uniswap-style V2 `PairCreated` and V3 `PoolCreated` event signatures. It does not require a guessed factory allowlist. Block cursors prevent gaps during normal restarts.

Pools.fun is covered at the documented Sushi V3 pool-creation layer. The official Pools.fun contracts page does not currently publish its PartyFactory address. Likewise, the scanner does not ship a scraped BaseStonk factory address as if it were authoritative. `SCANNER_FACTORY_FEEDS_JSON` provides direct event ingestion as soon as a current factory/event is verified from a first-party source.

Robinhood Chain is therefore discoverable even when a market-data indexer has not added a named Robinhood network. A new pair is kept in state until DexScreener or an optional overlay can supply enough market data to score it. For a production Robinhood deployment, add known quote-token addresses to `SCANNER_QUOTE_TOKENS_JSON` so the non-quote side is selected reliably.

## State and deduplication

SQLite stores only public market data:

- normalized candidates and first/launch timestamps;
- rolling snapshots used for acceleration and fading detection;
- per-feed block/order cursors;
- feed success/error timestamps;
- prior alerts and their scores.
- scheduled alert outcomes, MFE, and MAE;
- early-buyer observations and aggregate wallet reputation;
- cached public contract-risk profiles.

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
- direct-launch provenance and project identity;
- contract safety, creator history, and holder concentration.

The anti-late gate rejects or penalizes candidates that are already extended, above the configured microcap ceiling, dominated by sells, drawing down from a recent peak, or fading in both price and volume. The quality gate also requires a checked contract, project/social or proven-wallet evidence, minimum liquidity and transactions, and no hard honeypot/buy-sell restriction. Duplicate identities, serial deployers, excessive owner/creator concentration, prior creator honeypots, modifiable taxes, mint/pause/blacklist controls, and unverified source code reduce or block conviction.

Outputs are `MONITOR`, `EARLY WATCH`, `STRONG WATCH`, or `AVOID LATE`. None of these outputs places an order.

## Forward outcomes and calibration

Every sent alert stores its entry price, market cap, and liquidity. Alerted candidates stay in the active set until the first available observation after 15, 60, 360, and 1,440 minutes. Each observation updates the alert's best and worst sampled return (MFE/MAE). At a five-minute cadence these are observed excursions, not tick-perfect extrema.

WATCH and BUY begin at 74 and 84. Automatic calibration waits for at least 30 completed 24-hour samples, then searches only for thresholds supported by minimum sample sizes, win rate, median return, MFE, and MAE. It will not lower the conservative starting thresholds. This deliberately avoids retuning from the first bad $6k alert or one lucky winner.

Early buyer addresses are resolved from transaction senders rather than router recipients. A wallet is promoted into the learned cohort only after repeated 24-hour results meet the configured observation, win-rate, and average-return floors.

## Operational limits

This architecture closes the hourly/two-hour latency gap; it does not prove profitability. The measurements make false-positive review and threshold changes evidence-led, but public APIs and RPCs still have rate and indexing delays.

The free deployment uses a five-minute scan and two ten-minute health checks (one internal and one scheduled through GitHub Actions) to remain below Render's 15-minute idle timeout. The public repository's standard GitHub-hosted Actions do not consume paid minutes. Scheduled Actions can be delayed, which is why the internal check is retained as the primary keep-awake path.

Free Render storage remains ephemeral. After a restart the scanner restores recent candidates from launch feeds and a 1,800-block RPC lookback, warms up for one cycle, and then resumes. Alert history from before the restart is not permanent, so an unusually timed restart can still cause a repeated alert later. Permanent deduplication would require an external durable datastore.

Social quality is limited to market/profile metadata unless `SCANNER_SIGNAL_OVERLAY_URL` is connected to an X/social listener. The automatic wallet cohort requires three completed observations by default, so it intentionally starts empty. These are explicit inputs, not hidden claims of profitability.

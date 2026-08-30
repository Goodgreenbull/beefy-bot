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
- Base: polls standard V2 `PairCreated` and V3 `PoolCreated` events only from verified Uniswap and Sushi factory addresses. Robinhood's generic factory lane stays disabled until a first-party address is published; its verified o1 factories and public profile/index feeds remain active. Block cursors prevent gaps during normal restarts.

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

The anti-late gate rejects or penalizes candidates that are already extended, above the configured microcap ceiling, dominated by sells, drawing down from a recent peak, or fading in both price and volume. The quality gate also requires a positive USD price, conclusive admin checks, a successful Base sell simulation when the token did not come from a verified launch factory, project/social or proven-wallet evidence, minimum liquidity and transactions, and no hard honeypot/buy-sell restriction. Duplicate identities, serial deployers, excessive owner/creator concentration, prior creator honeypots, high buy/sell taxes, unlocked liquidity on untrusted launches, modifiable taxes, mint/pause/blacklist controls, and unverified source code reduce or block conviction.

Outputs are `MONITOR`, `EARLY WATCH`, `STRONG WATCH`, or `AVOID LATE`. None of these outputs places an order.

Telegram converts eligible outputs into one compact verdict line. Its bounded upside scenario combines score, buyer share, volume/liquidity churn, smart-wallet support, social evidence, stage, liquidity depth, and short-term extension. It is measured from the alert price, capped at 2.0x for WATCH and 2.8x for BUY, and is explicitly presented as a scenario rather than a guaranteed return.

## Forward outcomes and calibration

Every sent alert stores its entry price, market cap, and liquidity. Alerted candidates stay in the active set until the first available observation after 15, 60, 360, and 1,440 minutes. Each horizon stores the best and worst sampled return available at that point (MFE/MAE), rather than reusing the eventual 24-hour extrema. Materially late samples are retained but excluded from calibration. After three successful empty market lookups, a disappeared pool is recorded as a terminal -100% outcome; provider/network failures do not count as confirmations. At a five-minute cadence these are observed excursions, not tick-perfect extrema.

WATCH and BUY begin at 74 and 84. Automatic calibration waits for at least 30 completed 24-hour samples, then searches only for thresholds supported by minimum sample sizes, win rate, median return, MFE, and MAE. It will not lower the conservative starting thresholds. This deliberately avoids retuning from the first bad $6k alert or one lucky winner.

Early buyer addresses are resolved from transaction senders rather than router recipients. A wallet is promoted into the learned cohort only after results across distinct token contracts meet the configured observation, win-rate, and average-return floors.

## Operational limits

This architecture closes the hourly/two-hour latency gap; it does not prove profitability. The measurements make false-positive review and threshold changes evidence-led, but public APIs and RPCs still have rate and indexing delays.

The free deployment uses a five-minute scan and two ten-minute health checks (one internal and one scheduled through GitHub Actions) to remain below Render's 15-minute idle timeout. The public repository's standard GitHub-hosted Actions do not consume paid minutes. Scheduled Actions can be delayed, which is why the internal check is retained as the primary keep-awake path.

Free Render storage remains ephemeral. After a fresh-state restart the scanner restores recent candidates from launch feeds and a 1,800-block RPC lookback and permanently marks that initial backlog as bootstrap data. It cannot emit a recycled ignition alert, although a later evidence-backed reawakening remains eligible. New post-start discoveries resume normally after warm-up. This removes routine redeploy repeats without introducing an external paid datastore.

Social quality is limited to market/profile metadata unless `SCANNER_SIGNAL_OVERLAY_URL` is connected to an X/social listener. The automatic wallet cohort requires three completed observations by default, so it intentionally starts empty. These are explicit inputs, not hidden claims of profitability.

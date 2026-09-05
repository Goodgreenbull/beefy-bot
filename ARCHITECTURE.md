# First-Leg Scanner Architecture

## Data path

```text
launch APIs / profiles ─┐
verified factory logs ──┼─> candidate state ─> market + contract enrichment
GMGN rank/hot/signal evidence ─┤
V2/V3 pool events ──────┘                         │
token Transfer logs ─────────────────────────────┼─> unique buyer/holder inflection
exact-CA social / creator overlay ───────────────┤
manual + learned smart wallets ───────────────────┤
                                                  ├─> quality + anti-late gates
                                                  └─> dedupe ─> PULSE / trade call
                                                                       │
                                              15m / 1h / 6h / 24h outcomes
                                                                       │
                                      MFE/MAE + calibration + PROTECT warnings
```

Every external feed is isolated. One failing provider is recorded in `feed_health` but does not stop the remaining feeds or the next cycle.

## Direct discovery

- Bankr: polls its unauthenticated recent-launch endpoint and keeps both Base and Robinhood deployments, including deployer and supplied social links.
- Flaunch: advances the documented `orderId` cursor and stores it in SQLite.
- Baseline: polls its public Base CoinGecko adapter and asset/pair metadata.
- o1/B20: reads verified production `Launched` events from the Base and Robinhood factories, including Robinhood tokenized-stock launches.
- pons: reads both factory event formats directly. Current curve launches get on-chain price, real quote liquidity, 5m/15m trade wallets, creator selling, fees, holder concentration and supplied social links; Uniswap V3 launches get direct slot-price and locked-pool liquidity estimates.
- DexScreener profiles: adds recently profiled Base/Robinhood tokens so non-standard launches are not solely dependent on pool indexing.
- New pools: polls GeckoTerminal's per-network `new_pools` feed.
- Base: polls standard V2 `PairCreated` and V3 `PoolCreated` events only from verified Uniswap and Sushi factory addresses.
- Robinhood: polls the officially documented pons Uniswap V3 and pools.fun Sushi V3 factories, selecting the non-WETH/USDG side. It also uses HooderScan's no-key cached market endpoint for a rotating maximum of 18 candidates per cycle when direct pricing and DexScreener are unavailable. Block cursors prevent gaps during normal restarts.
- GMGN: makes six read-only calls per cycle for Base/Robinhood 5m rank, launchpad trenches, recent Robinhood smart/KOL/platform-call events, and a combined Base/Robinhood 5m hot-search ranking. The hot-search request uses GMGN's EVM verified/renounced/not-honeypot filters. Beefy then hard-filters unsafe, illiquid, late, stock/RWA-copy, SpaceX, stablecoin and oil themes before admitting at most 80 diversified candidates. The client has an explicit route allowlist and no wallet, portfolio, quote, swap, order or signing surface.

Dedicated Clanker and Zora feed lanes are disabled. In the measured free-tier run they occupied 15 of 50 analysis slots while producing one usable snapshot. Their liquid tokens remain discoverable through GeckoTerminal, DexScreener, verified pool events, or GMGN.

Pools.fun is covered at the documented Sushi V3 pool-creation layer. The official Pools.fun contracts page does not currently publish its PartyFactory address. Likewise, the scanner does not ship a scraped BaseStonk factory address as if it were authoritative. `SCANNER_FACTORY_FEEDS_JSON` provides direct event ingestion as soon as a current factory/event is verified from a first-party source.

Robinhood Chain is therefore discoverable even when a market-data indexer has not added a named Robinhood network. The default quote-token set contains the documented Robinhood WETH and USDG addresses. A new pair remains in state and is rechecked until direct RPC, DexScreener, HooderScan, or an optional overlay supplies enough evidence to score it.

## State and deduplication

SQLite stores only public market data:

- normalized candidates and first/launch timestamps;
- rolling snapshots used for acceleration and fading detection;
- first-detected market cap and on-chain 5m/15m unique-wallet observations;
- per-feed block/order cursors;
- feed success/error timestamps;
- prior alerts and their scores.
- scheduled alert outcomes, MFE, and MAE;
- early-buyer observations and aggregate wallet reputation;
- cached public contract-risk profiles.

The same token can arrive from several feeds and is merged by `chain:token_address`. Routine repeats are suppressed for 24 hours. One meaningful PULSE-to-trade-quality upgrade can alert after the cooldown; otherwise a repeat requires a later measured reawakening.

## Scoring

The 0–100 score is inspectable and gives the largest weights to change happening now:

- freshness or reawakening persistence;
- 5m/15m distinct buyer acceleration and net-new-wallet growth from pool-linked Transfer logs;
- buyer-count acceleration and improving buy/sell balance versus stored snapshots;
- dips being absorbed rather than volume merely being large;
- exact-CA social acceleration and fresh creator activity when supplied by the optional overlay;
- two or more distinct pool-confirmed curated smart-wallet entries for A+;
- direct-launch provenance, creator outcome history and explicit product/narrative evidence;
- liquidity depth and a free sell-simulation-based £20 sellability proxy;
- contract safety, creator history, and holder concentration.
- GMGN hot-search rank changes and fresh smart/KOL/platform-call events as attention evidence, without treating events or historical tagged-wallet totals as distinct proven-wallet entries.

The anti-late gate penalizes anything already above 2x from its measured local base, vertical blow-offs, extended hourly moves, sell dominance, post-peak distribution and fading flow. High churn without buyer/holder growth, many transactions from very few unique wallets, unidentifiable or poor-history deployers, deployer selling, fake associations, duplicate identities and serial launching all reduce or block conviction. The safety gate retains the honeypot, sell restriction, dangerous concentration, tax, unlocked-liquidity and admin-control checks.

Outputs are `MONITOR`, `PULSE`, `SCOUT`, `ACTION`, `A+`, or `AVOID LATE`. PULSE is a 48–59 early-attention breadcrumb, never a buy verdict: it requires live GMGN attention, at least two separate confirmations, basic contract checks, healthy flow, minimum liquidity, no vertical extension, and one precise upgrade trigger. SCOUT begins at 60, ACTION at 70, and A+ at 80 with at least five independent confirmations including two pool-confirmed proven wallets and sellability. Verified launch provenance is capped below ACTION and can support SCOUT only when live buyer/holder flow is exceptional and either the independent contract screen or project identity is already present, leaving one critical gate to upgrade. ACTION and A+ require both an independent contract screen and project/social or proven-wallet evidence. None places an order. A score alone is insufficient: every alert must independently pass its safety, liquidity, inflection and evidence gates.

Telegram converts eligible outputs into one compact verdict line. PULSE messages deliberately omit a target. Only after a token passes the stricter trade-quality, safety, and anti-late gates does its uncapped upside model combine valuation, liquidity depth, buyer share, volume, smart-wallet support, social evidence, stage, and short-term extension. Once five comparable completed calls exist, the structural estimate is blended with their 70th-percentile maximum favourable movement, preferring accurate 24-hour outcomes and falling back to six-hour outcomes. This lets genuinely supported 10x+ cases appear without allowing a large theoretical number to rescue a weak or unsafe token. The figure is measured from the alert price and is not a guaranteed return.

## Forward outcomes and calibration

Every candidate stores the first market cap Beefy could actually observe. Every alert then stores the distinct actual alert MC, latest/current MC and peak MC observed after that alert. These are not reconstructed from an earlier discovery timestamp. Alerted candidates stay active for observations after 15, 60, 360 and 1,440 minutes, with MFE/MAE measured from the actual alert price.

SCOUT/ACTION/A+ calls are also checked for material post-alert deterioration. A one-shot PROTECT message is sent when Beefy observes deployer selling, failed sellability, at least a 40% liquidity drain, a 50% fall from alert price, a 35% fall combined with dominant sells, or a market disappearance confirmed by three successful empty lookups. PULSE messages do not trigger PROTECT because they are explicitly not trade calls.

The requested tiers remain fixed at SCOUT 60, ACTION 70 and A+ 80 so a label always means the same score range. Completed outcomes calibrate the upside model and support later evidence-led rubric reviews without silently moving those tier boundaries. This avoids retuning from one bad microcap or one lucky winner.

Early buyer addresses are resolved from transaction senders rather than router recipients. A wallet is promoted into the learned cohort only after results across distinct token contracts meet the configured observation, win-rate, and average-return floors.

## Operational limits

This architecture closes the hourly/two-hour latency gap; it does not prove profitability. The measurements make false-positive review and threshold changes evidence-led, but public APIs and RPCs still have rate and indexing delays.

The free deployment uses a five-minute scan and two ten-minute health checks (one internal and one scheduled through GitHub Actions) to remain below Render's 15-minute idle timeout. The public repository's standard GitHub-hosted Actions do not consume paid minutes. Scheduled Actions can be delayed, which is why the internal check is retained as the primary keep-awake path.

Free Render storage remains ephemeral. After a fresh-state restart the scanner restores recent candidates from launch feeds and a 1,800-block RPC lookback and permanently marks that initial backlog as bootstrap data. It cannot emit a recycled ignition alert, although a later evidence-backed reawakening remains eligible. New post-start discoveries resume normally after warm-up. This removes routine redeploy repeats without introducing an external paid datastore.

Social quality is limited to profile identity unless `SCANNER_SIGNAL_OVERLAY_URL` supplies exact-CA mention counts, credible mentions and creator activity. Ticker-only mentions never qualify as exact-CA evidence. The automatic wallet cohort requires three completed observations by default, so it intentionally starts empty. These are explicit inputs, not hidden claims of profitability.

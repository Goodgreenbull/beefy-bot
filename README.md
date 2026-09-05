# Beefy Bot v3

Beefy Bot remains the Good Green Bull Telegram community bot and now includes an alerts-only first-leg scanner for Base and Robinhood Chain, tuned for Render's free web-service tier.

It does **not** hold a wallet, sign transactions, or auto-trade. The scanner produces evidence-ranked alerts for human review.

## What changed

The previous alpha job sampled promoted/trending tokens and compared 24-hour volume every two hours. That was too late for first-leg discovery and lost all state on restart.

The quality-v2 free profile scans every five minutes and separates the job into:

1. Direct discovery from Bankr on Base and Robinhood, pools.fun, Flaunch, Baseline, verified o1/B20 and pons factory events, GeckoTerminal, DexScreener profiles, and allowlisted V2/V3 factory events including Robinhood Uniswap/Sushi. A read-only GMGN lane adds quality-filtered Base/Robinhood 5m rank, faster 1m activity rank, launchpad and smart/KOL/platform-call attention evidence. Dedicated Clanker and Zora polling is disabled because it consumed disproportionate free-tier analysis capacity; genuinely active tokens can still re-enter through market/pool discovery.
2. SQLite state for candidates, rolling market snapshots, feed cursors, scores, feed health, alert outcomes, and deduplication while the free instance remains alive.
3. DexScreener enrichment plus direct on-chain pons pricing/flow, a rate-limited HooderScan Robinhood fallback, and free GoPlus/Honeypot.is contract screening.
4. Direct on-chain Transfer-log enrichment for distinct 5m/15m buyers and sellers, net-new-wallet velocity, pool-confirmed smart-wallet entries/exits, and deployer selling.
5. Transparent inflection scoring with anti-late, local-base extension, bot/wash-flow, identity-copy, serial-deployer, concentration, tax, honeypot, and dangerous-admin filters. SpaceX, USD/stablecoin, US-oil, stock-wrapper and RWA-copy themes are hard-blocked. Large volume or a large historical smart-wallet count alone earns no conviction.
6. Two-speed Telegram output. `PULSE` (48–59) is an explicitly non-buy breadcrumb that requires live GMGN attention plus at least two independent confirmations, clean basic safety, adequate liquidity and a precise upgrade trigger; it is capped at one per cycle. SCOUT/ACTION/A+ remain trade-quality calls with an uncapped evidence-led upside model, measured evidence, first-detected versus alert market cap, sellability proxy and invalidation. Standard SCOUT/ACTION/A+ floors remain 60/70/80; a narrow 55–59 SCOUT lane is allowed only for verified launchpads when independent wallet-flow checks show exceptional early acceleration and every common safety/anti-late gate passes. Target estimates are never attached to PULSE messages.
7. Every alert is re-sampled after 15 minutes, one hour, six hours, and 24 hours. Beefy records first-detected MC, actual alert MC, current MC and peak-after-alert MC separately alongside return, observed maximum favourable excursion (MFE), and observed maximum adverse excursion (MAE). A prior SCOUT/ACTION/A+ can also produce one `PROTECT` warning when deployer selling, failed sellability, a 40% liquidity drain, a material price collapse with sell pressure, or a market disappearance confirmed across repeated successful checks appears.

The old scheduled daily alpha report and two-hour breakout alert are no longer scheduled, so there is one automated signal path. `/trending` and `/lookup` remain available as manual research tools.

The same contract is normally suppressed for 24 hours regardless of how many feeds discover it. The only same-day exception is one meaningful PULSE-to-SCOUT/ACTION/A+ quality upgrade after cooldown; routine repeated PULSE messages remain blocked. After 24 hours, a repeat requires a genuinely new reawakening setup. Scenario multiples are explanatory model outputs, not promised returns.

## Safety boundaries

- Alerts only: there is no order execution code in the scanner.
- No private key, seed phrase, or wallet credential is read by the scanner.
- Telegram and provider credentials are read only from environment variables.
- The Telegram token is no longer embedded in startup logs or used directly as the visible webhook path.
- `.env`, SQLite state, and local databases are excluded from Git.
- Smart-wallet configuration accepts public addresses only.
- GMGN access is restricted in code to four read-only discovery routes. Beefy never reads a GMGN signing/private key and never calls swap, quote, order, portfolio, or wallet routes.

## Existing Telegram credentials

The scanner reuses the credentials already stored in Render. It sends to the first available destination in this order:

1. `SIGNAL_TELEGRAM_CHAT_ID` when an explicit signal chat is configured.
2. The existing `ADMIN_CHAT_ID` private conversation.
3. The existing `TELEGRAM_GROUP_ID`.

No Telegram token or chat ID is copied into GitHub. A normal deployment only needs the existing values:

```text
BOT_TOKEN=<Telegram BotFather token>
TELEGRAM_GROUP_ID=<current group/chat id>
```

`SIGNAL_TELEGRAM_CHAT_ID` remains an optional override if signals should later move to a different conversation.

## Free Render operation

Render sleeps a free web service after 15 minutes without inbound traffic. This repository now uses two no-secret keep-awake checks:

- Beefy Bot calls its own public `/health` endpoint every ten minutes.
- `.github/workflows/keep-render-awake.yml` calls the same endpoint every ten minutes from GitHub Actions. The repository is public, so standard GitHub-hosted Actions are free. Scheduled workflows only begin after this workflow is on the default branch.

The scanner defaults to a five-minute cadence, enriches at most 50 active candidates plus 50 alerted tokens awaiting outcomes, measures on-chain wallet flow for the 12 highest-priority candidates, runs at most 12 new contract checks per cycle, checks at most 18 Robinhood markets through the free fallback, and sends at most three alerts per cycle—including no more than one PULSE. Each cycle evaluates every enriched candidate before selecting the highest-quality eligible alerts. Public RPC requests are serialized, gently spaced and retried after rate-limit responses. Candidate selection reserves capacity for both chains and direct platform lanes, while 45% of the active budget is retained for maturing rechecks; candidates already scoring 40+ are measured again before low-quality backlog. Outcome candidates are ordered by their next due measurement so an older alert is not starved by newer ones.

Render can still restart a free service at any time and its local filesystem is ephemeral. On a fresh state database, Beefy Bot rebuilds candidates from the direct feeds and a 1,800-block RPC lookback and marks that backlog as a bootstrap cohort. Those contracts cannot produce recycled new-launch alerts; they become eligible only after a later measured reawakening. Genuinely new discoveries after startup alert normally. This is a recovery strategy rather than permanent storage, but it avoids introducing a paid database.

No wallet key is needed or wanted.

## Scanner controls

Admin-only Telegram commands:

- `/scannerstatus` — last cycle, 24-hour candidates/snapshots/PULSE/call/PROTECT counts, feed health, score-funnel counts, the main rejection reasons, and the best current near-misses with their real blocking reason.
- `/signalstats` — 15m/1h/6h/24h sample counts, PULSE/SCOUT/ACTION/A+ results, MFE/MAE, fixed tiers, latest first/alert/current/peak MC audit, and wallet-cohort progress.
- `/scannow` — run one cycle immediately.
- `/alerttest` — send a clearly labelled test message to the configured signal destination.

Existing community commands and moderation remain in `server.py`.

## Optional signal inputs

### Curated smart wallets

Set `SCANNER_SMART_WALLETS` to comma-separated, manually verified public addresses. Beefy counts each wallet once and only accepts an entry when tokens moved from the candidate's actual pool to that wallet; exits must move back to that pool. Unsolicited dust transfers cannot manufacture smart-wallet conviction.

Beefy also records the transaction senders buying an alerted token from its pool. Repeat alerts on one token count once. By default a wallet must therefore succeed across at least three distinct completed token observations, with at least a 60% rate of +20% winners and at least +10% average 24-hour return. The resulting cohort is then monitored automatically. Wallet addresses are public data; no wallet credential is used.

### Additional launch factories

The repository contains verified first-party o1/B20 production factories and direct pons event parsing. Current pons curve launches are priced and measured directly from public Robinhood RPC state instead of waiting for an indexer; its Uniswap V3 launches have a direct on-chain price/liquidity fallback. Bankr's public recent-launch feed keeps Robinhood as well as Base, Pools.fun has its own direct launch lane, and Baseline uses its public first-party feed. GMGN's read-only feed prioritises Pons, Long.xyz, Bankr, o1, Baseapp, Flaunch, Virtuals and other active launch mechanisms while excluding the operator-rejected themes before they consume enrichment calls.

GMGN's published read-only demo credential is used by default and can be overridden with `GMGN_API_KEY`. Recent smart-money/KOL/platform-call events and 1m activity rank count as attention evidence only. They do not identify distinct wallets and therefore never become Beefy's proven-wallet entries; those require pool-confirmed public addresses observed independently on-chain. GMGN's total tagged-wallet counts remain context only. The shared demo service can be unavailable or rate-limited, so every direct feed remains isolated and operational without it.

For a launcher whose first-party factory is not published or cannot yet be verified, `SCANNER_FACTORY_FEEDS_JSON` accepts a platform name, factory address, event topic, and indexed-field positions. This is the safe path for a current BaseStonk/Stonks or another future factory once its official address is published; the repository deliberately does not hard-code scraped or stale addresses.

### Social/smart-wallet overlay

`SCANNER_SIGNAL_OVERLAY_URL` may point to a JSON endpoint with this contract:

```json
{
  "signals": [
    {
      "chain": "base",
      "tokenAddress": "0x...",
      "socialVelocity": 2.4,
      "socialLinks": 3,
      "exactCaMentions5m": 4,
      "exactCaMentions15m": 6,
      "credibleSocialMentions5m": 2,
      "creatorActivityScore": 0.8,
      "creatorReputation": 0.7,
      "narrativeScore": 0.9,
      "smartWalletBuys": 2,
      "smartWalletSells": 0,
      "smartWalletNetUsd": 1800,
      "deployerSells15m": 0
    }
  ]
}
```

This lets an X/listening service contribute exact-CA and creator activity without coupling Beefy Bot to one vendor. There is no reliable unauthenticated X mention feed in the free deployment, so these fields score zero when the overlay is absent; Beefy never substitutes ticker-only chatter or guesses social velocity.

## Local verification

```text
python -m compileall server.py scanner tests
python -m unittest discover -s tests -v
python -m scanner --state scanner_dry_run.sqlite3 --limit 5
python -m scripts.live_validate
```

See `ARCHITECTURE.md` for scoring, deduplication, feed behavior, and known limitations.

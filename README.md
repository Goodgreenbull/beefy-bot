# Beefy Bot v3

Beefy Bot remains the Good Green Bull Telegram community bot and now includes an alerts-only first-leg scanner for Base and Robinhood Chain, tuned for Render's free web-service tier.

It does **not** hold a wallet, sign transactions, or auto-trade. The scanner produces watch alerts for human review.

## What changed

The previous alpha job sampled promoted/trending tokens and compared 24-hour volume every two hours. That was too late for first-leg discovery and lost all state on restart.

The quality-v2 free profile scans every five minutes and separates the job into:

1. Direct discovery from Bankr, Flaunch, Clanker, Baseline, verified o1/B20 factory events, GeckoTerminal, DexScreener profiles, and allowlisted V2/V3 factory events.
2. SQLite state for candidates, rolling market snapshots, feed cursors, scores, feed health, alert outcomes, and deduplication while the free instance remains alive.
3. DexScreener enrichment plus free GoPlus and Honeypot.is contract screening.
4. Transparent ignition/reawakening scoring with anti-late, identity-copy, serial-deployer, concentration, tax, honeypot, and dangerous-admin filters.
5. Concise Telegram WATCH/BUY verdicts with an uncapped, evidence-led upside model from the alert price, a setup-specific one-line explanation, score, stage, age, liquidity, market cap, 5-minute flow, contract-check status, and invalidation. The target is calculated only after the independent quality and safety gate, so theoretical upside cannot make a poor token eligible.
6. Every alert is re-sampled after 15 minutes, one hour, six hours, and 24 hours. Beefy records return, observed maximum favourable excursion (MFE), and observed maximum adverse excursion (MAE). Three successful empty market lookups classify a disappeared pool as a terminal loss rather than silently dropping it.

The old scheduled daily alpha report and two-hour breakout alert are no longer scheduled, so there is one automated signal path. `/trending` and `/lookup` remain available as manual research tools.

The same contract is suppressed for 24 hours regardless of score changes or how many feeds discover it. After that, it can alert again only as a genuinely new reawakening setup. Scenario multiples are explanatory model outputs, not promised returns.

## Safety boundaries

- Alerts only: there is no order execution code in the scanner.
- No private key, seed phrase, or wallet credential is read by the scanner.
- Telegram and provider credentials are read only from environment variables.
- The Telegram token is no longer embedded in startup logs or used directly as the visible webhook path.
- `.env`, SQLite state, and local databases are excluded from Git.
- Smart-wallet configuration accepts public addresses only.

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

The scanner defaults to a five-minute cadence, enriches at most 50 active candidates plus 50 alerted tokens awaiting outcomes, runs at most 12 new contract checks per cycle, and sends at most three alerts per cycle. Outcome candidates are ordered by their next due measurement so an older alert is not starved by newer ones. Seventy percent of each normal cycle is reserved for the newest candidates and thirty percent rotates through older candidates so reawakenings are not starved by new launches.

Render can still restart a free service at any time and its local filesystem is ephemeral. On a fresh state database, Beefy Bot rebuilds candidates from the direct feeds and a 1,800-block RPC lookback and marks that backlog as a bootstrap cohort. Those contracts cannot produce recycled new-launch alerts; they become eligible only after a later measured reawakening. Genuinely new discoveries after startup alert normally. This is a recovery strategy rather than permanent storage, but it avoids introducing a paid database.

No wallet key is needed or wanted.

## Scanner controls

Admin-only Telegram commands:

- `/scannerstatus` — last cycle, 24-hour candidates/snapshots/alerts, and feed health.
- `/signalstats` — 15m/1h/6h/24h sample counts, WATCH/BUY 24-hour win rate, median return, MFE/MAE, live thresholds, and wallet-cohort progress.
- `/scannow` — run one cycle immediately.
- `/alerttest` — send a clearly labelled test message to the configured signal destination.

Existing community commands and moderation remain in `server.py`.

## Optional signal inputs

### Curated smart wallets

Set `SCANNER_SMART_WALLETS` to comma-separated, manually verified public addresses. The scanner counts ERC-20 transfers into and out of those wallets for active candidates on Base and Robinhood Chain.

Beefy also records the transaction senders buying an alerted token from its pool. Repeat alerts on one token count once. By default a wallet must therefore succeed across at least three distinct completed token observations, with at least a 60% rate of +20% winners and at least +10% average 24-hour return. The resulting cohort is then monitored automatically. Wallet addresses are public data; no wallet credential is used.

### Additional launch factories

The repository contains verified first-party o1/B20 production factories. Pools.fun launches also enter through its documented Sushi V3 pool layer. Baseline and Clanker use their public first-party feeds.

For a launcher whose first-party factory is not published or cannot yet be verified, `SCANNER_FACTORY_FEEDS_JSON` accepts a platform name, factory address, event topic, and indexed-field positions. This is the safe path for a current BaseStonk/Stonks or future Pools.fun factory once its official address is published; the repository deliberately does not hard-code scraped or stale addresses.

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
      "smartWalletBuys": 2,
      "smartWalletSells": 0,
      "smartWalletNetUsd": 1800
    }
  ]
}
```

This lets an X/listening service or wallet analytics provider contribute signals without coupling Beefy Bot to one vendor.

## Local verification

```text
python -m compileall server.py scanner tests
python -m unittest discover -s tests -v
python -m scanner --state scanner_dry_run.sqlite3 --limit 5
python -m scripts.live_validate
```

See `ARCHITECTURE.md` for scoring, deduplication, feed behavior, and known limitations.

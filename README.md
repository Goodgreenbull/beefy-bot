# Beefy Bot v3

Beefy Bot remains the Good Green Bull Telegram community bot and now includes an alerts-only first-leg scanner for Base and Robinhood Chain, tuned for Render's free web-service tier.

It does **not** hold a wallet, sign transactions, or auto-trade. The scanner produces watch alerts for human review.

## What changed

The previous alpha job sampled promoted/trending tokens and compared 24-hour volume every two hours. That was too late for first-leg discovery and lost all state on restart.

The free profile scans every five minutes and separates the job into:

1. Direct discovery from Bankr, Flaunch, GeckoTerminal new pools, and standard V2/V3 pool-creation events on Base and Robinhood Chain.
2. SQLite state for candidates, rolling market snapshots, feed cursors, scores, feed health, and sent-alert deduplication while the free instance remains alive.
3. DexScreener market enrichment plus optional social and smart-wallet overlays.
4. Transparent ignition/reawakening scoring with an anti-late gate.
5. Concise Telegram alerts with score, stage, age, liquidity, market cap, 5-minute flow, drivers, and invalidation.

The old scheduled daily alpha report and two-hour breakout alert are no longer scheduled, so there is one automated signal path. `/trending` and `/lookup` remain available as manual research tools.

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

The scanner defaults to a five-minute cadence, enriches at most 50 active candidates, and sends at most three alerts per cycle. Seventy percent of each cycle is reserved for the newest candidates and thirty percent rotates through older candidates so reawakenings are not starved by new launches.

Render can still restart a free service at any time and its local filesystem is ephemeral. On a fresh state database, Beefy Bot suppresses the first alert cycle, rebuilds candidates from the direct feeds and a 1,800-block RPC lookback, then resumes alerts. This is a recovery strategy rather than permanent storage, but it avoids introducing a paid database.

No wallet key is needed or wanted.

## Scanner controls

Admin-only Telegram commands:

- `/scannerstatus` — last cycle, 24-hour candidates/snapshots/alerts, and feed health.
- `/scannow` — run one cycle immediately.
- `/alerttest` — send a clearly labelled test message to the configured signal destination.

Existing community commands and moderation remain in `server.py`.

## Optional signal inputs

### Curated smart wallets

Set `SCANNER_SMART_WALLETS` to comma-separated public addresses. The scanner counts ERC-20 transfers into and out of those wallets for active candidates on Base and Robinhood Chain.

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
```

See `ARCHITECTURE.md` for scoring, deduplication, feed behavior, and known limitations.

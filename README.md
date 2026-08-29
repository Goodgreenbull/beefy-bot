# Beefy Bot v3

Beefy Bot remains the Good Green Bull Telegram community bot and now includes an opt-in, alerts-only first-leg scanner for Base and Robinhood Chain.

It does **not** hold a wallet, sign transactions, or auto-trade. The scanner produces watch alerts for human review.

## What changed

The previous alpha job sampled promoted/trending tokens and compared 24-hour volume every two hours. That was too late for first-leg discovery and lost all state on restart.

The new scanner runs every 30–60 seconds and separates the job into:

1. Direct discovery from Bankr, Flaunch, GeckoTerminal new pools, and standard V2/V3 pool-creation events on Base and Robinhood Chain.
2. Durable SQLite state for candidates, rolling market snapshots, feed cursors, scores, feed health, and sent-alert deduplication.
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

## Enable on Render

Copy the names from `.env.example` into Render's Environment page. At minimum set:

```text
BOT_TOKEN=<Telegram BotFather token>
TELEGRAM_GROUP_ID=<community group id>
SIGNAL_TELEGRAM_CHAT_ID=<private signal chat or group id>
SCANNER_ENABLED=true
SCANNER_INTERVAL_SECONDS=45
```

For reliable 24/7 use, set `BASE_RPC_URL` and `ROBINHOOD_RPC_URL` to production provider endpoints. The public endpoints are useful for development but are rate limited. Use a persistent Render disk and set `SCANNER_STATE_DB=/var/data/scanner_state.sqlite3`; otherwise state survives process restarts but not a fresh ephemeral deployment.

Render's free web service can sleep between requests, so it cannot guarantee a 30–60 second scanner. Keep this feature branch in dry-run/disabled mode until the service is moved to an always-on plan (or another always-on host) and persistent storage is attached.

No wallet key is needed or wanted.

## Scanner controls

Admin-only Telegram commands:

- `/scannerstatus` — last cycle, 24-hour candidates/snapshots/alerts, and feed health.
- `/scannow` — run one cycle immediately.

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

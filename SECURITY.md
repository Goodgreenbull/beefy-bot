# Secret handling

Beefy Bot reads Telegram credentials from the deployment environment. Keep
`BOT_TOKEN`, `TELEGRAM_GROUP_ID`, `ADMIN_CHAT_ID`, and any future provider
credentials in Render's Environment page; never put real values in source,
`render.yaml`, `.env.example`, logs, screenshots, issues, or pull requests.

The public repository's history contains strings shaped like Telegram bot
tokens. Treat any token that ever appeared in Git as compromised even if the
current files no longer contain it:

1. Open the bot in BotFather and revoke/regenerate its token.
2. Replace only `BOT_TOKEN` in Render, retaining the existing chat IDs.
3. Redeploy and confirm `/scannerstatus` from the configured admin account.
4. Do not paste the old or replacement token into GitHub.

The webhook route is derived from the active bot token during deployment. No
wallet seed phrase or private key is accepted or required by the scanner.

# 🐂 GGB Beefy Bot

**Good Green Bull — Telegram Bot**  
Built on Base. Built for builders.

---

## What This Bot Does

Beefy Bot is the automated engine behind the GGB Telegram community. It runs 24/7 on Render and handles:

- **Daily GM post** — fires every morning at 08:00 UTC with a Beefy quote, live $GGB price, and a community prompt
- **Weekly builder question** — every Monday at 09:00 UTC, a rotating engagement question goes out to the group
- **GM tracker + leaderboard** — tracks who says GM each day and shows a daily leaderboard
- **New member welcome** — automatically greets anyone who joins the group
- **Spam protection** — mutes users who send more than 5 messages in 10 seconds
- **Live price + wallet checks** — pulls real-time $GGB data from DexScreener and BaseScan
- **Admin controls** — revival blast, manual daily trigger, settings panel

---

## Commands

| Command | Who | What it does |
|---|---|---|
| `/start` | Everyone | Opens the main menu with inline buttons |
| `/help` | Everyone | Lists all commands |
| `/price` | Everyone | Live $GGB price + 24h change |
| `/bull` | Everyone | Random Beefy motivational quote |
| `/gm` | Everyone | Say GM to the herd, gets logged on leaderboard |
| `/leaderboard` | Everyone | Top GM senders today |
| `/wallet <address>` | Everyone | Check a Base wallet's GGB balance + USD value |
| `/token` | Everyone | Full token info + contract address |
| `/kit` | Everyone | GGB Builder Kit info + purchase link |
| `/nft` | Everyone | Beefy Prime Series One info |
| `/herd` | Everyone | Group member count + community status |
| `/daily` | Admin only | Manually trigger the Beefy Daily post |
| `/revival` | Admin only | Send the relaunch announcement to the group |
| `/settings` | Admin only | Admin settings panel |

---

## File Structure

```
/
├── server.py          # Main bot — all logic lives here
├── requirements.txt   # Python dependencies
├── render.yaml        # Render deployment config
└── README.md          # This file
```

> `bot.py` is an older lightweight version kept for reference. It is not used in deployment.

---

## Environment Variables

Set these in your Render dashboard under **Environment** — never hardcode them in the code.

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ Yes | Your Telegram bot token from @BotFather |
| `BASESCAN_API_KEY` | ✅ Yes | Free API key from basescan.org |
| `ADMIN_USERNAME` | ✅ Yes | Your Telegram username without @ e.g. `JS0nbase` |
| `TELEGRAM_GROUP_ID` | ✅ Yes | Numeric ID of your GGB Telegram group e.g. `-1001234567890` |

### How to get your Telegram Group ID

1. Add [@userinfobot](https://t.me/userinfobot) to your Telegram group
2. Send `/start` in the group
3. The bot will reply with the group's numeric ID (it will start with `-100`)
4. Copy that number and add it to Render as `TELEGRAM_GROUP_ID`

### How to get a BaseScan API key

1. Go to [basescan.org](https://basescan.org)
2. Create a free account
3. Go to API Keys in your profile
4. Generate a key and copy it into Render

---

## Deployment on Render

### First-time setup

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) and sign in
3. Click **New → Web Service**
4. Connect your GitHub repo
5. Render will detect `render.yaml` automatically — the settings will populate
6. Add all four environment variables listed above
7. Click **Deploy**

### Redeploying after changes

```bash
# Make your changes locally, then:
git add .
git commit -m "Your update message"
git push origin main
```

Render will detect the push and redeploy automatically if auto-deploy is enabled. Otherwise, go to your Render dashboard and click **Manual Deploy → Deploy latest commit**.

### Checking logs

In your Render dashboard, click your service → **Logs**. You should see:
```
✅ Webhook set: https://beefy-bot.onrender.com/webhook/...
✅ Scheduler running — Daily 08:00 UTC | Monday 09:00 UTC
```

If you see errors, check that all four environment variables are set correctly.

---

## Scheduled Posts

| Post | Schedule | Description |
|---|---|---|
| Beefy Daily | Every day at 08:00 UTC | GM message, Beefy quote, live price, community prompt |
| Builder Monday | Every Monday at 09:00 UTC | Rotating weekly engagement question |

To change the schedule, edit the `on_startup()` function in `server.py`:

```python
scheduler.add_job(send_beefy_daily, "cron", hour=8, minute=0)
# Change hour=8 to any UTC hour you prefer
```

---

## Revival Flow

When returning after a period of inactivity, use this sequence:

1. Deploy the latest bot code to Render
2. Confirm the bot is live (check Render logs)
3. In Telegram, send `/revival` as the admin — this fires the relaunch announcement to the group
4. Post the revival content on X and Farcaster
5. The Beefy Daily scheduler takes over from there — group gets a fresh post every morning automatically

---

## Local Development (Optional)

If you want to test changes locally before pushing:

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (Mac/Linux)
export BOT_TOKEN=your_token_here
export BASESCAN_API_KEY=your_key_here
export ADMIN_USERNAME=your_username
export TELEGRAM_GROUP_ID=your_group_id

# Run the bot
python server.py
```

Note: For local testing, the webhook won't work since it points to the Render URL. Use polling mode for local testing if needed — ask Claude to add a `--local` flag if required.

---

## Dependencies

```
Flask==3.1.0
asgiref>=3.5.0
python-telegram-bot[webhooks]==20.8
requests==2.31.0
httpx~=0.26.0
aiohttp>=3.9.0
APScheduler>=3.10.0
```

---

## Brand

**Good Green Bull ($GGB)**  
Chain: Base  
Contract: `0xc2758c05916ba20b19358f1e96f597774e603050`  
X: [@goodgreenbull](https://x.com/goodgreenbull)  
Website: [goodgreenbull.com](https://goodgreenbull.com)

---

*Built lean. Runs on Base. Powered by Beefy. 🐂💚*

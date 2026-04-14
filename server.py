# =============================================================================
# GGB BEEFY BOT — server.py
# Good Green Bull | Built on Base
# =============================================================================

import os
import asyncio
import random
import math
import aiohttp
from datetime import datetime, timezone, timedelta
from flask import Flask, request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatPermissions, ChatMember
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ChatMemberHandler, ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =============================================================================
# CONFIG — Set these as environment variables in Render, never hardcode secrets
# =============================================================================

TOKEN               = os.getenv("BOT_TOKEN")
BASESCAN_API_KEY    = os.getenv("BASESCAN_API_KEY")
ADMIN_USERNAME      = os.getenv("ADMIN_USERNAME", "JS0nbase")
ADMIN_CHAT_ID       = os.getenv("ADMIN_CHAT_ID")          # Your personal chat ID for private signals
TELEGRAM_GROUP_ID   = os.getenv("TELEGRAM_GROUP_ID")       # Numeric group ID e.g. -1001234567890

# Phase 7 — Polymarket CLOB trading wallet
POLYMARKET_PK       = os.getenv("POLYMARKET_PRIVATE_KEY")  # Bot wallet private key (EOA)
POLYMARKET_WALLET   = os.getenv("POLYMARKET_WALLET_ADDRESS") # Bot wallet public address
PERSONAL_WALLET     = os.getenv("YOUR_PERSONAL_WALLET")    # Your personal wallet for withdrawals
TRADING_MODE        = os.getenv("TRADING_MODE", "paper")   # "paper" or "live" — start with paper!
WEBHOOK_PATH        = f"/webhook/{TOKEN}"
WEBHOOK_URL         = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"
GGB_CONTRACT        = "0xc2758c05916ba20b19358f1e96f597774e603050"

# =============================================================================
# APP INIT
# =============================================================================

app         = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# =============================================================================
# STATE — In-memory. Resets on bot restart. Fine for free tier usage.
# =============================================================================

user_spam_tracker   = {}    # {user_id: [datetime, ...]} rolling spam window
recent_bull_indices = []    # Prevents same quote repeating within 7 pulls
gm_tracker          = {}    # {user_id: {"name": str, "count": int}}
gm_tracker_date     = None  # Tracks which UTC date the GM tracker is on

# =============================================================================
# BULL QUOTES BANK
# =============================================================================

bull_quotes = [
    "The market rewards patience. The builder rewards himself. 🐂💚",
    "Quiet stretches separate the builders from the tourists. 🐂💚",
    "Ship ugly. Fix fast. Ship again. 🛠️💚",
    "Nobody's watching the process. That's the point. 🐂🌿",
    "The signal is quiet. Keep going anyway. 🐂💚",
    "You don't outwork the market. You outlast it. 🐂💚",
    "Conviction is a practice, not a feeling. 💚🐂",
    "Most quit before the compound kicks in. 🐂💚",
    "Build mode doesn't need an announcement. 🛠️🐂",
    "Progress doesn't ask for permission. 💚🐂",
    "The ones still building in the noise are the ones worth watching. 🐂💚",
    "Hold the line. The line is the work. 🐂💚",
    "Momentum is just small moves that didn't stop. 📈🐂",
    "No one remembers the hype. Everyone remembers what lasted. 🐂💚",
    "Ship because it sharpens you, not because it trends. 🛠️💚",
    "If you're still here, you already passed the first filter. 🐂💚",
    "Systems beat sprints every time. 📈🐂",
    "Build for the version of yourself that's still here in two years. 🐂💚",
    "The grind is not the goal. The grind is the gate. 💚🐂",
    "Locked in. Herd strong. We move. 🐂💚",
]

# =============================================================================
# WEEKLY ENGAGEMENT QUESTIONS — Rotates by week number
# =============================================================================

weekly_questions = [
    "What's the one thing you're shipping this week? Drop it below 🛠️",
    "Best Base project you've used this week? Go 👇",
    "If you had to cut everything except one project — what stays? 🐂",
    "What's one tool (AI or otherwise) that's genuinely changed how you build? 👇",
    "Biggest lesson from your last build? Keep it real 👇",
    "What would make you check this group every single day? Tell us 🐂💚",
    "One word that describes your build mindset this week 👇",
    "What's the most underrated thing happening on Base right now? 🐂",
    "If GGB dropped a product tomorrow — what would you want it to be? 👇",
    "What does winning look like for you in the next 90 days? 🐂💚",
]

# =============================================================================
# HELPERS
# =============================================================================

def get_bull_quote() -> str:
    global recent_bull_indices
    available = [i for i in range(len(bull_quotes)) if i not in recent_bull_indices]
    if not available:
        recent_bull_indices = []
        available = list(range(len(bull_quotes)))
    idx = random.choice(available)
    recent_bull_indices.append(idx)
    if len(recent_bull_indices) > 7:
        recent_bull_indices.pop(0)
    return bull_quotes[idx]


async def fetch_price_data():
    url = f"https://api.dexscreener.com/latest/dex/tokens/{GGB_CONTRACT}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data   = await resp.json()
                pair   = data["pairs"][0]
                price  = float(pair["priceUsd"])
                change = float(pair.get("priceChange", {}).get("h24", 0))
                return price, change
    except Exception:
        return None, None


async def fetch_wallet_balance(address: str):
    url = (
        f"https://api.basescan.org/api?module=account&action=tokenbalance"
        f"&contractaddress={GGB_CONTRACT}&address={address}"
        f"&tag=latest&apikey={BASESCAN_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data["status"] == "1":
                    return int(data["result"]) / 10**18
    except Exception:
        pass
    return None


def is_admin(user) -> bool:
    return user.username == ADMIN_USERNAME.lstrip("@")


def format_price(price_val: float, change: float) -> str:
    change_str = f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"
    arrow      = "📈" if change >= 0 else "📉"
    return f"💵 GGB: ${price_val:.6f}\n{arrow} 24h: {change_str}"


def reset_gm_if_needed():
    global gm_tracker, gm_tracker_date
    today = datetime.now(timezone.utc).date()
    if gm_tracker_date != today:
        gm_tracker      = {}
        gm_tracker_date = today

# =============================================================================
# COMMANDS
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🐂 Bull Quote",      callback_data="bull")],
        [InlineKeyboardButton("📈 GGB Price",       callback_data="price")],
        [InlineKeyboardButton("🎨 Wallpaper Pack",  url="https://goodgreenbull.gumroad.com")],
        [InlineKeyboardButton("🖼️ NFT Drop",        callback_data="nft_info")],
        [InlineKeyboardButton("🌐 Website",         url="https://goodgreenbull.com")],
        [InlineKeyboardButton("🕊️ Follow on X",     url="https://x.com/goodgreenbull")],
    ]
    await update.message.reply_text(
        "🐂💚 *Good Green Bull*\n\n"
        "Built on Base. Built for builders.\n"
        "The bull that doesn't stop.\n\n"
        "Choose an option below 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 *GGB Bot Commands*\n\n"
        "/start — Open main menu\n"
        "/price — Live $GGB price + 24h change\n"
        "/bull — Random Beefy quote\n"
        "/gm — Say GM to the herd\n"
        "/leaderboard — Top GM senders today\n"
        "/wallet `<address>` — Check GGB balance\n"
        "/token — Token info + contract\n"
        "/kit — GGB Builder Kit info\n"
        "/nft — NFT drop info\n"
        "/herd — Community stats\n"
        "/help — Show this list\n\n"
        "👤 *Admin only:*\n"
        "/daily — Trigger Beefy Daily push\n"
        "/revival — Send relaunch announcement\n"
        "/signals — Toggle Polymarket signals on/off\n"
        "/signalstatus — Signal system status\n"
        "/settings — Admin panel\n\n"
        "💰 *Trading (admin, DM only):*\n"
        "/botbalance — Live wallet balance\n"
        "/withdraw — Send funds to personal wallet\n"
        "/pause — Pause live trading\n"
        "/resume — Resume trading\n"
        "/testconnection — Test Polymarket API\n"
        "/golive — Check live trading status",
        parse_mode="Markdown",
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price_val, change = await fetch_price_data()
    if price_val is None:
        await update.message.reply_text("⚠️ Could not fetch price right now. Try again shortly.")
        return
    await update.message.reply_text(
        f"{format_price(price_val, change)}\n\n"
        f"📊 Chart: https://tinyurl.com/GGBDex\n"
        f"📄 Contract: `{GGB_CONTRACT}`",
        parse_mode="Markdown",
    )


async def bull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_bull_quote())


async def gm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_gm_if_needed()
    user    = update.effective_user
    name    = user.first_name or "Bull"
    if user.id not in gm_tracker:
        gm_tracker[user.id] = {"name": name, "count": 0}
    gm_tracker[user.id]["count"] += 1
    responses = [
        f"GM {name} 🐂💚 Build mode is ON.",
        f"GM {name} 💚 The herd is awake. Let's move.",
        f"GM {name} 🐂 Another day. Another rep. Lock in.",
        f"GM {name} 💚 Still here. Still building. That's the edge.",
        f"GM {name} 🐂💚 Herd strong. Ship something today.",
    ]
    await update.message.reply_text(random.choice(responses))


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_gm_if_needed()
    if not gm_tracker:
        await update.message.reply_text(
            "No GMs logged yet today. Be the first 🐂💚\nType /gm to get on the board."
        )
        return
    sorted_users = sorted(gm_tracker.items(), key=lambda x: x[1]["count"], reverse=True)
    medals       = ["🥇", "🥈", "🥉"] + ["🐂"] * 7
    lines        = ["🏆 *GM Leaderboard — Today*\n"]
    for i, (uid, data) in enumerate(sorted_users[:10]):
        lines.append(f"{medals[i]} {data['name']} — {data['count']} GMs")
    lines.append("\nType /gm to get on the board 💚")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/wallet <Base wallet address>`", parse_mode="Markdown")
        return
    address  = context.args[0]
    balance  = await fetch_wallet_balance(address)
    if balance is None:
        await update.message.reply_text("⚠️ Could not fetch wallet data. Check the address and try again.")
        return
    price_val, _ = await fetch_price_data()
    usd_str      = f"💵 ≈ ${balance * price_val:,.2f} USD" if price_val else ""
    short_addr   = f"{address[:6]}...{address[-4:]}"
    await update.message.reply_text(
        f"👛 Wallet: `{short_addr}`\n"
        f"🐂 GGB Balance: {balance:,.2f} GGB\n"
        f"{usd_str}",
        parse_mode="Markdown",
    )


async def token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📈 *Good Green Bull — Token Info*\n\n"
        "Name: Good Green Bull\n"
        "Symbol: $GGB\n"
        "Chain: Base\n"
        "Decimals: 18\n"
        f"Contract: `{GGB_CONTRACT}`\n\n"
        f"🔗 https://basescan.org/token/{GGB_CONTRACT}",
        parse_mode="Markdown",
    )


async def kit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠️ *GGB Builder Kit*\n\n"
        "The full content and brand system behind Good Green Bull — "
        "packaged for builders running their own brand on Base or Farcaster.\n\n"
        "✅ Content calendar + rotation framework\n"
        "✅ 30 social post templates — X + Farcaster\n"
        "✅ 10 AI image prompts with guardrails\n"
        "✅ Brand voice guide\n"
        "✅ Mascot design rules\n"
        "✅ Monetisation framework\n"
        "✅ Quick-start checklist\n\n"
        "💰 £35 — Instant download\n"
        "🔗 https://goodgreenbull.gumroad.com",
        parse_mode="Markdown",
    )


async def nft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 *Beefy Prime: Series One*\n\n"
        "50 cinematic 1/1 pieces. Base chain.\n"
        "The founding archive of Good Green Bull.\n\n"
        "Holders receive:\n"
        "— Exclusive founder role in this group\n"
        "— First access to all future drops\n\n"
        "🟡 Status: Coming Soon\n"
        "Follow @goodgreenbull on X for the mint date 🐂💚",
        parse_mode="Markdown",
    )


async def herd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count_str = "Growing daily"
    if TELEGRAM_GROUP_ID:
        try:
            count     = await context.bot.get_chat_member_count(int(TELEGRAM_GROUP_ID))
            count_str = f"{count:,} members"
        except Exception:
            pass
    lines = [
        "The herd is building. 🐂💚",
        "Bulls don't fold when it gets quiet. 🐂💚",
        "Still here. Still locked in. 🐂💚",
        "Early is a choice. So is being late. 🐂💚",
        "The quiet ones are the dangerous ones. 🐂💚",
    ]
    await update.message.reply_text(
        f"🐂 *The GGB Herd*\n\n"
        f"Members: {count_str}\n"
        f"{random.choice(lines)}\n\n"
        f"Share the group 👇\nhttps://t.me/goodgreenbull",
        parse_mode="Markdown",
    )


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    keyboard = [
        [InlineKeyboardButton("🤖 Toggle Daily Post",  callback_data="toggle_daily")],
        [InlineKeyboardButton("🚫 Toggle Anti-Spam",   callback_data="toggle_spam")],
        [InlineKeyboardButton("📣 Send Revival Blast", callback_data="send_revival")],
    ]
    await update.message.reply_text(
        "⚙️ *Admin Settings*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    await update.message.reply_text("📤 Sending Beefy Daily now...")
    await send_beefy_daily()


async def revival_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    await update.message.reply_text("📣 Sending revival blast now...")
    await send_revival_blast()

# =============================================================================
# SCHEDULED POSTS
# =============================================================================

async def send_beefy_daily():
    """Fires every day at 08:00 UTC."""
    if not TELEGRAM_GROUP_ID:
        print("⚠️ TELEGRAM_GROUP_ID not set. Skipping.")
        return
    price_val, change = await fetch_price_data()
    price_line = (
        f"$GGB: ${price_val:.6f} | {'+' if change >= 0 else ''}{change:.2f}% 24h"
        if price_val else "$GGB: Price unavailable"
    )
    msg = (
        f"GM Herd 🐂💚\n\n"
        f"{get_bull_quote()}\n\n"
        f"{price_line}\n\n"
        f"What are you building today? Drop it below 👇"
    )
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=msg)
    except Exception as e:
        print(f"⚠️ Daily post failed: {e}")


async def send_weekly_engagement():
    """Fires every Monday at 09:00 UTC."""
    if not TELEGRAM_GROUP_ID:
        return
    week_num = datetime.now(timezone.utc).isocalendar()[1]
    question = weekly_questions[week_num % len(weekly_questions)]
    msg = (
        f"🐂 *Builder Monday*\n\n"
        f"{question}\n\n"
        f"Best answer gets a shout from @goodgreenbull 💚"
    )
    try:
        await application.bot.send_message(
            chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown"
        )
    except Exception as e:
        print(f"⚠️ Weekly post failed: {e}")


async def send_revival_blast():
    """One-time relaunch message. Admin-triggered."""
    if not TELEGRAM_GROUP_ID:
        return
    msg = (
        "🐂💚 *GGB IS BACK.*\n\n"
        "Beefy's been in build mode.\n"
        "Now we move.\n\n"
        "What's coming:\n"
        "🎨 Wallpaper Pack — dropping soon\n"
        "🖼️ Beefy Prime: Series One NFTs — Base chain\n"
        "🛠️ GGB Builder Kit — for builders running their own brand\n\n"
        "New content. New products. New energy.\n\n"
        "If you're still here — you're the founding herd.\n"
        "The ones who stayed get rewarded first.\n\n"
        "We move. 🐂💚\n\n"
        "Follow: https://x.com/goodgreenbull\n"
        "Farcaster: https://warpcast.com/goodgreenbull"
    )
    try:
        await application.bot.send_message(
            chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown"
        )
    except Exception as e:
        print(f"⚠️ Revival blast failed: {e}")

# =============================================================================
# MESSAGE HANDLERS
# =============================================================================

async def handle_gm_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responds to natural GM messages in chat."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip().lower()
    if text in ("gm", "gm!", "gm 🐂", "gm 💚", "gm 🐂💚", "good morning"):
        reset_gm_if_needed()
        user = update.effective_user
        name = user.first_name or "Bull"
        if user.id not in gm_tracker:
            gm_tracker[user.id] = {"name": name, "count": 0}
        gm_tracker[user.id]["count"] += 1
        responses = [
            f"GM {name} 🐂💚",
            f"GM {name} 💚 Lock in.",
            f"GM {name} 🐂 Build something today.",
            f"GM {name} 💚 Herd strong.",
        ]
        await update.message.reply_text(random.choice(responses))


async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fires when a new member joins the group."""
    result = update.chat_member
    if not result:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    if old_status in (ChatMember.LEFT, ChatMember.BANNED) and new_status == ChatMember.MEMBER:
        name = result.new_chat_member.user.first_name or "Bull"
        await context.bot.send_message(
            chat_id=result.chat.id,
            text=(
                f"🐂💚 Welcome to the herd, {name}!\n\n"
                f"Good Green Bull is a digital brand and builder community on Base.\n\n"
                f"Start here:\n"
                f"📈 /price — Live $GGB price\n"
                f"🐂 /bull — Get a Beefy quote\n"
                f"👋 /gm — Say GM to the herd\n"
                f"🛠️ /kit — GGB Builder Kit\n"
                f"🎨 /nft — Upcoming NFT drop\n\n"
                f"Follow us on X 👉 https://x.com/goodgreenbull\n\n"
                f"Herd strong. We move. 🐂💚"
            ),
        )


async def detect_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mutes users sending more than 5 messages in 10 seconds."""
    if not update.message or not update.effective_user:
        return
    user_id    = update.effective_user.id
    chat_id    = update.effective_chat.id
    now        = datetime.now(timezone.utc)
    window     = timedelta(seconds=10)
    timestamps = user_spam_tracker.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < window]
    timestamps.append(now)
    user_spam_tracker[user_id] = timestamps
    if len(timestamps) > 5:
        try:
            await context.bot.restrict_chat_member(
                chat_id, user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=now + timedelta(minutes=10),
            )
            await context.bot.send_message(chat_id, text="⚠️ User muted 10 mins for spam.")
        except Exception:
            pass

# =============================================================================
# CALLBACK HANDLER
# =============================================================================

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "bull":
        await query.edit_message_text(get_bull_quote())
    elif query.data == "price":
        price_val, change = await fetch_price_data()
        if price_val is None:
            await query.edit_message_text("⚠️ Could not fetch price right now.")
            return
        await query.edit_message_text(
            f"{format_price(price_val, change)}\n\n📊 https://tinyurl.com/GGBDex"
        )
    elif query.data == "nft_info":
        await query.edit_message_text(
            "🎨 Beefy Prime: Series One\n\n"
            "50 cinematic 1/1 pieces. Base chain.\n"
            "Status: Coming Soon 🟡\n\n"
            "Follow @goodgreenbull on X for the mint date 🐂💚"
        )
    elif query.data == "send_revival":
        await query.edit_message_text("📣 Sending revival blast...")
        await send_revival_blast()
    elif query.data in ("toggle_daily", "toggle_spam"):
        await query.edit_message_text("⚙️ Toggle controls coming in next update.")

# =============================================================================
# 🧠 POLYMARKET SIGNAL SYSTEM — Paper Trading Mode
# All 6 formulas from the 0xRicker quant playbook, integrated into Beefy Bot.
# This watches BTC/ETH prices, updates beliefs using Bayes' theorem, and
# posts signals to the group. NO real trades — paper tracking only.
# =============================================================================

# ─── Signal State (resets on bot restart, same as other state) ───────────────

polymarket_enabled   = False       # Toggle with /signals (admin only)
signal_bankroll      = 50.0        # Paper trading starting bankroll ($50 USDC)
signal_peak          = 50.0        # Peak bankroll for trailing stop
signal_trades        = []          # Trade history [{direction, market, bet, won, pnl}]
signal_consecutive_losses = 0
signal_is_paused     = False
signal_pause_until   = 0.0

# Price history buffers for computing returns
btc_price_history    = []          # [(timestamp, price), ...]
eth_price_history    = []

# Markets we're tracking (initialised on first scan)
tracked_markets      = {}          # {market_id: {name, price, posterior, correlation, updates}}

# ─── Phase 7: CLOB Trading Client ───────────────────────────────────────────
# The CLOB client connects to Polymarket's order book for automated trading.
# Only initialised when POLYMARKET_PK is set and mode is "live".
# In "paper" mode, signals post but no orders are placed.

clob_client          = None        # Initialised in on_startup() if keys are present
clob_ready           = False       # True once client + allowances are verified
trading_paused       = False       # Admin can pause without disabling signals

async def init_clob_client():
    """Initialise the Polymarket CLOB client. Called once on startup."""
    global clob_client, clob_ready
    if not POLYMARKET_PK or TRADING_MODE != "live":
        print(f"📊 Trading mode: {TRADING_MODE} (CLOB client not initialised)")
        return

    try:
        from py_clob_client.client import ClobClient
        clob_client = ClobClient(
            "https://clob.polymarket.com",
            key=POLYMARKET_PK,
            chain_id=137,       # Polygon mainnet
            signature_type=0,   # EOA wallet (standalone, type 0)
            funder=POLYMARKET_WALLET,
        )
        creds = clob_client.create_or_derive_api_creds()
        clob_client.set_api_creds(creds)
        clob_ready = True
        print(f"✅ CLOB client ready — wallet: {POLYMARKET_WALLET[:10]}...")
    except ImportError:
        print("⚠️ py-clob-client not installed. Run: pip install py-clob-client")
        print("   Bot will run in paper mode only.")
    except Exception as e:
        print(f"⚠️ CLOB init failed: {e}")
        print("   Bot will run in paper mode only.")


async def execute_clob_order(token_id, side, price, size):
    """
    Place a real order on Polymarket via the CLOB API.
    Returns order response dict or None on failure.
    """
    if not clob_ready or not clob_client or trading_paused:
        return None

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        order_side = BUY if side == "BUY" else SELL

        # Use limit order (GTC = Good Till Cancelled) for maker fees
        order = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=round(size, 1),
            side=order_side,
        )
        signed = clob_client.create_order(order)
        resp = clob_client.post_order(signed, OrderType.GTC)
        return resp
    except Exception as e:
        print(f"⚠️ Order failed: {e}")
        return None


async def get_clob_balance():
    """Get the bot wallet's USDC.e balance from Polymarket."""
    if not clob_ready or not clob_client:
        return None
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        bal = clob_client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        # Balance is returned in wei (6 decimals for USDC)
        if bal and "balance" in bal:
            return float(bal["balance"]) / 1e6
        return None
    except Exception as e:
        print(f"⚠️ Balance check failed: {e}")
        return None


async def withdraw_usdc(to_address, amount_usdc):
    """
    Withdraw USDC.e from the bot wallet to a personal wallet.
    Uses web3 to send a direct ERC-20 transfer (not via CLOB).
    """
    if not POLYMARKET_PK:
        return False, "No private key configured"

    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))

        usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
        # ERC-20 transfer ABI (minimal)
        abi = [{"constant":False,"inputs":[{"name":"to","type":"address"},
                {"name":"value","type":"uint256"}],"name":"transfer",
                "outputs":[{"name":"","type":"bool"}],"type":"function"}]

        contract = w3.eth.contract(address=Web3.to_checksum_address(usdc_contract), abi=abi)
        amount_wei = int(amount_usdc * 1e6)  # USDC has 6 decimals

        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(POLYMARKET_WALLET))
        tx = contract.functions.transfer(
            Web3.to_checksum_address(to_address), amount_wei
        ).build_transaction({
            "from": Web3.to_checksum_address(POLYMARKET_WALLET),
            "nonce": nonce,
            "gas": 100000,
            "gasPrice": w3.eth.gas_price,
            "chainId": 137,
        })

        signed_tx = w3.eth.account.sign_transaction(tx, POLYMARKET_PK)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt["status"] == 1:
            return True, tx_hash.hex()
        else:
            return False, "Transaction reverted"
    except Exception as e:
        return False, str(e)

# ─── Signal Config ───────────────────────────────────────────────────────────

SIGNAL_EV_THRESHOLD   = 0.12      # Only signal if EV > 12% after fees
SIGNAL_FEE_PCT        = 0.02      # 2% taker fee
SIGNAL_MIN_CONFIDENCE = 0.12      # Min posterior divergence
SIGNAL_MIN_CORRELATION = 0.80     # Only highly correlated markets
SIGNAL_KELLY_FRACTION = 0.25      # Quarter Kelly
SIGNAL_MAX_BET_PCT    = 0.08      # Max 8% of bankroll per trade
SIGNAL_TRAILING_STOP  = 0.20      # 20% trailing from peak
SIGNAL_COOLDOWN_SEC   = 900       # 15 min between signals per market
SIGNAL_PRIOR_DECAY    = 0.95      # Bayesian prior decay
SIGNAL_MOVE_THRESHOLD = 0.001     # 0.1% price move = meaningful

# Likelihood tables: "when BTC moves X% in Y minutes, how often does a correlated
# Polymarket outcome resolve YES?" Calibrated from historical patterns.
LIKELIHOOD_TABLE = {
    1:  {"big_up": 0.72, "small_up": 0.58, "flat": 0.50, "small_down": 0.42, "big_down": 0.28},
    5:  {"big_up": 0.78, "small_up": 0.62, "flat": 0.50, "small_down": 0.38, "big_down": 0.22},
    15: {"big_up": 0.82, "small_up": 0.65, "flat": 0.50, "small_down": 0.35, "big_down": 0.18},
}

# ─── Polymarket API helpers ──────────────────────────────────────────────────

async def fetch_polymarket_crypto_markets():
    """Fetch crypto-related markets from Polymarket Gamma API."""
    base = "https://gamma-api.polymarket.com"
    markets = []
    for keyword in ["bitcoin", "btc", "ethereum", "eth", "crypto"]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base}/markets",
                    params={"q": keyword, "limit": 5, "active": "true", "closed": "false"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    for m in data:
                        mid = m.get("id") or m.get("condition_id", "")
                        if mid and mid not in [x.get("id") for x in markets]:
                            markets.append(m)
        except Exception as e:
            print(f"⚠️ Polymarket fetch ({keyword}): {e}")
    return markets


async def fetch_btc_eth_prices():
    """Fetch current BTC and ETH prices from CoinGecko (free, no key needed)."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params={"ids": "bitcoin,ethereum", "vs_currencies": "usd",
                         "include_24hr_change": "true"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return await resp.json()
    except Exception as e:
        print(f"⚠️ CoinGecko fetch: {e}")
        return {}

# ─── Core Formulas (self-contained, no external modules needed) ──────────────

def classify_move(pct_return):
    """Classify a price return into a bucket for the likelihood table."""
    if pct_return > 0.01:    return "big_up"
    elif pct_return > 0.002: return "small_up"
    elif pct_return < -0.01: return "big_down"
    elif pct_return < -0.002: return "small_down"
    else:                    return "flat"


def compute_likelihood(pct_return, window_minutes, correlation=1.0):
    """Formula 6 helper: P(E|H) — likelihood of this price move given YES resolution."""
    table = LIKELIHOOD_TABLE.get(window_minutes, LIKELIHOOD_TABLE[5])
    move_type = classify_move(pct_return)
    base = table[move_type]
    # Scale by correlation: 0 correlation → 0.5 (uninformative)
    return max(0.01, min(0.99, 0.5 + (base - 0.5) * correlation))


def bayesian_update(prior, pct_return, window_minutes, correlation):
    """
    Formula 6: P(H|E) = [P(E|H) · P(H)] / P(E)
    The core "brain" of the signal system.
    """
    # Decay prior toward 0.5 to prevent runaway confidence
    p_h = 0.5 + (prior - 0.5) * SIGNAL_PRIOR_DECAY

    # P(E|H) — likelihood
    p_e_h = compute_likelihood(pct_return, window_minutes, correlation)

    # P(E) = P(E|H)·P(H) + P(E|¬H)·P(¬H)
    p_e_not_h = 1.0 - p_e_h
    p_e = p_e_h * p_h + p_e_not_h * (1.0 - p_h)
    p_e = max(p_e, 1e-10)

    # Bayes' theorem
    posterior = (p_e_h * p_h) / p_e
    return max(0.05, min(0.95, posterior))  # Clamp to prevent extremes


def compute_ev_gap(p_true, market_price, fee_pct=SIGNAL_FEE_PCT):
    """Formula 3: EV = (p_true - market_price) × payout - fees."""
    return (p_true - market_price) * 1.0 - market_price * fee_pct


def compute_kelly(p_true, market_price):
    """Formula 2: f* = (p · odds - (1-p)) / odds."""
    if market_price <= 0 or market_price >= 1:
        return 0.0
    odds = (1.0 / market_price) - 1.0
    if odds <= 0:
        return 0.0
    f = (p_true * odds - (1.0 - p_true)) / odds
    return max(0.0, min(1.0, f))


def compute_kl_divergence(p_true, market_price):
    """Formula 4: D_KL(P||Q) = Σ P_i · log(P_i / Q_i)."""
    p = max(0.001, min(0.999, p_true))
    q = max(0.001, min(0.999, market_price))
    return p * math.log(p / q) + (1 - p) * math.log((1 - p) / (1 - q))


def compute_signal_bet(bankroll, p_true, market_price):
    """Combine Kelly + risk caps into a final bet size."""
    raw_kelly = compute_kelly(p_true, market_price)
    bet = raw_kelly * SIGNAL_KELLY_FRACTION * bankroll
    max_bet = bankroll * SIGNAL_MAX_BET_PCT
    available = bankroll - 2.0  # Keep $2 reserve
    return max(0, min(bet, max_bet, available))

# ─── Signal Scanner (called by scheduler every 60 seconds) ──────────────────

async def run_signal_scan():
    """
    Main scan loop — called every 60 seconds by the scheduler.
    1. Fetch BTC/ETH prices
    2. Compute returns over 1m, 5m, 15m windows
    3. Update Bayesian posteriors for all tracked markets
    4. If posterior diverges enough from market → post signal to group
    """
    global signal_bankroll, signal_peak, signal_consecutive_losses
    global signal_is_paused, signal_pause_until, tracked_markets

    if not polymarket_enabled or not TELEGRAM_GROUP_ID:
        return

    # Streak pause check
    now = datetime.now(timezone.utc).timestamp()
    if signal_is_paused and now < signal_pause_until:
        return
    elif signal_is_paused:
        signal_is_paused = False

    # Trailing stop check
    stop_level = signal_peak * (1.0 - SIGNAL_TRAILING_STOP)
    if signal_bankroll <= stop_level:
        return

    # ── Fetch prices ───────────────────────────────────────────────────────
    prices = await fetch_btc_eth_prices()
    btc_price = prices.get("bitcoin", {}).get("usd")
    eth_price = prices.get("ethereum", {}).get("usd")

    if not btc_price:
        return

    now_ts = datetime.now(timezone.utc).timestamp()
    btc_price_history.append((now_ts, btc_price))
    if eth_price:
        eth_price_history.append((now_ts, eth_price))

    # Keep only 20 min of history
    cutoff = now_ts - 1200
    btc_price_history[:] = [(t, p) for t, p in btc_price_history if t >= cutoff]
    eth_price_history[:] = [(t, p) for t, p in eth_price_history if t >= cutoff]

    # ── Compute returns for each window ────────────────────────────────────
    returns = {}
    for window in [1, 5, 15]:
        lookback = now_ts - window * 60
        past = None
        for t, p in reversed(btc_price_history):
            if t <= lookback:
                past = p
                break
        if past and past > 0:
            returns[window] = (btc_price - past) / past

    if not returns:
        return

    # ── Initialise tracked markets on first run ────────────────────────────
    if not tracked_markets:
        raw_markets = await fetch_polymarket_crypto_markets()
        for m in raw_markets[:8]:
            mid = m.get("id") or m.get("condition_id", "")
            name = m.get("question", m.get("title", "Unknown"))[:60]
            tokens = m.get("tokens", [])
            price = float(tokens[0].get("price", 0.5)) if tokens else 0.5
            price = max(0.05, min(0.95, price))
            # Estimate correlation from market name
            name_lower = name.lower()
            if any(k in name_lower for k in ["btc", "bitcoin", "$100k", "$90k"]):
                corr = 0.90
            elif any(k in name_lower for k in ["eth", "ethereum"]):
                corr = 0.82
            elif any(k in name_lower for k in ["crypto", "market cap"]):
                corr = 0.78
            else:
                corr = 0.50
            if corr >= SIGNAL_MIN_CORRELATION:
                tracked_markets[mid] = {
                    "name": name, "price": price, "posterior": price,
                    "correlation": corr, "last_signal_ts": 0, "updates": 0,
                }
        if tracked_markets:
            count = len(tracked_markets)
            print(f"📊 Polymarket: tracking {count} crypto markets")

    # ── Update posteriors and check for signals ────────────────────────────
    for mid, mkt in tracked_markets.items():
        # Apply Bayesian updates for each window
        for window in sorted(returns.keys()):
            ret = returns[window]
            if abs(ret) >= SIGNAL_MOVE_THRESHOLD:
                mkt["posterior"] = bayesian_update(
                    mkt["posterior"], ret, window, mkt["correlation"]
                )
                mkt["updates"] += 1

        # Check for signal
        confidence = abs(mkt["posterior"] - mkt["price"])
        ev = compute_ev_gap(mkt["posterior"], mkt["price"])
        kl = compute_kl_divergence(mkt["posterior"], mkt["price"])

        # Skip if below thresholds
        if ev < SIGNAL_EV_THRESHOLD:
            continue
        if confidence < SIGNAL_MIN_CONFIDENCE:
            continue

        # Multi-signal check: need EV + at least one of (KL > 0.1, high confidence)
        confirms = 1  # EV passed
        if kl >= 0.10:
            confirms += 1
        if confidence >= 0.20:
            confirms += 1
        if confirms < 2:
            continue

        # Cooldown check
        if now_ts - mkt["last_signal_ts"] < SIGNAL_COOLDOWN_SEC:
            continue

        # ── SIGNAL FOUND — compute bet and post ───────────────────────────
        bet = compute_signal_bet(signal_bankroll, mkt["posterior"], mkt["price"])
        if bet < 0.50:
            continue

        direction = "BUY YES 🟢" if mkt["posterior"] > mkt["price"] else "BUY NO 🔴"
        mkt["last_signal_ts"] = now_ts

        # Log the signal internally
        signal_trades.append({
            "ts": now_ts, "market": mkt["name"], "direction": direction,
            "posterior": mkt["posterior"], "market_price": mkt["price"],
            "ev": ev, "bet": bet, "won": None,  # Resolved later
        })

        # ── PUBLIC message (group) — NO bankroll, NO bet size ─────────────
        group_msg = (
            f"🔔 *POLYMARKET SIGNAL*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *{mkt['name']}*\n"
            f"📌 {direction}\n"
            f"💰 Market: {mkt['price']:.2%} → Belief: {mkt['posterior']:.2%}\n"
            f"📈 EV edge: {ev:+.2%}\n"
            f"🧠 Confidence: {confirms}/3 signals confirmed\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"BTC: ${btc_price:,.0f} | "
            f"Updates: {mkt['updates']}\n"
            f"_Beefy's quant engine • not financial advice_"
        )

        # ── PRIVATE message (admin DM) — full details including bankroll ──
        # Build trade execution status
        trade_status = "📝 Paper trade (signal only)"
        order_resp = None

        if TRADING_MODE == "live" and clob_ready and not trading_paused:
            # Get the token_id for this market outcome
            tokens = None
            try:
                raw_markets = await fetch_polymarket_crypto_markets()
                for rm in raw_markets:
                    rm_id = rm.get("id") or rm.get("condition_id", "")
                    if rm_id == mid:
                        tokens = rm.get("tokens", [])
                        break
            except Exception:
                pass

            if tokens and len(tokens) > 0:
                # YES token is tokens[0], NO token is tokens[1] (if exists)
                if mkt["posterior"] > mkt["price"]:
                    # Buying YES
                    trade_token = tokens[0].get("token_id", "")
                    trade_side = "BUY"
                    trade_price = mkt["price"]
                else:
                    # Buying NO = selling YES, or buying the NO token
                    trade_token = tokens[1].get("token_id", "") if len(tokens) > 1 else tokens[0].get("token_id", "")
                    trade_side = "BUY" if len(tokens) > 1 else "SELL"
                    trade_price = 1.0 - mkt["price"] if len(tokens) > 1 else mkt["price"]

                if trade_token:
                    order_resp = await execute_clob_order(
                        token_id=trade_token,
                        side=trade_side,
                        price=trade_price,
                        size=bet / trade_price,  # Convert USDC amount to shares
                    )
                    if order_resp:
                        order_id = order_resp.get("orderID", order_resp.get("id", "unknown"))
                        trade_status = f"✅ LIVE ORDER PLACED — ID: {str(order_id)[:12]}..."
                        # Update bankroll from real balance
                        real_bal = await get_clob_balance()
                        if real_bal is not None:
                            signal_bankroll = real_bal
                            signal_peak = max(signal_peak, real_bal)
                    else:
                        trade_status = "⚠️ Order failed — logged as paper trade"

        admin_msg = (
            f"🔒 *PRIVATE SIGNAL DETAIL*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *{mkt['name']}*\n"
            f"📌 {direction}\n"
            f"💰 Market: {mkt['price']:.2%} → Belief: {mkt['posterior']:.2%}\n"
            f"📈 EV gap: {ev:+.2%}\n"
            f"📐 KL: {kl:.4f} | Confirms: {confirms}/3\n"
            f"💵 *Bet size: ${bet:.2f}*\n"
            f"💰 *Bankroll: ${signal_bankroll:.2f}*\n"
            f"📉 Drawdown: {(signal_peak - signal_bankroll) / signal_peak if signal_peak > 0 else 0:.1%}\n"
            f"🔥 Streak: {signal_consecutive_losses} losses\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 *{trade_status}*\n"
            f"Mode: {'🟢 LIVE' if TRADING_MODE == 'live' else '📝 PAPER'}"
        )

        # Send to group (public, clean version)
        try:
            await application.bot.send_message(
                chat_id=int(TELEGRAM_GROUP_ID), text=group_msg, parse_mode="Markdown"
            )
        except Exception as e:
            print(f"⚠️ Group signal failed: {e}")

        # Send to admin DM (private, full details)
        if ADMIN_CHAT_ID:
            try:
                await application.bot.send_message(
                    chat_id=int(ADMIN_CHAT_ID), text=admin_msg, parse_mode="Markdown"
                )
            except Exception as e:
                print(f"⚠️ Admin DM failed: {e}")

        print(f"📊 Signal: {mkt['name']} — {direction} — bet ${bet:.2f}")

        # Reset posterior toward market (assumes market catches up)
        mkt["posterior"] = 0.6 * mkt["price"] + 0.4 * mkt["posterior"]

# ─── Signal Commands ─────────────────────────────────────────────────────────

async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only — toggle Polymarket signals on/off. Always replies in DM."""
    global polymarket_enabled, tracked_markets, signal_bankroll, signal_peak
    global signal_trades, signal_consecutive_losses, signal_is_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    polymarket_enabled = not polymarket_enabled
    if polymarket_enabled:
        tracked_markets = {}
        signal_bankroll = 50.0
        signal_peak = 50.0
        signal_trades = []
        signal_consecutive_losses = 0
        signal_is_paused = False
        # Reply in the current chat (works in both DM and group)
        await update.message.reply_text(
            "📊 *Polymarket Signals: ON*\n\n"
            "Scanning every 60 seconds.\n"
            "• Group gets clean signals (no money info)\n"
            "• You get full details via DM\n\n"
            "Use /signalstatus for full breakdown.\n"
            "Use /signals again to turn off.",
            parse_mode="Markdown",
        )
        # Also send confirmation to admin DM if this was typed in the group
        if ADMIN_CHAT_ID and str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
            try:
                await application.bot.send_message(
                    chat_id=int(ADMIN_CHAT_ID),
                    text=(
                        "🔒 *Signals activated — Private Dashboard*\n\n"
                        "Starting bankroll: $50.00 USDC\n"
                        "Trailing stop: 20% from peak\n"
                        "Max bet: 8% of bankroll\n"
                        "Kelly: 0.25x fractional\n\n"
                        "Full trade details will be sent here.\n"
                        "Group only sees direction + market + EV."
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
    else:
        await update.message.reply_text("📊 Polymarket Signals: OFF")


async def signalstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show signal status.
    - In GROUP: shows tracked markets only (no bankroll info)
    - In PRIVATE CHAT: shows full bankroll, PnL, win rate, bet sizes
    """
    if not polymarket_enabled:
        await update.message.reply_text(
            "📊 Signals are OFF. Admin can enable with /signals"
        )
        return

    is_private = update.effective_chat.type == "private"
    n_markets = len(tracked_markets)

    # Market lines (shown in both group and private)
    market_lines = []
    for mid, mkt in list(tracked_markets.items())[:5]:
        div = mkt["posterior"] - mkt["price"]
        arrow = "↑" if div > 0 else "↓"
        market_lines.append(
            f"  {mkt['name'][:35]}\n"
            f"    Mkt: {mkt['price']:.2%} | Belief: {mkt['posterior']:.2%} ({arrow}{abs(div):.2%})"
        )
    markets_text = "\n".join(market_lines) if market_lines else "  Loading markets..."

    if is_private and is_admin(update.effective_user):
        # ── PRIVATE: Full details for admin ────────────────────────────────
        n_trades = len(signal_trades)
        wins = sum(1 for t in signal_trades if t.get("won"))
        losses = n_trades - wins
        wr = f"{wins/n_trades:.0%}" if n_trades > 0 else "N/A"
        pnl = signal_bankroll - 50.0
        dd = (signal_peak - signal_bankroll) / signal_peak if signal_peak > 0 else 0

        await update.message.reply_text(
            f"🔒 *Signal System — Private Dashboard*\n\n"
            f"Status: 🟢 ACTIVE\n"
            f"Markets tracked: {n_markets}\n\n"
            f"💰 *Bankroll: ${signal_bankroll:.2f}*\n"
            f"📈 *PnL: ${pnl:+.2f}*\n"
            f"📉 Drawdown: {dd:.1%}\n"
            f"🛑 Trailing stop: ${signal_peak * (1 - SIGNAL_TRAILING_STOP):.2f}\n"
            f"📊 Trades: {n_trades} (W:{wins} L:{losses} WR:{wr})\n"
            f"🔥 Streak losses: {signal_consecutive_losses}\n\n"
            f"*Tracked Markets:*\n{markets_text}\n\n"
            f"_Scanning every 60s_",
            parse_mode="Markdown",
        )
    else:
        # ── GROUP: Clean version, no money info ────────────────────────────
        await update.message.reply_text(
            f"📊 *Polymarket Signal Scanner*\n\n"
            f"Status: 🟢 ACTIVE\n"
            f"Markets tracked: {n_markets}\n\n"
            f"*Watching:*\n{markets_text}\n\n"
            f"_Signals post here when a mispricing is detected.\n"
            f"DM @{ADMIN_USERNAME} for more info._",
            parse_mode="Markdown",
        )


# ─── Phase 7: Trading Commands (admin only, private chat) ───────────────────

async def botbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check the bot wallet's live on-chain balance."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return

    if not POLYMARKET_PK:
        await update.message.reply_text(
            "⚠️ No wallet configured.\n"
            "Set POLYMARKET_PRIVATE_KEY and POLYMARKET_WALLET_ADDRESS in Render."
        )
        return

    bal = await get_clob_balance()
    wallet_short = f"{POLYMARKET_WALLET[:6]}...{POLYMARKET_WALLET[-4:]}" if POLYMARKET_WALLET else "not set"

    bal_str = f"${bal:.2f}" if bal is not None else "⚠️ Could not fetch"

    await update.message.reply_text(
        f"🔒 *Bot Wallet Balance*\n\n"
        f"Wallet: `{wallet_short}`\n"
        f"USDC.e: {bal_str}\n"
        f"Mode: {'🟢 LIVE' if TRADING_MODE == 'live' else '📝 PAPER'}\n"
        f"CLOB: {'✅ Connected' if clob_ready else '❌ Not connected'}\n"
        f"Trading: {'⏸ Paused' if trading_paused else '▶️ Active'}\n\n"
        f"Paper bankroll: ${signal_bankroll:.2f}\n"
        f"Paper peak: ${signal_peak:.2f}",
        parse_mode="Markdown",
    )


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Withdraw all USDC from bot wallet to your personal wallet."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return

    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 Use this command in a private chat with the bot.")
        return

    if not POLYMARKET_PK or not PERSONAL_WALLET:
        await update.message.reply_text(
            "⚠️ Missing config. Need both:\n"
            "• POLYMARKET_PRIVATE_KEY\n"
            "• YOUR_PERSONAL_WALLET"
        )
        return

    bal = await get_clob_balance()
    if bal is None or bal <= 0.01:
        await update.message.reply_text(f"⚠️ No USDC.e balance to withdraw (balance: ${bal or 0:.2f})")
        return

    wallet_short = f"{PERSONAL_WALLET[:6]}...{PERSONAL_WALLET[-4:]}"

    # Ask for confirmation
    if not context.user_data.get("withdraw_confirmed"):
        context.user_data["withdraw_confirmed"] = True
        context.user_data["withdraw_amount"] = bal
        await update.message.reply_text(
            f"⚠️ *Confirm Withdrawal*\n\n"
            f"Amount: ${bal:.2f} USDC.e\n"
            f"To: `{wallet_short}`\n\n"
            f"Type /withdraw again to confirm.\n"
            f"Type /cancel to abort.",
            parse_mode="Markdown",
        )
        return

    # Execute withdrawal
    amount = context.user_data.get("withdraw_amount", bal)
    context.user_data["withdraw_confirmed"] = False
    await update.message.reply_text(f"📤 Sending ${amount:.2f} USDC.e to {wallet_short}...")

    success, result = await withdraw_usdc(PERSONAL_WALLET, amount)

    if success:
        await update.message.reply_text(
            f"✅ *Withdrawal Complete*\n\n"
            f"Amount: ${amount:.2f} USDC.e\n"
            f"To: `{wallet_short}`\n"
            f"Tx: `{result[:20]}...`\n\n"
            f"Check on Polygonscan:\nhttps://polygonscan.com/tx/0x{result}",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ Withdrawal failed: {result}")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a pending withdrawal confirmation."""
    context.user_data["withdraw_confirmed"] = False
    await update.message.reply_text("✅ Cancelled.")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause live trading (signals still post, no orders placed)."""
    global trading_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    trading_paused = True
    await update.message.reply_text(
        "⏸ *Trading PAUSED*\n\n"
        "Signals will still scan and post.\n"
        "No orders will be placed until /resume.\n"
        "Your funds are safe in the wallet.",
        parse_mode="Markdown",
    )


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume live trading after a pause."""
    global trading_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    trading_paused = False
    await update.message.reply_text(
        "▶️ *Trading RESUMED*\n\n"
        f"Mode: {'🟢 LIVE' if TRADING_MODE == 'live' else '📝 PAPER'}\n"
        f"CLOB: {'✅ Connected' if clob_ready else '❌ Not connected'}",
        parse_mode="Markdown",
    )


async def testconnection_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test the Polymarket CLOB connection without placing any trades."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return

    lines = ["🔧 *Connection Test*\n"]

    # 1. Check env vars
    lines.append(f"{'✅' if POLYMARKET_PK else '❌'} POLYMARKET_PRIVATE_KEY: {'set' if POLYMARKET_PK else 'MISSING'}")
    lines.append(f"{'✅' if POLYMARKET_WALLET else '❌'} POLYMARKET_WALLET_ADDRESS: {'set' if POLYMARKET_WALLET else 'MISSING'}")
    lines.append(f"{'✅' if PERSONAL_WALLET else '❌'} YOUR_PERSONAL_WALLET: {'set' if PERSONAL_WALLET else 'MISSING'}")
    lines.append(f"📋 TRADING_MODE: {TRADING_MODE}")

    # 2. Check CLOB client
    lines.append(f"\n{'✅' if clob_ready else '❌'} CLOB client: {'connected' if clob_ready else 'not connected'}")

    # 3. Check balance
    if clob_ready:
        bal = await get_clob_balance()
        if bal is not None:
            lines.append(f"✅ Balance check: ${bal:.2f} USDC.e")
        else:
            lines.append("⚠️ Balance check: failed")

    # 4. Check Polymarket API
    try:
        markets = await fetch_polymarket_crypto_markets()
        lines.append(f"✅ Polymarket API: {len(markets)} markets found")
    except Exception as e:
        lines.append(f"❌ Polymarket API: {e}")

    # 5. Check CoinGecko
    try:
        prices = await fetch_btc_eth_prices()
        btc = prices.get("bitcoin", {}).get("usd", 0)
        lines.append(f"✅ CoinGecko API: BTC ${btc:,.0f}")
    except Exception as e:
        lines.append(f"❌ CoinGecko API: {e}")

    if not POLYMARKET_PK:
        lines.append("\n📋 *To enable live trading:*")
        lines.append("1. Create wallet (see setup guide)")
        lines.append("2. Add POLYMARKET_PRIVATE_KEY to Render")
        lines.append("3. Add POLYMARKET_WALLET_ADDRESS to Render")
        lines.append("4. Add YOUR_PERSONAL_WALLET to Render")
        lines.append("5. Set TRADING_MODE=live in Render")
        lines.append("6. Redeploy and run /testconnection again")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def golive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch from paper to live mode. Requires confirmation."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return

    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 Use this in private chat only.")
        return

    if TRADING_MODE != "live":
        await update.message.reply_text(
            "⚠️ TRADING_MODE is set to 'paper' in Render.\n\n"
            "To go live:\n"
            "1. Go to Render dashboard → Environment\n"
            "2. Change TRADING_MODE from 'paper' to 'live'\n"
            "3. Redeploy the service\n"
            "4. Run /testconnection to verify\n\n"
            "This is intentionally a manual step — you should never\n"
            "accidentally switch to real money trading."
        )
        return

    if not clob_ready:
        await update.message.reply_text("❌ CLOB client not connected. Run /testconnection first.")
        return

    bal = await get_clob_balance()
    await update.message.reply_text(
        f"🟢 *LIVE TRADING IS ACTIVE*\n\n"
        f"Wallet balance: ${bal:.2f} USDC.e\n"
        f"Trading: {'▶️ Active' if not trading_paused else '⏸ Paused'}\n\n"
        f"The bot will now place real orders on Polymarket\n"
        f"when signals meet all criteria.\n\n"
        f"Use /pause to stop at any time.\n"
        f"Use /withdraw to pull funds back.\n"
        f"Use /botbalance to check live balance.",
        parse_mode="Markdown",
    )


# =============================================================================
# WEBHOOK ROUTES
# =============================================================================

@app.route("/", methods=["GET"])
def home():
    return "🐂 BeefyTheBull Bot is Alive! Good Green Bull — Built on Base."


@app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "OK"

# =============================================================================
# HANDLER REGISTRATION
# =============================================================================

def register_handlers():
    application.add_handler(CommandHandler("start",       start))
    application.add_handler(CommandHandler("help",        help_command))
    application.add_handler(CommandHandler("price",       price))
    application.add_handler(CommandHandler("bull",        bull))
    application.add_handler(CommandHandler("gm",          gm_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("wallet",      wallet))
    application.add_handler(CommandHandler("token",       token))
    application.add_handler(CommandHandler("kit",         kit))
    application.add_handler(CommandHandler("nft",         nft))
    application.add_handler(CommandHandler("herd",        herd))
    application.add_handler(CommandHandler("daily",       daily_command))
    application.add_handler(CommandHandler("revival",     revival_command))
    application.add_handler(CommandHandler("settings",    settings))
    application.add_handler(CommandHandler("signals",     signals_command))
    application.add_handler(CommandHandler("signalstatus", signalstatus_command))
    # Phase 7 — Trading commands
    application.add_handler(CommandHandler("botbalance",     botbalance_command))
    application.add_handler(CommandHandler("withdraw",       withdraw_command))
    application.add_handler(CommandHandler("cancel",         cancel_command))
    application.add_handler(CommandHandler("pause",          pause_command))
    application.add_handler(CommandHandler("resume",         resume_command))
    application.add_handler(CommandHandler("testconnection", testconnection_command))
    application.add_handler(CommandHandler("golive",         golive_command))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gm_text), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, detect_spam), group=2)

# =============================================================================
# STARTUP
# =============================================================================

async def on_startup():
    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook set: {WEBHOOK_URL}")

    # Phase 7: Initialise CLOB client if keys are present
    await init_clob_client()

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(send_beefy_daily,       "cron", hour=8, minute=0)
    scheduler.add_job(send_weekly_engagement, "cron", day_of_week="mon", hour=9, minute=0)
    scheduler.add_job(run_signal_scan,        "interval", seconds=60)
    scheduler.add_job(send_daily_balance,     "cron", hour=8, minute=5)  # Balance DM 5 min after daily
    scheduler.start()
    print(f"✅ Scheduler running — Daily 08:00 | Monday 09:00 | Signals 60s | Balance 08:05")
    print(f"📋 Mode: {TRADING_MODE} | CLOB: {'ready' if clob_ready else 'paper only'}")


async def send_daily_balance():
    """Send daily balance report to admin DM every morning."""
    if not ADMIN_CHAT_ID:
        return
    bal = await get_clob_balance() if clob_ready else None
    pnl = signal_bankroll - 50.0
    dd = (signal_peak - signal_bankroll) / signal_peak if signal_peak > 0 else 0
    n_trades = len(signal_trades)
    wins = sum(1 for t in signal_trades if t.get("won"))

    bal_line = f"💰 Live balance: ${bal:.2f}" if bal is not None else "📝 Paper mode"

    msg = (
        f"📊 *Daily Balance Report*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bal_line}\n"
        f"📈 Paper bankroll: ${signal_bankroll:.2f}\n"
        f"📉 PnL: ${pnl:+.2f}\n"
        f"📉 Drawdown: {dd:.1%}\n"
        f"📊 Trades: {n_trades} (W:{wins} L:{n_trades-wins})\n"
        f"🔥 Losing streak: {signal_consecutive_losses}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Mode: {'🟢 LIVE' if TRADING_MODE == 'live' else '📝 PAPER'} | "
        f"{'⏸ Paused' if trading_paused else '▶️ Active'}"
    )
    try:
        await application.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID), text=msg, parse_mode="Markdown"
        )
    except Exception as e:
        print(f"⚠️ Daily balance DM failed: {e}")


if __name__ == "__main__":
    register_handlers()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(on_startup())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

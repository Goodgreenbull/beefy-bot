# =============================================================================
# GGB BEEFY BOT — server.py
# Good Green Bull | Built on Base
# =============================================================================

import os
import asyncio
import random
import threading
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
from apscheduler.schedulers.background import BackgroundScheduler

# =============================================================================
# CONFIG — Set all of these in Render environment variables
# =============================================================================

TOKEN             = os.getenv("BOT_TOKEN")
BASESCAN_API_KEY  = os.getenv("BASESCAN_API_KEY")
ADMIN_USERNAME    = os.getenv("ADMIN_USERNAME", "JS0nbase").lstrip("@").strip()
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
WEBHOOK_PATH      = f"/webhook/{TOKEN}"
WEBHOOK_URL       = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"
GGB_CONTRACT      = "0xc2758c05916ba20b19358f1e96f597774e603050"

# =============================================================================
# APP INIT
# =============================================================================

app         = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# Dedicated event loop for all async bot work — lives on a background thread
bot_loop = asyncio.new_event_loop()

def run_bot_loop(loop):
    """Runs the bot event loop forever on a background thread."""
    asyncio.set_event_loop(loop)
    loop.run_forever()

bot_thread = threading.Thread(target=run_bot_loop, args=(bot_loop,), daemon=True)
bot_thread.start()

def run_async(coro):
    """Submit a coroutine to the bot loop from any thread and wait for result."""
    future = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return future.result(timeout=30)

# =============================================================================
# STATE
# =============================================================================

user_spam_tracker   = {}
recent_bull_indices = []
gm_tracker          = {}
gm_tracker_date     = None

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
                pairs  = data.get("pairs")
                if not pairs:
                    return None, None
                pair   = pairs[0]
                price  = float(pair["priceUsd"])
                change = float(pair.get("priceChange", {}).get("h24", 0))
                return price, change
    except Exception as e:
        print(f"⚠️ Price fetch error: {e}")
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
                else:
                    print(f"⚠️ BaseScan error: {data.get('message')}")
    except Exception as e:
        print(f"⚠️ Wallet fetch error: {e}")
    return None


def is_admin(user) -> bool:
    """Works in groups and DMs. Case-insensitive username match."""
    if not user or not user.username:
        return False
    return user.username.lstrip("@").strip().lower() == ADMIN_USERNAME.lower()


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
        [InlineKeyboardButton("🎨 Wallpaper Pack",  url="https://goodgreenbull.com")],
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
        "/settings — Admin panel",
        parse_mode="Markdown",
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching price... 📊")
    price_val, change = await fetch_price_data()
    if price_val is None:
        await update.message.reply_text(
            "⚠️ Could not fetch price right now.\n"
            "Check manually: https://tinyurl.com/GGBDex"
        )
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
    user = update.effective_user
    name = user.first_name or "Bull"
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
        await update.message.reply_text(
            "Usage: `/wallet <Base wallet address>`", parse_mode="Markdown"
        )
        return
    address = context.args[0]
    await update.message.reply_text("Checking wallet... 👛")
    balance = await fetch_wallet_balance(address)
    if balance is None:
        await update.message.reply_text(
            "⚠️ Could not fetch wallet data.\n"
            "Check the address is a valid Base wallet and try again."
        )
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
        "✅ 10 AI image prompts with character guardrails\n"
        "✅ Brand voice guide\n"
        "✅ Mascot design rules\n"
        "✅ Monetisation framework\n"
        "✅ Quick-start checklist\n\n"
        "💰 £35 — Instant download\n"
        "🔗 https://goodgreenbull.com/kit",
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
        except Exception as e:
            print(f"⚠️ Herd count error: {e}")
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
        await update.message.reply_text(
            f"⛔ Admin only.\n"
            f"(Your username: @{update.effective_user.username} | "
            f"Required: @{ADMIN_USERNAME})"
        )
        return
    keyboard = [
        [InlineKeyboardButton("🤖 Toggle Daily Post",   callback_data="toggle_daily")],
        [InlineKeyboardButton("🚫 Toggle Anti-Spam",    callback_data="toggle_spam")],
        [InlineKeyboardButton("📣 Send Revival Blast",  callback_data="send_revival")],
    ]
    await update.message.reply_text(
        "⚙️ *Admin Settings*\n\nYou're in, boss. Choose an action:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text(
            f"⛔ Admin only.\n"
            f"(Your username: @{update.effective_user.username} | "
            f"Required: @{ADMIN_USERNAME})"
        )
        return

    if not TELEGRAM_GROUP_ID:
        # If no group ID set, send the daily post content directly to the chat
        price_val, change = await fetch_price_data()
        price_line = (
            f"$GGB: ${price_val:.6f} | {'+' if change >= 0 else ''}{change:.2f}% 24h"
            if price_val else "$GGB: Price unavailable"
        )
        msg = (
            f"📤 *Daily post preview* (TELEGRAM_GROUP_ID not set)\n\n"
            f"GM Herd 🐂💚\n\n"
            f"{get_bull_quote()}\n\n"
            f"{price_line}\n\n"
            f"What are you building today? Drop it below 👇"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    await update.message.reply_text("📤 Sending Beefy Daily to the group now...")
    await send_beefy_daily()
    await update.message.reply_text("✅ Daily post sent.")


async def revival_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text(
            f"⛔ Admin only.\n"
            f"(Your username: @{update.effective_user.username} | "
            f"Required: @{ADMIN_USERNAME})"
        )
        return

    if not TELEGRAM_GROUP_ID:
        await update.message.reply_text(
            "⚠️ TELEGRAM_GROUP_ID is not set in environment variables.\n"
            "Add it in your Render dashboard and redeploy."
        )
        return

    await update.message.reply_text("📣 Sending revival blast to the group...")
    await send_revival_blast()
    await update.message.reply_text("✅ Revival blast sent.")


# =============================================================================
# SCHEDULED POSTS — Called from BackgroundScheduler via run_async()
# =============================================================================

async def send_beefy_daily():
    if not TELEGRAM_GROUP_ID:
        print("⚠️ TELEGRAM_GROUP_ID not set. Skipping daily post.")
        return
    price_val, change = await fetch_price_data()
    price_line = (
        f"$GGB: ${price_val:.6f} | {'+' if change >= 0 else ''}{change:.2f}% 24h"
        if price_val else "$GGB: Price unavailable right now"
    )
    msg = (
        f"GM Herd 🐂💚\n\n"
        f"{get_bull_quote()}\n\n"
        f"{price_line}\n\n"
        f"What are you building today? Drop it below 👇"
    )
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=msg)
        print(f"✅ Daily post sent at {datetime.now(timezone.utc)}")
    except Exception as e:
        print(f"⚠️ Daily post failed: {e}")


async def send_weekly_engagement():
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


# Sync wrappers for BackgroundScheduler
def scheduled_daily():
    run_async(send_beefy_daily())

def scheduled_weekly():
    run_async(send_weekly_engagement())


# =============================================================================
# MESSAGE HANDLERS
# =============================================================================

async def handle_gm_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    if not update.message or not update.effective_user:
        return
    # Never spam-check DMs or admin
    if update.effective_chat.type == "private":
        return
    if is_admin(update.effective_user):
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
            await context.bot.send_message(
                chat_id, text="⚠️ User muted 10 mins for spam."
            )
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
            await query.edit_message_text(
                "⚠️ Could not fetch price right now.\n📊 https://tinyurl.com/GGBDex"
            )
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
        if not is_admin(update.effective_user):
            await query.edit_message_text("⛔ Admin only.")
            return
        await query.edit_message_text("📣 Sending revival blast...")
        await send_revival_blast()
        await query.edit_message_text("✅ Revival blast sent.")
    elif query.data in ("toggle_daily", "toggle_spam"):
        await query.edit_message_text("⚙️ Toggle controls coming in next update.")


# =============================================================================
# WEBHOOK ROUTES
# =============================================================================

@app.route("/", methods=["GET"])
def home():
    return "🐂 BeefyTheBull Bot is Alive! Good Green Bull — Built on Base."


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    """Sync route — submits update processing to the bot event loop."""
    data   = request.get_json(force=True)
    update = Update.de_json(data, run_async(application.bot.get_me).__class__ and application.bot)
    run_async(application.process_update(Update.de_json(data, application.bot)))
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
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gm_text), group=1
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, detect_spam), group=2
    )


# =============================================================================
# STARTUP
# =============================================================================

def start_scheduler():
    """BackgroundScheduler runs on its own thread — no event loop conflicts."""
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(scheduled_daily,  "cron", hour=8,  minute=0)
    scheduler.add_job(scheduled_weekly, "cron", day_of_week="mon", hour=9, minute=0)
    scheduler.start()
    print("✅ Scheduler running — Daily 08:00 UTC | Monday 09:00 UTC")


if __name__ == "__main__":
    register_handlers()
    run_async(application.initialize())
    run_async(application.bot.set_webhook(url=WEBHOOK_URL))
    print(f"✅ Webhook set: {WEBHOOK_URL}")
    start_scheduler()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

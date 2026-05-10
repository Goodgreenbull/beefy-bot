# =============================================================================
# GGB BEEFY BOT v2 — server.py
# Good Green Bull | Built on Base | Brand Engine
# =============================================================================

import os
import asyncio
import random
import aiohttp
from datetime import datetime, timezone, timedelta
from quart import Quart, request
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
# CONFIG
# =============================================================================

TOKEN            = os.getenv("BOT_TOKEN")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY")
ADMIN_USERNAME   = os.getenv("ADMIN_USERNAME", "JS0nbase")
ADMIN_CHAT_ID    = os.getenv("ADMIN_CHAT_ID")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL  = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"
GGB_CONTRACT = "0xc2758c05916ba20b19358f1e96f597774e603050"

# =============================================================================
# APP INIT
# =============================================================================

app         = Quart(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# =============================================================================
# STATE — In-memory. Resets on restart. Fine for free tier.
# =============================================================================

user_spam_tracker   = {}
recent_bull_indices = []
gm_tracker          = {}
gm_tracker_date     = None
gm_streaks          = {}     # {user_id: {"name": str, "current": int, "best": int, "last_date": date}}
last_milestone      = 0      # Last celebrated member count milestone
alerted_tokens      = set()  # Token addresses already alerted (prevent spam)

# =============================================================================
# BULL QUOTES BANK
# =============================================================================

bull_quotes = [
    "The market rewards patience. The builder rewards himself. \ud83d\udc02\ud83d\udc9a",
    "Quiet stretches separate the builders from the tourists. \ud83d\udc02\ud83d\udc9a",
    "Ship ugly. Fix fast. Ship again. \ud83d\udee0\ufe0f\ud83d\udc9a",
    "Nobody's watching the process. That's the point. \ud83d\udc02\ud83c\udf3f",
    "The signal is quiet. Keep going anyway. \ud83d\udc02\ud83d\udc9a",
    "You don't outwork the market. You outlast it. \ud83d\udc02\ud83d\udc9a",
    "Conviction is a practice, not a feeling. \ud83d\udc9a\ud83d\udc02",
    "Most quit before the compound kicks in. \ud83d\udc02\ud83d\udc9a",
    "Build mode doesn't need an announcement. \ud83d\udee0\ufe0f\ud83d\udc02",
    "Progress doesn't ask for permission. \ud83d\udc9a\ud83d\udc02",
    "The ones still building in the noise are the ones worth watching. \ud83d\udc02\ud83d\udc9a",
    "Hold the line. The line is the work. \ud83d\udc02\ud83d\udc9a",
    "Momentum is just small moves that didn't stop. \ud83d\udcc8\ud83d\udc02",
    "No one remembers the hype. Everyone remembers what lasted. \ud83d\udc02\ud83d\udc9a",
    "Ship because it sharpens you, not because it trends. \ud83d\udee0\ufe0f\ud83d\udc9a",
    "If you're still here, you already passed the first filter. \ud83d\udc02\ud83d\udc9a",
    "Systems beat sprints every time. \ud83d\udcc8\ud83d\udc02",
    "Build for the version of yourself that's still here in two years. \ud83d\udc02\ud83d\udc9a",
    "The grind is not the goal. The grind is the gate. \ud83d\udc9a\ud83d\udc02",
    "Locked in. Herd strong. We move. \ud83d\udc02\ud83d\udc9a",
]

# =============================================================================
# CONTENT CALENDAR — Daily themed posts
# =============================================================================

# 0=Monday, 1=Tuesday, ... 6=Sunday
DAILY_THEMES = {
    0: {
        "title": "\ud83d\udcaa Motivation Monday",
        "prompts": [
            "New week. New reps. What's the one thing you're locking in this week? \ud83d\udc47",
            "Monday energy: name one thing you're shipping before Friday \ud83d\udee0\ufe0f",
            "The week belongs to the ones who start. What's your first move? \ud83d\udc02",
            "Most people plan on Monday and quit by Wednesday. Not this herd. What's yours? \ud83d\udc9a",
            "Momentum starts now. What are you building this week? \ud83d\udc47",
        ],
    },
    1: {
        "title": "\ud83e\udde0 Alpha Tuesday",
        "prompts": [
            "Drop one thing you learned recently that changed how you think \ud83d\udc47",
            "Best thread, podcast, or article you consumed this week? Share it \ud83e\udde0",
            "What's one alpha that most people are sleeping on right now? \ud83d\udc47",
            "Share a tool, strategy, or insight that levelled you up recently \ud83d\udc9a",
            "What's something you know now that you wish you knew 6 months ago? \ud83d\udc47",
        ],
    },
    2: {
        "title": "\ud83d\udee0\ufe0f Builder Wednesday",
        "prompts": [
            "Midweek check: what have you shipped so far this week? \ud83d\udc47",
            "Show your work. Screenshot, link, or progress update \u2014 drop it \ud83d\udee0\ufe0f",
            "Builder Wednesday: what's the hardest part of what you're building right now? \ud83d\udc47",
            "What's one thing on your build list that keeps getting pushed back? \ud83d\udc02",
            "Share what you're working on. No pitch, just progress \ud83d\udc9a",
        ],
    },
    3: {
        "title": "\ud83d\udcc8 Base Thursday",
        "prompts": [
            "What's the most interesting project you've seen on Base lately? \ud83d\udc47",
            "Base ecosystem check: what token or dApp caught your eye this week? \ud83d\udcc8",
            "If you could only hold 3 Base projects long term \u2014 what makes the cut? \ud83d\udc02",
            "What does Base need more of right now? Builders, speak up \ud83d\udc47",
            "Drop a Base project that deserves more attention \ud83d\udc9a",
        ],
    },
    4: {
        "title": "\ud83d\udd25 Flex Friday",
        "prompts": [
            "It's Friday. What's your W this week? Big or small, drop it \ud83d\udc47 \ud83d\udd25",
            "Flex Friday: what did you accomplish that you're proud of? \ud83d\udc9a",
            "End the week strong. What's one thing that went right? \ud83d\udc02",
            "Friday flex: show a win, a ship, or a lesson from this week \ud83d\udc47",
            "The weekend is earned. What did you build to deserve yours? \ud83d\udd25",
        ],
    },
    5: {
        "title": "\ud83c\udf3f Chill Saturday",
        "prompts": [
            "Saturday vibes. What are you recharging with today? \ud83c\udf3f",
            "Builders need rest too. What's your go-to way to switch off? \ud83d\udc47",
            "Weekend mode. Reading, gaming, touching grass \u2014 what's the move? \ud83d\udc02\ud83d\udc9a",
            "No hustle today. Just vibes. What's good in your world? \ud83c\udf3f",
            "Saturday reset. What are you grateful for this week? \ud83d\udc9a",
        ],
    },
    6: {
        "title": "\ud83d\udccb Sunday Reset",
        "prompts": [
            "Sunday planning: what's the #1 priority for next week? \ud83d\udc47",
            "Reset day. What are you carrying forward and what are you dropping? \ud83d\udc02",
            "Sunday question: what would make next week a 10/10? \ud83d\udc9a",
            "End of week. Rate your week 1-10 and tell us why \ud83d\udc47",
            "Tomorrow starts a new cycle. What's the play? \ud83d\udccb",
        ],
    },
}

# =============================================================================
# WEEKLY ENGAGEMENT QUESTIONS — Rotates by week number
# =============================================================================

weekly_questions = [
    "What's the one thing you're shipping this week? Drop it below \ud83d\udee0\ufe0f",
    "Best Base project you've used this week? Go \ud83d\udc47",
    "If you had to cut everything except one project \u2014 what stays? \ud83d\udc02",
    "What's one tool (AI or otherwise) that's genuinely changed how you build? \ud83d\udc47",
    "Biggest lesson from your last build? Keep it real \ud83d\udc47",
    "What would make you check this group every single day? Tell us \ud83d\udc02\ud83d\udc9a",
    "One word that describes your build mindset this week \ud83d\udc47",
    "What's the most underrated thing happening on Base right now? \ud83d\udc02",
    "If GGB dropped a product tomorrow \u2014 what would you want it to be? \ud83d\udc47",
    "What does winning look like for you in the next 90 days? \ud83d\udc02\ud83d\udc9a",
]

# =============================================================================
# DISCUSSION TOPICS — Extra daily conversation starters (posted at 12:00 UTC)
# =============================================================================

discussion_topics = [
    "Hot take time: what's one popular crypto opinion you disagree with? \ud83d\udd25",
    "If you had $100 to put into one Base token today \u2014 where's it going? \ud83d\udc47",
    "Builders vs traders \u2014 which one are you and why? \ud83d\udc02",
    "What's the biggest mistake you've made in crypto? No judgment \ud83d\udc9a",
    "AI + crypto \u2014 overrated, underrated, or perfectly rated? \ud83e\udde0",
    "Name a project that died but had a great idea worth reviving \ud83d\udc47",
    "What separates a good community from a dead one? Real answers only \ud83d\udc02",
    "If you could mass-adopt ONE thing about Web3 \u2014 what would it be? \ud83d\udc9a",
    "Unpopular opinion: memecoins are ______ . Fill in the blank \ud83d\udc47",
    "Best trade you ever made? Worst? Drop both \ud83d\udcc8\ud83d\udcc9",
    "What would you build if money and time weren't a factor? \ud83d\udee0\ufe0f",
    "Is on-chain reputation the next big thing or just hype? \ud83e\udde0",
    "DeFi, NFTs, or social \u2014 what's the next big wave? \ud83d\udc47",
    "What's one thing the crypto space needs to stop doing? \ud83d\udd25",
    "If you had to explain Base to your nan \u2014 how would you do it? \ud83d\udc02\ud83d\udc9a",
    "What's your daily crypto routine? Walk us through it \ud83d\udc47",
    "One year from now \u2014 where do you see yourself? Be specific \ud83d\udc9a",
    "What project outside of crypto inspires how you build? \ud83d\udee0\ufe0f",
    "Would you rather have 10k followers or 100 paying customers? \ud83d\udc47",
    "What's the most underrated skill in crypto right now? \ud83e\udde0",
]

# =============================================================================
# ROTATING CTAs — Product/brand pushes (attached to daily post)
# =============================================================================

rotating_ctas = [
    "\ud83d\udee0\ufe0f Build your own brand on Base \u2192 https://goodgreenbull.gumroad.com",
    "\ud83c\udfa8 Beefy Prime NFTs dropping soon \u2014 follow @goodgreenbull on X for the date \ud83d\udc02",
    "\ud83c\udf10 goodgreenbull.com \u2014 the home of the herd \ud83d\udc9a",
    "\ud83d\udd4a\ufe0f Follow the bull on X \u2192 https://x.com/goodgreenbull",
    "\ud83d\udce3 Share this group with a builder \u2192 https://t.me/goodgreenbull",
    "\ud83d\udee0\ufe0f GGB Builder Kit \u2014 templates, prompts, brand system \u2192 https://goodgreenbull.gumroad.com",
]

# =============================================================================
# MILESTONE THRESHOLDS
# =============================================================================

MILESTONES = [25, 50, 75, 100, 150, 200, 250, 500, 750, 1000, 2500, 5000, 10000]

# =============================================================================
# REACTIVE REPLY KEYWORDS
# =============================================================================

REACTIVE_REPLIES = {
    "base": [
        "Base is the move. \ud83d\udc02\ud83d\udc9a",
        "Building on Base hits different. \ud83d\udc9a",
        "Base chain, best chain. \ud83d\udee0\ufe0f\ud83d\udc02",
    ],
    "token": [
        "Tokens come and go. Builders stay. \ud83d\udc02",
        "Always DYOR. The herd is smart. \ud83d\udc9a",
        "What token's got your attention? \ud83d\udc47",
    ],
    "build": [
        "Builder energy detected. \ud83d\udee0\ufe0f\ud83d\udc9a",
        "Ship it. Fix it. Ship again. \ud83d\udc02",
        "That's the builder mindset. Lock in. \ud83d\udc9a",
    ],
    "ship": [
        "Ship > talk. Always. \ud83d\udee0\ufe0f\ud83d\udc02",
        "Shipped? Respect. \ud83d\udc9a",
        "The ones who ship are the ones who win. \ud83d\udc02",
    ],
    "ggb": [
        "GGB \ud83d\udc02\ud83d\udc9a Herd strong.",
        "Good Green Bull. Built to last. \ud83d\udc9a",
        "The bull that doesn't stop. \ud83d\udc02\ud83d\udc9a",
    ],
    "bull": [
        "Bull mode activated. \ud83d\udc02\ud83d\udc9a",
        "The herd stays bullish. \ud83d\udc9a",
        "Beefy approves. \ud83d\udc02",
    ],
    "wagmi": [
        "WAGMI \u2014 but only if you keep building. \ud83d\udc02\ud83d\udc9a",
        "WAGMI. Herd strong. \ud83d\udc9a",
    ],
    "ngmi": [
        "Not with that attitude. Lock in. \ud83d\udc02",
        "Nah, we don't do that here. WAGMI. \ud83d\udc9a",
    ],
    "gn": [
        "GN bull. Rest up, we build tomorrow. \ud83d\udc02\ud83d\udc9a",
        "GN \ud83d\udc9a See you at the next GM.",
    ],
}

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
    """Fetch GGB price from DexScreener."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{GGB_CONTRACT}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                pair = data["pairs"][0]
                price = float(pair["priceUsd"])
                change = float(pair.get("priceChange", {}).get("h24", 0))
                return price, change
    except Exception:
        return None, None


async def fetch_wallet_balance(address: str):
    """Fetch GGB token balance for a wallet."""
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


async def fetch_base_trending_tokens():
    """
    Fetch trending/new tokens on Base from DexScreener.
    Returns list of token dicts with name, symbol, price, change, volume, address.
    """
    try:
        async with aiohttp.ClientSession() as session:
            # Get latest token profiles
            async with session.get(
                "https://api.dexscreener.com/token-profiles/latest/v1",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                profiles = await resp.json()

            # Filter for Base chain tokens
            base_tokens = [p for p in profiles if p.get("chainId") == "base"][:20]

            # Get price data for each
            trending = []
            for token in base_tokens[:10]:
                addr = token.get("tokenAddress", "")
                if not addr or addr in alerted_tokens:
                    continue
                try:
                    async with session.get(
                        f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as resp2:
                        data = await resp2.json()
                        pairs = data.get("pairs", [])
                        if not pairs:
                            continue
                        pair = pairs[0]
                        vol = float(pair.get("volume", {}).get("h24", 0))
                        change = float(pair.get("priceChange", {}).get("h24", 0))
                        liquidity = float(pair.get("liquidity", {}).get("usd", 0))
                        # Only alert on tokens with real activity
                        if vol >= 10000 and liquidity >= 5000:
                            trending.append({
                                "name": pair.get("baseToken", {}).get("name", "Unknown"),
                                "symbol": pair.get("baseToken", {}).get("symbol", "???"),
                                "price": float(pair.get("priceUsd", 0)),
                                "change_24h": change,
                                "volume_24h": vol,
                                "liquidity": liquidity,
                                "address": addr,
                                "url": pair.get("url", ""),
                            })
                except Exception:
                    continue

            # Sort by volume
            trending.sort(key=lambda x: x["volume_24h"], reverse=True)
            return trending[:5]
    except Exception as e:
        print(f"\u26a0\ufe0f Base token scan failed: {e}")
        return []


def is_admin(user) -> bool:
    """Check if user is admin \u2014 by chat ID first, then username."""
    if ADMIN_CHAT_ID and str(user.id) == str(ADMIN_CHAT_ID):
        return True
    return user.username == ADMIN_USERNAME.lstrip("@")


def format_price(price_val: float, change: float) -> str:
    change_str = f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"
    arrow = "\ud83d\udcc8" if change >= 0 else "\ud83d\udcc9"
    return f"\ud83d\udcb5 GGB: ${price_val:.6f}\n{arrow} 24h: {change_str}"


def reset_gm_if_needed():
    global gm_tracker, gm_tracker_date
    today = datetime.now(timezone.utc).date()
    if gm_tracker_date != today:
        gm_tracker = {}
        gm_tracker_date = today


def update_gm_streak(user_id, name):
    """Track consecutive GM days for a user."""
    today = datetime.now(timezone.utc).date()
    if user_id not in gm_streaks:
        gm_streaks[user_id] = {"name": name, "current": 1, "best": 1, "last_date": today}
        return 1

    streak = gm_streaks[user_id]
    streak["name"] = name

    if streak["last_date"] == today:
        return streak["current"]  # Already counted today

    if streak["last_date"] == today - timedelta(days=1):
        streak["current"] += 1  # Consecutive day
    else:
        streak["current"] = 1   # Streak broken

    streak["best"] = max(streak["best"], streak["current"])
    streak["last_date"] = today
    return streak["current"]

# =============================================================================
# COMMANDS
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("\ud83d\udc02 Bull Quote",     callback_data="bull")],
        [InlineKeyboardButton("\ud83d\udcc8 GGB Price",      callback_data="price")],
        [InlineKeyboardButton("\ud83c\udfa8 Wallpaper Pack", url="https://goodgreenbull.gumroad.com")],
        [InlineKeyboardButton("\ud83d\uddbc\ufe0f NFT Drop",       callback_data="nft_info")],
        [InlineKeyboardButton("\ud83c\udf10 Website",        url="https://goodgreenbull.com")],
        [InlineKeyboardButton("\ud83d\udd4a\ufe0f Follow on X",    url="https://x.com/goodgreenbull")],
    ]
    await update.message.reply_text(
        "\ud83d\udc02\ud83d\udc9a *Good Green Bull*\n\n"
        "Built on Base. Built for builders.\n"
        "The bull that doesn't stop.\n\n"
        "Choose an option below \ud83d\udc47",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\ud83d\udcdc *GGB Bot Commands*\n\n"
        "/start \u2014 Open main menu\n"
        "/price \u2014 Live $GGB price + 24h change\n"
        "/bull \u2014 Random Beefy quote\n"
        "/gm \u2014 Say GM to the herd\n"
        "/leaderboard \u2014 Top GM senders today\n"
        "/streaks \u2014 Top GM streak holders\n"
        "/wallet `<address>` \u2014 Check GGB balance\n"
        "/token \u2014 Token info + contract\n"
        "/kit \u2014 GGB Builder Kit info\n"
        "/nft \u2014 NFT drop info\n"
        "/herd \u2014 Community stats\n"
        "/trending \u2014 Trending tokens on Base\n"
        "/help \u2014 Show this list\n\n"
        "\ud83d\udc64 *Admin only:*\n"
        "/daily \u2014 Trigger Beefy Daily push\n"
        "/revival \u2014 Send relaunch announcement\n"
        "/broadcast `<msg>` \u2014 Send message to group\n"
        "/settings \u2014 Admin panel",
        parse_mode="Markdown",
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price_val, change = await fetch_price_data()
    if price_val is None:
        await update.message.reply_text("\u26a0\ufe0f Could not fetch price right now. Try again shortly.")
        return
    await update.message.reply_text(
        f"{format_price(price_val, change)}\n\n"
        f"\ud83d\udcca Chart: https://tinyurl.com/GGBDex\n"
        f"\ud83d\udcc4 Contract: `{GGB_CONTRACT}`",
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

    # Update streak
    streak = update_gm_streak(user.id, name)
    streak_text = f"\n\ud83d\udd25 Streak: {streak} day{'s' if streak > 1 else ''}!" if streak >= 2 else ""

    responses = [
        f"GM {name} \ud83d\udc02\ud83d\udc9a Build mode is ON.{streak_text}",
        f"GM {name} \ud83d\udc9a The herd is awake. Let's move.{streak_text}",
        f"GM {name} \ud83d\udc02 Another day. Another rep. Lock in.{streak_text}",
        f"GM {name} \ud83d\udc9a Still here. Still building. That's the edge.{streak_text}",
        f"GM {name} \ud83d\udc02\ud83d\udc9a Herd strong. Ship something today.{streak_text}",
    ]
    await update.message.reply_text(random.choice(responses))


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_gm_if_needed()
    if not gm_tracker:
        await update.message.reply_text(
            "No GMs logged yet today. Be the first \ud83d\udc02\ud83d\udc9a\nType /gm to get on the board."
        )
        return
    sorted_users = sorted(gm_tracker.items(), key=lambda x: x[1]["count"], reverse=True)
    medals = ["\ud83e\udd47", "\ud83e\udd48", "\ud83e\udd49"] + ["\ud83d\udc02"] * 7
    lines = ["\ud83c\udfc6 *GM Leaderboard \u2014 Today*\n"]
    for i, (uid, data) in enumerate(sorted_users[:10]):
        lines.append(f"{medals[i]} {data['name']} \u2014 {data['count']} GMs")
    lines.append("\nType /gm to get on the board \ud83d\udc9a")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def streaks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top GM streak holders."""
    if not gm_streaks:
        await update.message.reply_text("No streaks yet. Say /gm every day to build yours \ud83d\udd25")
        return
    sorted_streaks = sorted(gm_streaks.items(), key=lambda x: x[1]["current"], reverse=True)
    medals = ["\ud83e\udd47", "\ud83e\udd48", "\ud83e\udd49"] + ["\ud83d\udd25"] * 7
    lines = ["\ud83d\udd25 *GM Streak Leaderboard*\n"]
    for i, (uid, data) in enumerate(sorted_streaks[:10]):
        lines.append(f"{medals[i]} {data['name']} \u2014 {data['current']} day streak (best: {data['best']})")
    lines.append("\nSay /gm every day to build your streak \ud83d\udc9a")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/wallet <Base wallet address>`", parse_mode="Markdown")
        return
    address = context.args[0]
    balance = await fetch_wallet_balance(address)
    if balance is None:
        await update.message.reply_text("\u26a0\ufe0f Could not fetch wallet data. Check the address and try again.")
        return
    price_val, _ = await fetch_price_data()
    usd_str = f"\ud83d\udcb5 \u2248 ${balance * price_val:,.2f} USD" if price_val else ""
    short_addr = f"{address[:6]}...{address[-4:]}"
    await update.message.reply_text(
        f"\ud83d\udc5b Wallet: `{short_addr}`\n"
        f"\ud83d\udc02 GGB Balance: {balance:,.2f} GGB\n"
        f"{usd_str}",
        parse_mode="Markdown",
    )


async def token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\ud83d\udcc8 *Good Green Bull \u2014 Token Info*\n\n"
        "Name: Good Green Bull\n"
        "Symbol: $GGB\n"
        "Chain: Base\n"
        "Decimals: 18\n"
        f"Contract: `{GGB_CONTRACT}`\n\n"
        f"\ud83d\udd17 https://basescan.org/token/{GGB_CONTRACT}",
        parse_mode="Markdown",
    )


async def kit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\ud83d\udee0\ufe0f *GGB Builder Kit*\n\n"
        "The full content and brand system behind Good Green Bull \u2014 "
        "packaged for builders running their own brand on Base or Farcaster.\n\n"
        "\u2705 Content calendar + rotation framework\n"
        "\u2705 30 social post templates \u2014 X + Farcaster\n"
        "\u2705 10 AI image prompts with guardrails\n"
        "\u2705 Brand voice guide\n"
        "\u2705 Mascot design rules\n"
        "\u2705 Monetisation framework\n"
        "\u2705 Quick-start checklist\n\n"
        "\ud83d\udcb0 \u00a335 \u2014 Instant download\n"
        "\ud83d\udd17 https://goodgreenbull.gumroad.com",
        parse_mode="Markdown",
    )


async def nft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\ud83c\udfa8 *Beefy Prime: Series One*\n\n"
        "50 cinematic 1/1 pieces. Base chain.\n"
        "The founding archive of Good Green Bull.\n\n"
        "Holders receive:\n"
        "\u2014 Exclusive founder role in this group\n"
        "\u2014 First access to all future drops\n\n"
        "\ud83d\udfe1 Status: Coming Soon\n"
        "Follow @goodgreenbull on X for the mint date \ud83d\udc02\ud83d\udc9a",
        parse_mode="Markdown",
    )


async def herd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count_str = "Growing daily"
    if TELEGRAM_GROUP_ID:
        try:
            count = await context.bot.get_chat_member_count(int(TELEGRAM_GROUP_ID))
            count_str = f"{count:,} members"
        except Exception:
            pass
    lines = [
        "The herd is building. \ud83d\udc02\ud83d\udc9a",
        "Bulls don't fold when it gets quiet. \ud83d\udc02\ud83d\udc9a",
        "Still here. Still locked in. \ud83d\udc02\ud83d\udc9a",
        "Early is a choice. So is being late. \ud83d\udc02\ud83d\udc9a",
        "The quiet ones are the dangerous ones. \ud83d\udc02\ud83d\udc9a",
    ]
    await update.message.reply_text(
        f"\ud83d\udc02 *The GGB Herd*\n\n"
        f"Members: {count_str}\n"
        f"{random.choice(lines)}\n\n"
        f"Share the group \ud83d\udc47\nhttps://t.me/goodgreenbull",
        parse_mode="Markdown",
    )


async def trending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trending tokens on Base chain."""
    await update.message.reply_text("\ud83d\udd0d Scanning Base for trending tokens...")
    tokens = await fetch_base_trending_tokens()
    if not tokens:
        await update.message.reply_text("No trending tokens found right now. Check back later \ud83d\udc02")
        return
    lines = ["\ud83d\udcc8 *Trending on Base*\n"]
    for t in tokens:
        change_str = f"+{t['change_24h']:.1f}%" if t['change_24h'] >= 0 else f"{t['change_24h']:.1f}%"
        arrow = "\ud83d\udfe2" if t['change_24h'] >= 0 else "\ud83d\udd34"
        lines.append(
            f"{arrow} *{t['symbol']}* \u2014 ${t['price']:.6f}\n"
            f"   Vol: ${t['volume_24h']:,.0f} | {change_str}\n"
            f"   `{t['address'][:8]}...{t['address'][-4:]}`"
        )
    lines.append("\n_Always DYOR. Not financial advice._ \ud83d\udc02\ud83d\udc9a")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("\u26d4 Admin only.")
        return
    keyboard = [
        [InlineKeyboardButton("\ud83d\udce4 Trigger Daily Post",  callback_data="trigger_daily")],
        [InlineKeyboardButton("\ud83d\udce3 Send Revival Blast",  callback_data="send_revival")],
        [InlineKeyboardButton("\ud83d\udcc8 Scan Base Tokens Now", callback_data="scan_tokens")],
    ]
    await update.message.reply_text(
        "\u2699\ufe0f *Admin Settings*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("\u26d4 Admin only.")
        return
    await update.message.reply_text("\ud83d\udce4 Sending Beefy Daily now...")
    await send_beefy_daily()


async def revival_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("\u26d4 Admin only.")
        return
    await update.message.reply_text("\ud83d\udce3 Sending revival blast now...")
    await send_revival_blast()


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only \u2014 send a custom message to the group."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("\u26d4 Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/broadcast Your message here`", parse_mode="Markdown")
        return
    msg = " ".join(context.args)
    if not TELEGRAM_GROUP_ID:
        await update.message.reply_text("\u26a0\ufe0f TELEGRAM_GROUP_ID not set.")
        return
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=msg)
        await update.message.reply_text("\u2705 Broadcast sent.")
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Failed: {e}")

# =============================================================================
# SCHEDULED POSTS
# =============================================================================

async def send_beefy_daily():
    """Fires every day at 08:00 UTC \u2014 themed content calendar post."""
    if not TELEGRAM_GROUP_ID:
        print("\u26a0\ufe0f TELEGRAM_GROUP_ID not set. Skipping.")
        return

    price_val, change = await fetch_price_data()
    price_line = (
        f"$GGB: ${price_val:.6f} | {'+' if change >= 0 else ''}{change:.2f}% 24h"
        if price_val else "$GGB: Price unavailable"
    )

    # Get today's themed content
    weekday = datetime.now(timezone.utc).weekday()
    theme = DAILY_THEMES[weekday]
    prompt = random.choice(theme["prompts"])

    # Get rotating CTA
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    cta = rotating_ctas[day_of_year % len(rotating_ctas)]

    msg = (
        f"GM Herd \ud83d\udc02\ud83d\udc9a\n\n"
        f"{theme['title']}\n\n"
        f"{get_bull_quote()}\n\n"
        f"{price_line}\n\n"
        f"{prompt}\n\n"
        f"{cta}"
    )
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=msg)
        print(f"\u2705 Daily post sent: {theme['title']}")
    except Exception as e:
        print(f"\u26a0\ufe0f Daily post failed: {e}")


async def send_discussion_topic():
    """Fires every day at 12:00 UTC \u2014 midday conversation starter."""
    if not TELEGRAM_GROUP_ID:
        return
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    topic = discussion_topics[day_of_year % len(discussion_topics)]
    msg = f"\ud83d\udcac *Midday Discussion*\n\n{topic}"
    try:
        await application.bot.send_message(
            chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown"
        )
    except Exception as e:
        print(f"\u26a0\ufe0f Discussion topic failed: {e}")


async def send_weekly_engagement():
    """Fires every Monday at 09:00 UTC."""
    if not TELEGRAM_GROUP_ID:
        return
    week_num = datetime.now(timezone.utc).isocalendar()[1]
    question = weekly_questions[week_num % len(weekly_questions)]
    msg = (
        f"\ud83d\udc02 *Builder Monday*\n\n"
        f"{question}\n\n"
        f"Best answer gets a shout from @goodgreenbull \ud83d\udc9a"
    )
    try:
        await application.bot.send_message(
            chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown"
        )
    except Exception as e:
        print(f"\u26a0\ufe0f Weekly post failed: {e}")


async def send_revival_blast():
    """One-time relaunch message. Admin-triggered."""
    if not TELEGRAM_GROUP_ID:
        return
    msg = (
        "\ud83d\udc02\ud83d\udc9a *GGB IS BACK.*\n\n"
        "Beefy's been in build mode.\n"
        "Now we move.\n\n"
        "What's coming:\n"
        "\ud83c\udfa8 Wallpaper Pack \u2014 dropping soon\n"
        "\ud83d\uddbc\ufe0f Beefy Prime: Series One NFTs \u2014 Base chain\n"
        "\ud83d\udee0\ufe0f GGB Builder Kit \u2014 for builders running their own brand\n\n"
        "New content. New products. New energy.\n\n"
        "If you're still here \u2014 you're the founding herd.\n"
        "The ones who stayed get rewarded first.\n\n"
        "We move. \ud83d\udc02\ud83d\udc9a\n\n"
        "Follow: https://x.com/goodgreenbull\n"
        "Farcaster: https://warpcast.com/goodgreenbull"
    )
    try:
        await application.bot.send_message(
            chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown"
        )
    except Exception as e:
        print(f"\u26a0\ufe0f Revival blast failed: {e}")


async def scan_base_tokens():
    """Scheduled scan \u2014 posts trending Base tokens to the group every 6 hours."""
    if not TELEGRAM_GROUP_ID:
        return

    tokens = await fetch_base_trending_tokens()
    if not tokens:
        return

    # Filter out already alerted tokens
    new_tokens = [t for t in tokens if t["address"] not in alerted_tokens]
    if not new_tokens:
        return

    lines = ["\ud83d\udcc8 *Trending on Base right now*\n"]
    for t in new_tokens[:3]:
        change_str = f"+{t['change_24h']:.1f}%" if t['change_24h'] >= 0 else f"{t['change_24h']:.1f}%"
        arrow = "\ud83d\udfe2" if t['change_24h'] >= 0 else "\ud83d\udd34"
        lines.append(
            f"{arrow} *{t['symbol']}* \u2014 ${t['price']:.6f}\n"
            f"   Vol: ${t['volume_24h']:,.0f} | {change_str}"
        )
        alerted_tokens.add(t["address"])
    lines.append("\n_DYOR. Not financial advice._ \ud83d\udc02\ud83d\udc9a")

    try:
        await application.bot.send_message(
            chat_id=int(TELEGRAM_GROUP_ID), text="\n".join(lines), parse_mode="Markdown"
        )
    except Exception as e:
        print(f"\u26a0\ufe0f Token scan post failed: {e}")

    # Keep alerted set from growing forever (clear after 200)
    if len(alerted_tokens) > 200:
        alerted_tokens.clear()


async def check_milestones():
    """Check if member count crossed a milestone. Runs every hour."""
    global last_milestone
    if not TELEGRAM_GROUP_ID:
        return
    try:
        count = await application.bot.get_chat_member_count(int(TELEGRAM_GROUP_ID))
        for m in MILESTONES:
            if count >= m and last_milestone < m:
                last_milestone = m
                msg = (
                    f"\ud83c\udf89\ud83d\udc02 *MILESTONE: {m} MEMBERS!*\n\n"
                    f"The herd just hit {m}. That's {m} builders locked in.\n\n"
                    f"We're just getting started. Share the group \ud83d\udc47\n"
                    f"https://t.me/goodgreenbull\n\n"
                    f"Herd strong. We move. \ud83d\udc02\ud83d\udc9a"
                )
                await application.bot.send_message(
                    chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown"
                )
                break
    except Exception as e:
        print(f"\u26a0\ufe0f Milestone check failed: {e}")

# =============================================================================
# MESSAGE HANDLERS
# =============================================================================

async def handle_gm_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responds to natural GM messages in chat."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip().lower()
    if text in ("gm", "gm!", "gm \ud83d\udc02", "gm \ud83d\udc9a", "gm \ud83d\udc02\ud83d\udc9a", "good morning"):
        reset_gm_if_needed()
        user = update.effective_user
        name = user.first_name or "Bull"
        if user.id not in gm_tracker:
            gm_tracker[user.id] = {"name": name, "count": 0}
        gm_tracker[user.id]["count"] += 1
        streak = update_gm_streak(user.id, name)
        streak_text = f" \ud83d\udd25 {streak}-day streak!" if streak >= 2 else ""
        responses = [
            f"GM {name} \ud83d\udc02\ud83d\udc9a{streak_text}",
            f"GM {name} \ud83d\udc9a Lock in.{streak_text}",
            f"GM {name} \ud83d\udc02 Build something today.{streak_text}",
            f"GM {name} \ud83d\udc9a Herd strong.{streak_text}",
        ]
        await update.message.reply_text(random.choice(responses))


async def handle_reactive_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """React to keywords in group chat with brand-voice replies."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip().lower()

    # Don't reply to every message \u2014 only trigger on short messages
    if len(text) > 100:
        return

    # Don't reply to commands
    if text.startswith("/"):
        return

    # Check for keyword matches (only reply ~30% of the time to avoid spam)
    for keyword, replies in REACTIVE_REPLIES.items():
        if keyword in text.split():  # Match whole words only
            if random.random() < 0.30:  # 30% reply rate
                await update.message.reply_text(random.choice(replies))
            return  # Only match one keyword per message


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
                f"\ud83d\udc02\ud83d\udc9a Welcome to the herd, {name}!\n\n"
                f"Good Green Bull is a digital brand and builder community on Base.\n\n"
                f"Start here:\n"
                f"\ud83d\udcc8 /price \u2014 Live $GGB price\n"
                f"\ud83d\udc02 /bull \u2014 Get a Beefy quote\n"
                f"\ud83d\udc4b /gm \u2014 Say GM to the herd\n"
                f"\ud83d\udee0\ufe0f /kit \u2014 GGB Builder Kit\n"
                f"\ud83c\udfa8 /nft \u2014 Upcoming NFT drop\n\n"
                f"Follow us on X \ud83d\udc49 https://x.com/goodgreenbull\n\n"
                f"Herd strong. We move. \ud83d\udc02\ud83d\udc9a"
            ),
        )


async def detect_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mutes users sending more than 5 messages in 10 seconds."""
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    now = datetime.now(timezone.utc)
    window = timedelta(seconds=10)
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
            await context.bot.send_message(chat_id, text="\u26a0\ufe0f User muted 10 mins for spam.")
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
            await query.edit_message_text("\u26a0\ufe0f Could not fetch price right now.")
            return
        await query.edit_message_text(
            f"{format_price(price_val, change)}\n\n\ud83d\udcca https://tinyurl.com/GGBDex"
        )
    elif query.data == "nft_info":
        await query.edit_message_text(
            "\ud83c\udfa8 Beefy Prime: Series One\n\n"
            "50 cinematic 1/1 pieces. Base chain.\n"
            "Status: Coming Soon \ud83d\udfe1\n\n"
            "Follow @goodgreenbull on X for the mint date \ud83d\udc02\ud83d\udc9a"
        )
    elif query.data == "trigger_daily":
        await query.edit_message_text("\ud83d\udce4 Sending daily post...")
        await send_beefy_daily()
    elif query.data == "send_revival":
        await query.edit_message_text("\ud83d\udce3 Sending revival blast...")
        await send_revival_blast()
    elif query.data == "scan_tokens":
        await query.edit_message_text("\ud83d\udd0d Scanning Base tokens...")
        await scan_base_tokens()

# =============================================================================
# WEBHOOK ROUTES
# =============================================================================

@app.route("/", methods=["GET"])
async def home():
    return "\ud83d\udc02 Beefy Bot v2 \u2014 GGB Brand Engine. Built on Base."


@app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    data = await request.get_json(force=True)
    update = Update.de_json(data, application.bot)
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
    application.add_handler(CommandHandler("streaks",     streaks_command))
    application.add_handler(CommandHandler("wallet",      wallet))
    application.add_handler(CommandHandler("token",       token))
    application.add_handler(CommandHandler("kit",         kit))
    application.add_handler(CommandHandler("nft",         nft))
    application.add_handler(CommandHandler("herd",        herd))
    application.add_handler(CommandHandler("trending",    trending_command))
    application.add_handler(CommandHandler("daily",       daily_command))
    application.add_handler(CommandHandler("revival",     revival_command))
    application.add_handler(CommandHandler("broadcast",   broadcast_command))
    application.add_handler(CommandHandler("settings",    settings))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gm_text), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reactive_replies), group=2)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, detect_spam), group=3)

# =============================================================================
# STARTUP
# =============================================================================

async def on_startup():
    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print(f"\u2705 Webhook set: {WEBHOOK_URL}")

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(send_beefy_daily,       "cron", hour=8, minute=0)
    scheduler.add_job(send_discussion_topic,  "cron", hour=12, minute=0)
    scheduler.add_job(send_weekly_engagement, "cron", day_of_week="mon", hour=9, minute=0)
    scheduler.add_job(scan_base_tokens,       "interval", hours=6)
    scheduler.add_job(check_milestones,       "interval", hours=1)
    scheduler.start()
    print("\u2705 Scheduler: Daily 08:00 | Discussion 12:00 | Monday 09:00 | Token scan 6h | Milestones 1h")
    print("\ud83d\udc02 Beefy Bot v2 \u2014 Brand Engine \u2014 LIVE")


if __name__ == "__main__":
    register_handlers()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(on_startup())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

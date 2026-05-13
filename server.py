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
# CONTENT CALENDAR — Daily themed posts
# =============================================================================

DAILY_THEMES = {
    0: {
        "title": "💪 Motivation Monday",
        "prompts": [
            "New week. New reps. What's the one thing you're locking in this week? 👇",
            "Monday energy: name one thing you're shipping before Friday 🛠️",
            "The week belongs to the ones who start. What's your first move? 🐂",
            "Most people plan on Monday and quit by Wednesday. Not this herd. What's yours? 💚",
            "Momentum starts now. What are you building this week? 👇",
        ],
    },
    1: {
        "title": "🧠 Alpha Tuesday",
        "prompts": [
            "Drop one thing you learned recently that changed how you think 👇",
            "Best thread, podcast, or article you consumed this week? Share it 🧠",
            "What's one alpha that most people are sleeping on right now? 👇",
            "Share a tool, strategy, or insight that levelled you up recently 💚",
            "What's something you know now that you wish you knew 6 months ago? 👇",
        ],
    },
    2: {
        "title": "🛠️ Builder Wednesday",
        "prompts": [
            "Midweek check: what have you shipped so far this week? 👇",
            "Show your work. Screenshot, link, or progress update — drop it 🛠️",
            "Builder Wednesday: what's the hardest part of what you're building right now? 👇",
            "What's one thing on your build list that keeps getting pushed back? 🐂",
            "Share what you're working on. No pitch, just progress 💚",
        ],
    },
    3: {
        "title": "📈 Base Thursday",
        "prompts": [
            "What's the most interesting project you've seen on Base lately? 👇",
            "Base ecosystem check: what token or dApp caught your eye this week? 📈",
            "If you could only hold 3 Base projects long term — what makes the cut? 🐂",
            "What does Base need more of right now? Builders, speak up 👇",
            "Drop a Base project that deserves more attention 💚",
        ],
    },
    4: {
        "title": "🔥 Flex Friday",
        "prompts": [
            "It's Friday. What's your W this week? Big or small, drop it 👇 🔥",
            "Flex Friday: what did you accomplish that you're proud of? 💚",
            "End the week strong. What's one thing that went right? 🐂",
            "Friday flex: show a win, a ship, or a lesson from this week 👇",
            "The weekend is earned. What did you build to deserve yours? 🔥",
        ],
    },
    5: {
        "title": "🌿 Chill Saturday",
        "prompts": [
            "Saturday vibes. What are you recharging with today? 🌿",
            "Builders need rest too. What's your go-to way to switch off? 👇",
            "Weekend mode. Reading, gaming, touching grass — what's the move? 🐂💚",
            "No hustle today. Just vibes. What's good in your world? 🌿",
            "Saturday reset. What are you grateful for this week? 💚",
        ],
    },
    6: {
        "title": "📋 Sunday Reset",
        "prompts": [
            "Sunday planning: what's the #1 priority for next week? 👇",
            "Reset day. What are you carrying forward and what are you dropping? 🐂",
            "Sunday question: what would make next week a 10/10? 💚",
            "End of week. Rate your week 1-10 and tell us why 👇",
            "Tomorrow starts a new cycle. What's the play? 📋",
        ],
    },
}

# =============================================================================
# WEEKLY ENGAGEMENT QUESTIONS
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
# DISCUSSION TOPICS — Posted at 12:00 UTC
# =============================================================================

discussion_topics = [
    "Hot take time: what's one popular crypto opinion you disagree with? 🔥",
    "If you had $100 to put into one Base token today — where's it going? 👇",
    "Builders vs traders — which one are you and why? 🐂",
    "What's the biggest mistake you've made in crypto? No judgment 💚",
    "AI + crypto — overrated, underrated, or perfectly rated? 🧠",
    "Name a project that died but had a great idea worth reviving 👇",
    "What separates a good community from a dead one? Real answers only 🐂",
    "If you could mass-adopt ONE thing about Web3 — what would it be? 💚",
    "Unpopular opinion: memecoins are ______ . Fill in the blank 👇",
    "Best trade you ever made? Worst? Drop both 📈📉",
    "What would you build if money and time weren't a factor? 🛠️",
    "Is on-chain reputation the next big thing or just hype? 🧠",
    "DeFi, NFTs, or social — what's the next big wave? 👇",
    "What's one thing the crypto space needs to stop doing? 🔥",
    "If you had to explain Base to your nan — how would you do it? 🐂💚",
    "What's your daily crypto routine? Walk us through it 👇",
    "One year from now — where do you see yourself? Be specific 💚",
    "What project outside of crypto inspires how you build? 🛠️",
    "Would you rather have 10k followers or 100 paying customers? 👇",
    "What's the most underrated skill in crypto right now? 🧠",
]

# =============================================================================
# ROTATING CTAs
# =============================================================================

rotating_ctas = [
    "🛠️ Build your own brand on Base → https://goodgreenbull.gumroad.com",
    "🎨 Beefy Prime NFTs dropping soon — follow @goodgreenbull on X for the date 🐂",
    "🌐 goodgreenbull.com — the home of the herd 💚",
    "🕊️ Follow the bull on X → https://x.com/goodgreenbull",
    "📣 Share this group with a builder → https://t.me/goodgreenbull",
    "🛠️ GGB Builder Kit — templates, prompts, brand system → https://goodgreenbull.gumroad.com",
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
        "Base is the move. 🐂💚",
        "Building on Base hits different. 💚",
        "Base chain, best chain. 🛠️🐂",
    ],
    "token": [
        "Tokens come and go. Builders stay. 🐂",
        "Always DYOR. The herd is smart. 💚",
        "What token's got your attention? 👇",
    ],
    "build": [
        "Builder energy detected. 🛠️💚",
        "Ship it. Fix it. Ship again. 🐂",
        "That's the builder mindset. Lock in. 💚",
    ],
    "ship": [
        "Ship > talk. Always. 🛠️🐂",
        "Shipped? Respect. 💚",
        "The ones who ship are the ones who win. 🐂",
    ],
    "ggb": [
        "GGB 🐂💚 Herd strong.",
        "Good Green Bull. Built to last. 💚",
        "The bull that doesn't stop. 🐂💚",
    ],
    "bull": [
        "Bull mode activated. 🐂💚",
        "The herd stays bullish. 💚",
        "Beefy approves. 🐂",
    ],
    "wagmi": [
        "WAGMI — but only if you keep building. 🐂💚",
        "WAGMI. Herd strong. 💚",
    ],
    "ngmi": [
        "Not with that attitude. Lock in. 🐂",
        "Nah, we don't do that here. WAGMI. 💚",
    ],
    "gn": [
        "GN bull. Rest up, we build tomorrow. 🐂💚",
        "GN 💚 See you at the next GM.",
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
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.dexscreener.com/token-profiles/latest/v1",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                profiles = await resp.json()

            base_tokens = [p for p in profiles if p.get("chainId") == "base"][:20]
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

            trending.sort(key=lambda x: x["volume_24h"], reverse=True)
            return trending[:5]
    except Exception as e:
        print(f"⚠️ Base token scan failed: {e}")
        return []


def is_admin(user) -> bool:
    if ADMIN_CHAT_ID and str(user.id) == str(ADMIN_CHAT_ID):
        return True
    return user.username == ADMIN_USERNAME.lstrip("@")


def format_price(price_val: float, change: float) -> str:
    change_str = f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"
    arrow = "📈" if change >= 0 else "📉"
    return f"💵 GGB: ${price_val:.6f}\n{arrow} 24h: {change_str}"


def reset_gm_if_needed():
    global gm_tracker, gm_tracker_date
    today = datetime.now(timezone.utc).date()
    if gm_tracker_date != today:
        gm_tracker = {}
        gm_tracker_date = today


def update_gm_streak(user_id, name):
    today = datetime.now(timezone.utc).date()
    if user_id not in gm_streaks:
        gm_streaks[user_id] = {"name": name, "current": 1, "best": 1, "last_date": today}
        return 1
    streak = gm_streaks[user_id]
    streak["name"] = name
    if streak["last_date"] == today:
        return streak["current"]
    if streak["last_date"] == today - timedelta(days=1):
        streak["current"] += 1
    else:
        streak["current"] = 1
    streak["best"] = max(streak["best"], streak["current"])
    streak["last_date"] = today
    return streak["current"]

# =============================================================================
# COMMANDS
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🐂 Bull Quote",     callback_data="bull")],
        [InlineKeyboardButton("📈 GGB Price",      callback_data="price")],
        [InlineKeyboardButton("🎨 Wallpaper Pack", url="https://goodgreenbull.gumroad.com")],
        [InlineKeyboardButton("🖼️ NFT Drop",       callback_data="nft_info")],
        [InlineKeyboardButton("🌐 Website",        url="https://goodgreenbull.com")],
        [InlineKeyboardButton("🕊️ Follow on X",    url="https://x.com/goodgreenbull")],
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
        "/streaks — Top GM streak holders\n"
        "/wallet `<address>` — Check GGB balance\n"
        "/token — Token info + contract\n"
        "/kit — GGB Builder Kit info\n"
        "/nft — NFT drop info\n"
        "/herd — Community stats\n"
        "/trending — Trending tokens on Base\n"
        "/help — Show this list\n\n"
        "👤 *Admin only:*\n"
        "/daily — Trigger Beefy Daily push\n"
        "/revival — Send relaunch announcement\n"
        "/broadcast `<msg>` — Send message to group\n"
        "/settings — Admin panel",
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
    user = update.effective_user
    name = user.first_name or "Bull"
    if user.id not in gm_tracker:
        gm_tracker[user.id] = {"name": name, "count": 0}
    gm_tracker[user.id]["count"] += 1
    streak = update_gm_streak(user.id, name)
    streak_text = f"\n🔥 Streak: {streak} day{'s' if streak > 1 else ''}!" if streak >= 2 else ""
    responses = [
        f"GM {name} 🐂💚 Build mode is ON.{streak_text}",
        f"GM {name} 💚 The herd is awake. Let's move.{streak_text}",
        f"GM {name} 🐂 Another day. Another rep. Lock in.{streak_text}",
        f"GM {name} 💚 Still here. Still building. That's the edge.{streak_text}",
        f"GM {name} 🐂💚 Herd strong. Ship something today.{streak_text}",
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
    medals = ["🥇", "🥈", "🥉"] + ["🐂"] * 7
    lines = ["🏆 *GM Leaderboard — Today*\n"]
    for i, (uid, data) in enumerate(sorted_users[:10]):
        lines.append(f"{medals[i]} {data['name']} — {data['count']} GMs")
    lines.append("\nType /gm to get on the board 💚")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def streaks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not gm_streaks:
        await update.message.reply_text("No streaks yet. Say /gm every day to build yours 🔥")
        return
    sorted_streaks = sorted(gm_streaks.items(), key=lambda x: x[1]["current"], reverse=True)
    medals = ["🥇", "🥈", "🥉"] + ["🔥"] * 7
    lines = ["🔥 *GM Streak Leaderboard*\n"]
    for i, (uid, data) in enumerate(sorted_streaks[:10]):
        lines.append(f"{medals[i]} {data['name']} — {data['current']} day streak (best: {data['best']})")
    lines.append("\nSay /gm every day to build your streak 💚")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/wallet <Base wallet address>`", parse_mode="Markdown")
        return
    address = context.args[0]
    balance = await fetch_wallet_balance(address)
    if balance is None:
        await update.message.reply_text("⚠️ Could not fetch wallet data. Check the address and try again.")
        return
    price_val, _ = await fetch_price_data()
    usd_str = f"💵 ≈ ${balance * price_val:,.2f} USD" if price_val else ""
    short_addr = f"{address[:6]}...{address[-4:]}"
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
            count = await context.bot.get_chat_member_count(int(TELEGRAM_GROUP_ID))
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


async def trending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning Base for trending tokens...")
    tokens = await fetch_base_trending_tokens()
    if not tokens:
        await update.message.reply_text("No trending tokens found right now. Check back later 🐂")
        return
    lines = ["📈 *Trending on Base*\n"]
    for t in tokens:
        change_str = f"+{t['change_24h']:.1f}%" if t['change_24h'] >= 0 else f"{t['change_24h']:.1f}%"
        arrow = "🟢" if t['change_24h'] >= 0 else "🔴"
        lines.append(
            f"{arrow} *{t['symbol']}* — ${t['price']:.6f}\n"
            f"   Vol: ${t['volume_24h']:,.0f} | {change_str}\n"
            f"   `{t['address'][:8]}...{t['address'][-4:]}`"
        )
    lines.append("\n_Always DYOR. Not financial advice._ 🐂💚")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    keyboard = [
        [InlineKeyboardButton("📤 Trigger Daily Post",  callback_data="trigger_daily")],
        [InlineKeyboardButton("📣 Send Revival Blast",  callback_data="send_revival")],
        [InlineKeyboardButton("📈 Scan Base Tokens Now", callback_data="scan_tokens")],
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


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/broadcast Your message here`", parse_mode="Markdown")
        return
    msg = " ".join(context.args)
    if not TELEGRAM_GROUP_ID:
        await update.message.reply_text("⚠️ TELEGRAM_GROUP_ID not set.")
        return
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=msg)
        await update.message.reply_text("✅ Broadcast sent.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed: {e}")

# =============================================================================
# SCHEDULED POSTS
# =============================================================================

async def send_beefy_daily():
    if not TELEGRAM_GROUP_ID:
        print("⚠️ TELEGRAM_GROUP_ID not set. Skipping.")
        return
    price_val, change = await fetch_price_data()
    price_line = (
        f"$GGB: ${price_val:.6f} | {'+' if change >= 0 else ''}{change:.2f}% 24h"
        if price_val else "$GGB: Price unavailable"
    )
    weekday = datetime.now(timezone.utc).weekday()
    theme = DAILY_THEMES[weekday]
    prompt = random.choice(theme["prompts"])
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    cta = rotating_ctas[day_of_year % len(rotating_ctas)]
    msg = (
        f"GM Herd 🐂💚\n\n"
        f"{theme['title']}\n\n"
        f"{get_bull_quote()}\n\n"
        f"{price_line}\n\n"
        f"{prompt}\n\n"
        f"{cta}"
    )
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=msg)
        print(f"✅ Daily post sent: {theme['title']}")
    except Exception as e:
        print(f"⚠️ Daily post failed: {e}")


async def send_discussion_topic():
    if not TELEGRAM_GROUP_ID:
        return
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    topic = discussion_topics[day_of_year % len(discussion_topics)]
    msg = f"💬 *Midday Discussion*\n\n{topic}"
    try:
        await application.bot.send_message(
            chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown"
        )
    except Exception as e:
        print(f"⚠️ Discussion topic failed: {e}")


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


async def scan_base_tokens():
    if not TELEGRAM_GROUP_ID:
        return
    tokens = await fetch_base_trending_tokens()
    if not tokens:
        return
    new_tokens = [t for t in tokens if t["address"] not in alerted_tokens]
    if not new_tokens:
        return
    lines = ["📈 *Trending on Base right now*\n"]
    for t in new_tokens[:3]:
        change_str = f"+{t['change_24h']:.1f}%" if t['change_24h'] >= 0 else f"{t['change_24h']:.1f}%"
        arrow = "🟢" if t['change_24h'] >= 0 else "🔴"
        lines.append(
            f"{arrow} *{t['symbol']}* — ${t['price']:.6f}\n"
            f"   Vol: ${t['volume_24h']:,.0f} | {change_str}"
        )
        alerted_tokens.add(t["address"])
    lines.append("\n_DYOR. Not financial advice._ 🐂💚")
    try:
        await application.bot.send_message(
            chat_id=int(TELEGRAM_GROUP_ID), text="\n".join(lines), parse_mode="Markdown"
        )
    except Exception as e:
        print(f"⚠️ Token scan post failed: {e}")
    if len(alerted_tokens) > 200:
        alerted_tokens.clear()


async def check_milestones():
    global last_milestone
    if not TELEGRAM_GROUP_ID:
        return
    try:
        count = await application.bot.get_chat_member_count(int(TELEGRAM_GROUP_ID))
        for m in MILESTONES:
            if count >= m and last_milestone < m:
                last_milestone = m
                msg = (
                    f"🎉🐂 *MILESTONE: {m} MEMBERS!*\n\n"
                    f"The herd just hit {m}. That's {m} builders locked in.\n\n"
                    f"We're just getting started. Share the group 👇\n"
                    f"https://t.me/goodgreenbull\n\n"
                    f"Herd strong. We move. 🐂💚"
                )
                await application.bot.send_message(
                    chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown"
                )
                break
    except Exception as e:
        print(f"⚠️ Milestone check failed: {e}")

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
        streak = update_gm_streak(user.id, name)
        streak_text = f" 🔥 {streak}-day streak!" if streak >= 2 else ""
        responses = [
            f"GM {name} 🐂💚{streak_text}",
            f"GM {name} 💚 Lock in.{streak_text}",
            f"GM {name} 🐂 Build something today.{streak_text}",
            f"GM {name} 💚 Herd strong.{streak_text}",
        ]
        await update.message.reply_text(random.choice(responses))


async def handle_reactive_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip().lower()
    if len(text) > 100:
        return
    if text.startswith("/"):
        return
    for keyword, replies in REACTIVE_REPLIES.items():
        if keyword in text.split():
            if random.random() < 0.30:
                await update.message.reply_text(random.choice(replies))
            return


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
    elif query.data == "trigger_daily":
        await query.edit_message_text("📤 Sending daily post...")
        await send_beefy_daily()
    elif query.data == "send_revival":
        await query.edit_message_text("📣 Sending revival blast...")
        await send_revival_blast()
    elif query.data == "scan_tokens":
        await query.edit_message_text("🔍 Scanning Base tokens...")
        await scan_base_tokens()

# =============================================================================
# WEBHOOK ROUTES
# =============================================================================

@app.route("/", methods=["GET"])
async def home():
    return "🐂 Beefy Bot v2 — GGB Brand Engine. Built on Base."


@app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    data = await request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "OK"

# =============================================================================
# HANDLER REGISTRATION — runs at module import (required for Hypercorn)
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

# Register handlers immediately so they're ready when Hypercorn imports this module
register_handlers()

# =============================================================================
# STARTUP — @app.before_serving runs when Hypercorn starts serving
# =============================================================================

@app.before_serving
async def on_startup():
    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook set: {WEBHOOK_URL}")

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(send_beefy_daily,       "cron", hour=8, minute=0)
    scheduler.add_job(send_discussion_topic,  "cron", hour=12, minute=0)
    scheduler.add_job(send_weekly_engagement, "cron", day_of_week="mon", hour=9, minute=0)
    scheduler.add_job(scan_base_tokens,       "interval", hours=6)
    scheduler.add_job(check_milestones,       "interval", hours=1)
    scheduler.start()
    print("✅ Scheduler: Daily 08:00 | Discussion 12:00 | Monday 09:00 | Token scan 6h | Milestones 1h")
    print("🐂 Beefy Bot v2 — Brand Engine — LIVE")

# =============================================================================
# GGB BEEFY BOT v2.1 — server.py
# Good Green Bull | Built on Base | Brand Engine + Alpha Discovery
# =============================================================================

import os
import hashlib
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
from scanner import ScannerConfig, ScannerService, SQLiteState
from scanner.alerts import format_alert
from scanner.models import Candidate, MarketSnapshot, ScoreResult

TOKEN             = os.getenv("BOT_TOKEN")
ADMIN_USERNAME    = os.getenv("ADMIN_USERNAME", "BeefytheBull")
ADMIN_CHAT_ID     = os.getenv("ADMIN_CHAT_ID")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET") or hashlib.sha256(
    (TOKEN or "unset").encode("utf-8")
).hexdigest()[:32]
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://beefy-bot.onrender.com").rstrip("/")
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

app         = Quart(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# STATE
user_spam_tracker   = {}
recent_bull_indices = []
gm_tracker          = {}
gm_tracker_date     = None
gm_streaks          = {}
last_milestone      = 0
alerted_tokens      = set()
poll_votes          = {}
volume_snapshots    = {}
scanner_config      = ScannerConfig.from_env()
scanner_service     = None

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

DAILY_THEMES = {
    0: {"title": "💪 Motivation Monday", "prompts": ["New week. New reps. What's the one thing you're locking in this week? 👇", "Monday energy: name one thing you're shipping before Friday 🛠️", "The week belongs to the ones who start. What's your first move? 🐂", "Most people plan on Monday and quit by Wednesday. Not this herd. What's yours? 💚", "Momentum starts now. What are you building this week? 👇"]},
    1: {"title": "🧠 Alpha Tuesday", "prompts": ["Drop one thing you learned recently that changed how you think 👇", "Best thread, podcast, or article you consumed this week? Share it 🧠", "What's one alpha that most people are sleeping on right now? 👇", "Share a tool, strategy, or insight that levelled you up recently 💚", "What's something you know now that you wish you knew 6 months ago? 👇"]},
    2: {"title": "🛠️ Builder Wednesday", "prompts": ["Midweek check: what have you shipped so far this week? 👇", "Show your work. Screenshot, link, or progress update — drop it 🛠️", "Builder Wednesday: what's the hardest part of what you're building right now? 👇", "What's one thing on your build list that keeps getting pushed back? 🐂", "Share what you're working on. No pitch, just progress 💚"]},
    3: {"title": "📈 Base Thursday", "prompts": ["What's the most interesting project you've seen on Base lately? 👇", "Base ecosystem check: what token or dApp caught your eye this week? 📈", "If you could only hold 3 Base projects long term — what makes the cut? 🐂", "What does Base need more of right now? Builders, speak up 👇", "Drop a Base project that deserves more attention 💚"]},
    4: {"title": "🔥 Flex Friday", "prompts": ["It's Friday. What's your W this week? Big or small, drop it 👇 🔥", "Flex Friday: what did you accomplish that you're proud of? 💚", "End the week strong. What's one thing that went right? 🐂", "Friday flex: show a win, a ship, or a lesson from this week 👇", "The weekend is earned. What did you build to deserve yours? 🔥"]},
    5: {"title": "🌿 Chill Saturday", "prompts": ["Saturday vibes. What are you recharging with today? 🌿", "Builders need rest too. What's your go-to way to switch off? 👇", "Weekend mode. Reading, gaming, touching grass — what's the move? 🐂💚", "No hustle today. Just vibes. What's good in your world? 🌿", "Saturday reset. What are you grateful for this week? 💚"]},
    6: {"title": "📋 Sunday Reset", "prompts": ["Sunday planning: what's the #1 priority for next week? 👇", "Reset day. What are you carrying forward and what are you dropping? 🐂", "Sunday question: what would make next week a 10/10? 💚", "End of week. Rate your week 1-10 and tell us why 👇", "Tomorrow starts a new cycle. What's the play? 📋"]},
}

weekly_questions = ["What's the one thing you're shipping this week? Drop it below 🛠️", "Best Base project you've used this week? Go 👇", "If you had to cut everything except one project — what stays? 🐂", "What's one tool (AI or otherwise) that's genuinely changed how you build? 👇", "Biggest lesson from your last build? Keep it real 👇", "What would make you check this group every single day? Tell us 🐂💚", "One word that describes your build mindset this week 👇", "What's the most underrated thing happening on Base right now? 🐂", "If GGB dropped a product tomorrow — what would you want it to be? 👇", "What does winning look like for you in the next 90 days? 🐂💚"]

discussion_topics = ["Hot take time: what's one popular crypto opinion you disagree with? 🔥", "If you had $100 to put into one Base token today — where's it going? 👇", "Builders vs traders — which one are you and why? 🐂", "What's the biggest mistake you've made in crypto? No judgment 💚", "AI + crypto — overrated, underrated, or perfectly rated? 🧠", "Name a project that died but had a great idea worth reviving 👇", "What separates a good community from a dead one? Real answers only 🐂", "If you could mass-adopt ONE thing about Web3 — what would it be? 💚", "Unpopular opinion: memecoins are ______ . Fill in the blank 👇", "Best trade you ever made? Worst? Drop both 📈📉", "What would you build if money and time weren't a factor? 🛠️", "Is on-chain reputation the next big thing or just hype? 🧠", "DeFi, NFTs, or social — what's the next big wave? 👇", "What's one thing the crypto space needs to stop doing? 🔥", "If you had to explain Base to your nan — how would you do it? 🐂💚", "What's your daily crypto routine? Walk us through it 👇", "One year from now — where do you see yourself? Be specific 💚", "What project outside of crypto inspires how you build? 🛠️", "Would you rather have 10k followers or 100 paying customers? 👇", "What's the most underrated skill in crypto right now? 🧠"]

rotating_ctas = ["🕊️ Follow Beefy on X → https://x.com/BeefytheBull", "📣 Share this group with a builder → https://t.me/goodgreenbull", "🌐 goodgreenbull.com — the home of the herd 💚", "🐂 The bull that doesn't stop. Herd strong. 💚", "🛠️ Builders build. That's the culture. Lock in. 🐂"]

MILESTONES = [25, 50, 75, 100, 150, 200, 250, 500, 750, 1000, 2500, 5000, 10000]

REACTIVE_REPLIES = {
    "base": ["Base is the move. 🐂💚", "Building on Base hits different. 💚", "Base chain, best chain. 🛠️🐂"],
    "token": ["Tokens come and go. Builders stay. 🐂", "Always DYOR. The herd is smart. 💚", "What token's got your attention? 👇"],
    "build": ["Builder energy detected. 🛠️💚", "Ship it. Fix it. Ship again. 🐂", "That's the builder mindset. Lock in. 💚"],
    "ship": ["Ship > talk. Always. 🛠️🐂", "Shipped? Respect. 💚", "The ones who ship are the ones who win. 🐂"],
    "ggb": ["GGB 🐂💚 Herd strong.", "Good Green Bull. Built to last. 💚", "The bull that doesn't stop. 🐂💚"],
    "bull": ["Bull mode activated. 🐂💚", "The herd stays bullish. 💚", "Beefy approves. 🐂"],
    "beefy": ["Beefy's always watching. 🐂💚", "You rang? 🐂", "Beefy approves this message. 💚"],
    "wagmi": ["WAGMI — but only if you keep building. 🐂💚", "WAGMI. Herd strong. 💚"],
    "ngmi": ["Not with that attitude. Lock in. 🐂", "Nah, we don't do that here. WAGMI. 💚"],
    "gn": ["GN bull. Rest up, we build tomorrow. 🐂💚", "GN 💚 See you at the next GM."],
    "alpha": ["Alpha hunters welcome. 🧠🐂", "The herd finds alpha together. 💚", "Drop what you've found 👇"],
    "pump": ["DYOR before you ape. The herd is smart. 🐂", "Pump or substance? That's the question. 🧠"],
    "degen": ["Controlled degen is a skill. 🐂💚", "Degen with discipline. That's the way. 🧠"],
}

# === HELPERS ===

def get_bull_quote():
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

async def fetch_base_trending_tokens():
    """
    Fetch trending Base tokens using multiple DexScreener strategies:
    1. Try /token-boosts/top/v1 (actively boosted tokens)
    2. If that returns nothing for Base, search popular Base token names directly
    """
    trending = []
    seen_addrs = set()

    async with aiohttp.ClientSession() as session:
        # Strategy 1: Token boosts (actively promoted/trending)
        try:
            async with session.get("https://api.dexscreener.com/token-boosts/top/v1", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    boosts = await resp.json()
                    base_boosts = [b for b in boosts if b.get("chainId") == "base"][:15]
                    for b in base_boosts:
                        addr = b.get("tokenAddress", "")
                        if not addr or addr in seen_addrs:
                            continue
                        seen_addrs.add(addr)
                        try:
                            async with session.get(f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=aiohttp.ClientTimeout(total=8)) as resp2:
                                data = await resp2.json()
                                pairs = data.get("pairs", [])
                                if not pairs:
                                    continue
                                pair = pairs[0]
                                vol = float(pair.get("volume", {}).get("h24", 0))
                                liquidity = float(pair.get("liquidity", {}).get("usd", 0))
                                if vol >= 5000 and liquidity >= 2000:
                                    created = pair.get("pairCreatedAt", 0)
                                    age_days = (datetime.now(timezone.utc).timestamp() * 1000 - created) / 86400000 if created else 999
                                    trending.append({"name": pair.get("baseToken", {}).get("name", "Unknown"), "symbol": pair.get("baseToken", {}).get("symbol", "???"), "price": float(pair.get("priceUsd", 0)), "change_24h": float(pair.get("priceChange", {}).get("h24", 0)), "volume_24h": vol, "liquidity": liquidity, "address": addr, "url": pair.get("url", ""), "age_days": age_days})
                        except Exception:
                            continue
        except Exception as e:
            print(f"⚠️ Token boosts fetch failed: {e}")

        # Strategy 2: If we got fewer than 5, search popular Base token names
        if len(trending) < 5:
            search_terms = ["brett base", "degen base", "toshi base", "aero base", "higher base", "normie base", "bankr base", "mog base", "keycat base", "bald base"]
            random.shuffle(search_terms)
            for term in search_terms:
                if len(trending) >= 10:
                    break
                try:
                    async with session.get(f"https://api.dexscreener.com/latest/dex/search?q={term}", timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        base_pairs = [p for p in pairs if p.get("chainId") == "base"]
                        if not base_pairs:
                            continue
                        pair = base_pairs[0]
                        addr = pair.get("baseToken", {}).get("address", "")
                        if addr in seen_addrs:
                            continue
                        seen_addrs.add(addr)
                        vol = float(pair.get("volume", {}).get("h24", 0))
                        liquidity = float(pair.get("liquidity", {}).get("usd", 0))
                        if vol >= 5000 and liquidity >= 2000:
                            created = pair.get("pairCreatedAt", 0)
                            age_days = (datetime.now(timezone.utc).timestamp() * 1000 - created) / 86400000 if created else 999
                            trending.append({"name": pair.get("baseToken", {}).get("name", "Unknown"), "symbol": pair.get("baseToken", {}).get("symbol", "???"), "price": float(pair.get("priceUsd", 0)), "change_24h": float(pair.get("priceChange", {}).get("h24", 0)), "volume_24h": vol, "liquidity": liquidity, "address": addr, "url": pair.get("url", ""), "age_days": age_days})
                except Exception:
                    continue

    trending.sort(key=lambda x: x["volume_24h"], reverse=True)
    return trending[:10]

async def lookup_token(query):
    """
    Look up a token by name or address. Strips $ prefix.
    If first search fails, retries with 'base' appended to find Base chain tokens.
    """
    # Clean up the query
    query = query.strip().lstrip("$").strip()
    if not query:
        return None

    async with aiohttp.ClientSession() as session:
        # Determine if it's an address or a name search
        is_address = query.startswith("0x") and len(query) >= 10

        # Try up to 2 search strategies
        search_urls = []
        if is_address:
            search_urls.append(f"https://api.dexscreener.com/latest/dex/tokens/{query}")
        else:
            search_urls.append(f"https://api.dexscreener.com/latest/dex/search?q={query}")
            search_urls.append(f"https://api.dexscreener.com/latest/dex/search?q={query} base")

        for url in search_urls:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    if not pairs:
                        continue

                    # Prioritize Base chain pairs
                    base_pairs = [p for p in pairs if p.get("chainId") == "base"]
                    pair = base_pairs[0] if base_pairs else pairs[0]

                    created = pair.get("pairCreatedAt", 0)
                    age_days = (datetime.now(timezone.utc).timestamp() * 1000 - created) / 86400000 if created else None

                    return {
                        "name": pair.get("baseToken", {}).get("name", "Unknown"),
                        "symbol": pair.get("baseToken", {}).get("symbol", "???"),
                        "chain": pair.get("chainId", "unknown"),
                        "price": float(pair.get("priceUsd", 0)),
                        "change_5m": float(pair.get("priceChange", {}).get("m5", 0)),
                        "change_1h": float(pair.get("priceChange", {}).get("h1", 0)),
                        "change_6h": float(pair.get("priceChange", {}).get("h6", 0)),
                        "change_24h": float(pair.get("priceChange", {}).get("h24", 0)),
                        "volume_24h": float(pair.get("volume", {}).get("h24", 0)),
                        "liquidity": float(pair.get("liquidity", {}).get("usd", 0)),
                        "address": pair.get("baseToken", {}).get("address", ""),
                        "dex": pair.get("dexId", "unknown"),
                        "url": pair.get("url", ""),
                        "age_days": age_days,
                        "txns_24h_buys": pair.get("txns", {}).get("h24", {}).get("buys", 0),
                        "txns_24h_sells": pair.get("txns", {}).get("h24", {}).get("sells", 0),
                    }
            except Exception:
                continue

    return None

def is_admin(user):
    if ADMIN_CHAT_ID and str(user.id) == str(ADMIN_CHAT_ID): return True
    return user.username == ADMIN_USERNAME.lstrip("@")

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
    if streak["last_date"] == today: return streak["current"]
    if streak["last_date"] == today - timedelta(days=1): streak["current"] += 1
    else: streak["current"] = 1
    streak["best"] = max(streak["best"], streak["current"])
    streak["last_date"] = today
    return streak["current"]

# === COMMANDS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🐂 Bull Quote", callback_data="bull"), InlineKeyboardButton("📈 Trending", callback_data="trending")],
        [InlineKeyboardButton("ℹ️ About GGB", callback_data="about")],
        [InlineKeyboardButton("🌐 Website", url="https://goodgreenbull.com"), InlineKeyboardButton("🕊️ Follow on X", url="https://x.com/BeefytheBull")],
    ]
    await update.message.reply_text("🐂💚 *Good Green Bull*\n\nBuilt on Base. Built for builders.\nThe bull that doesn't stop.\n\nChoose an option below 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 *GGB Bot Commands*\n\n🐂 *Community*\n/gm — Say GM to the herd\n/bull — Random Beefy quote\n/leaderboard — Top GM senders today\n/streaks — Top GM streak holders\n/herd — Community stats\n/about — What is GGB?\n\n📈 *Alpha Discovery*\n/trending — Top trending tokens on Base\n/lookup `<token>` — Deep dive any token\n\n🗳️ *Engagement*\n/poll `<question>` — Create a Yes/No poll\n\n👤 *Admin only*\n/scannerstatus — Scanner and feed health\n/scannow — Run a scan immediately\n/alerttest — Test the configured signal destination\n/daily — Trigger daily post\n/revival — Relaunch announcement\n/broadcast `<msg>` — Message the group\n/settings — Admin panel\n\n/help — Show this list", parse_mode="Markdown")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐂💚 *What is Good Green Bull?*\n\nGGB is a builder community on Base chain.\n\nWe track trending tokens, share alpha, and support each other in building projects that actually last.\n\nBeefy Bot is the group's engine — it posts daily content, scans Base for trending tokens, alerts the group to volume breakouts, and keeps the herd engaged.\n\nNo hype. No empty promises.\nJust builders who ship.\n\n🌐 goodgreenbull.com\n🕊️ https://x.com/BeefytheBull\n📣 https://t.me/goodgreenbull\n\nHerd strong. We move. 🐂💚", parse_mode="Markdown")

async def bull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_bull_quote())

async def gm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_gm_if_needed()
    user = update.effective_user
    name = user.first_name or "Bull"
    if user.id not in gm_tracker: gm_tracker[user.id] = {"name": name, "count": 0}
    gm_tracker[user.id]["count"] += 1
    streak = update_gm_streak(user.id, name)
    streak_text = f"\n🔥 Streak: {streak} day{'s' if streak > 1 else ''}!" if streak >= 2 else ""
    responses = [f"GM {name} 🐂💚 Build mode is ON.{streak_text}", f"GM {name} 💚 The herd is awake. Let's move.{streak_text}", f"GM {name} 🐂 Another day. Another rep. Lock in.{streak_text}", f"GM {name} 💚 Still here. Still building. That's the edge.{streak_text}", f"GM {name} 🐂💚 Herd strong. Ship something today.{streak_text}"]
    await update.message.reply_text(random.choice(responses))

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_gm_if_needed()
    if not gm_tracker:
        await update.message.reply_text("No GMs logged yet today. Be the first 🐂💚\nType /gm to get on the board.")
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

async def herd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count_str = "Growing daily"
    if TELEGRAM_GROUP_ID:
        try:
            count = await context.bot.get_chat_member_count(int(TELEGRAM_GROUP_ID))
            count_str = f"{count:,} members"
        except Exception: pass
    await update.message.reply_text(f"🐂 *The GGB Herd*\n\nMembers: {count_str}\n{random.choice(['The herd is building. 🐂💚', 'Bulls dont fold when it gets quiet. 🐂💚', 'Still here. Still locked in. 🐂💚', 'The quiet ones are the dangerous ones. 🐂💚'])}\n\nShare the group 👇\nhttps://t.me/goodgreenbull", parse_mode="Markdown")

async def trending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning Base for trending tokens...")
    tokens = await fetch_base_trending_tokens()
    if not tokens:
        await update.message.reply_text("No trending tokens found right now. Check back later 🐂")
        return
    lines = ["📈 *Trending on Base*\n"]
    for t in tokens[:5]:
        change_str = f"+{t['change_24h']:.1f}%" if t['change_24h'] >= 0 else f"{t['change_24h']:.1f}%"
        arrow = "🟢" if t['change_24h'] >= 0 else "🔴"
        new_tag = " 🆕" if t.get("age_days", 999) < 7 else ""
        lines.append(f"{arrow} *{t['symbol']}*{new_tag} — ${t['price']:.6f}\n   Vol: ${t['volume_24h']:,.0f} | Liq: ${t['liquidity']:,.0f} | {change_str}\n   `{t['address'][:8]}...{t['address'][-4:]}`")
    lines.append("\n_/lookup <token> for deep dive | DYOR_ 🐂💚")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def lookup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/lookup <token name or address>`\n\nExamples:\n`/lookup BRETT`\n`/lookup $BANKR`\n`/lookup 0x532f27...`", parse_mode="Markdown")
        return
    query = " ".join(context.args)
    display_query = query.lstrip("$")
    await update.message.reply_text(f"🔍 Looking up *{display_query}*...", parse_mode="Markdown")
    token = await lookup_token(query)
    if not token:
        await update.message.reply_text(f"❌ No results for '{display_query}'. Try the full token name or contract address.")
        return
    def fmt(val):
        return f"🟢 +{val:.1f}%" if val >= 0 else f"🔴 {val:.1f}%"
    age_str = f"{token['age_days']:.0f} days" if token['age_days'] and token['age_days'] < 999 else "Unknown"
    new_tag = " 🆕 NEW" if token.get('age_days', 999) < 7 else ""
    chain_str = token['chain'].upper()
    buys = token.get('txns_24h_buys', 0)
    sells = token.get('txns_24h_sells', 0)
    ratio = f"{buys}B / {sells}S" if buys or sells else "N/A"
    msg = (f"🔎 *{token['name']} ({token['symbol']})*{new_tag}\n━━━━━━━━━━━━━━━━━━━━━━\n⛓️ Chain: {chain_str}\n💰 Price: ${token['price']:.8f}\n\n📊 *Price Changes*\n   5m: {fmt(token['change_5m'])}\n   1h: {fmt(token['change_1h'])}\n   6h: {fmt(token['change_6h'])}\n  24h: {fmt(token['change_24h'])}\n\n💎 Volume 24h: ${token['volume_24h']:,.0f}\n💧 Liquidity: ${token['liquidity']:,.0f}\n🔄 Txns 24h: {ratio}\n📅 Age: {age_str}\n🏦 DEX: {token['dex']}\n━━━━━━━━━━━━━━━━━━━━━━\n📄 `{token['address']}`\n")
    if token.get('url'): msg += f"\n📊 [Chart on DexScreener]({token['url']})"
    msg += "\n\n_DYOR. Not financial advice._ 🐂💚"
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def poll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/poll Is BTC hitting 200k this year?`", parse_mode="Markdown")
        return
    question = " ".join(context.args)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👍 Yes (0)", callback_data="poll_yes"), InlineKeyboardButton("👎 No (0)", callback_data="poll_no")]])
    sent = await update.message.reply_text(f"🗳️ *Poll*\n\n{question}\n\n_Tap to vote — you can change your vote_", parse_mode="Markdown", reply_markup=keyboard)
    poll_votes[sent.message_id] = {"question": question, "yes": set(), "no": set()}

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    keyboard = [[InlineKeyboardButton("📤 Trigger Daily Post", callback_data="trigger_daily")], [InlineKeyboardButton("📈 Trigger Alpha Report", callback_data="trigger_alpha")], [InlineKeyboardButton("📣 Send Revival Blast", callback_data="send_revival")], [InlineKeyboardButton("🔍 Scan Base Tokens Now", callback_data="scan_tokens")]]
    await update.message.reply_text("⚙️ *Admin Settings*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

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


async def scannerstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not scanner_config.enabled or scanner_service is None:
        await update.message.reply_text(
            "⏸ First-Leg Scanner is disabled. Set SCANNER_ENABLED=true in Render to activate alerts."
        )
        return
    status = scanner_service.status()
    last_cycle = status.get("last_cycle_at") or "not run yet"
    unhealthy = [item["feed_name"] for item in status.get("feeds", []) if item.get("last_error")]
    if not unhealthy:
        health_line = "All feeds healthy"
    elif "telegram-alerts" in unhealthy:
        health_line = f"Needs attention: {', '.join(unhealthy)}\nRun /alerttest to diagnose Telegram delivery."
    else:
        health_line = f"Needs attention: {', '.join(unhealthy)}"
    await update.message.reply_text(
        "🔎 First-Leg Scanner\n\n"
        f"Cadence: every {scanner_config.interval_seconds // 60} minute(s)\n"
        f"Last cycle: {last_cycle}\n"
        f"Candidates (24h): {status.get('candidates_24h', 0)}\n"
        f"Snapshots (24h): {status.get('snapshots_24h', 0)}\n"
        f"Alerts (24h): {status.get('alerts_24h', 0)}\n"
        f"{health_line}"
    )


async def scannow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not scanner_config.enabled or scanner_service is None:
        await update.message.reply_text("⏸ Scanner is disabled in the environment.")
        return
    await update.message.reply_text("🔎 Running a first-leg scan now…")
    status = await scanner_service.run_cycle()
    await update.message.reply_text(
        f"✅ Scan complete: {status.get('discovered', 0)} feed hits, "
        f"{status.get('enriched', 0)} scored, {status.get('alerts', 0)} alerts."
    )


def telegram_chat_target(value: str):
    """Allow numeric chat IDs and Telegram @channel usernames."""
    cleaned = str(value).strip()
    try:
        return int(cleaned)
    except ValueError:
        return cleaned


def telegram_delivery_diagnosis(error: Exception) -> str:
    message = str(error).lower()
    if "chat not found" in message:
        return "Chat not found. Set SIGNAL_TELEGRAM_CHAT_ID to this chat's numeric ID in Render."
    if "bot was blocked" in message:
        return "The destination user has blocked Beefy Bot. Unblock it and send /start."
    if "not enough rights" in message or "forbidden" in message:
        return "Beefy Bot lacks permission to post in the configured destination."
    if "empty" in message or "not set" in message:
        return "No alert chat is configured in Render."
    return f"Telegram rejected the test ({type(error).__name__}). Check the configured chat ID and bot permissions."


async def alerttest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not scanner_config.alert_chat_id:
        await update.message.reply_text(
            "❌ No signal destination is configured. Set SIGNAL_TELEGRAM_CHAT_ID in Render."
        )
        return
    try:
        await application.bot.send_message(
            chat_id=telegram_chat_target(scanner_config.alert_chat_id),
            text=(
                "🧪 <b>Beefy First-Leg Alert Test</b>\n\n"
                "Telegram delivery is working. This is a test only—not a trading signal."
            ),
            parse_mode="HTML",
        )
        if scanner_state is not None:
            scanner_state.mark_feed_success("telegram-alerts", 1)
        await update.message.reply_text("✅ Test alert delivered to the configured signal destination.")
    except Exception as error:
        if scanner_state is not None:
            scanner_state.mark_feed_error("telegram-alerts", error)
        await update.message.reply_text(f"❌ {telegram_delivery_diagnosis(error)}")


async def send_first_leg_alert(
    candidate: Candidate, snapshot: MarketSnapshot, result: ScoreResult
):
    if not scanner_config.alert_chat_id:
        raise RuntimeError(
            "SIGNAL_TELEGRAM_CHAT_ID, ADMIN_CHAT_ID, or TELEGRAM_GROUP_ID is not set"
        )
    await application.bot.send_message(
        chat_id=telegram_chat_target(scanner_config.alert_chat_id),
        text=format_alert(candidate, snapshot, result),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def keep_render_awake():
    """Generate one inbound health request before Render's free idle timeout."""
    try:
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{WEBHOOK_BASE_URL}/health") as response:
                if response.status >= 400:
                    print(f"⚠️ Keep-awake health check returned HTTP {response.status}")
    except Exception as error:
        print(f"⚠️ Keep-awake health check failed: {type(error).__name__}")

# === SCHEDULED POSTS ===

async def send_beefy_daily():
    if not TELEGRAM_GROUP_ID: return
    weekday = datetime.now(timezone.utc).weekday()
    theme = DAILY_THEMES[weekday]
    prompt = random.choice(theme["prompts"])
    cta = rotating_ctas[datetime.now(timezone.utc).timetuple().tm_yday % len(rotating_ctas)]
    msg = f"GM Herd 🐂💚\n\n{theme['title']}\n\n{get_bull_quote()}\n\n{prompt}\n\n{cta}"
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=msg)
        print(f"✅ Daily post sent: {theme['title']}")
    except Exception as e: print(f"⚠️ Daily post failed: {e}")

async def send_discussion_topic():
    if not TELEGRAM_GROUP_ID: return
    topic = discussion_topics[datetime.now(timezone.utc).timetuple().tm_yday % len(discussion_topics)]
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=f"💬 *Midday Discussion*\n\n{topic}", parse_mode="Markdown")
    except Exception as e: print(f"⚠️ Discussion topic failed: {e}")

async def send_weekly_engagement():
    if not TELEGRAM_GROUP_ID: return
    question = weekly_questions[datetime.now(timezone.utc).isocalendar()[1] % len(weekly_questions)]
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=f"🐂 *Builder Monday*\n\n{question}\n\nBest answer gets a shout from @BeefytheBull 💚", parse_mode="Markdown")
    except Exception as e: print(f"⚠️ Weekly post failed: {e}")

async def send_revival_blast():
    if not TELEGRAM_GROUP_ID: return
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text="🐂💚 *GGB IS BACK.*\n\nBeefy's been in build mode.\nNow we move.\n\nNew content. New energy. New era.\n\nIf you're still here — you're the founding herd.\nThe ones who stayed get rewarded first.\n\nWe move. 🐂💚\n\nFollow: https://x.com/BeefytheBull\nGroup: https://t.me/goodgreenbull", parse_mode="Markdown")
    except Exception as e: print(f"⚠️ Revival blast failed: {e}")

async def send_daily_alpha_report():
    if not TELEGRAM_GROUP_ID: return
    tokens = await fetch_base_trending_tokens()
    if not tokens: return
    lines = ["📊 *DAILY BASE ALPHA REPORT*", f"_{datetime.now(timezone.utc).strftime('%A %d %B %Y')}_\n", "Top tokens on Base by 24h volume:\n"]
    for i, t in enumerate(tokens[:5], 1):
        change_str = f"+{t['change_24h']:.1f}%" if t['change_24h'] >= 0 else f"{t['change_24h']:.1f}%"
        arrow = "🟢" if t['change_24h'] >= 0 else "🔴"
        new_tag = " 🆕" if t.get("age_days", 999) < 7 else ""
        lines.append(f"*{i}. {t['symbol']}*{new_tag}\n   {arrow} ${t['price']:.6f} ({change_str})\n   Vol: ${t['volume_24h']:,.0f} | Liq: ${t['liquidity']:,.0f}\n   `{t['address'][:8]}...{t['address'][-4:]}`")
    new_count = sum(1 for t in tokens[:5] if t.get("age_days", 999) < 7)
    if new_count: lines.append(f"\n🆕 {new_count} token{'s' if new_count > 1 else ''} less than 7 days old")
    lines.append("\n_Use /lookup <token> for a deep dive_\n_DYOR. Not financial advice._ 🐂💚")
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text="\n".join(lines), parse_mode="Markdown")
        print("✅ Daily alpha report sent")
    except Exception as e: print(f"⚠️ Alpha report failed: {e}")

async def check_breakout_volumes():
    global volume_snapshots
    if not TELEGRAM_GROUP_ID: return
    tokens = await fetch_base_trending_tokens()
    if not tokens: return
    now_ts = datetime.now(timezone.utc).timestamp()
    alerts = []
    for t in tokens:
        addr = t["address"]
        current_vol = t["volume_24h"]
        if addr in volume_snapshots:
            prev = volume_snapshots[addr]
            if prev["vol"] > 0 and (now_ts - prev["ts"]) < 14400:
                change_pct = ((current_vol - prev["vol"]) / prev["vol"]) * 100
                if change_pct >= 100:
                    alerts.append({"symbol": t["symbol"], "address": addr, "prev_vol": prev["vol"], "current_vol": current_vol, "change_pct": change_pct, "price": t["price"], "price_change": t["change_24h"]})
        volume_snapshots[addr] = {"symbol": t["symbol"], "vol": current_vol, "ts": now_ts}
    volume_snapshots = {k: v for k, v in volume_snapshots.items() if now_ts - v["ts"] < 86400}
    for a in alerts[:3]:
        price_arrow = "🟢" if a["price_change"] >= 0 else "🔴"
        msg = f"🚨 *VOLUME BREAKOUT — {a['symbol']}*\n━━━━━━━━━━━━━━━━━━━━━━\n📈 Volume spike: +{a['change_pct']:.0f}%\n   ${a['prev_vol']:,.0f} → ${a['current_vol']:,.0f}\n{price_arrow} Price: ${a['price']:.6f} ({'+' if a['price_change'] >= 0 else ''}{a['price_change']:.1f}% 24h)\n━━━━━━━━━━━━━━━━━━━━━━\n`{a['address'][:8]}...{a['address'][-4:]}`\n\n_/lookup {a['symbol']} for details | DYOR_ 🐂"
        try:
            await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=msg, parse_mode="Markdown")
            print(f"🚨 Breakout alert: {a['symbol']} +{a['change_pct']:.0f}% vol")
        except Exception as e: print(f"⚠️ Breakout alert failed: {e}")

async def scan_base_tokens():
    if not TELEGRAM_GROUP_ID: return
    tokens = await fetch_base_trending_tokens()
    if not tokens: return
    new_tokens = [t for t in tokens if t["address"] not in alerted_tokens]
    if not new_tokens: return
    lines = ["📈 *Trending on Base right now*\n"]
    for t in new_tokens[:3]:
        change_str = f"+{t['change_24h']:.1f}%" if t['change_24h'] >= 0 else f"{t['change_24h']:.1f}%"
        arrow = "🟢" if t['change_24h'] >= 0 else "🔴"
        lines.append(f"{arrow} *{t['symbol']}* — ${t['price']:.6f}\n   Vol: ${t['volume_24h']:,.0f} | {change_str}")
        alerted_tokens.add(t["address"])
    lines.append("\n_DYOR. Not financial advice._ 🐂💚")
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text="\n".join(lines), parse_mode="Markdown")
    except Exception as e: print(f"⚠️ Token scan post failed: {e}")
    if len(alerted_tokens) > 200: alerted_tokens.clear()

async def check_milestones():
    global last_milestone
    if not TELEGRAM_GROUP_ID: return
    try:
        count = await application.bot.get_chat_member_count(int(TELEGRAM_GROUP_ID))
        for m in MILESTONES:
            if count >= m and last_milestone < m:
                last_milestone = m
                await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), text=f"🎉🐂 *MILESTONE: {m} MEMBERS!*\n\nThe herd just hit {m}. That's {m} builders locked in.\n\nShare the group 👇\nhttps://t.me/goodgreenbull\n\nHerd strong. We move. 🐂💚", parse_mode="Markdown")
                break
    except Exception as e: print(f"⚠️ Milestone check failed: {e}")

# === MESSAGE HANDLERS ===

async def handle_gm_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip().lower()
    if text in ("gm", "gm!", "gm 🐂", "gm 💚", "gm 🐂💚", "good morning"):
        reset_gm_if_needed()
        user = update.effective_user
        name = user.first_name or "Bull"
        if user.id not in gm_tracker: gm_tracker[user.id] = {"name": name, "count": 0}
        gm_tracker[user.id]["count"] += 1
        streak = update_gm_streak(user.id, name)
        streak_text = f" 🔥 {streak}-day streak!" if streak >= 2 else ""
        await update.message.reply_text(random.choice([f"GM {name} 🐂💚{streak_text}", f"GM {name} 💚 Lock in.{streak_text}", f"GM {name} 🐂 Build something today.{streak_text}", f"GM {name} 💚 Herd strong.{streak_text}"]))

async def handle_reactive_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip().lower()
    if len(text) > 100 or text.startswith("/"): return
    for keyword, replies in REACTIVE_REPLIES.items():
        if keyword in text.split():
            if random.random() < 0.30: await update.message.reply_text(random.choice(replies))
            return

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: return
    if result.old_chat_member.status in (ChatMember.LEFT, ChatMember.BANNED) and result.new_chat_member.status == ChatMember.MEMBER:
        name = result.new_chat_member.user.first_name or "Bull"
        await context.bot.send_message(chat_id=result.chat.id, text=f"🐂💚 Welcome to the herd, {name}!\n\nGood Green Bull is a builder community on Base.\n\nStart here:\n🐂 /bull — Get a Beefy quote\n👋 /gm — Say GM to the herd\n📈 /trending — What's moving on Base\n🔍 /lookup — Deep dive any token\n\nFollow us on X 👉 https://x.com/BeefytheBull\n\nHerd strong. We move. 🐂💚")

async def detect_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    now = datetime.now(timezone.utc)
    timestamps = user_spam_tracker.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < timedelta(seconds=10)]
    timestamps.append(now)
    user_spam_tracker[user_id] = timestamps
    if len(timestamps) > 5:
        try:
            await context.bot.restrict_chat_member(chat_id, user_id, permissions=ChatPermissions(can_send_messages=False), until_date=now + timedelta(minutes=10))
            await context.bot.send_message(chat_id, text="⚠️ User muted 10 mins for spam.")
        except Exception: pass

# === CALLBACK HANDLER ===

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "bull":
        await query.edit_message_text(get_bull_quote())
    elif query.data == "trending":
        await query.edit_message_text("🔍 Scanning Base for trending tokens...")
        tokens = await fetch_base_trending_tokens()
        if not tokens:
            await query.edit_message_text("No trending tokens found right now. Check back later 🐂")
            return
        lines = ["📈 *Trending on Base*\n"]
        for t in tokens[:3]:
            change_str = f"+{t['change_24h']:.1f}%" if t['change_24h'] >= 0 else f"{t['change_24h']:.1f}%"
            arrow = "🟢" if t['change_24h'] >= 0 else "🔴"
            lines.append(f"{arrow} *{t['symbol']}* — ${t['price']:.6f} | {change_str}")
        lines.append("\n_/lookup <token> for deep dive | DYOR_ 🐂💚")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
    elif query.data == "about":
        await query.edit_message_text("🐂💚 *What is Good Green Bull?*\n\nGGB is a builder community on Base chain. We track trending tokens, share alpha, and support builders who ship.\n\nBeefy Bot scans Base for trending tokens, alerts volume breakouts, and keeps the herd engaged.\n\n🌐 goodgreenbull.com\n🕊️ x.com/BeefytheBull\n\nHerd strong. We move. 🐂💚", parse_mode="Markdown")
    elif query.data in ("poll_yes", "poll_no"):
        msg_id = query.message.message_id
        user_id = query.from_user.id
        if msg_id not in poll_votes: return
        poll = poll_votes[msg_id]
        vote = "yes" if query.data == "poll_yes" else "no"
        other = "no" if vote == "yes" else "yes"
        poll[other].discard(user_id)
        poll[vote].add(user_id)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(f"👍 Yes ({len(poll['yes'])})", callback_data="poll_yes"), InlineKeyboardButton(f"👎 No ({len(poll['no'])})", callback_data="poll_no")]])
        await query.edit_message_text(f"🗳️ *Poll*\n\n{poll['question']}\n\n_Tap to vote — you can change your vote_", parse_mode="Markdown", reply_markup=keyboard)
    elif query.data == "trigger_daily":
        await query.edit_message_text("📤 Sending daily post...")
        await send_beefy_daily()
    elif query.data == "trigger_alpha":
        await query.edit_message_text("📈 Sending alpha report...")
        await send_daily_alpha_report()
    elif query.data == "send_revival":
        await query.edit_message_text("📣 Sending revival blast...")
        await send_revival_blast()
    elif query.data == "scan_tokens":
        await query.edit_message_text("🔍 Scanning Base tokens...")
        await scan_base_tokens()

# === ROUTES ===

@app.route("/", methods=["GET"])
async def home():
    return "🐂 Beefy Bot v3 — Community + alerts-only First-Leg Scanner."


@app.route("/health", methods=["GET"])
async def health():
    if scanner_service is None:
        return {"bot": "ok", "scanner": "disabled"}
    status = scanner_service.status()
    return {
        "bot": "ok",
        "scanner": "running" if scanner_config.enabled else "disabled",
        "last_cycle_at": status.get("last_cycle_at"),
        "feeds_with_errors": status.get("errors", 0),
    }

@app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    data = await request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "OK"

# === HANDLER REGISTRATION ===

def register_handlers():
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("bull", bull))
    application.add_handler(CommandHandler("gm", gm_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("streaks", streaks_command))
    application.add_handler(CommandHandler("herd", herd))
    application.add_handler(CommandHandler("trending", trending_command))
    application.add_handler(CommandHandler("lookup", lookup_command))
    application.add_handler(CommandHandler("poll", poll_command))
    application.add_handler(CommandHandler("daily", daily_command))
    application.add_handler(CommandHandler("revival", revival_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("scannerstatus", scannerstatus_command))
    application.add_handler(CommandHandler("scannow", scannow_command))
    application.add_handler(CommandHandler("alerttest", alerttest_command))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gm_text), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reactive_replies), group=2)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, detect_spam), group=3)

register_handlers()

# === STARTUP ===

@app.before_serving
async def on_startup():
    global scanner_service, scanner_state
    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print("✅ Telegram webhook configured")
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(send_beefy_daily, "cron", hour=8, minute=0)
    scheduler.add_job(send_discussion_topic, "cron", hour=12, minute=0)
    scheduler.add_job(send_weekly_engagement, "cron", day_of_week="mon", hour=9, minute=0)
    scheduler.add_job(check_milestones, "interval", hours=1)
    scheduler.add_job(
        keep_render_awake,
        "interval",
        minutes=10,
        max_instances=1,
        coalesce=True,
        jitter=20,
    )
    if scanner_config.enabled:
        scanner_state = SQLiteState(scanner_config.state_db)
        scanner_service = ScannerService(scanner_config, scanner_state, send_first_leg_alert)
        await scanner_service.start()
        scheduler.add_job(
            scanner_service.run_cycle,
            "interval",
            seconds=scanner_config.interval_seconds,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=scanner_config.interval_seconds,
        )
        asyncio.create_task(scanner_service.run_cycle())
    scheduler.start()
    print(
        f"✅ Scheduler: community posts + free-tier keep-awake + first-leg scanner "
        f"({'every ' + str(scanner_config.interval_seconds) + 's' if scanner_config.enabled else 'disabled'})"
    )
    print("🐂 Beefy Bot v3 — Alerts only — LIVE")


@app.after_serving
async def on_shutdown():
    if scanner_service is not None:
        await scanner_service.stop()
    await application.shutdown()

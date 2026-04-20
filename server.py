# server.py — GGB Beefy Bot | Good Green Bull | Built on Base
import os, asyncio, random, aiohttp
from datetime import datetime, timezone, timedelta
from quart import Quart, request
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                       ChatPermissions, ChatMember)
from telegram.ext import (ApplicationBuilder, CommandHandler, CallbackQueryHandler,
                           MessageHandler, ChatMemberHandler, ContextTypes, filters)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import signals

# ── Config ──────────────────────────────────────────────────────────────────

TOKEN             = os.getenv("BOT_TOKEN")
BASESCAN_API_KEY  = os.getenv("BASESCAN_API_KEY")
ADMIN_USERNAME    = os.getenv("ADMIN_USERNAME", "JS0nbase")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
WEBHOOK_PATH      = f"/webhook/{TOKEN}"
WEBHOOK_URL       = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"
GGB_CONTRACT      = "0xc2758c05916ba20b19358f1e96f597774e603050"

app         = Quart(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# ── State (in-memory, resets on restart) ────────────────────────────────────

user_spam_tracker   = {}
recent_bull_indices = []
gm_tracker          = {}
gm_tracker_date     = None

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

# ── Helpers ─────────────────────────────────────────────────────────────────

def get_bull_quote():
    global recent_bull_indices
    avail = [i for i in range(len(bull_quotes)) if i not in recent_bull_indices]
    if not avail:
        recent_bull_indices, avail = [], list(range(len(bull_quotes)))
    idx = random.choice(avail)
    recent_bull_indices.append(idx)
    if len(recent_bull_indices) > 7: recent_bull_indices.pop(0)
    return bull_quotes[idx]

async def fetch_price_data():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.dexscreener.com/latest/dex/tokens/{GGB_CONTRACT}",
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                pair = (await r.json())["pairs"][0]
                return float(pair["priceUsd"]), float(pair.get("priceChange", {}).get("h24", 0))
    except Exception:
        return None, None

async def fetch_wallet_balance(address):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.basescan.org/api?module=account&action=tokenbalance"
                f"&contractaddress={GGB_CONTRACT}&address={address}&tag=latest&apikey={BASESCAN_API_KEY}",
                timeout=aiohttp.ClientTimeout(total=10)) as r:
                d = await r.json()
                if d["status"] == "1": return int(d["result"]) / 10**18
    except Exception: pass
    return None

def format_price(p, c):
    return f"💵 GGB: ${p:.6f}\n{'📈' if c >= 0 else '📉'} 24h: {'+' if c >= 0 else ''}{c:.2f}%"

def reset_gm():
    global gm_tracker, gm_tracker_date
    today = datetime.now(timezone.utc).date()
    if gm_tracker_date != today:
        gm_tracker, gm_tracker_date = {}, today

# ── Commands ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🐂 Bull Quote", callback_data="bull")],
          [InlineKeyboardButton("📈 GGB Price", callback_data="price")],
          [InlineKeyboardButton("🎨 Wallpaper Pack", url="https://goodgreenbull.gumroad.com")],
          [InlineKeyboardButton("🖼️ NFT Drop", callback_data="nft_info")],
          [InlineKeyboardButton("🌐 Website", url="https://goodgreenbull.com")],
          [InlineKeyboardButton("🕊️ Follow on X", url="https://x.com/goodgreenbull")]]
    await update.message.reply_text(
        "🐂💚 *Good Green Bull*\n\nBuilt on Base. Built for builders.\nThe bull that doesn't stop.\n\nChoose below 👇",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 *GGB Commands*\n\n"
        "/start /price /bull /gm /leaderboard /wallet /token /kit /nft /herd\n\n"
        "👤 *Admin:* /daily /revival /signals /signalstatus /settings\n\n"
        "💰 *Trading DM:* /botbalance /withdraw /pause /resume /testconnection /golive",
        parse_mode="Markdown")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pv, ch = await fetch_price_data()
    if pv is None:
        await update.message.reply_text("⚠️ Price unavailable. Try again shortly."); return
    await update.message.reply_text(
        f"{format_price(pv, ch)}\n\n📊 https://tinyurl.com/GGBDex\n📄 `{GGB_CONTRACT}`",
        parse_mode="Markdown")

async def bull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_bull_quote())

async def gm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_gm()
    user = update.effective_user
    name = user.first_name or "Bull"
    if user.id not in gm_tracker: gm_tracker[user.id] = {"name": name, "count": 0}
    gm_tracker[user.id]["count"] += 1
    await update.message.reply_text(random.choice([
        f"GM {name} 🐂💚 Build mode is ON.",
        f"GM {name} 💚 Herd is awake. Let's move.",
        f"GM {name} 🐂 Another rep. Lock in.",
        f"GM {name} 💚 Still building. That's the edge.",
        f"GM {name} 🐂💚 Herd strong. Ship something."]))

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_gm()
    if not gm_tracker:
        await update.message.reply_text("No GMs yet today. /gm to get on the board 🐂💚"); return
    ranked = sorted(gm_tracker.items(), key=lambda x: x[1]["count"], reverse=True)
    medals = ["🥇", "🥈", "🥉"] + ["🐂"] * 7
    lines = ["🏆 *GM Leaderboard — Today*\n"]
    for i, (_, d) in enumerate(ranked[:10]):
        lines.append(f"{medals[i]} {d['name']} — {d['count']} GMs")
    lines.append("\n/gm to get on the board 💚")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/wallet <Base address>`", parse_mode="Markdown"); return
    bal = await fetch_wallet_balance(context.args[0])
    if bal is None:
        await update.message.reply_text("⚠️ Wallet fetch failed. Check address."); return
    pv, _ = await fetch_price_data()
    addr = context.args[0]
    usd = f"\n💵 ≈ ${bal * pv:,.2f} USD" if pv else ""
    await update.message.reply_text(
        f"👛 `{addr[:6]}...{addr[-4:]}`\n🐂 {bal:,.2f} GGB{usd}", parse_mode="Markdown")

async def token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📈 *$GGB Token*\n\nName: Good Green Bull | Chain: Base | Decimals: 18\n"
        f"Contract: `{GGB_CONTRACT}`\n\n🔗 https://basescan.org/token/{GGB_CONTRACT}",
        parse_mode="Markdown")

async def kit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠️ *GGB Builder Kit*\n\nFull content + brand system for builders on Base/Farcaster.\n\n"
        "✅ Content calendar ✅ 30 templates ✅ AI image prompts\n"
        "✅ Brand voice guide ✅ Mascot rules ✅ Monetisation framework\n\n"
        "💰 £35 | 🔗 https://goodgreenbull.gumroad.com", parse_mode="Markdown")

async def nft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 *Beefy Prime: Series One*\n\n50 cinematic 1/1 pieces on Base.\n"
        "Holders: founder role + first access to drops.\n\n🟡 Coming Soon — follow @goodgreenbull 🐂💚",
        parse_mode="Markdown")

async def herd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cs = "Growing daily"
    if TELEGRAM_GROUP_ID:
        try:
            cs = f"{await context.bot.get_chat_member_count(int(TELEGRAM_GROUP_ID)):,} members"
        except Exception: pass
    await update.message.reply_text(
        f"🐂 *The GGB Herd*\n\nMembers: {cs}\n"
        f"{random.choice(['Still here. Still building. 🐂💚', 'Bulls dont fold when quiet. 🐂💚', 'Early is a choice. 🐂💚'])}\n\n"
        f"https://t.me/goodgreenbull", parse_mode="Markdown")

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not signals.is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    kb = [[InlineKeyboardButton("🤖 Toggle Daily", callback_data="toggle_daily")],
          [InlineKeyboardButton("🚫 Toggle Anti-Spam", callback_data="toggle_spam")],
          [InlineKeyboardButton("📣 Send Revival", callback_data="send_revival")]]
    await update.message.reply_text("⚙️ *Admin Settings*", parse_mode="Markdown",
                                     reply_markup=InlineKeyboardMarkup(kb))

async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not signals.is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    await update.message.reply_text("📤 Sending Beefy Daily...")
    await send_beefy_daily()

async def revival_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not signals.is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    await update.message.reply_text("📣 Sending revival blast...")
    await send_revival_blast()

# ── Scheduled posts ─────────────────────────────────────────────────────────

async def send_beefy_daily():
    if not TELEGRAM_GROUP_ID: return
    pv, ch = await fetch_price_data()
    price_line = (f"$GGB: ${pv:.6f} | {'+' if ch >= 0 else ''}{ch:.2f}% 24h"
                  if pv else "$GGB: unavailable")
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID),
            text=f"GM Herd 🐂💚\n\n{get_bull_quote()}\n\n{price_line}\n\n"
                 f"What are you building today? 👇")
    except Exception as e:
        print(f"⚠️ Daily: {e}")

async def send_weekly_engagement():
    if not TELEGRAM_GROUP_ID: return
    q = weekly_questions[datetime.now(timezone.utc).isocalendar()[1] % len(weekly_questions)]
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID),
            text=f"🐂 *Builder Monday*\n\n{q}\n\nBest answer gets a shout 💚",
            parse_mode="Markdown")
    except Exception as e:
        print(f"⚠️ Weekly: {e}")

async def send_revival_blast():
    if not TELEGRAM_GROUP_ID: return
    try:
        await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), parse_mode="Markdown",
            text="🐂💚 *GGB IS BACK.*\n\nBeefy's been building. Now we move.\n\n"
                 "🎨 Wallpaper Pack | 🖼️ NFTs | 🛠️ Builder Kit\n\n"
                 "Founding herd gets rewarded first. We move. 🐂💚\n\nhttps://x.com/goodgreenbull")
    except Exception as e:
        print(f"⚠️ Revival: {e}")

async def send_daily_balance():
    ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
    if not ADMIN_CHAT_ID: return
    bal = await signals.get_clob_balance() if signals.clob_ready else None
    pnl = signals.signal_bankroll - 50.0
    dd = ((signals.signal_peak - signals.signal_bankroll) / signals.signal_peak
          if signals.signal_peak else 0)
    n = len(signals.signal_trades)
    wins = sum(1 for t in signals.signal_trades if t.get("won"))
    bal_line = "💰 Live: ${:.2f}".format(bal) if bal else "📝 Paper"
    try:
        await application.bot.send_message(chat_id=int(ADMIN_CHAT_ID), parse_mode="Markdown",
            text=f"📊 *Daily Balance*\n"
                 f"{bal_line} | Paper: ${signals.signal_bankroll:.2f}\n"
                 f"PnL: ${pnl:+.2f} | DD: {dd:.1%} | Trades: {n} W:{wins} L:{n - wins}\n"
                 f"{'🟢 LIVE' if signals.TRADING_MODE == 'live' else '📝 PAPER'} | "
                 f"{'⏸' if signals.trading_paused else '▶️'}")
    except Exception as e:
        print(f"⚠️ Balance DM: {e}")

# ── Message handlers ────────────────────────────────────────────────────────

async def handle_gm_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if update.message.text.strip().lower() in ("gm", "gm!", "gm 🐂", "gm 💚", "gm 🐂💚", "good morning"):
        reset_gm()
        user = update.effective_user
        name = user.first_name or "Bull"
        if user.id not in gm_tracker: gm_tracker[user.id] = {"name": name, "count": 0}
        gm_tracker[user.id]["count"] += 1
        await update.message.reply_text(random.choice([
            f"GM {name} 🐂💚", f"GM {name} 💚 Lock in.",
            f"GM {name} 🐂 Build something today.", f"GM {name} 💚 Herd strong."]))

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = update.chat_member
    if not r: return
    if (r.old_chat_member.status in (ChatMember.LEFT, ChatMember.BANNED)
            and r.new_chat_member.status == ChatMember.MEMBER):
        name = r.new_chat_member.user.first_name or "Bull"
        await context.bot.send_message(chat_id=r.chat.id,
            text=f"🐂💚 Welcome {name}!\n\nGood Green Bull — builder community on Base.\n\n"
                 f"📈 /price | 🐂 /bull | 👋 /gm | 🛠️ /kit | 🎨 /nft\n\n"
                 f"https://x.com/goodgreenbull\n\nHerd strong. 🐂💚")

async def detect_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    uid, cid = update.effective_user.id, update.effective_chat.id
    now = datetime.now(timezone.utc)
    ts = [t for t in user_spam_tracker.get(uid, []) if now - t < timedelta(seconds=10)]
    ts.append(now)
    user_spam_tracker[uid] = ts
    if len(ts) > 5:
        try:
            await context.bot.restrict_chat_member(cid, uid,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=now + timedelta(minutes=10))
            await context.bot.send_message(cid, "⚠️ User muted 10 mins for spam.")
        except Exception: pass

# ── Callback handler ────────────────────────────────────────────────────────

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "bull":
        await q.edit_message_text(get_bull_quote())
    elif q.data == "price":
        pv, ch = await fetch_price_data()
        await q.edit_message_text("⚠️ Unavailable." if pv is None
                                   else f"{format_price(pv, ch)}\n\n📊 https://tinyurl.com/GGBDex")
    elif q.data == "nft_info":
        await q.edit_message_text(
            "🎨 Beefy Prime: Series One\n50 cinematic 1/1 pieces on Base.\n"
            "Coming Soon 🟡 — follow @goodgreenbull 🐂💚")
    elif q.data == "send_revival":
        await q.edit_message_text("📣 Sending revival blast...")
        await send_revival_blast()
    elif q.data in ("toggle_daily", "toggle_spam"):
        await q.edit_message_text("⚙️ Toggle controls coming in next update.")

# ── Webhook routes ──────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
async def home():
    return "🐂 BeefyTheBull Bot is Alive! Good Green Bull — Built on Base."

@app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    update = Update.de_json(await request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "OK"

# ── Handler registration ────────────────────────────────────────────────────

def register_handlers():
    h = application.add_handler
    h(CommandHandler("start",          start))
    h(CommandHandler("help",           help_command))
    h(CommandHandler("price",          price))
    h(CommandHandler("bull",           bull))
    h(CommandHandler("gm",             gm_command))
    h(CommandHandler("leaderboard",    leaderboard))
    h(CommandHandler("wallet",         wallet))
    h(CommandHandler("token",          token))
    h(CommandHandler("kit",            kit))
    h(CommandHandler("nft",            nft))
    h(CommandHandler("herd",           herd))
    h(CommandHandler("daily",          daily_command))
    h(CommandHandler("revival",        revival_command))
    h(CommandHandler("settings",       settings))
    h(CommandHandler("signals",        signals.signals_command))
    h(CommandHandler("signalstatus",   signals.signalstatus_command))
    h(CommandHandler("botbalance",     signals.botbalance_command))
    h(CommandHandler("withdraw",       signals.withdraw_command))
    h(CommandHandler("cancel",         signals.cancel_command))
    h(CommandHandler("pause",          signals.pause_command))
    h(CommandHandler("resume",         signals.resume_command))
    h(CommandHandler("testconnection", signals.testconnection_command))
    h(CommandHandler("golive",         signals.golive_command))
    h(CallbackQueryHandler(button))
    h(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    h(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gm_text), group=1)
    h(MessageHandler(filters.TEXT & ~filters.COMMAND, detect_spam),    group=2)

# ── Startup ─────────────────────────────────────────────────────────────────

async def on_startup():
    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook: {WEBHOOK_URL}")
    await signals.init_clob_client()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(send_beefy_daily,        "cron",     hour=8, minute=0)
    scheduler.add_job(send_weekly_engagement,  "cron",     day_of_week="mon", hour=9, minute=0)
    scheduler.add_job(signals.run_signal_scan, "interval", seconds=60)
    scheduler.add_job(send_daily_balance,      "cron",     hour=8, minute=5)
    scheduler.start()
    print(f"✅ Scheduler started | Mode: {signals.TRADING_MODE} | "
          f"CLOB: {'ready' if signals.clob_ready else 'paper'}")

# ── Entry point ─────────────────────────────────────────────────────────────
# Fix: Quart (async-native) + hypercorn (ASGI) so Telegram, scheduler and
# webhook handler all share ONE asyncio event loop. The old Flask + app.run()
# approach created a second loop per webhook request, which caused
# "task attached to a different loop" errors.

async def main():
    register_handlers()
    await on_startup()
    from hypercorn.asyncio import serve as hypercorn_serve
    from hypercorn.config import Config
    cfg = Config()
    cfg.bind = [f"0.0.0.0:{os.environ.get('PORT', '10000')}"]
    await hypercorn_serve(app, cfg)

if __name__ == "__main__":
    asyncio.run(main())

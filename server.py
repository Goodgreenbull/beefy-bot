# GGB Beefy Bot — Polymarket Quant Trading Engine
# Built on Base | Powered by Beefy 🐂

import os, asyncio, random, math, aiohttp
from datetime import datetime, timezone, timedelta
from quart import Quart, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Config ───────────────────────────────────────────────────────────────────
TOKEN             = os.getenv("BOT_TOKEN")
BASESCAN_API_KEY  = os.getenv("BASESCAN_API_KEY")
ADMIN_USERNAME    = os.getenv("ADMIN_USERNAME", "JS0nbase")
ADMIN_CHAT_ID     = os.getenv("ADMIN_CHAT_ID")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
POLYMARKET_PK     = os.getenv("POLYMARKET_PRIVATE_KEY")
POLYMARKET_WALLET = os.getenv("POLYMARKET_WALLET_ADDRESS")
PERSONAL_WALLET   = os.getenv("YOUR_PERSONAL_WALLET")
TRADING_MODE      = os.getenv("TRADING_MODE", "paper")
WEBHOOK_PATH      = f"/webhook/{TOKEN}"
WEBHOOK_URL       = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"

app         = Quart(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# ── State ────────────────────────────────────────────────────────────────────
polymarket_enabled = False
signal_bankroll    = 50.0
signal_peak        = 50.0
signal_trades      = []
signal_consecutive_losses = 0
signal_is_paused   = False
signal_pause_until = 0.0
btc_price_history  = []
eth_price_history  = []
tracked_markets    = {}
clob_client        = None
clob_ready         = False
trading_paused     = False

# ── Signal Config ────────────────────────────────────────────────────────────
EV_THRESH   = 0.12;  FEE_PCT    = 0.02;  MIN_CONF   = 0.12
MIN_CORR    = 0.80;  KELLY_FRAC = 0.25;  MAX_BET    = 0.08
TRAIL_STOP  = 0.20;  COOLDOWN   = 900;   PRIOR_DECAY = 0.95
MOVE_THRESH = 0.001

LIKELIHOOD = {
    1:  {"big_up":0.72,"small_up":0.58,"flat":0.50,"small_down":0.42,"big_down":0.28},
    5:  {"big_up":0.78,"small_up":0.62,"flat":0.50,"small_down":0.38,"big_down":0.22},
    15: {"big_up":0.82,"small_up":0.65,"flat":0.50,"small_down":0.35,"big_down":0.18},
}

# ── Helpers ──────────────────────────────────────────────────────────────────
def is_admin(user): return user.username == ADMIN_USERNAME.lstrip("@")

def classify(r):
    if r > 0.01: return "big_up"
    if r > 0.002: return "small_up"
    if r < -0.01: return "big_down"
    if r < -0.002: return "small_down"
    return "flat"

def likelihood(r, w, corr=1.0):
    base = LIKELIHOOD.get(w, LIKELIHOOD[5])[classify(r)]
    return max(0.01, min(0.99, 0.5 + (base - 0.5) * corr))

def bayes(prior, r, w, corr):
    ph = 0.5 + (prior - 0.5) * PRIOR_DECAY
    peh = likelihood(r, w, corr)
    pe = peh * ph + (1 - peh) * (1 - ph)
    return max(0.05, min(0.95, (peh * ph) / max(pe, 1e-10)))

def ev_gap(p, mkt): return (p - mkt) - mkt * FEE_PCT

def kelly(p, mkt):
    if mkt <= 0 or mkt >= 1: return 0.0
    odds = 1 / mkt - 1
    return max(0.0, min(1.0, (p * odds - (1 - p)) / odds)) if odds > 0 else 0.0

def kl_div(p, q):
    p, q = max(.001, min(.999, p)), max(.001, min(.999, q))
    return p * math.log(p / q) + (1 - p) * math.log((1 - p) / (1 - q))

def calc_bet(bankroll, p, mkt):
    return max(0, min(kelly(p, mkt) * KELLY_FRAC * bankroll, bankroll * MAX_BET, bankroll - 2.0))

# ── CLOB Client ──────────────────────────────────────────────────────────────
async def init_clob():
    global clob_client, clob_ready
    if not POLYMARKET_PK or TRADING_MODE != "live":
        print(f"📊 Mode: {TRADING_MODE} (CLOB not initialised)"); return
    try:
        from py_clob_client.client import ClobClient
        clob_client = ClobClient("https://clob.polymarket.com", key=POLYMARKET_PK,
                                  chain_id=137, signature_type=0, funder=POLYMARKET_WALLET)
        clob_client.set_api_creds(clob_client.create_or_derive_api_creds())
        clob_ready = True
        print(f"✅ CLOB ready — {POLYMARKET_WALLET[:10]}...")
    except Exception as e:
        print(f"⚠️ CLOB init failed: {e}")

async def clob_order(token_id, side, price, size):
    if not clob_ready or not clob_client or trading_paused: return None
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        o = OrderArgs(token_id=token_id, price=round(price, 2),
                      size=round(size, 1), side=BUY if side == "BUY" else SELL)
        return clob_client.post_order(clob_client.create_order(o), OrderType.GTC)
    except Exception as e:
        print(f"⚠️ Order failed: {e}"); return None

async def clob_balance():
    if not clob_ready or not clob_client: return None
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        b = clob_client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return float(b["balance"]) / 1e6 if b and "balance" in b else None
    except Exception as e:
        print(f"⚠️ Balance check failed: {e}"); return None

async def withdraw_usdc(to_addr, amount):
    if not POLYMARKET_PK: return False, "No private key"
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        abi = [{"constant":False,"inputs":[{"name":"to","type":"address"},
                {"name":"value","type":"uint256"}],"name":"transfer",
                "outputs":[{"name":"","type":"bool"}],"type":"function"}]
        c = w3.eth.contract(address=Web3.to_checksum_address(
            "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), abi=abi)
        tx = c.functions.transfer(
            Web3.to_checksum_address(to_addr), int(amount * 1e6)
        ).build_transaction({
            "from": Web3.to_checksum_address(POLYMARKET_WALLET),
            "nonce": w3.eth.get_transaction_count(Web3.to_checksum_address(POLYMARKET_WALLET)),
            "gas": 100000, "gasPrice": w3.eth.gas_price, "chainId": 137})
        signed = w3.eth.account.sign_transaction(tx, POLYMARKET_PK)
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(signed.raw_transaction), timeout=60)
        if receipt["status"] == 1: return True, receipt["transactionHash"].hex()
        return False, "Reverted"
    except Exception as e: return False, str(e)

# ── API Helpers ──────────────────────────────────────────────────────────────
async def fetch_poly_markets():
    markets = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        for kw in ["bitcoin", "btc", "ethereum", "eth", "crypto"]:
            try:
                async with s.get("https://gamma-api.polymarket.com/markets",
                    params={"q": kw, "limit": 5, "active": "true", "closed": "false"}) as r:
                    for m in await r.json():
                        mid = m.get("id") or m.get("condition_id", "")
                        if mid and mid not in [x.get("id") for x in markets]:
                            markets.append(m)
            except Exception as e: print(f"⚠️ Poly fetch ({kw}): {e}")
    return markets

async def fetch_btc_eth():
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get("https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin,ethereum", "vs_currencies": "usd",
                         "include_24hr_change": "true"}) as r:
                return await r.json()
    except Exception as e:
        print(f"⚠️ CoinGecko: {e}"); return {}

# ── Signal Scanner ───────────────────────────────────────────────────────────
async def run_signal_scan():
    global signal_bankroll, signal_peak, signal_consecutive_losses
    global signal_is_paused, signal_pause_until, tracked_markets
    if not polymarket_enabled or not TELEGRAM_GROUP_ID: return
    now = datetime.now(timezone.utc).timestamp()
    if signal_is_paused:
        if now < signal_pause_until: return
        signal_is_paused = False
    if signal_bankroll <= signal_peak * (1 - TRAIL_STOP): return

    prices = await fetch_btc_eth()
    btc = prices.get("bitcoin", {}).get("usd")
    if not btc: return
    ts = now
    btc_price_history.append((ts, btc))
    eth = prices.get("ethereum", {}).get("usd")
    if eth: eth_price_history.append((ts, eth))
    cutoff = ts - 1200
    btc_price_history[:] = [(t, p) for t, p in btc_price_history if t >= cutoff]
    eth_price_history[:] = [(t, p) for t, p in eth_price_history if t >= cutoff]

    rets = {}
    for w in [1, 5, 15]:
        past = next((p for t, p in reversed(btc_price_history) if t <= ts - w * 60), None)
        if past and past > 0: rets[w] = (btc - past) / past
    if not rets: return

    if not tracked_markets:
        for m in (await fetch_poly_markets())[:8]:
            mid = m.get("id") or m.get("condition_id", "")
            name = m.get("question", m.get("title", "Unknown"))[:60]
            tokens = m.get("tokens", [])
            p = max(0.05, min(0.95, float(tokens[0].get("price", 0.5)) if tokens else 0.5))
            nl = name.lower()
            corr = (0.90 if any(k in nl for k in ["btc","bitcoin","$100k","$90k"])
                    else 0.82 if any(k in nl for k in ["eth","ethereum"])
                    else 0.78 if any(k in nl for k in ["crypto","market cap"]) else 0.50)
            if corr >= MIN_CORR:
                tracked_markets[mid] = {"name": name, "price": p, "posterior": p,
                                         "correlation": corr, "last_signal_ts": 0, "updates": 0}
        if tracked_markets: print(f"📊 Tracking {len(tracked_markets)} markets")

    for mid, mkt in tracked_markets.items():
        for w in sorted(rets):
            if abs(rets[w]) >= MOVE_THRESH:
                mkt["posterior"] = bayes(mkt["posterior"], rets[w], w, mkt["correlation"])
                mkt["updates"] += 1
        ev = ev_gap(mkt["posterior"], mkt["price"])
        kl = kl_div(mkt["posterior"], mkt["price"])
        conf = abs(mkt["posterior"] - mkt["price"])
        if ev < EV_THRESH or conf < MIN_CONF: continue
        confirms = 1 + (kl >= 0.10) + (conf >= 0.20)
        if confirms < 2 or ts - mkt["last_signal_ts"] < COOLDOWN: continue
        bet = calc_bet(signal_bankroll, mkt["posterior"], mkt["price"])
        if bet < 0.50: continue

        direction = "BUY YES 🟢" if mkt["posterior"] > mkt["price"] else "BUY NO 🔴"
        mkt["last_signal_ts"] = ts
        signal_trades.append({"ts": ts, "market": mkt["name"], "direction": direction,
            "posterior": mkt["posterior"], "market_price": mkt["price"],
            "ev": ev, "bet": bet, "won": None})

        trade_status = "📝 Paper trade"
        if TRADING_MODE == "live" and clob_ready and not trading_paused:
            try:
                raw = await fetch_poly_markets()
                tokens = next((rm.get("tokens", []) for rm in raw
                    if (rm.get("id") or rm.get("condition_id", "")) == mid), None)
                if tokens:
                    if mkt["posterior"] > mkt["price"]:
                        tid, tside, tp = tokens[0].get("token_id", ""), "BUY", mkt["price"]
                    else:
                        tid = tokens[1].get("token_id", "") if len(tokens) > 1 else tokens[0].get("token_id", "")
                        tside = "BUY" if len(tokens) > 1 else "SELL"
                        tp = 1 - mkt["price"] if len(tokens) > 1 else mkt["price"]
                    if tid:
                        resp = await clob_order(tid, tside, tp, bet / tp)
                        if resp:
                            oid = str(resp.get("orderID", resp.get("id", "?")))[:12]
                            trade_status = f"✅ LIVE ORDER — {oid}..."
                            rb = await clob_balance()
                            if rb: signal_bankroll = rb; signal_peak = max(signal_peak, rb)
                        else: trade_status = "⚠️ Order failed — paper logged"
            except Exception: pass

        dd = (signal_peak - signal_bankroll) / signal_peak if signal_peak else 0

        try:
            await application.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), parse_mode="Markdown",
                text=(f"🔔 *POLYMARKET SIGNAL*\n━━━━━━━━━━━━━━━━━━━━━━\n"
                      f"📊 *{mkt['name']}*\n📌 {direction}\n"
                      f"💰 Market: {mkt['price']:.2%} → Belief: {mkt['posterior']:.2%}\n"
                      f"📈 EV: {ev:+.2%} | Confidence: {confirms}/3\n"
                      f"BTC: ${btc:,.0f} | Updates: {mkt['updates']}\n"
                      f"_Beefy quant engine • not financial advice_"))
        except Exception as e: print(f"⚠️ Group signal: {e}")

        if ADMIN_CHAT_ID:
            try:
                await application.bot.send_message(chat_id=int(ADMIN_CHAT_ID), parse_mode="Markdown",
                    text=(f"🔒 *PRIVATE SIGNAL*\n━━━━━━━━━━━━━━━━━━━━━━\n"
                          f"📊 *{mkt['name']}*\n📌 {direction}\n"
                          f"💰 {mkt['price']:.2%} → {mkt['posterior']:.2%}\n"
                          f"📈 EV: {ev:+.2%} | KL: {kl:.4f} | {confirms}/3\n"
                          f"💵 Bet: ${bet:.2f} | Bank: ${signal_bankroll:.2f}\n"
                          f"📉 DD: {dd:.1%} | Streak: {signal_consecutive_losses}\n"
                          f"🤖 {trade_status} | {'LIVE' if TRADING_MODE=='live' else 'PAPER'}"))
            except Exception as e: print(f"⚠️ Admin DM: {e}")

        print(f"📊 Signal: {mkt['name']} — {direction} — ${bet:.2f}")
        mkt["posterior"] = 0.6 * mkt["price"] + 0.4 * mkt["posterior"]

# ── Commands ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐂 *Beefy Quant Engine*\n\n"
        "/signals — Toggle scanner on/off\n"
        "/signalstatus — Dashboard\n"
        "/botbalance — Wallet balance\n"
        "/withdraw — Send USDC to personal wallet\n"
        "/pause /resume — Trading control\n"
        "/testconnection — API check\n"
        "/golive — Activate live trading",
        parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def signals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global polymarket_enabled, tracked_markets, signal_bankroll, signal_peak
    global signal_trades, signal_consecutive_losses, signal_is_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    polymarket_enabled = not polymarket_enabled
    if polymarket_enabled:
        tracked_markets, signal_trades = {}, []
        signal_bankroll = signal_peak = 50.0
        signal_consecutive_losses = 0; signal_is_paused = False
        await update.message.reply_text(
            "📊 *Signals: ON*\nScanning every 60s.\n/signalstatus for dashboard | /signals to toggle off",
            parse_mode="Markdown")
        if ADMIN_CHAT_ID and str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
            try:
                await application.bot.send_message(chat_id=int(ADMIN_CHAT_ID),
                    text="🔒 *Signals ON* — $50 bankroll | 20% stop | 8% max | 0.25x Kelly",
                    parse_mode="Markdown")
            except Exception: pass
    else:
        await update.message.reply_text("📊 Signals: OFF")

async def signalstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not polymarket_enabled:
        await update.message.reply_text("📊 Signals OFF. /signals to enable"); return
    mlines = []
    for _, mkt in list(tracked_markets.items())[:5]:
        d = mkt["posterior"] - mkt["price"]
        mlines.append(f"  {mkt['name'][:35]}\n    {mkt['price']:.2%} → {mkt['posterior']:.2%} "
                       f"({'↑' if d > 0 else '↓'}{abs(d):.2%})")
    mt = "\n".join(mlines) or "  Loading..."
    if update.effective_chat.type == "private" and is_admin(update.effective_user):
        n, wins = len(signal_trades), sum(1 for t in signal_trades if t.get("won"))
        pnl = signal_bankroll - 50.0
        dd = (signal_peak - signal_bankroll) / signal_peak if signal_peak else 0
        await update.message.reply_text(
            f"🔒 *Dashboard*\n\nStatus: 🟢 ACTIVE | Markets: {len(tracked_markets)}\n"
            f"💰 ${signal_bankroll:.2f} | PnL: ${pnl:+.2f} | DD: {dd:.1%}\n"
            f"Stop: ${signal_peak*(1-TRAIL_STOP):.2f} | Trades: {n} (W:{wins} L:{n-wins})\n"
            f"Streak: {signal_consecutive_losses}\n\n{mt}", parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"📊 *Scanner*\n\n🟢 ACTIVE | {len(tracked_markets)} markets\n\n{mt}",
            parse_mode="Markdown")

async def botbalance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    if not POLYMARKET_PK:
        await update.message.reply_text("⚠️ Set POLYMARKET_PRIVATE_KEY in Render."); return
    bal = await clob_balance()
    ws = f"{POLYMARKET_WALLET[:6]}...{POLYMARKET_WALLET[-4:]}" if POLYMARKET_WALLET else "n/a"
    await update.message.reply_text(
        f"🔒 *Wallet*\n`{ws}`\n"
        f"USDC: {'${:.2f}'.format(bal) if bal is not None else 'fetch failed'}\n"
        f"Mode: {'LIVE' if TRADING_MODE=='live' else 'PAPER'} | "
        f"CLOB: {'✅' if clob_ready else '❌'} | "
        f"{'⏸' if trading_paused else '▶️'}\n"
        f"Paper: ${signal_bankroll:.2f} (peak ${signal_peak:.2f})", parse_mode="Markdown")

async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 DM only."); return
    if not POLYMARKET_PK or not PERSONAL_WALLET:
        await update.message.reply_text("⚠️ Need POLYMARKET_PRIVATE_KEY + YOUR_PERSONAL_WALLET"); return
    bal = await clob_balance()
    if not bal or bal <= 0.01:
        await update.message.reply_text(f"⚠️ No balance (${bal or 0:.2f})"); return
    ws = f"{PERSONAL_WALLET[:6]}...{PERSONAL_WALLET[-4:]}"
    if not context.user_data.get("withdraw_confirmed"):
        context.user_data.update({"withdraw_confirmed": True, "withdraw_amount": bal})
        await update.message.reply_text(
            f"⚠️ *Confirm:* ${bal:.2f} USDC → `{ws}`\n/withdraw to confirm | /cancel to abort",
            parse_mode="Markdown"); return
    amount = context.user_data.get("withdraw_amount", bal)
    context.user_data["withdraw_confirmed"] = False
    await update.message.reply_text(f"📤 Sending ${amount:.2f}...")
    ok, res = await withdraw_usdc(PERSONAL_WALLET, amount)
    if ok:
        await update.message.reply_text(
            f"✅ ${amount:.2f} → `{ws}`\nhttps://polygonscan.com/tx/0x{res}", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Failed: {res}")

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["withdraw_confirmed"] = False
    await update.message.reply_text("✅ Cancelled.")

async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    trading_paused = True
    await update.message.reply_text("⏸ *Trading PAUSED* — /resume to restart", parse_mode="Markdown")

async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    trading_paused = False
    await update.message.reply_text(
        f"▶️ *Trading RESUMED* — {'LIVE' if TRADING_MODE=='live' else 'PAPER'} | "
        f"CLOB: {'✅' if clob_ready else '❌'}", parse_mode="Markdown")

async def testconn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    lines = ["🔧 *Connection Test*\n",
        f"{'✅' if POLYMARKET_PK else '❌'} PRIVATE_KEY",
        f"{'✅' if POLYMARKET_WALLET else '❌'} WALLET_ADDRESS",
        f"{'✅' if PERSONAL_WALLET else '❌'} PERSONAL_WALLET",
        f"Mode: {TRADING_MODE} | CLOB: {'✅' if clob_ready else '❌'}"]
    if clob_ready:
        bal = await clob_balance()
        lines.append(f"Balance: {'${:.2f}'.format(bal) if bal else 'failed'}")
    try:
        mkts = await fetch_poly_markets()
        lines.append(f"Polymarket: {len(mkts)} markets")
    except Exception as e: lines.append(f"Polymarket: {e}")
    try:
        p = await fetch_btc_eth()
        lines.append(f"CoinGecko: BTC ${p.get('bitcoin',{}).get('usd',0):,.0f}")
    except Exception as e: lines.append(f"CoinGecko: {e}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def golive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 DM only."); return
    if TRADING_MODE != "live":
        await update.message.reply_text(
            "⚠️ TRADING_MODE is 'paper'.\n"
            "Render → Environment → TRADING_MODE=live → Redeploy → /testconnection"); return
    if not clob_ready:
        await update.message.reply_text("❌ CLOB not connected. /testconnection first."); return
    bal = await clob_balance()
    await update.message.reply_text(
        f"🟢 *LIVE TRADING ACTIVE*\n"
        f"Balance: ${bal:.2f} | {'▶️' if not trading_paused else '⏸'}\n"
        f"/pause | /withdraw | /botbalance", parse_mode="Markdown")

# ── Scheduled: Daily Balance DM ──────────────────────────────────────────────
async def send_daily_balance():
    if not ADMIN_CHAT_ID: return
    bal = await clob_balance() if clob_ready else None
    pnl = signal_bankroll - 50.0
    dd = (signal_peak - signal_bankroll) / signal_peak if signal_peak else 0
    n = len(signal_trades); wins = sum(1 for t in signal_trades if t.get("won"))
    try:
        await application.bot.send_message(chat_id=int(ADMIN_CHAT_ID), parse_mode="Markdown",
            text=(f"📊 *Daily Report*\n"
                  f"{'💰 $'+f'{bal:.2f}' if bal else '📝 Paper'} | "
                  f"Bank: ${signal_bankroll:.2f} | PnL: ${pnl:+.2f}\n"
                  f"DD: {dd:.1%} | Trades: {n} (W:{wins} L:{n-wins})\n"
                  f"{'LIVE' if TRADING_MODE=='live' else 'PAPER'} | "
                  f"{'⏸' if trading_paused else '▶️'}"))
    except Exception as e: print(f"⚠️ Daily balance DM: {e}")

# ── Scheduled: Keep-alive ping ───────────────────────────────────────────────
async def self_ping():
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            await s.get(f"https://beefy-bot.onrender.com/ping")
    except Exception: pass

# ── Safe scheduler wrapper ───────────────────────────────────────────────────
async def safe_job(coro_func, timeout=30):
    try: await asyncio.wait_for(coro_func(), timeout=timeout)
    except asyncio.TimeoutError: print(f"⚠️ Job timed out: {coro_func.__name__}")
    except Exception as e: print(f"⚠️ Job error ({coro_func.__name__}): {e}")

# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/ping", methods=["GET"])
async def ping(): return "pong", 200

@app.route("/", methods=["GET"])
async def home(): return "🐂 Beefy Quant Engine — Running"

@app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    data = await request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "OK"

# ── Startup ──────────────────────────────────────────────────────────────────
def register_handlers():
    h = application.add_handler
    h(CommandHandler("start", start));           h(CommandHandler("help", help_cmd))
    h(CommandHandler("signals", signals_cmd));   h(CommandHandler("signalstatus", signalstatus_cmd))
    h(CommandHandler("botbalance", botbalance_cmd))
    h(CommandHandler("withdraw", withdraw_cmd)); h(CommandHandler("cancel", cancel_cmd))
    h(CommandHandler("pause", pause_cmd));       h(CommandHandler("resume", resume_cmd))
    h(CommandHandler("testconnection", testconn_cmd))
    h(CommandHandler("golive", golive_cmd))

@app.before_serving
async def startup():
    register_handlers()
    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook: {WEBHOOK_URL}")
    await init_clob()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(safe_job, "interval", seconds=60, args=[run_signal_scan, 30])
    scheduler.add_job(safe_job, "cron", hour=8, minute=5, args=[send_daily_balance, 15])
    scheduler.add_job(safe_job, "interval", minutes=10, args=[self_ping, 10])
    scheduler.start()
    print(f"✅ Scheduler: signals 60s | balance 08:05 | ping 10m")
    print(f"📋 Mode: {TRADING_MODE} | CLOB: {'ready' if clob_ready else 'paper'}")

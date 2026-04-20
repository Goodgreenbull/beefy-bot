# signals.py — GGB Beefy Bot | Polymarket Signal System + Trading
import os, math, aiohttp
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import ContextTypes

ADMIN_CHAT_ID     = os.getenv("ADMIN_CHAT_ID")
ADMIN_USERNAME    = os.getenv("ADMIN_USERNAME", "JS0nbase")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
TRADING_MODE      = os.getenv("TRADING_MODE", "paper")
POLYMARKET_PK     = os.getenv("POLYMARKET_PRIVATE_KEY")
POLYMARKET_WALLET = os.getenv("POLYMARKET_WALLET_ADDRESS")
PERSONAL_WALLET   = os.getenv("YOUR_PERSONAL_WALLET")

def get_app():
    """Late import avoids circular dependency with server.py."""
    import server
    return server.application

def is_admin(user):
    return user.username == ADMIN_USERNAME.lstrip("@")

# Signal state
polymarket_enabled        = False
signal_bankroll           = 50.0
signal_peak               = 50.0
signal_trades             = []
signal_consecutive_losses = 0
signal_is_paused          = False
signal_pause_until        = 0.0
btc_price_history         = []
eth_price_history         = []
tracked_markets           = {}
clob_client               = None
clob_ready                = False
trading_paused            = False

# Signal config
EV_THRESH   = 0.12
FEE_PCT     = 0.02
MIN_CONF    = 0.12
MIN_CORR    = 0.80
KELLY_FRAC  = 0.25
MAX_BET     = 0.08
TRAIL_STOP  = 0.20
COOLDOWN    = 900
PRIOR_DECAY = 0.95
MOVE_THRESH = 0.001

LT = {
    1:  {"big_up":0.72,"small_up":0.58,"flat":0.50,"small_down":0.42,"big_down":0.28},
    5:  {"big_up":0.78,"small_up":0.62,"flat":0.50,"small_down":0.38,"big_down":0.22},
    15: {"big_up":0.82,"small_up":0.65,"flat":0.50,"small_down":0.35,"big_down":0.18},
}

# ── CLOB trading client ─────────────────────────────────────────────────────

async def init_clob_client():
    global clob_client, clob_ready
    if not POLYMARKET_PK or TRADING_MODE != "live":
        print(f"📊 Mode: {TRADING_MODE} (CLOB not init)")
        return
    try:
        from py_clob_client.client import ClobClient
        clob_client = ClobClient("https://clob.polymarket.com", key=POLYMARKET_PK,
                                  chain_id=137, signature_type=0, funder=POLYMARKET_WALLET)
        clob_client.set_api_creds(clob_client.create_or_derive_api_creds())
        clob_ready = True
        print(f"✅ CLOB ready — {POLYMARKET_WALLET[:10]}...")
    except ImportError:
        print("⚠️ py-clob-client missing. Paper mode only.")
    except Exception as e:
        print(f"⚠️ CLOB init: {e}")

async def execute_clob_order(token_id, side, price, size):
    if not clob_ready or not clob_client or trading_paused:
        return None
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        o = OrderArgs(token_id=token_id, price=round(price, 2), size=round(size, 1),
                      side=BUY if side == "BUY" else SELL)
        return clob_client.post_order(clob_client.create_order(o), OrderType.GTC)
    except Exception as e:
        print(f"⚠️ Order: {e}")
        return None

async def get_clob_balance():
    if not clob_ready or not clob_client:
        return None
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        b = clob_client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return float(b["balance"]) / 1e6 if b and "balance" in b else None
    except Exception as e:
        print(f"⚠️ Balance: {e}")
        return None

async def withdraw_usdc(to_addr, amount):
    if not POLYMARKET_PK:
        return False, "No private key"
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        abi = [{"constant":False,"inputs":[{"name":"to","type":"address"},{"name":"value","type":"uint256"}],
                "name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}]
        c = w3.eth.contract(address=Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), abi=abi)
        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(POLYMARKET_WALLET))
        tx = c.functions.transfer(Web3.to_checksum_address(to_addr), int(amount * 1e6)).build_transaction(
            {"from": Web3.to_checksum_address(POLYMARKET_WALLET), "nonce": nonce,
             "gas": 100000, "gasPrice": w3.eth.gas_price, "chainId": 137})
        signed = w3.eth.account.sign_transaction(tx, POLYMARKET_PK)
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(signed.raw_transaction), 60)
        return (True, receipt["transactionHash"].hex()) if receipt["status"] == 1 else (False, "Reverted")
    except Exception as e:
        return False, str(e)

# ── External APIs ───────────────────────────────────────────────────────────

async def fetch_poly_markets():
    markets = []
    for kw in ["bitcoin", "btc", "ethereum", "eth", "crypto"]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://gamma-api.polymarket.com/markets",
                    params={"q": kw, "limit": 5, "active": "true", "closed": "false"},
                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                    for m in await r.json():
                        mid = m.get("id") or m.get("condition_id", "")
                        if mid and mid not in [x.get("id") for x in markets]:
                            markets.append(m)
        except Exception as e:
            print(f"⚠️ Poly ({kw}): {e}")
    return markets

async def fetch_btc_eth():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin,ethereum", "vs_currencies": "usd",
                        "include_24hr_change": "true"},
                timeout=aiohttp.ClientTimeout(total=10)) as r:
                return await r.json()
    except Exception as e:
        print(f"⚠️ CoinGecko: {e}")
        return {}

# ── Core formulas ───────────────────────────────────────────────────────────

def _classify(r):
    if r > 0.01: return "big_up"
    if r > 0.002: return "small_up"
    if r < -0.01: return "big_down"
    if r < -0.002: return "small_down"
    return "flat"

def _likelihood(r, w, corr=1.0):
    base = LT.get(w, LT[5])[_classify(r)]
    return max(0.01, min(0.99, 0.5 + (base - 0.5) * corr))

def _bayes(prior, r, w, corr):
    ph = 0.5 + (prior - 0.5) * PRIOR_DECAY
    peh = _likelihood(r, w, corr)
    pe = peh * ph + (1 - peh) * (1 - ph)
    return max(0.05, min(0.95, (peh * ph) / max(pe, 1e-10)))

def _ev(p, mkt): return (p - mkt) - mkt * FEE_PCT

def _kelly(p, mkt):
    if mkt <= 0 or mkt >= 1: return 0.0
    odds = 1 / mkt - 1
    return max(0.0, min(1.0, (p * odds - (1 - p)) / odds)) if odds > 0 else 0.0

def _kl(p, q):
    p, q = max(0.001, min(0.999, p)), max(0.001, min(0.999, q))
    return p * math.log(p / q) + (1 - p) * math.log((1 - p) / (1 - q))

def _bet(bankroll, p, mkt):
    return max(0, min(_kelly(p, mkt) * KELLY_FRAC * bankroll,
                      bankroll * MAX_BET, bankroll - 2.0))

# ── Scanner (runs every 60s via scheduler) ──────────────────────────────────

async def run_signal_scan():
    global signal_bankroll, signal_peak, signal_consecutive_losses
    global signal_is_paused, signal_pause_until, tracked_markets

    if not polymarket_enabled or not TELEGRAM_GROUP_ID:
        return
    now = datetime.now(timezone.utc).timestamp()
    if signal_is_paused:
        if now < signal_pause_until: return
        signal_is_paused = False
    if signal_bankroll <= signal_peak * (1 - TRAIL_STOP):
        return

    prices = await fetch_btc_eth()
    btc = prices.get("bitcoin", {}).get("usd")
    if not btc: return

    ts = datetime.now(timezone.utc).timestamp()
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
            corr = (0.90 if any(k in nl for k in ["btc", "bitcoin", "$100k", "$90k"])
                    else 0.82 if any(k in nl for k in ["eth", "ethereum"])
                    else 0.78 if any(k in nl for k in ["crypto", "market cap"])
                    else 0.50)
            if corr >= MIN_CORR:
                tracked_markets[mid] = {"name": name, "price": p, "posterior": p,
                                         "correlation": corr, "last_signal_ts": 0, "updates": 0}
        if tracked_markets:
            print(f"📊 Tracking {len(tracked_markets)} markets")

    app = get_app()
    for mid, mkt in tracked_markets.items():
        for w in sorted(rets):
            if abs(rets[w]) >= MOVE_THRESH:
                mkt["posterior"] = _bayes(mkt["posterior"], rets[w], w, mkt["correlation"])
                mkt["updates"] += 1
        ev = _ev(mkt["posterior"], mkt["price"])
        kl = _kl(mkt["posterior"], mkt["price"])
        conf = abs(mkt["posterior"] - mkt["price"])
        if ev < EV_THRESH or conf < MIN_CONF: continue
        confirms = 1 + (kl >= 0.10) + (conf >= 0.20)
        if confirms < 2 or ts - mkt["last_signal_ts"] < COOLDOWN: continue
        bet = _bet(signal_bankroll, mkt["posterior"], mkt["price"])
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
                        tid, ts_, tp = tokens[0].get("token_id", ""), "BUY", mkt["price"]
                    else:
                        tid = tokens[1].get("token_id", "") if len(tokens) > 1 else tokens[0].get("token_id", "")
                        ts_ = "BUY" if len(tokens) > 1 else "SELL"
                        tp = 1 - mkt["price"] if len(tokens) > 1 else mkt["price"]
                    if tid:
                        resp = await execute_clob_order(tid, ts_, tp, bet / tp)
                        if resp:
                            trade_status = f"✅ ORDER — {str(resp.get('orderID', resp.get('id', '?')))[:12]}..."
                            rb = await get_clob_balance()
                            if rb:
                                signal_bankroll = rb
                                signal_peak = max(signal_peak, rb)
                        else:
                            trade_status = "⚠️ Order failed — paper"
            except Exception:
                pass

        dd = (signal_peak - signal_bankroll) / signal_peak if signal_peak else 0
        try:
            await app.bot.send_message(chat_id=int(TELEGRAM_GROUP_ID), parse_mode="Markdown",
                text=f"🔔 *POLYMARKET SIGNAL*\n━━━━━━━━━━━━━━━\n"
                     f"📊 *{mkt['name']}*\n📌 {direction}\n"
                     f"💰 Mkt: {mkt['price']:.2%} → Belief: {mkt['posterior']:.2%}\n"
                     f"📈 EV: {ev:+.2%} | 🧠 {confirms}/3\n━━━━━━━━━━━━━━━\n"
                     f"BTC: ${btc:,.0f} | Updates: {mkt['updates']}\n_not financial advice_")
        except Exception as e:
            print(f"⚠️ Group signal: {e}")
        if ADMIN_CHAT_ID:
            try:
                await app.bot.send_message(chat_id=int(ADMIN_CHAT_ID), parse_mode="Markdown",
                    text=f"🔒 *PRIVATE*\n━━━━━━━━━━━━━━━\n📊 *{mkt['name']}*\n📌 {direction}\n"
                         f"💰 {mkt['price']:.2%}→{mkt['posterior']:.2%} | EV:{ev:+.2%}\n"
                         f"KL:{kl:.4f} | {confirms}/3 | Bet:${bet:.2f} | Roll:${signal_bankroll:.2f}\n"
                         f"DD:{dd:.1%} | Streak:{signal_consecutive_losses}\n━━━━━━━━━━━━━━━\n"
                         f"🤖 {trade_status} | {'🟢 LIVE' if TRADING_MODE == 'live' else '📝 PAPER'}")
            except Exception as e:
                print(f"⚠️ Admin DM: {e}")
        print(f"📊 Signal: {mkt['name']} — {direction} — ${bet:.2f}")
        mkt["posterior"] = 0.6 * mkt["price"] + 0.4 * mkt["posterior"]

# ── Commands ────────────────────────────────────────────────────────────────

async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global polymarket_enabled, tracked_markets, signal_bankroll, signal_peak
    global signal_trades, signal_consecutive_losses, signal_is_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    polymarket_enabled = not polymarket_enabled
    if polymarket_enabled:
        tracked_markets, signal_trades = {}, []
        signal_bankroll = signal_peak = 50.0
        signal_consecutive_losses = 0
        signal_is_paused = False
        await update.message.reply_text(
            "📊 *Signals: ON*\n\nScanning every 60s.\nGroup: clean | You: full DM details.\n/signalstatus or /signals to toggle.",
            parse_mode="Markdown")
        if ADMIN_CHAT_ID and str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
            try:
                await get_app().bot.send_message(chat_id=int(ADMIN_CHAT_ID),
                    text="🔒 *Signals ON*\n$50 bankroll | 20% stop | 8% max bet | 0.25x Kelly",
                    parse_mode="Markdown")
            except Exception: pass
    else:
        await update.message.reply_text("📊 Signals: OFF")

async def signalstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not polymarket_enabled:
        await update.message.reply_text("📊 Signals OFF. /signals to enable"); return
    mlines = []
    for _, mkt in list(tracked_markets.items())[:5]:
        d = mkt["posterior"] - mkt["price"]
        mlines.append(f"  {mkt['name'][:35]}\n    {mkt['price']:.2%}→{mkt['posterior']:.2%} ({'↑' if d > 0 else '↓'}{abs(d):.2%})")
    mt = "\n".join(mlines) or "  Loading..."
    if update.effective_chat.type == "private" and is_admin(update.effective_user):
        n, wins = len(signal_trades), sum(1 for t in signal_trades if t.get("won"))
        pnl = signal_bankroll - 50.0
        dd = (signal_peak - signal_bankroll) / signal_peak if signal_peak else 0
        await update.message.reply_text(
            f"🔒 *Dashboard*\n🟢 {len(tracked_markets)} markets\n"
            f"💰 ${signal_bankroll:.2f} | PnL: ${pnl:+.2f}\n"
            f"DD: {dd:.1%} | Stop: ${signal_peak * (1 - TRAIL_STOP):.2f}\n"
            f"Trades: {n} W:{wins} L:{n - wins} | Streak: {signal_consecutive_losses}\n\n{mt}",
            parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"📊 *Signal Scanner*\n🟢 {len(tracked_markets)} markets\n\n{mt}\n\n_DM @{ADMIN_USERNAME}_",
            parse_mode="Markdown")

async def botbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    if not POLYMARKET_PK:
        await update.message.reply_text("⚠️ Set POLYMARKET_PRIVATE_KEY in Render."); return
    bal = await get_clob_balance()
    ws = f"{POLYMARKET_WALLET[:6]}...{POLYMARKET_WALLET[-4:]}" if POLYMARKET_WALLET else "not set"
    bal_str = "${:.2f}".format(bal) if bal is not None else "fetch failed"
    await update.message.reply_text(
        f"🔒 *Wallet*\n`{ws}`\nUSDC: {bal_str}\n"
        f"{'🟢 LIVE' if TRADING_MODE == 'live' else '📝 PAPER'} | {'✅' if clob_ready else '❌'} CLOB | {'⏸' if trading_paused else '▶️'}\n"
        f"Paper: ${signal_bankroll:.2f} (peak ${signal_peak:.2f})", parse_mode="Markdown")

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 Private chat only."); return
    if not POLYMARKET_PK or not PERSONAL_WALLET:
        await update.message.reply_text("⚠️ Need POLYMARKET_PRIVATE_KEY + YOUR_PERSONAL_WALLET"); return
    bal = await get_clob_balance()
    if not bal or bal <= 0.01:
        await update.message.reply_text(f"⚠️ No balance (${bal or 0:.2f})"); return
    ws = f"{PERSONAL_WALLET[:6]}...{PERSONAL_WALLET[-4:]}"
    if not context.user_data.get("withdraw_confirmed"):
        context.user_data.update({"withdraw_confirmed": True, "withdraw_amount": bal})
        await update.message.reply_text(
            f"⚠️ *Confirm*\n${bal:.2f} → `{ws}`\n/withdraw again | /cancel",
            parse_mode="Markdown"); return
    amount = context.user_data.get("withdraw_amount", bal)
    context.user_data["withdraw_confirmed"] = False
    await update.message.reply_text(f"📤 Sending ${amount:.2f}...")
    ok, res = await withdraw_usdc(PERSONAL_WALLET, amount)
    if ok:
        await update.message.reply_text(
            f"✅ Done\n${amount:.2f} → `{ws}`\nhttps://polygonscan.com/tx/0x{res}",
            parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Failed: {res}")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["withdraw_confirmed"] = False
    await update.message.reply_text("✅ Cancelled.")

async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    trading_paused = True
    await update.message.reply_text("⏸ *Trading PAUSED*\nSignals still run. /resume to restart.", parse_mode="Markdown")

async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_paused
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    trading_paused = False
    await update.message.reply_text(
        f"▶️ *Resumed*\n{'🟢 LIVE' if TRADING_MODE == 'live' else '📝 PAPER'} | {'✅' if clob_ready else '❌'} CLOB",
        parse_mode="Markdown")

async def testconnection_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    lines = ["🔧 *Connection Test*",
             f"{'✅' if POLYMARKET_PK else '❌'} POLYMARKET_PK: {'set' if POLYMARKET_PK else 'MISSING'}",
             f"{'✅' if POLYMARKET_WALLET else '❌'} WALLET: {'set' if POLYMARKET_WALLET else 'MISSING'}",
             f"{'✅' if PERSONAL_WALLET else '❌'} PERSONAL_WALLET: {'set' if PERSONAL_WALLET else 'MISSING'}",
             f"📋 Mode: {TRADING_MODE}",
             f"{'✅' if clob_ready else '❌'} CLOB: {'connected' if clob_ready else 'not connected'}"]
    if clob_ready:
        bal = await get_clob_balance()
        lines.append(f"{'✅' if bal else '⚠️'} Balance: {'${:.2f}'.format(bal) if bal else 'failed'}")
    try:
        mkts = await fetch_poly_markets()
        lines.append(f"✅ Polymarket: {len(mkts)} markets")
    except Exception as e:
        lines.append(f"❌ Polymarket: {e}")
    try:
        p = await fetch_btc_eth()
        lines.append(f"✅ CoinGecko: BTC ${p.get('bitcoin', {}).get('usd', 0):,.0f}")
    except Exception as e:
        lines.append(f"❌ CoinGecko: {e}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def golive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Admin only."); return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 Private chat only."); return
    if TRADING_MODE != "live":
        await update.message.reply_text(
            "⚠️ Mode is 'paper'.\n\nTo go live: Render → Environment → TRADING_MODE=live → Redeploy → /testconnection"); return
    if not clob_ready:
        await update.message.reply_text("❌ CLOB not connected. Run /testconnection first."); return
    bal = await get_clob_balance()
    await update.message.reply_text(
        f"🟢 *LIVE ACTIVE*\nBalance: ${bal:.2f}\n{'▶️' if not trading_paused else '⏸'}\n/pause | /withdraw | /botbalance",
        parse_mode="Markdown")

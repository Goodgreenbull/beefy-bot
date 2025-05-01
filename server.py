# File: server.py

import os
import asyncio
import random
import aiohttp
import nest_asyncio
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# --- Environment Variables ---
TOKEN = os.getenv("BOT_TOKEN")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@JS0nbase")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"

# --- Flask App ---
app = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# --- Anti-Spam Tracker ---
user_spam_tracker = {}
spam_detection_enabled = True
max_messages = 3

# --- Bull Quotes List ---
bull_quotes = [
    "Hold the line. Green is coming. 🐂💚",
    "Strength grows in the green fields. 🐂🌿",
    "In the end, bulls always win. 🐂💚",
    "Courage is contagious. So is green. 🐂🌿",
    "Herd strong. Market stronger. 🐂💚",
    "Bulls build. Bears retreat. 🐂💚",
    "The pasture belongs to the bold. 🐂🌿",
    "Plant seeds now. Harvest green later. 🐂🌿",
    "Only bulls brave the storms. 🐂💚",
    "Stay bullish. Stay legendary. 🐂💚"
]

# --- Helper Functions ---
async def fetch_price():
    url = "https://api.dexscreener.com/latest/dex/tokens/0xc2758c05916ba20b19358f1e96f597774e603050"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            price = data['pairs'][0]['priceUsd']
            return f"${float(price):.6f}"

async def fetch_wallet_balance(address: str):
    url = f"https://api.basescan.org/api?module=account&action=tokenbalance&contractaddress=0xc2758c05916ba20b19358f1e96f597774e603050&address={address}&tag=latest&apikey={BASESCAN_API_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            if data['status'] == '1':
                balance = int(data['result']) / 10**18
                return balance
            else:
                return None

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🐂 Bull Quote", callback_data='bull')],
        [InlineKeyboardButton("📈 GGB Price", callback_data='price')],
        [InlineKeyboardButton("🌐 Website", url="https://goodgreenbull.com")],
        [InlineKeyboardButton("🕊️ X (Twitter)", url="https://x.com/goodgreenbull")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome to Good Green Bull! Choose an option:", reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Open menu\n/help - List Commands\n/price - Get GGB Price\n/bull - Bullish Quote\n/wallet <address> - Check GGB Balance\n/token - View Token Info\n/settings - Admin Settings"
    )

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = await fetch_price()
    await update.message.reply_text(f"💵 GGB Price: {price}\nhttps://tinyurl.com/GGBDex")

async def bull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quote = random.choice(bull_quotes)
    await update.message.reply_text(quote)

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        address = context.args[0]
    except IndexError:
        await update.message.reply_text("Please provide a wallet address!")
        return

    balance = await fetch_wallet_balance(address)
    if balance is not None:
        await update.message.reply_text(f"Wallet {address} holds {balance:.2f} GGB")
    else:
        await update.message.reply_text("Could not fetch wallet data. Please try again.")

async def token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📈 Good Green Bull Token Info:\n"
        "Name: Good Green Bull\nSymbol: $GGB\nDecimals: 18\nContract: 0xc2758c05916ba20b19358f1e96f597774e603050"
    )

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username != ADMIN_USERNAME.lstrip("@"):  # Only admin can open settings
        await update.message.reply_text("Only Admins can access settings!")
        return

    keyboard = [
        [InlineKeyboardButton("✅ Price Alerts ON/OFF", callback_data='toggle_price')],
        [InlineKeyboardButton("❌ Anti-Spam ON/OFF", callback_data='toggle_spam')],
        [InlineKeyboardButton("➕ Add Bull Quote", callback_data='add_quote')],
        [InlineKeyboardButton("🎭 Toggle Personality", callback_data='toggle_personality')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("⚙️ Admin Settings:", reply_markup=reply_markup)

# Track beefy personality state
beefy_personality = True

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    global spam_detection_enabled, beefy_personality

    if query.data == 'bull':
        await bull(update, context)
    elif query.data == 'price':
        await price(update, context)
    elif query.data == 'toggle_spam':
        spam_detection_enabled = not spam_detection_enabled
        state = "ON" if spam_detection_enabled else "OFF"
        await query.edit_message_text(text=f"Anti-Spam filter is now {state}.")
    elif query.data == 'toggle_personality':
        beefy_personality = not beefy_personality
        state = "ENABLED" if beefy_personality else "DISABLED"
        await query.edit_message_text(text=f"Beefy's personality replies are now {state}.")
    elif query.data == 'add_quote':
        await query.edit_message_text(text="Send me the new bull quote now. 🐂")
        context.user_data['awaiting_quote'] = True
    else:
        await query.edit_message_text(text="Settings toggled (placeholder)")

# --- Spam Detection ---
async def detect_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not spam_detection_enabled:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    user_spam_tracker.setdefault(user_id, []).append(update.message.date)

    if len(user_spam_tracker[user_id]) > max_messages:
        await context.bot.restrict_chat_member(chat_id, user_id, permissions={})
        await context.bot.send_message(chat_id, text=f"User {user_id} muted for spamming.")

# --- Personality + Quote Add Handler ---
async def text_responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_quote'):
        new_quote = update.message.text.strip()
        if new_quote:
            bull_quotes.append(new_quote)
            await update.message.reply_text("✅ New bull quote added!")
        else:
            await update.message.reply_text("❌ Invalid quote.")
        context.user_data['awaiting_quote'] = False
        return

    if beefy_personality and "/beefy" in update.message.text.lower():
        replies = [
            "I don’t know about these other meme guys, but the $GGB charts are looking GREEN and BEEFY!",
            "We build. We bull. We buy $GGB. No distractions. ",
            "Don't ask me about other tokens. I'm loyal to the herd. $GGB all day. *Not financial advice* ",
            "You already know the answer. Zoom out, stay bullish, and HODL $GGB!",
            "GGB is built different. I'm not here for games, I'm here for the green."
        ]
        await update.message.reply_text(random.choice(replies))

# --- Flask Routes ---
@app.route("/", methods=["GET"])
def home():
    return "🐂 BeefyTheBull Bot is Alive!"

@app.route("/status")
def status():
    return "Bot is running ✅"

@app.route(WEBHOOK_PATH, methods=["GET", "POST"])
async def webhook():
    if request.method == "GET":
        return "Webhook is active."
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "OK"

# --- Main Startup ---
async def main():
    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook correctly set: {WEBHOOK_URL}")
    await application.start()
    await application.updater.start_polling()
    await application.updater.idle()

if __name__ == "__main__":
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("bull", bull))
    application.add_handler(CommandHandler("wallet", wallet))
    application.add_handler(CommandHandler("token", token))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CallbackQueryHandler(button))

    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_responder))
    application.add_handler(MessageHandler(filters.Regex("(?i)^/beefy"), text_responder))

    nest_asyncio.apply()
    asyncio.run(main())

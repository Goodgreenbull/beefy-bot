from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import os
import telegram
import asyncio
import json
import random
import requests

# Load or initialize settings
CONFIG_FILE = "config.json"

if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r") as f:
        settings = json.load(f)
else:
    settings = {
        "spam_filter": True,
        "welcome_enabled": True,
        "token_lookup_enabled": True
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(settings, f)

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"

app = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# === Core Command Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to the Good Green Bull Herd! Type /help to see commands!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    commands = [
        "/start", "/help", "/price", "/holders", "/contract", "/links", "/quote", "/meme", "/settings"
    ]
    await update.message.reply_text("Available Commands:\\n" + "\\n".join(commands))

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = "https://api.dexscreener.com/latest/dex/pairs/base/0xf58523c1e3b794ba41ef085147f9136c128649de"
        response = requests.get(url).json()
        price_usd = response.get("pair", {}).get("priceUsd", "N/A")
        volume = response.get("pair", {}).get("volume", {}).get("h24", "N/A")
        await update.message.reply_text(f"$GGB Price: ${price_usd}\\n24h Volume: ${volume}")
    except:
        await update.message.reply_text("Error fetching price!")

async def holders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        resp = requests.get("https://api.basescan.org/api?module=stats&action=tokensupply&contractaddress=0xc2758c05916ba20b19358f1e96f597774e603050&apikey=YourBaseScanAPIKey")
        holders_count = "106+ (estimated)"  # Static for now or update manually
        await update.message.reply_text(f"Current GGB Holders: {holders_count}")
    except:
        await update.message.reply_text("Error fetching holders!")

async def contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("GGB Contract: 0xc2758c05916ba20b19358f1e96f597774e603050")

async def links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Website: https://GoodGreenBull.com\\n"
        "Telegram: https://t.me/GoodGreenBulls\\n"
        "X: https://x.com/goodgreenbull?s=21\\n"
        "Dex: https://tinyurl.com/GGBDex"
    )

async def quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quotes = [
        "“Hold the line. Green is coming.”",
        "“Chop builds champions.”",
        "“Strong horns, stronger hands.”"
    ]
    await update.message.reply_text(random.choice(quotes))

async def meme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memes = [
        "https://i.imgur.com/1FQn6Xk.png",
        "https://i.imgur.com/fQf9kUr.jpeg"
    ]
    await update.message.reply_photo(random.choice(memes))

# === Spam Filter ===
async def check_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not settings.get("spam_filter", True):
        return
    text = update.message.text.lower()
    scam_words = ["airdrop", "claim", "giveaway", "free eth"]
    if any(word in text for word in scam_words):
        await update.message.delete()

# === Token Lookup via @Mention ===
async def mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if settings.get("token_lookup_enabled", True):
        if update.message.text and "0x" in update.message.text:
            addr = update.message.text.split("0x")[1][:40]
            addr = "0x" + addr
            url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
            try:
                r = requests.get(url).json()
                info = r.get("pairs", [{}])[0]
                price = info.get("priceUsd", "N/A")
                await update.message.reply_text(f"Token Lookup:\\nAddress: {addr}\\nPrice: ${price}")
            except:
                await update.message.reply_text(f"Could not fetch token info for {addr}")

# === Settings Menu ===
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [{"text": "Toggle Spam Filter", "callback_data": "toggle_spam"}],
        [{"text": "Toggle Welcome Msg", "callback_data": "toggle_welcome"}],
        [{"text": "Toggle Token Lookup", "callback_data": "toggle_lookup"}]
    ]
    reply_markup = {"inline_keyboard": keyboard}
    await update.message.reply_text("⚙️ Beefy Settings Menu", reply_markup=telegram.InlineKeyboardMarkup(reply_markup))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "toggle_spam":
        settings["spam_filter"] = not settings["spam_filter"]
    elif data == "toggle_welcome":
        settings["welcome_enabled"] = not settings["welcome_enabled"]
    elif data == "toggle_lookup":
        settings["token_lookup_enabled"] = not settings["token_lookup_enabled"]
    with open(CONFIG_FILE, "w") as f:
        json.dump(settings, f)
    await query.edit_message_text(f"✅ Settings updated!")

# === Routing ===
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("price", price))
application.add_handler(CommandHandler("holders", holders))
application.add_handler(CommandHandler("contract", contract))
application.add_handler(CommandHandler("links", links))
application.add_handler(CommandHandler("quote", quote))
application.add_handler(CommandHandler("meme", meme))
application.add_handler(CommandHandler("settings", settings_command))
application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_spam))
application.add_handler(MessageHandler(filters.Entity("mention"), mention_handler))
application.add_handler(CallbackQueryHandler(button_callback))

# Health Check
@app.route("/", methods=["GET"])
def index():
    return "Beefy Bot is online!"

# Webhook to receive Telegram updates
@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put(update)
    return "ok"

if __name__ == "__main__":
    async def setup():
        bot = telegram.Bot(token=TOKEN)
        await bot.set_webhook(WEBHOOK_URL)
    asyncio.run(setup())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

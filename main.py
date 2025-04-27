# File: main.py

import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Environment Variables ---
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"

# --- Flask App ---
app = Flask(__name__)

# --- Telegram Bot Application ---
application = ApplicationBuilder().token(TOKEN).build()

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐂 Welcome to the Good Green Bull Herd! Type /help for commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 Available commands:\n"
        "/start - Join the Herd\n"
        "/help - List Commands\n"
        "/price - Check GGB Price\n"
        "/contract - GGB Contract Address\n"
        "/bull - Motivational Bull Quote\n"
        "/settings - Coming Soon!"
    )

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💵 Check the GGB price: https://tinyurl.com/GGBDex")

async def contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📄 GGB Contract Address: 0xc2758c05916ba20b19358f1e96f597774e603050")

async def bull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\"Hold the line. Green is coming.\" - Good Green Bull 🐂💚")

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Settings menu coming soon!")

# --- Register Command Handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("price", price))
application.add_handler(CommandHandler("contract", contract))
application.add_handler(CommandHandler("bull", bull))
application.add_handler(CommandHandler("settings", settings))

# --- Flask Routes ---

@app.route("/", methods=["GET"])
def index():
    return "🐂 Beefy Bot is Alive!"

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    """Receive updates from Telegram and process them."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    asyncio.create_task(application.process_update(update))
    return "OK", 200

# --- Run Flask ---
if __name__ == "__main__":
    async def setup_webhook():
        await application.initialize()
        await application.bot.set_webhook(url=WEBHOOK_URL)
        print(f"✅ Webhook set at {WEBHOOK_URL}")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(setup_webhook())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

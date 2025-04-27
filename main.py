import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Setup ---
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"

# --- Flask App ---
app = Flask(__name__)

# --- Telegram Bot Application ---
application = ApplicationBuilder().token(TOKEN).build()

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐂 Welcome to the Good Green Bull Herd! Type /help to see what I can do!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 Available Commands:\n"
        "/start - Welcome Message\n"
        "/help - List Commands\n"
        "/price - Check GGB Token Price\n"
        "/contract - Show GGB Contract\n"
        "/bull - Motivational Quote\n"
        "/settings - Configure Settings"
    )

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💸 Check the GGB price here: https://tinyurl.com/GGBDex")

async def contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 GGB Contract Address:\n0xc2758c05916ba20b19358f1e96f597774e603050")

async def bullquote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐂 \"Hold the line. Green is coming.\" - Good Green Bull")

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Settings menu coming soon!")

# --- Register Command Handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("price", price))
application.add_handler(CommandHandler("contract", contract))
application.add_handler(CommandHandler("bull", bullquote))
application.add_handler(CommandHandler("settings", settings))

# --- Webhook Receiver Endpoint ---
@app.route(WEBHOOK_PATH, methods=["POST"])
async def telegram_webhook():
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, application.bot)
    await application.update_queue.put(update)
    return "ok"

# --- Health Check Endpoint ---
@app.route("/", methods=["GET"])
def index():
    return "✅ Beefy Bot is running!"

# --- Main Async Start ---
async def main():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    await application.updater.start_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=WEBHOOK_URL
    )
    await application.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())

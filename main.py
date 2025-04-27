from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import os
import asyncio

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"

# --- Create Flask app
flask_app = Flask(__name__)

# --- Create Telegram Application
application = ApplicationBuilder().token(TOKEN).build()

# --- Command Handlers

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to the Good Green Bull Herd! Type /help for commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Available commands:\n/start\n/help\n/price\n/contract\n/bull\n/settings")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Check the GGB price here: https://tinyurl.com/GGBDex")

async def contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("GGB Contract Address: 0xc2758c05916ba20b19358f1e96f597774e603050")

async def bullquote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\"Hold the line. Green is coming.\" - Good Green Bull")

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Settings menu coming soon! Stay tuned!")

# --- Add Handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("price", price))
application.add_handler(CommandHandler("contract", contract))
application.add_handler(CommandHandler("bull", bullquote))
application.add_handler(CommandHandler("settings", settings))

# --- Webhook route for Telegram
@flask_app.route(WEBHOOK_PATH, methods=["POST"])
async def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.update_queue.put(update)
    return "ok"

# --- Health check route
@flask_app.route("/", methods=["GET"])
def index():
    return "BeefyBot is online!"

# --- Startup
import asyncio

async def main():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    await application.updater.start_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 10000)), webhook_url=WEBHOOK_URL)
    await application.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())

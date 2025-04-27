import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- ENV Variables ---
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"

# --- Create Flask App ---
flask_app = Flask(__name__)

# --- Create Telegram Application ---
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
    await update.message.reply_text("💬 \"Hold the line. Green is coming.\" - Good Green Bull 🐂💚")

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Settings menu coming soon!")

# --- Register Handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("price", price))
application.add_handler(CommandHandler("contract", contract))
application.add_handler(CommandHandler("bull", bull))
application.add_handler(CommandHandler("settings", settings))

# --- Flask Routes ---

@flask_app.route("/", methods=["GET"])
def index():
    return "🐂 Beefy Bot is online and strong!"

@flask_app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.update_queue.put(update)
    return "OK"

# --- Startup Telegram Bot ---

async def start_bot():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook successfully set to: {WEBHOOK_URL}")

# --- Run App ---

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_bot())
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# --- Tell Hypercorn where the app is ---
app = flask_app

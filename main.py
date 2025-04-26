from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import os
import telegram

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://beefy-bot.onrender.com{WEBHOOK_PATH}"

app = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to the Good Green Bull Herd! Type /help for commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/price - GGB token price\n"
        "/contract - Token address\n"
        "/bull - Wise words from the bull"
    )

async def contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("GGB Contract: 0xc2758c05916ba20b19358f1e96f597774e603050")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Check price here: https://tinyurl.com/GGBDex")

async def bullquote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("“Hold the line. Green is coming.” – Good Green Bull")

# --- Register Commands ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("contract", contract))
application.add_handler(CommandHandler("price", price))
application.add_handler(CommandHandler("bull", bullquote))

# --- Routes ---
@app.route("/", methods=["GET"])
def index():
    return "Beefy Bot is online!"

# --- Correct Async Webhook ---
@app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    if request.method == "POST":
        json_data = await request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)
        await application.process_update(update)
        return jsonify(success=True)

# --- Run Local Dev (Not used in Render) ---
if __name__ == "__main__":
    bot = telegram.Bot(token=TOKEN)
    bot.set_webhook(WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

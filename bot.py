# bot.py
import os
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")

# Create Bot Application
application = ApplicationBuilder().token(TOKEN).build()

# --- Bot Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐂 Welcome to the Good Green Bull Herd! Type /help for commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 Commands:\n"
        "/start - Join the Herd\n"
        "/help - List Commands\n"
        "/price - GGB Price\n"
        "/contract - GGB Contract\n"
        "/bull - Motivational Bull Quote\n"
        "/settings - Coming Soon!"
    )

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💵 GGB price: https://tinyurl.com/GGBDex")

async def contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📄 Contract: 0xc2758c05916ba20b19358f1e96f597774e603050")

async def bull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\"Hold the line. Green is coming.\" 🐂💚")

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Settings are coming soon!")

# --- Register Commands ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("price", price))
application.add_handler(CommandHandler("contract", contract))
application.add_handler(CommandHandler("bull", bull))
application.add_handler(CommandHandler("settings", settings))

# Export application
bot = application.bot

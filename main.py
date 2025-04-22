from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = "7651594810:AAGg0e7rvr7FCIDqS7kPCB6flyxBaHYMvqQ"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to the Good Green Bull Herd! Type /help for commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/price - GGB token price\n/contract - Token address\n/bull - Wise words from the bull")

async def contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("GGB Contract: 0xc2758c05916ba20b19358f1e96f597774e603050")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Check price here: https://tinyurl.com/GGBDex")

async def bullquote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("“Hold the line. Green is coming.” – Good Green Bull")

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("contract", contract))
app.add_handler(CommandHandler("price", price))
app.add_handler(CommandHandler("bull", bullquote))

app.run_polling()

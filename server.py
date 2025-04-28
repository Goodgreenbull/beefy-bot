# server.py
import os
from flask import Flask, request
from telegram import Update
from bot import application, bot

app = Flask(__name__)
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = f"https://beefy-bot.onrender.com/webhook/{TOKEN}"

@app.route("/", methods=["GET"])
def home():
    return "BeefyBot is alive! 🐂"

@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        application.update_queue.put_nowait(update)
        return "OK"
    return "Invalid method", 405

if __name__ == "__main__":
    # Set Webhook
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

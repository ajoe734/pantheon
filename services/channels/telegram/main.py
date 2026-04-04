"""
Channel — Telegram Bot
Receives user messages → calls Router → replies with Agent response.

Env vars:
  TELEGRAM_TOKEN  — bot token from BotFather
  ROUTER_URL      — default http://localhost:8001
"""
import os

import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ROUTER_URL = os.getenv("ROUTER_URL", "http://localhost:8001")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    text = update.message.text or ""

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{ROUTER_URL}/route",
                json={"channel": "telegram", "user_id": user_id, "message": text},
                timeout=30,
            )
            resp.raise_for_status()
            reply = resp.json().get("response", "[no response]")
        except Exception as exc:
            reply = f"[router error: {exc}]"

    await update.message.reply_text(reply, parse_mode="Markdown")


def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()

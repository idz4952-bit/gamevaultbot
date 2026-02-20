import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎮 مرحبا بك في GameVault 🔐")

def main():
    if not TOKEN:
        raise RuntimeError("TOKEN is missing")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    logging.info("Bot is starting polling...")
    app.run_polling()

if __name__ == "__main__":
    main()

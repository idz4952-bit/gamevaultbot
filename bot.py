import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ["TOKEN"]

BTN_EXPLORE = "🛒 Explore Products"
BTN_MANUAL = "📈 MANUAL ORDER"
BTN_ORDERS = "📦 MY ORDERS"
BTN_WALLET = "💵 MY WALLET"
BTN_SUPPORT = "☎️ CONTACT SUPPORT"

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [BTN_EXPLORE, BTN_MANUAL],
        [BTN_ORDERS, BTN_WALLET],
        [BTN_SUPPORT],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ أهلاً بك! اختر من القائمة 👇",
        reply_markup=main_menu_keyboard()
    )

async def on_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == BTN_EXPLORE:
        await update.message.reply_text("🛒 اختر المنتجات")
    elif text == BTN_MANUAL:
        await update.message.reply_text("📈 طلب يدوي")
    elif text == BTN_ORDERS:
        await update.message.reply_text("📦 طلباتك")
    elif text == BTN_WALLET:
        await update.message.reply_text("💵 محفظتك")
    elif text == BTN_SUPPORT:
        await update.message.reply_text("☎️ تواصل مع الدعم")

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_menu_click))

    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()

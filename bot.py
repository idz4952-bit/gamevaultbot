import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ["TOKEN"]

# ===== Keyboards =====

main_menu = ReplyKeyboardMarkup(
    [
        ["🛍 Explore Products", "📝 MANUAL ORDER"],
        ["📦 MY ORDERS", "💰 MY WALLET"],
        ["☎️ CONTACT SUPPORT"],
    ],
    resize_keyboard=True,
)

products_menu = ReplyKeyboardMarkup(
    [
        ["🎮 PUBG MOBILE UC CODES"],
        ["💎 FREE FIRE PINS"],
        ["⭐ LUDO STAR"],
        ["🍏 ITUNES GIFTCARDS"],
        ["🔥 STEAM GIFTCARDS"],
        ["🎮 PLAYSTATION GIFTCARDS"],
        ["🤖 ROBLOX"],
        ["🔙 Back"],
    ],
    resize_keyboard=True,
)

manual_menu = ReplyKeyboardMarkup(
    [
        ["🆔 GAMES ID"],
        ["⚙️ APPLICATION SERVICES"],
        ["🔙 Back"],
    ],
    resize_keyboard=True,
)

wallet_menu = ReplyKeyboardMarkup(
    [
        ["🟣 BYBIT ID", "🟡 BINANCE ID"],
        ["🔗 USDT TRC20", "🔗 USDT BEP20"],
        ["📊 MY TRANSACTIONS"],
        ["🔙 Back"],
    ],
    resize_keyboard=True,
)

# ===== Handlers =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🎮 GameVault\n\nاختر من القائمة 👇"
    await update.message.reply_text(text, reply_markup=main_menu)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # ===== Explore Products =====
    if text == "🛍 Explore Products":
        msg = """🛒 Product Categories:

Explore our premium selection of gaming cards below 👇"""
        await update.message.reply_text(msg, reply_markup=products_menu)

    elif text in [
        "🎮 PUBG MOBILE UC CODES",
        "💎 FREE FIRE PINS",
        "⭐ LUDO STAR",
        "🍏 ITUNES GIFTCARDS",
        "🔥 STEAM GIFTCARDS",
        "🎮 PLAYSTATION GIFTCARDS",
        "🤖 ROBLOX",
    ]:
        await update.message.reply_text(
            f"📦 اخترت:\n{text}\n\nقريبًا سيتم إضافة المنتجات 👍",
            reply_markup=products_menu,
        )

    # ===== Manual Order =====
    elif text == "📝 MANUAL ORDER":
        msg = """💡 Select a service category:

Working Hours: 12 PM - 12 AM
Time Zone: GMT+2"""
        await update.message.reply_text(msg, reply_markup=manual_menu)

    elif text == "🆔 GAMES ID":
        await update.message.reply_text("📩 أرسل Game ID الخاص بك", reply_markup=manual_menu)

    elif text == "⚙️ APPLICATION SERVICES":
        await update.message.reply_text("🛠 اختر الخدمة المطلوبة", reply_markup=manual_menu)

    # ===== Orders =====
    elif text == "📦 MY ORDERS":
        msg = """📦 My Orders

ORDER #12345
✅ Status: COMPLETED
📅 Date: 2026-02-19
🎮 Product: PUBG CHECKER
💰 Total: $10"""
        await update.message.reply_text(msg, reply_markup=main_menu)

    # ===== Wallet =====
    elif text == "💰 MY WALLET":
        msg = """💼 WALLET OVERVIEW

Balance: 74.50$

Choose deposit method 👇"""
        await update.message.reply_text(msg, reply_markup=wallet_menu)

    elif text in ["🟣 BYBIT ID", "🟡 BINANCE ID", "🔗 USDT TRC20", "🔗 USDT BEP20"]:
        await update.message.reply_text("📩 سيتم عرض تفاصيل الدفع هنا", reply_markup=wallet_menu)

    elif text == "📊 MY TRANSACTIONS":
        await update.message.reply_text("📈 لا توجد معاملات حالياً", reply_markup=wallet_menu)

    # ===== Contact =====
    elif text == "☎️ CONTACT SUPPORT":
        await update.message.reply_text("📞 تواصل مع الدعم: @support", reply_markup=main_menu)

    # ===== Back =====
    elif text == "🔙 Back":
        await update.message.reply_text("رجعنا للقائمة الرئيسية 👇", reply_markup=main_menu)

    else:
        await update.message.reply_text("اختر من القائمة 👇", reply_markup=main_menu)


# ===== Run Bot =====

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()

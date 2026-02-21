import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.environ["TOKEN"]

# ===== MENUS =====

def main_menu():
    keyboard = [
        [InlineKeyboardButton("🛍 Explore Products", callback_data="products")],
        [InlineKeyboardButton("📝 MANUAL ORDER", callback_data="manual")],
        [InlineKeyboardButton("📦 MY ORDERS", callback_data="orders")],
        [InlineKeyboardButton("💰 MY WALLET", callback_data="wallet")],
        [InlineKeyboardButton("☎️ CONTACT SUPPORT", callback_data="support")],
    ]
    return InlineKeyboardMarkup(keyboard)


def products_menu():
    keyboard = [
        [InlineKeyboardButton("🎮 PUBG UC", callback_data="pubg")],
        [InlineKeyboardButton("💎 FREE FIRE", callback_data="freefire")],
        [InlineKeyboardButton("⭐ LUDO STAR", callback_data="ludo")],
        [InlineKeyboardButton("🍏 ITUNES", callback_data="itunes")],
        [InlineKeyboardButton("🔥 STEAM", callback_data="steam")],
        [InlineKeyboardButton("🔙 Back", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def manual_menu():
    keyboard = [
        [InlineKeyboardButton("🆔 GAMES ID", callback_data="gamesid")],
        [InlineKeyboardButton("⚙️ APPLICATION SERVICES", callback_data="apps")],
        [InlineKeyboardButton("🔙 Back", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def wallet_menu():
    keyboard = [
        [InlineKeyboardButton("🟣 BYBIT", callback_data="bybit")],
        [InlineKeyboardButton("🟡 BINANCE", callback_data="binance")],
        [InlineKeyboardButton("🔗 TRC20", callback_data="trc20")],
        [InlineKeyboardButton("🔗 BEP20", callback_data="bep20")],
        [InlineKeyboardButton("📊 TRANSACTIONS", callback_data="transactions")],
        [InlineKeyboardButton("🔙 Back", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ===== START =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 GameVault\n\nاختر من القائمة 👇",
        reply_markup=main_menu(),
    )


# ===== BUTTON HANDLER =====

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "products":
        await query.edit_message_text(
            "🛒 Product Categories",
            reply_markup=products_menu(),
        )

    elif data == "manual":
        await query.edit_message_text(
            "💡 اختر نوع الطلب",
            reply_markup=manual_menu(),
        )

    elif data == "wallet":
        await query.edit_message_text(
            "💰 WALLET OVERVIEW\nBalance: 74$",
            reply_markup=wallet_menu(),
        )

    elif data == "orders":
        await query.edit_message_text(
            "📦 Orders\n\nلا توجد طلبات حاليا",
            reply_markup=main_menu(),
        )

    elif data == "support":
        await query.edit_message_text(
            "☎️ تواصل مع الدعم @support",
            reply_markup=main_menu(),
        )

    elif data == "back":
        await query.edit_message_text(
            "🎮 القائمة الرئيسية",
            reply_markup=main_menu(),
        )

    else:
        await query.edit_message_text(
            f"📌 اخترت: {data}",
            reply_markup=main_menu(),
        )


# ===== RUN =====

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()

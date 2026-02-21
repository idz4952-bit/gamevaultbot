import os
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")


# ---------- Reply Keyboard (تحت خانة الكتابة) ----------
def kb_main_reply():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🛒 Explore Products"), KeyboardButton("⚡ Auto PUBG ID")],
            [KeyboardButton("⚡ MANUAL ORDER"), KeyboardButton("🔎 PUBG CHECKER")],
            [KeyboardButton("📦 MY ORDERS"), KeyboardButton("💵 MY WALLET")],
            [KeyboardButton("☎️ CONTACT SUPPORT")],
        ],
        resize_keyboard=True,
    )


# ---------- Inline Keyboards (داخل المحادثة) ----------
def kb_products_inline():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🪂 PUBG MOBILE UC CODES", callback_data="cat:pubg_uc")],
            [InlineKeyboardButton("💎 GARENA FREE FIRE PINS", callback_data="cat:free_fire")],
            [InlineKeyboardButton("⭐ Ludo Star Hearts | Royal Points", callback_data="cat:ludo")],
            [InlineKeyboardButton("🍏 iTunes [USA] GIFTCARDS", callback_data="cat:itunes")],
            [InlineKeyboardButton("🔥 STEAM [USA] GIFTCARDS", callback_data="cat:steam")],
            [InlineKeyboardButton("🎮 PLAYSTATION [USA] GIFTCARDS", callback_data="cat:ps")],
            [InlineKeyboardButton("🕹 ROBLOX [USA]", callback_data="cat:roblox")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back:main")],
        ]
    )


def kb_manual_inline():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎮 [MANUAL] GAMES ID", callback_data="manual:games_id")],
            [InlineKeyboardButton("⚙️ APPLICATION SERVICES", callback_data="manual:apps")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back:main")],
        ]
    )


def kb_wallet_inline():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🆔 BYBIT ID", callback_data="wallet:bybit")],
            [InlineKeyboardButton("🆔 BINANCE ID", callback_data="wallet:binance")],
            [InlineKeyboardButton("🔗 USDT [TRC20]", callback_data="wallet:trc20")],
            [InlineKeyboardButton("🔗 USDT [BEP20]", callback_data="wallet:bep20")],
            [InlineKeyboardButton("📜 MY TRANSACTIONS", callback_data="wallet:tx")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back:main")],
        ]
    )


# ---------- Pages ----------
async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 أهلاً بك في GameVault!\nاختر من القائمة بالأسفل 👇",
        reply_markup=kb_main_reply(),
    )


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛒 CODES & Gift Cards\n\n"
        "📦 Product Categories:\n"
        "Explore our premium selection of official gaming cards and digital services below.\n\n"
        "✅ Stock Guarantee:\n"
        "All cards valid for 1-year storage."
    )
    await update.message.reply_text(text, reply_markup=kb_products_inline())


async def show_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚡ MANUAL ORDER\n\n"
        "💡 Select a service category:\n"
        "Choose from PUBG offers, manual game top-ups, or application services.\n\n"
        "⏰ Working Hours: 12:00 PM - 12:00 AM\n"
        "🌍 Time Zone: Algeria (GMT+1)"
    )
    await update.message.reply_text(text, reply_markup=kb_manual_inline())


async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (
        "💵 WALLET OVERVIEW\n\n"
        f"🆔 Telegram ID: {user_id}\n"
        "💰 Balance: 0.000$\n\n"
        "Choose your preferred USDT deposit method:"
    )
    await update.message.reply_text(text, reply_markup=kb_wallet_inline())


# ---------- Text Router (للأزرار تحت خانة الكتابة) ----------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()

    if t in ("/start", "Menu", "القائمة"):
        return await show_main(update, context)

    if t == "🛒 Explore Products":
        return await show_products(update, context)

    if t == "⚡ MANUAL ORDER":
        return await show_manual(update, context)

    if t == "💵 MY WALLET":
        return await show_wallet(update, context)

    # باقي الأزرار (placeholder)
    if t == "📦 MY ORDERS":
        return await update.message.reply_text("📦 MY ORDERS (قريباً) ✅", reply_markup=kb_main_reply())
    if t == "☎️ CONTACT SUPPORT":
        return await update.message.reply_text("☎️ اكتب رسالتك للدعم هنا ✅", reply_markup=kb_main_reply())
    if t == "⚡ Auto PUBG ID":
        return await update.message.reply_text("⚡ Auto PUBG ID (قريباً) ✅", reply_markup=kb_main_reply())
    if t == "🔎 PUBG CHECKER":
        return await update.message.reply_text("🔎 PUBG CHECKER (قريباً) ✅", reply_markup=kb_main_reply())

    await update.message.reply_text("اكتب Menu للرجوع للقائمة ✅", reply_markup=kb_main_reply())


# ---------- Callback Router (للأزرار داخل المحادثة) ----------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # رجوع للقائمة (نعدل نفس الرسالة بدل إرسال رسالة جديدة)
    if data == "back:main":
        return await q.edit_message_text(
            "✅ رجعت للقائمة. استخدم الأزرار أسفل خانة الكتابة 👇"
        )

    # اختيار قسم منتجات
    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        return await q.edit_message_text(
            f"✅ اخترت القسم: {cat}\n(الخطوة التالية: نعرض المنتجات والأسعار هنا)",
            reply_markup=kb_products_inline(),
        )

    # Wallet options
    if data.startswith("wallet:"):
        w = data.split(":", 1)[1]
        return await q.edit_message_text(
            f"✅ خيار محفظة: {w}\n(الخطوة التالية: نعرض العنوان/المعرف/المعاملات)",
            reply_markup=kb_wallet_inline(),
        )

    # Manual options
    if data.startswith("manual:"):
        m = data.split(":", 1)[1]
        return await q.edit_message_text(
            f"✅ خيار طلب يدوي: {m}\n(الخطوة التالية: نسألك عن البيانات وننشئ الطلب)",
            reply_markup=kb_manual_inline(),
        )


def run():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", show_main))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()


if __name__ == "__main__":
    run()

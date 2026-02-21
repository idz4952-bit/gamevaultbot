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
            [InlineKeyboardButton("⬅️ Back", callback_data="nav:back")],
        ]
    )

def kb_manual_inline():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎮 [MANUAL] GAMES ID", callback_data="manual:games_id")],
            [InlineKeyboardButton("⚙️ APPLICATION SERVICES", callback_data="manual:apps")],
            [InlineKeyboardButton("⬅️ Back", callback_data="nav:back")],
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
            [InlineKeyboardButton("⬅️ Back", callback_data="nav:back")],
        ]
    )


# ---------- Pages content ----------
def page_main_text():
    return "🎮 أهلاً بك في GameVault!\nاختر من القائمة بالأسفل 👇"

def page_products_text():
    return (
        "🛒 CODES & Gift Cards\n\n"
        "📦 Product Categories:\n"
        "Explore our premium selection of official gaming cards and digital services below.\n\n"
        "✅ Stock Guarantee:\n"
        "All cards valid for 1-year storage."
    )

def page_manual_text():
    return (
        "⚡ MANUAL ORDER\n\n"
        "💡 Select a service category:\n"
        "Choose from PUBG offers, manual game top-ups, or application services.\n\n"
        "⏰ Working Hours: 12:00 PM - 12:00 AM\n"
        "🌍 Time Zone: Algeria (GMT+1)"
    )

def page_wallet_text(user_id: int):
    return (
        "💵 WALLET OVERVIEW\n\n"
        f"🆔 Telegram ID: {user_id}\n"
        "💰 Balance: 0.000$\n\n"
        "Choose your preferred USDT deposit method:"
    )


# ---------- Stack helpers (رجوع للخلف) ----------
def push_page(context: ContextTypes.DEFAULT_TYPE, page: str):
    stack = context.user_data.get("stack", [])
    stack.append(page)
    context.user_data["stack"] = stack

def pop_page(context: ContextTypes.DEFAULT_TYPE) -> str:
    stack = context.user_data.get("stack", [])
    if stack:
        stack.pop()
    context.user_data["stack"] = stack
    return stack[-1] if stack else "main"

def current_page(context: ContextTypes.DEFAULT_TYPE) -> str:
    stack = context.user_data.get("stack", [])
    return stack[-1] if stack else "main"


# ---------- Render (يعدل نفس الرسالة) ----------
async def render_inline(update: Update, context: ContextTypes.DEFAULT_TYPE, page: str):
    q = update.callback_query
    user_id = update.effective_user.id

    if page == "products":
        await q.edit_message_text(page_products_text(), reply_markup=kb_products_inline())
    elif page == "manual":
        await q.edit_message_text(page_manual_text(), reply_markup=kb_manual_inline())
    elif page == "wallet":
        await q.edit_message_text(page_wallet_text(user_id), reply_markup=kb_wallet_inline())
    else:
        # لو رجع main: نرسل تنبيه فقط (لأن القائمة الرئيسية ReplyKeyboard تحت الكتابة)
        await q.edit_message_text("✅ رجعت للقائمة. استخدم الأزرار أسفل خانة الكتابة 👇")


# ---------- /start ----------
async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stack"] = ["main"]
    await update.message.reply_text(page_main_text(), reply_markup=kb_main_reply())


# ---------- Text Router (ReplyKeyboard) ----------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()

    if t in ("/start", "Menu", "القائمة"):
        return await show_main(update, context)

    # نرسل رسالة واحدة Inline لكل صفحة (بدون تكديس؟ هنا تكديس طبيعي، إذا تريدها لا تتكدس قلّي)
    if t == "🛒 Explore Products":
        push_page(context, "products")
        return await update.message.reply_text(page_products_text(), reply_markup=kb_products_inline())

    if t == "⚡ MANUAL ORDER":
        push_page(context, "manual")
        return await update.message.reply_text(page_manual_text(), reply_markup=kb_manual_inline())

    if t == "💵 MY WALLET":
        push_page(context, "wallet")
        return await update.message.reply_text(page_wallet_text(update.effective_user.id), reply_markup=kb_wallet_inline())

    # placeholders
    if t == "📦 MY ORDERS":
        return await update.message.reply_text("📦 MY ORDERS (قريباً) ✅", reply_markup=kb_main_reply())
    if t == "☎️ CONTACT SUPPORT":
        return await update.message.reply_text("☎️ اكتب رسالتك للدعم هنا ✅", reply_markup=kb_main_reply())
    if t == "⚡ Auto PUBG ID":
        return await update.message.reply_text("⚡ Auto PUBG ID (قريباً) ✅", reply_markup=kb_main_reply())
    if t == "🔎 PUBG CHECKER":
        return await update.message.reply_text("🔎 PUBG CHECKER (قريباً) ✅", reply_markup=kb_main_reply())

    await update.message.reply_text("اكتب Menu للرجوع للقائمة ✅", reply_markup=kb_main_reply())


# ---------- Callback Router (Inline) ----------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # رجوع للخلف
    if data == "nav:back":
        page = pop_page(context)
        if page == "main":
            return await render_inline(update, context, "main")
        return await render_inline(update, context, page)

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

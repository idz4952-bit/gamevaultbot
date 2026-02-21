import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.environ.get("TOKEN")

# ====== نصوص ثابتة ======
HOME_TEXT = (
    "👋 مرحباً بك في *GameVault* 🎮\n\n"
    "اختر من القائمة:"
)

PRODUCTS_TEXT = (
    "🛒 *CODES & Gift Cards*\n\n"
    "📦 *Product Categories:*\n"
    "Explore our premium selection below.\n\n"
    "✅ *Stock Guarantee:*\n"
    "All cards valid for 1-year storage."
)

MANUAL_TEXT = (
    "⚡ *MANUAL ORDER*\n\n"
    "💡 Select a service category:\n"
    "Choose from offers, manual top-ups, or services.\n\n"
    "⏰ Working Hours: 12:00 PM - 12:00 AM\n"
    "🌍 Time Zone: Algeria (GMT+1)"
)

ORDERS_TEXT = (
    "📦 *My Orders*\n\n"
    "لا توجد طلبات حالياً.\n"
    " (لاحقاً نربطه بقاعدة البيانات)"
)

WALLET_TEXT = (
    "💰 *WALLET OVERVIEW*\n\n"
    "Telegram ID: (لاحقاً)\n"
    "Balance: 0.00$\n\n"
    "Choose your preferred deposit method:"
)

SUPPORT_TEXT = (
    "☎️ *CONTACT SUPPORT*\n\n"
    "اكتب رسالتك هنا وسيتم الرد عليك قريباً.\n"
    "(لاحقاً نربطها بإيميل/قناة دعم)"
)

# ====== لوحات الأزرار (Inline) ======
def home_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🛒 Explore Products", callback_data="nav:products")],
        [InlineKeyboardButton("⚡ MANUAL ORDER", callback_data="nav:manual")],
        [InlineKeyboardButton("📦 MY ORDERS", callback_data="nav:orders")],
        [InlineKeyboardButton("💰 MY WALLET", callback_data="nav:wallet")],
        [InlineKeyboardButton("☎️ CONTACT SUPPORT", callback_data="nav:support")],
    ]
    return InlineKeyboardMarkup(buttons)

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
    ])

def wallet_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🆔 BYBIT ID", callback_data="wallet:bybit")],
        [InlineKeyboardButton("🆔 BINANCE ID", callback_data="wallet:binance")],
        [InlineKeyboardButton("🔗 USDT [TRC20]", callback_data="wallet:trc20")],
        [InlineKeyboardButton("🔗 USDT [BEP20]", callback_data="wallet:bep20")],
        [InlineKeyboardButton("🧾 MY TRANSACTIONS", callback_data="wallet:tx")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
    ]
    return InlineKeyboardMarkup(buttons)

def products_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🎮 PUBG MOBILE UC CODES", callback_data="prod:pubg_uc")],
        [InlineKeyboardButton("💎 GARENA FREE FIRE PINS", callback_data="prod:ff_pins")],
        [InlineKeyboardButton("⭐ Ludo Star Hearts | Royal Points", callback_data="prod:ludo")],
        [InlineKeyboardButton("🍏 iTunes [USA] GIFTCARDS", callback_data="prod:itunes")],
        [InlineKeyboardButton("🔥 STEAM [USA] GIFTCARDS", callback_data="prod:steam")],
        [InlineKeyboardButton("🎮 PLAYSTATION [USA] GIFTCARDS", callback_data="prod:ps")],
        [InlineKeyboardButton("🎮 ROBLOX [USA]", callback_data="prod:roblox")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
    ]
    return InlineKeyboardMarkup(buttons)

# ====== أدوات تنقّل (Back Stack) ======
def push_page(context: ContextTypes.DEFAULT_TYPE, page: str):
    stack = context.user_data.get("stack", [])
    stack.append(page)
    context.user_data["stack"] = stack

def pop_page(context: ContextTypes.DEFAULT_TYPE) -> str:
    stack = context.user_data.get("stack", [])
    if stack:
        stack.pop()
    context.user_data["stack"] = stack
    return stack[-1] if stack else "home"

def current_page(context: ContextTypes.DEFAULT_TYPE) -> str:
    stack = context.user_data.get("stack", [])
    return stack[-1] if stack else "home"

async def render_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: str):
    # تحديد محتوى كل صفحة
    if page == "home":
        text, kb = HOME_TEXT, home_kb()
    elif page == "products":
        text, kb = PRODUCTS_TEXT, products_kb()
    elif page == "manual":
        text, kb = MANUAL_TEXT, back_kb()
    elif page == "orders":
        text, kb = ORDERS_TEXT, back_kb()
    elif page == "wallet":
        text, kb = WALLET_TEXT, wallet_kb()
    elif page == "support":
        text, kb = SUPPORT_TEXT, back_kb()
    else:
        text, kb = HOME_TEXT, home_kb()
        page = "home"

    # تعديل نفس الرسالة (Inline)
    q = update.callback_query
    if q:
        await q.edit_message_text(text=text, reply_markup=kb, parse_mode="Markdown")

# ====== Handlers ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not TOKEN:
        await update.message.reply_text("❌ TOKEN غير موجود في Environment Variables")
        return

    context.user_data["stack"] = ["home"]
    await update.message.reply_text(HOME_TEXT, reply_markup=home_kb(), parse_mode="Markdown")

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data

    # تنقل رئيسي
    if data.startswith("nav:"):
        action = data.split(":", 1)[1]

        if action == "home":
            context.user_data["stack"] = ["home"]
            await render_page(update, context, "home")
            return

        if action == "back":
            page = pop_page(context)
            await render_page(update, context, page)
            return

        # انتقال لصفحة جديدة
        page = action  # products/manual/orders/wallet/support
        # لا نكرر نفس الصفحة مرتين
        if current_page(context) != page:
            push_page(context, page)
        await render_page(update, context, page)
        return

    # أزرار المنتجات (حاليا مثال فقط)
    if data.startswith("prod:"):
        await q.edit_message_text(
            text=f"✅ اخترت: *{data.split(':',1)[1]}*\n\n(لاحقاً نعرض المنتجات والأسعار هنا)\n",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")],
                [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
            ]),
            parse_mode="Markdown",
        )
        return

    # أزرار المحفظة (مثال)
    if data.startswith("wallet:"):
        await q.edit_message_text(
            text=f"✅ خيار محفظة: *{data.split(':',1)[1]}*\n\n(لاحقاً نضيف التفاصيل هنا)",
            reply_markup=wallet_kb(),
            parse_mode="Markdown",
        )
        return

def main():
    if not TOKEN:
        raise RuntimeError("TOKEN is missing in environment variables")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))

    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()

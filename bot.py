import os
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")

# ---------- Keyboards ----------
def kb_main():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🛒 Explore Products"), KeyboardButton("👤 My Account")],
            [KeyboardButton("⚡ MANUAL ORDER"), KeyboardButton("➕ Add Balance")],
            [KeyboardButton("📦 MY ORDERS"), KeyboardButton("💵 MY WALLET")],
            [KeyboardButton("☎️ CONTACT SUPPORT")],
        ],
        resize_keyboard=True,
    )

def kb_back():
    return ReplyKeyboardMarkup([[KeyboardButton("⬅️ Back")]], resize_keyboard=True)

def kb_wallet():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🆔 BYBIT ID"), KeyboardButton("🆔 BINANCE ID")],
            [KeyboardButton("🔗 USDT [TRC20]"), KeyboardButton("🔗 USDT [BEP20]")],
            [KeyboardButton("📜 MY TRANSACTIONS")],
            [KeyboardButton("⬅️ Back")],
        ],
        resize_keyboard=True,
    )

def kb_manual_order():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🎮 [MANUAL] GAMES ID")],
            [KeyboardButton("⚙️ APPLICATION SERVICES")],
            [KeyboardButton("⬅️ Back")],
        ],
        resize_keyboard=True,
    )

def kb_products():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🪂 PUBG MOBILE UC CODES")],
            [KeyboardButton("💎 GARENA FREE FIRE PINS")],
            [KeyboardButton("⭐ Ludo Star Hearts | Royal Points")],
            [KeyboardButton("🍏 iTunes [USA] GIFTCARDS")],
            [KeyboardButton("🔥 STEAM [USA] GIFTCARDS")],
            [KeyboardButton("🎮 PLAYSTATION [USA] GIFTCARDS")],
            [KeyboardButton("🕹 ROBLOX [USA]")],
            [KeyboardButton("⬅️ Back")],
        ],
        resize_keyboard=True,
    )

# ---------- Pages ----------
async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 مرحباً بك!\nاختر من القائمة:",
        reply_markup=kb_main(),
    )

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛒 CODES & Gift Cards\n\n"
        "📦 Product Categories:\n"
        "Explore our premium selection of official gaming cards and digital services below.\n\n"
        "✅ Stock Guarantee:\n"
        "All cards valid for 1-year storage."
    )
    await update.message.reply_text(text, reply_markup=kb_products())

async def show_manual_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚡ MANUAL ORDER\n\n"
        "💡 Select a service category:\n"
        "Choose from PUBG offers, manual game top-ups, or application services.\n\n"
        "⏰ Working Hours: 12:00 PM - 12:00 AM\n"
        "🌍 Time Zone: Algeria (GMT+1)"
    )
    await update.message.reply_text(text, reply_markup=kb_manual_order())

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📦 My Orders\n\n"
        "لا توجد طلبات حالياً.\n"
        "عند إضافة قاعدة بيانات سنعرض الطلبات هنا."
    )
    await update.message.reply_text(text, reply_markup=kb_back())

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # لاحقاً سنجلب الرصيد من DB، الآن مثال
    user_id = update.effective_user.id
    text = (
        "💵 WALLET OVERVIEW\n\n"
        f"🆔 Telegram ID: {user_id}\n"
        "💰 Balance: 0.000$\n\n"
        "Choose your preferred USDT deposit method:"
    )
    await update.message.reply_text(text, reply_markup=kb_wallet())

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        "👤 My Account\n\n"
        f"🧾 Name: {user.full_name}\n"
        f"🆔 ID: {user.id}\n\n"
        "هذه صفحة الحساب (سنضيف خيارات لاحقاً)."
    )
    await update.message.reply_text(text, reply_markup=kb_back())

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "☎️ CONTACT SUPPORT\n\nاكتب رسالتك هنا وسيتم إرسالها للدعم."
    await update.message.reply_text(text, reply_markup=kb_back())

async def show_add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "➕ Add Balance\n\nاختر طريقة الإيداع من المحفظة 💵 MY WALLET."
    await update.message.reply_text(text, reply_markup=kb_back())

# ---------- Router ----------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()

    if t in ("/start", "Menu", "⬅️ Back"):
        return await show_main(update, context)

    if t == "🛒 Explore Products":
        return await show_products(update, context)
    if t == "⚡ MANUAL ORDER":
        return await show_manual_order(update, context)
    if t == "📦 MY ORDERS":
        return await show_orders(update, context)
    if t == "💵 MY WALLET":
        return await show_wallet(update, context)
    if t == "👤 My Account":
        return await show_account(update, context)
    if t == "☎️ CONTACT SUPPORT":
        return await show_support(update, context)
    if t == "➕ Add Balance":
        return await show_add_balance(update, context)

    # أزرار داخل صفحات (placeholder)
    if t.startswith("🪂") or t.startswith("💎") or t.startswith("⭐") or t.startswith("🍏") or t.startswith("🔥") or t.startswith("🎮") or t.startswith("🕹"):
        return await update.message.reply_text("✅ تم اختيار قسم. (الخطوة التالية: نعرض المنتجات والأسعار)", reply_markup=kb_products())

    if t in ("🆔 BYBIT ID", "🆔 BINANCE ID", "🔗 USDT [TRC20]", "🔗 USDT [BEP20]", "📜 MY TRANSACTIONS"):
        return await update.message.reply_text("✅ خيار محفظة. (الخطوة التالية: نضيف الإيداع/السحب/السجل)", reply_markup=kb_wallet())

    if t in ("🎮 [MANUAL] GAMES ID", "⚙️ APPLICATION SERVICES"):
        return await update.message.reply_text("✅ خيار طلب يدوي. (الخطوة التالية: نسألك عن البيانات وننشئ طلب)", reply_markup=kb_manual_order())

    # fallback
    await update.message.reply_text("اكتب Menu للرجوع للقائمة ✅", reply_markup=kb_main())

# ---------- Entrypoint ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main(update, context)

def run():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()

if __name__ == "__main__":
    run()

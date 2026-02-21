import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.environ.get("TOKEN")

# ====== Helpers ======
def kb(*rows):
    return InlineKeyboardMarkup(list(rows))

def main_menu():
    return kb(
        [
            InlineKeyboardButton("🛒 Explore Products", callback_data="m:explore"),
            InlineKeyboardButton("📦 My Orders", callback_data="m:orders"),
        ],
        [
            InlineKeyboardButton("📝 Manual Order", callback_data="m:manual"),
            InlineKeyboardButton("💳 My Wallet", callback_data="m:wallet"),
        ],
        [
            InlineKeyboardButton("☎️ Contact Support", callback_data="m:support"),
            InlineKeyboardButton("🆔 My ID", callback_data="m:myid"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="m:refresh"),
        ],
    )

def back_to_menu():
    return kb([InlineKeyboardButton("⬅️ Back to Menu", callback_data="m:menu")])

def explore_menu():
    return kb(
        [
            InlineKeyboardButton("🎮 Games", callback_data="e:games"),
            InlineKeyboardButton("🧩 DLC / Add-ons", callback_data="e:dlc"),
        ],
        [
            InlineKeyboardButton("🎁 Gift Cards", callback_data="e:giftcards"),
            InlineKeyboardButton("⭐ Top Deals", callback_data="e:deals"),
        ],
        [
            InlineKeyboardButton("🔎 Search", callback_data="e:search"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="m:menu"),
        ],
    )

def orders_menu():
    return kb(
        [
            InlineKeyboardButton("📋 Last Orders", callback_data="o:last"),
            InlineKeyboardButton("⏳ Pending", callback_data="o:pending"),
        ],
        [
            InlineKeyboardButton("✅ Completed", callback_data="o:done"),
            InlineKeyboardButton("❌ Canceled", callback_data="o:canceled"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="m:menu"),
        ],
    )

def wallet_menu():
    return kb(
        [
            InlineKeyboardButton("💰 Balance", callback_data="w:balance"),
            InlineKeyboardButton("➕ Add Funds", callback_data="w:add"),
        ],
        [
            InlineKeyboardButton("🧾 Transactions", callback_data="w:tx"),
            InlineKeyboardButton("🎟 Promo Code", callback_data="w:promo"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="m:menu"),
        ],
    )

def support_menu():
    return kb(
        [
            InlineKeyboardButton("💬 Send Message", callback_data="s:msg"),
            InlineKeyboardButton("📌 FAQ", callback_data="s:faq"),
        ],
        [
            InlineKeyboardButton("🧑‍💻 Live Agent", callback_data="s:agent"),
            InlineKeyboardButton("🕒 Working Hours", callback_data="s:hours"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="m:menu"),
        ],
    )

# ====== Text Pages ======
WELCOME_TEXT = "🎮 مرحباً بك في GameVault!\nاختر من القائمة 👇"
EXPLORE_TEXT = "🛒 Explore Products\nاختر فئة المنتجات:"
MANUAL_TEXT = (
    "📝 Manual Order\n\n"
    "اكتب الطلب بهذه الصيغة:\n"
    "GameName | Platform | Region\n\n"
    "مثال:\n"
    "FC 26 | PS5 | EU"
)
ORDERS_TEXT = "📦 My Orders\nاختر القسم:"
WALLET_TEXT = "💳 My Wallet\nاختر خيار:"
SUPPORT_TEXT = "☎️ Contact Support\nاختر خيار:"

# ====== Handlers ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu())

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📋 القائمة الرئيسية:", reply_markup=main_menu())

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # ---- Main menu routes ----
    if data in ("m:menu", "m:refresh"):
        await q.edit_message_text(WELCOME_TEXT, reply_markup=main_menu())
        return

    if data == "m:explore":
        await q.edit_message_text(EXPLORE_TEXT, reply_markup=explore_menu())
        return

    if data == "m:manual":
        await q.edit_message_text(MANUAL_TEXT, reply_markup=back_to_menu())
        return

    if data == "m:orders":
        await q.edit_message_text(ORDERS_TEXT, reply_markup=orders_menu())
        return

    if data == "m:wallet":
        await q.edit_message_text(WALLET_TEXT, reply_markup=wallet_menu())
        return

    if data == "m:support":
        await q.edit_message_text(SUPPORT_TEXT, reply_markup=support_menu())
        return

    if data == "m:myid":
        user_id = q.from_user.id
        await q.edit_message_text(f"🆔 Your ID: `{user_id}`", reply_markup=back_to_menu(), parse_mode="Markdown")
        return

    # ---- Explore sub pages ----
    if data == "e:games":
        await q.edit_message_text("🎮 Games\n(سنضيف قائمة ألعاب لاحقاً)", reply_markup=explore_menu())
        return
    if data == "e:dlc":
        await q.edit_message_text("🧩 DLC / Add-ons\n(قريباً)", reply_markup=explore_menu())
        return
    if data == "e:giftcards":
        await q.edit_message_text("🎁 Gift Cards\n(قريباً)", reply_markup=explore_menu())
        return
    if data == "e:deals":
        await q.edit_message_text("⭐ Top Deals\n(قريباً)", reply_markup=explore_menu())
        return
    if data == "e:search":
        await q.edit_message_text("🔎 Search\n(لاحقاً سنضيف بحث بالاسم)", reply_markup=explore_menu())
        return

    # ---- Orders sub pages ----
    if data.startswith("o:"):
        await q.edit_message_text("📦 Orders\n(لا توجد بيانات الآن — سنربطها لاحقاً)", reply_markup=orders_menu())
        return

    # ---- Wallet sub pages ----
    if data == "w:balance":
        await q.edit_message_text("💰 Balance: 0\n(سنربطه بقاعدة البيانات لاحقاً)", reply_markup=wallet_menu())
        return
    if data == "w:add":
        await q.edit_message_text("➕ Add Funds\n(سنضيف طرق الدفع لاحقاً)", reply_markup=wallet_menu())
        return
    if data == "w:tx":
        await q.edit_message_text("🧾 Transactions\n(قريباً)", reply_markup=wallet_menu())
        return
    if data == "w:promo":
        await q.edit_message_text("🎟 Promo Code\n(قريباً)", reply_markup=wallet_menu())
        return

    # ---- Support sub pages ----
    if data == "s:msg":
        await q.edit_message_text("💬 Send Message\nاكتب رسالتك في الدردشة الآن.\n(سنفعّل الإرسال للدعم لاحقاً)", reply_markup=support_menu())
        return
    if data == "s:faq":
        await q.edit_message_text("📌 FAQ\n(قريباً)", reply_markup=support_menu())
        return
    if data == "s:agent":
        await q.edit_message_text("🧑‍💻 Live Agent\n(قريباً)", reply_markup=support_menu())
        return
    if data == "s:hours":
        await q.edit_message_text("🕒 Working Hours\nكل يوم: 10:00 - 22:00", reply_markup=support_menu())
        return

    # fallback
    await q.edit_message_text("❓ خيار غير معروف", reply_markup=back_to_menu())

def main():
    if not TOKEN:
        raise RuntimeError("TOKEN is missing. Set it in Render Environment Variables.")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CallbackQueryHandler(on_button))

    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()

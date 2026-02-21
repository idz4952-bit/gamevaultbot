import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.environ.get("TOKEN")

# ====== Keyboards ======
def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🛒 Explore Products", callback_data="explore"),
            InlineKeyboardButton("🆔 My ID", callback_data="myid"),
        ],
        [
            InlineKeyboardButton("📈 MANUAL ORDER", callback_data="manual_order"),
            InlineKeyboardButton("💳 MY WALLET", callback_data="wallet"),
        ],
        [
            InlineKeyboardButton("📦 MY ORDERS", callback_data="my_orders"),
            InlineKeyboardButton("☎️ CONTACT SUPPORT", callback_data="support"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu")]])

# ====== Handlers ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 مرحبا بك في GameVault!\nاختر من القائمة 👇",
        reply_markup=main_menu_keyboard()
    )

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 القائمة الرئيسية:",
        reply_markup=main_menu_keyboard()
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu":
        await query.edit_message_text("📋 القائمة الرئيسية:", reply_markup=main_menu_keyboard())
        return

    if data == "explore":
        await query.edit_message_text(
            "🛒 Explore Products\n\n(هنا لاحقًا نعرض المنتجات من قاعدة البيانات)",
            reply_markup=back_keyboard()
        )
    elif data == "manual_order":
        await query.edit_message_text(
            "📈 MANUAL ORDER\n\nاكتب الطلب بهذه الصيغة:\nGameName | Platform | Region\n\n(لاحقًا نربطه بالحفظ في DB)",
            reply_markup=back_keyboard()
        )
    elif data == "my_orders":
        await query.edit_message_text(
            "📦 MY ORDERS\n\n(حاليًا لا توجد طلبات محفوظة — سنربطها بقاعدة البيانات لاحقًا)",
            reply_markup=back_keyboard()
        )
    elif data == "wallet":
        await query.edit_message_text(
            "💳 MY WALLET\n\nرصيدك الحالي: 0\n(سنربط الرصيد بقاعدة البيانات لاحقًا)",
            reply_markup=back_keyboard()
        )
    elif data == "support":
        await query.edit_message_text(
            "☎️ CONTACT SUPPORT\n\nاكتب رسالتك هنا وسيتم تحويلها للدعم (سنضيف قناة/ID للدعم لاحقًا).",
            reply_markup=back_keyboard()
        )
    elif data == "myid":
        user = query.from_user
        await query.edit_message_text(
            f"🆔 Your ID: `{user.id}`",
            reply_markup=back_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("❓ خيار غير معروف", reply_markup=back_keyboard())

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

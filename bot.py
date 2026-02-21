import os
from typing import List, Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")

# ---------- Demo data (استبدلها لاحقاً بقاعدة البيانات) ----------
CATEGORIES: List[Dict] = [
    {"id": "pubg_uc", "title": "🪂 PUBG MOBILE UC CODES"},
    {"id": "freefire", "title": "💎 GARENA FREE FIRE PINS"},
    {"id": "ludo", "title": "⭐ Ludo Star Hearts | Royal Points"},
    {"id": "itunes", "title": "🍏 iTunes [USA] GIFTCARDS"},
    {"id": "steam", "title": "🔥 STEAM [USA] GIFTCARDS"},
    {"id": "psn", "title": "🎮 PLAYSTATION [USA] GIFTCARDS"},
    {"id": "roblox", "title": "🎮 ROBLOX [USA]"},
]

# طلبات تجريبية للـ pagination
DEMO_ORDERS = [
    {
        "id": "GPBDF62F8D",
        "status": "✅ COMPLETED",
        "date": "2026-02-19 22:23 (+2)",
        "product": "📦 PUBG CHECKER",
        "fee": "$0.100",
    },
    {
        "id": "GPB3D19532",
        "status": "✅ COMPLETED",
        "date": "2026-02-19 21:30 (+2)",
        "category": "PUBG UC CODES",
        "product": "60 UC",
        "qty": "500",
        "total": "$437.500",
    },
    {
        "id": "GPF64D99F9",
        "status": "✅ COMPLETED",
        "date": "2026-02-01 22:59 (+2)",
        "category": "FREE FIRE PINS",
        "product": "1 USD - 100+10",
        "qty": "10",
        "total": "$9.100",
    },
    # زِد أكثر لو تحب
]

PAGE_SIZE = 3

# ---------- Helpers ----------
def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Explore Products", callback_data="menu:products"),
            InlineKeyboardButton("⚡ MANUAL ORDER", callback_data="menu:manual"),
        ],
        [
            InlineKeyboardButton("📦 MY ORDERS", callback_data="menu:orders:0"),
            InlineKeyboardButton("💵 MY WALLET", callback_data="menu:wallet"),
        ],
        [
            InlineKeyboardButton("☎️ CONTACT SUPPORT", callback_data="menu:support"),
        ],
    ])

def kb_back(to: str = "menu:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=to)]])

def kb_categories() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(c["title"], callback_data=f"cat:{c['id']}")] for c in CATEGORIES]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)

def format_orders_page(page: int) -> str:
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    items = DEMO_ORDERS[start:end]

    if not items:
        return "📦 My Orders\n\nلا توجد طلبات في هذه الصفحة."

    lines = ["📦 My Orders\n"]
    for o in items:
        lines.append(f"ORDER #{o['id']}")
        lines.append(f"┣ {o.get('status','')}")
        lines.append(f"┣ 📅 Date: {o.get('date','')}")
        if "category" in o:
            lines.append(f"┣ 📦 Category: {o.get('category')}")
        lines.append(f"┣ 📦 Product: {o.get('product','')}")
        if "qty" in o:
            lines.append(f"┣ 🔢 Quantity: {o.get('qty')}")
        if "total" in o:
            lines.append(f"┗ 💵 Total: {o.get('total')}")
        if "fee" in o:
            lines.append(f"┗ 🔎 Check Fee: {o.get('fee')}")
        lines.append("—" * 26)

    total_pages = max(1, (len(DEMO_ORDERS) + PAGE_SIZE - 1) // PAGE_SIZE)
    lines.append(f"Page {page+1}/{total_pages}")
    return "\n".join(lines)

def kb_orders_pager(page: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(DEMO_ORDERS) + PAGE_SIZE - 1) // PAGE_SIZE)
    prev_page = max(0, page - 1)
    next_page = min(total_pages - 1, page + 1)

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬅️ Prev", callback_data=f"menu:orders:{prev_page}"),
            InlineKeyboardButton("Next ➡️", callback_data=f"menu:orders:{next_page}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:home")]
    ])

def wallet_text(user_id: int) -> str:
    # لاحقاً اجلب الرصيد من DB
    return (
        "💼 WALLET OVERVIEW\n\n"
        f"🪪 Telegram ID: {user_id}\n"
        "💰 Balance: 0.000$\n\n"
        "Choose your preferred USDT deposit method:"
    )

def kb_wallet() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🆔 BYBIT ID", callback_data="wallet:bybit"),
            InlineKeyboardButton("🆔 BINANCE ID", callback_data="wallet:binance"),
        ],
        [
            InlineKeyboardButton("🔗 USDT [TRC20]", callback_data="wallet:trc20"),
            InlineKeyboardButton("🔗 USDT [BEP20]", callback_data="wallet:bep20"),
        ],
        [InlineKeyboardButton("🇮🇹 MY TRANSACTIONS", callback_data="wallet:tx")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:home")],
    ])

# ---------- Handlers ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🎮 مرحباً بك في GameVault!\nاختر من القائمة:"
    await update.message.reply_text(text, reply_markup=kb_main_menu())

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data or ""

    # HOME
    if data == "menu:home":
        await q.edit_message_text("🎮 GameVault Menu\nاختر:", reply_markup=kb_main_menu())
        return

    # PRODUCTS
    if data == "menu:products":
        msg = (
            "🛍️ CODES & Gift Cards\n\n"
            "📦 Product Categories:\n"
            "Explore our premium selection below.\n\n"
            "✅ Stock Guarantee:\n"
            "All cards valid for 1-year storage."
        )
        await q.edit_message_text(msg, reply_markup=kb_categories())
        return

    if data.startswith("cat:"):
        cat_id = data.split(":", 1)[1]
        cat = next((c for c in CATEGORIES if c["id"] == cat_id), None)
        title = cat["title"] if cat else "Category"

        # هنا لاحقاً تعرض منتجات داخل التصنيف (مع أزرار وأسعار)
        msg = f"{title}\n\nاختر منتجاً (سنضيف المنتجات هنا لاحقاً)."
        await q.edit_message_text(msg, reply_markup=kb_back("menu:products"))
        return

    # MANUAL ORDER
    if data == "menu:manual":
        msg = (
            "⚡ MANUAL ORDER\n\n"
            "💡 Select a service category:\n"
            "Choose from PUBG offers, manual game top-ups, or application services.\n\n"
            "⏰ Working Hours: 12:00 PM – 12:00 AM\n"
            "🌍 Time Zone: Algeria (GMT+1)"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ [MANUAL] GAMES ID", callback_data="manual:gamesid")],
            [InlineKeyboardButton("⚙️ APPLICATION SERVICES", callback_data="manual:apps")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu:home")],
        ])
        await q.edit_message_text(msg, reply_markup=kb)
        return

    if data.startswith("manual:"):
        section = data.split(":", 1)[1]
        if section == "gamesid":
            msg = "⚡ [MANUAL] GAMES ID\n\nارسل Game ID + Server + المطلوب."
        else:
            msg = "⚙️ APPLICATION SERVICES\n\nاكتب نوع الخدمة المطلوبة وسنرد عليك."
        await q.edit_message_text(msg, reply_markup=kb_back("menu:manual"))
        return

    # ORDERS (Pagination)
    if data.startswith("menu:orders:"):
        page = int(data.split(":")[-1])
        await q.edit_message_text(format_orders_page(page), reply_markup=kb_orders_pager(page))
        return

    # WALLET
    if data == "menu:wallet":
        await q.edit_message_text(wallet_text(q.from_user.id), reply_markup=kb_wallet())
        return

    if data.startswith("wallet:"):
        kind = data.split(":", 1)[1]
        if kind == "bybit":
            msg = "🆔 BYBIT ID\n\nأرسل BYBIT ID الخاص بك."
        elif kind == "binance":
            msg = "🆔 BINANCE ID\n\nأرسل BINANCE ID الخاص بك."
        elif kind == "trc20":
            msg = "🔗 USDT [TRC20]\n\nهذا عنوان الإيداع (ضعه لاحقاً من إعداداتك)."
        elif kind == "bep20":
            msg = "🔗 USDT [BEP20]\n\nهذا عنوان الإيداع (ضعه لاحقاً من إعداداتك)."
        else:
            msg = "🇮🇹 MY TRANSACTIONS\n\nلا يوجد معاملات حالياً."
        await q.edit_message_text(msg, reply_markup=kb_back("menu:wallet"))
        return

    # SUPPORT
    if data == "menu:support":
        msg = "☎️ CONTACT SUPPORT\n\nاكتب مشكلتك هنا وسيتم الرد عليك."
        await q.edit_message_text(msg, reply_markup=kb_back("menu:home"))
        return

    # fallback
    await q.edit_message_text("خيار غير معروف.", reply_markup=kb_main_menu())

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.run_polling()

if __name__ == "__main__":
    main()

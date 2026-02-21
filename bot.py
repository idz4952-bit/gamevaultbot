import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# Config
# =========================
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")

CURRENCY = "$"

# =========================
# Data Models
# =========================
@dataclass
class Product:
    pid: str
    title: str
    price: float
    stock: int


@dataclass
class Category:
    cid: str
    title: str
    products: List[Product]


# =========================
# Catalog (مثل صورك)
# =========================
CATALOG: List[Category] = [
    Category(
        cid="pubg_uc",
        title="🪂 PUBG MOBILE UC CODES",
        products=[
            Product("pubg_60", "60 UC", 0.875, 4690),
            Product("pubg_325", "325 UC", 4.375, 0),
            Product("pubg_660", "660 UC", 8.750, 0),
            Product("pubg_1800", "1800 UC", 22.000, 0),
            Product("pubg_3850", "3850 UC", 44.000, 0),
            Product("pubg_8100", "8100 UC", 88.000, 0),
        ],
    ),
    Category(
        cid="free_fire",
        title="💎 GARENA FREE FIRE PINS",
        products=[
            Product("ff_1", "1 USD - 100+10", 0.920, 196),
            Product("ff_2", "2 USD - 210+21", 1.840, 0),
            Product("ff_5", "5 USD - 530+53", 4.600, 0),
            Product("ff_10", "10 USD - 1080+108", 9.200, 0),
            Product("ff_20", "20 USD - 2200+220", 18.400, 0),
        ],
    ),
    Category(
        cid="ludo",
        title="⭐ Ludo Star Hearts | Royal Points",
        products=[
            Product("ludo_3_7", "3.7K Hearts + 10 RP", 9.000, 13),
            Product("ludo_7_5", "7.5K Hearts + 20 RP", 18.000, 10),
            Product("ludo_24", "24K Hearts + 60 RP", 54.000, 2),
            Product("ludo_41", "41K Hearts + 100 RP", 90.000, 1),
        ],
    ),
    Category(
        cid="itunes",
        title="🍏 iTunes [USA] GIFTCARDS",
        products=[
            Product("it_5", "5$ iTunes US", 4.600, 217),
            Product("it_10", "10$ iTunes US", 9.200, 124),
            Product("it_20", "20$ iTunes US", 18.400, 21),
            Product("it_25", "25$ iTunes US", 23.000, 13),
            Product("it_50", "50$ iTunes US", 46.000, 9),
            Product("it_100", "100$ iTunes US", 91.000, 31),
            Product("it_200", "200$ iTunes US", 180.000, 0),
        ],
    ),
    Category(
        cid="ps",
        title="🎮 PLAYSTATION [USA] GIFTCARDS",
        products=[
            Product("ps_10", "10$ PSN USA", 8.900, 0),
            Product("ps_25", "25$ PSN USA", 22.000, 10),
            Product("ps_50", "50$ PSN USA", 44.000, 0),
            Product("ps_100", "100$ PSN USA", 88.000, 5),
        ],
    ),
    Category(
        cid="roblox",
        title="🕹 ROBLOX [USA]",
        products=[
            Product("rbx_10", "Roblox 10$", 9.000, 65),
            Product("rbx_25", "Roblox 25$", 22.500, 2),
            Product("rbx_50", "Roblox 50$", 45.000, 1),
        ],
    ),
]

CAT_BY_ID: Dict[str, Category] = {c.cid: c for c in CATALOG}
PROD_BY_ID: Dict[str, Product] = {p.pid: p for c in CATALOG for p in c.products}
PROD_TO_CAT: Dict[str, str] = {p.pid: c.cid for c in CATALOG for p in c.products}

# =========================
# User State Keys
# =========================
UD_SELECTED_CAT = "selected_cat"
UD_SELECTED_PROD = "selected_prod"
UD_AWAITING_QTY = "awaiting_qty"


# =========================
# Helpers
# =========================
def money(x: float) -> str:
    # 3 decimals like: 0.875$
    return f"{x:.3f}{CURRENCY}"


def kb_categories() -> InlineKeyboardMarkup:
    rows = []
    for c in CATALOG:
        rows.append([InlineKeyboardButton(c.title, callback_data=f"cat:{c.cid}")])
    return InlineKeyboardMarkup(rows)


def kb_products(cat_id: str) -> InlineKeyboardMarkup:
    c = CAT_BY_ID[cat_id]
    rows = []
    for p in c.products:
        # مثل الصورة: "60 UC | 0.875$ | 4690"
        label = f"{p.title} | {money(p.price)} | {p.stock}"
        rows.append([InlineKeyboardButton(label, callback_data=f"prod:{p.pid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back:cats")])
    return InlineKeyboardMarkup(rows)


def kb_qty_controls(cat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{cat_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cat_id}")],
        ]
    )


async def edit_or_send(update: Update, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    """
    إذا كان عندنا callback -> نعدل نفس الرسالة (مثل الصور)
    إذا رسالة عادية -> نرسل رسالة جديدة
    """
    if update.callback_query:
        q = update.callback_query
        await q.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)


# =========================
# Pages
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "🛒 CODES & Gift Cards\n\n"
        "📦 Product Categories:\n"
        "Choose a category below:"
    )
    await update.message.reply_text(text, reply_markup=kb_categories())


async def show_products_page(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: str):
    c = CAT_BY_ID[cat_id]
    context.user_data[UD_SELECTED_CAT] = cat_id
    context.user_data[UD_SELECTED_PROD] = None
    context.user_data[UD_AWAITING_QTY] = False

    text = f"{c.title} - Choose a product:"
    await edit_or_send(update, text, reply_markup=kb_products(cat_id))


async def show_quantity_page(update: Update, context: ContextTypes.DEFAULT_TYPE, prod_id: str):
    p = PROD_BY_ID[prod_id]
    cat_id = PROD_TO_CAT[prod_id]
    c = CAT_BY_ID[cat_id]

    context.user_data[UD_SELECTED_CAT] = cat_id
    context.user_data[UD_SELECTED_PROD] = prod_id
    context.user_data[UD_AWAITING_QTY] = True

    text = (
        f"🛒 Your Order — Codes & Gift Cards ⚡\n\n"
        f"📦 {c.title}\n"
        f"🔹 Product: {p.title}\n"
        f"💎 Price: {money(p.price)}\n"
        f"📦 In Stock: {p.stock}\n\n"
        f"🔻 Enter a quantity between 1 and {p.stock}:"
    )
    await edit_or_send(update, text, reply_markup=kb_qty_controls(cat_id))


# =========================
# Callbacks (Inline Buttons داخل المحادثة)
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    # Category selected
    if data.startswith("cat:"):
        cat_id = data.split(":", 1)[1]
        if cat_id not in CAT_BY_ID:
            return await q.edit_message_text("❌ Category not found.")
        return await show_products_page(update, context, cat_id)

    # Product selected
    if data.startswith("prod:"):
        prod_id = data.split(":", 1)[1]
        if prod_id not in PROD_BY_ID:
            return await q.edit_message_text("❌ Product not found.")

        p = PROD_BY_ID[prod_id]
        if p.stock <= 0:
            # نفس الصفحة لكن نخبره أنه غير متوفر
            cat_id = PROD_TO_CAT[prod_id]
            return await q.edit_message_text(
                f"❌ هذا المنتج غير متوفر حالياً.\n\nاختر منتجاً آخر:",
                reply_markup=kb_products(cat_id),
            )

        return await show_quantity_page(update, context, prod_id)

    # Back to categories
    if data == "back:cats":
        context.user_data[UD_SELECTED_PROD] = None
        context.user_data[UD_AWAITING_QTY] = False
        text = (
            "🛒 CODES & Gift Cards\n\n"
            "📦 Product Categories:\n"
            "Choose a category below:"
        )
        return await q.edit_message_text(text, reply_markup=kb_categories())

    # Back to products list
    if data.startswith("back:prods:"):
        cat_id = data.split(":", 2)[2]
        if cat_id not in CAT_BY_ID:
            return await q.edit_message_text("❌ Category not found.")
        return await show_products_page(update, context, cat_id)

    # Cancel order
    if data.startswith("cancel:"):
        cat_id = data.split(":", 1)[1]
        context.user_data[UD_SELECTED_PROD] = None
        context.user_data[UD_AWAITING_QTY] = False
        return await q.edit_message_text(
            "✅ تم إلغاء الطلب.\nاختر منتجاً آخر:",
            reply_markup=kb_products(cat_id),
        )


# =========================
# Quantity input (كتابة رقم)
# =========================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # لو المستخدم ليس في وضع إدخال كمية، نرشده
    if not context.user_data.get(UD_AWAITING_QTY):
        if text.lower() in ("menu", "/start"):
            return await start(update, context)
        return await update.message.reply_text("اكتب /start لعرض الأقسام ✅")

    # ننتظر كمية
    prod_id = context.user_data.get(UD_SELECTED_PROD)
    if not prod_id or prod_id not in PROD_BY_ID:
        context.user_data[UD_AWAITING_QTY] = False
        return await update.message.reply_text("❌ حدث خطأ. اكتب /start من جديد.")

    p = PROD_BY_ID[prod_id]

    # لازم رقم صحيح
    try:
        qty = int(text)
    except ValueError:
        return await update.message.reply_text(f"❌ اكتب رقم فقط بين 1 و {p.stock}.")

    if qty < 1 or qty > p.stock:
        return await update.message.reply_text(f"❌ الكمية لازم تكون بين 1 و {p.stock}.")

    # مثال: “تأكيد الطلب” (هنا فقط Demo)
    total = qty * p.price

    # (اختياري) نقص المخزون في الذاكرة
    p.stock -= qty

    # نخرج من وضع إدخال الكمية
    context.user_data[UD_AWAITING_QTY] = False
    context.user_data[UD_SELECTED_PROD] = None

    cat_id = PROD_TO_CAT[prod_id]
    await update.message.reply_text(
        f"✅ تم استلام طلبك!\n\n"
        f"📦 المنتج: {p.title}\n"
        f"🔢 الكمية: {qty}\n"
        f"💰 الإجمالي: {money(total)}\n\n"
        f"اختر منتجاً آخر:",
        reply_markup=kb_products(cat_id),
    )


# =========================
# Run
# =========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling()


if __name__ == "__main__":
    main()

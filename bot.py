import os
import re
import time
import logging
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("digital-store-bot")


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")

# USDT receiving wallet addresses (put your own)
USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")
USDT_BEP20_ADDRESS = os.getenv("USDT_BEP20_ADDRESS", "0xXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")
STORE_NAME = os.getenv("STORE_NAME", "Digital Store")

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN env var")


# ===== Reply Menu (Bottom) =====
def main_menu_kb() -> ReplyKeyboardMarkup:
    # Arrange like typical shop bot
    rows = [
        [KeyboardButton("🛒 المتجر"), KeyboardButton("💼 محفظتي")],
        [KeyboardButton("➕ شحن USDT"), KeyboardButton("📦 طلباتي")],
        [KeyboardButton("📞 الدعم"), KeyboardButton("ℹ️ معلومات")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def back_inline(btn_text: str = "⬅️ رجوع", data: str = "nav:back") -> InlineKeyboardButton:
    return InlineKeyboardButton(btn_text, callback_data=data)


# ===== Conversation States =====
(
    ST_SHOP_CATEGORY,
    ST_SHOP_PRODUCT,
    ST_QTY_INPUT,
    ST_TOPUP_AMOUNT,
    ST_TOPUP_NETWORK,
    ST_TOPUP_TX,
) = range(6)


@dataclass
class PendingOrder:
    category: str
    product_id: int
    qty: int


def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


# ===== Utilities =====
def money(x: float) -> str:
    return f"{x:.2f} USDT"


def safe_int(text: str) -> Optional[int]:
    try:
        v = int(str(text).strip())
        return v
    except Exception:
        return None


def safe_float(text: str) -> Optional[float]:
    try:
        t = str(text).strip().replace(",", ".")
        v = float(t)
        return v
    except Exception:
        return None


def fmt_ts(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u:
        return
    db.ensure_user(u.id, u.username, u.first_name)

    text = (
        f"👋 أهلاً بك في *{STORE_NAME}*\n\n"
        "اختر من القائمة بالأسفل للتصفح والشراء."
    )
    await update.message.reply_text(text, reply_markup=main_menu_kb(), parse_mode=ParseMode.MARKDOWN)


async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u:
        return
    db.ensure_user(u.id, u.username, u.first_name)
    await update.message.reply_text("🏠 القائمة الرئيسية", reply_markup=main_menu_kb())


# ===== Shop Flow =====
async def shop_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = update.effective_user
    if not u:
        return ConversationHandler.END
    db.ensure_user(u.id, u.username, u.first_name)

    cats = db.list_categories()
    buttons = []
    for c in cats:
        buttons.append([InlineKeyboardButton(f"📦 {c}", callback_data=f"cat:{c}")])

    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")])

    await update.message.reply_text(
        "🛒 *اختر القسم:*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_SHOP_CATEGORY


async def shop_cat_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    if data == "nav:home":
        await q.edit_message_text("🏠 عدنا للرئيسية. استخدم القائمة بالأسفل.")
        return ConversationHandler.END

    m = re.match(r"^cat:(.+)$", data)
    if not m:
        await q.edit_message_text("حدث خطأ في الاختيار.")
        return ConversationHandler.END

    category = m.group(1)
    products = db.get_products_by_category(category)

    if not products:
        kb = InlineKeyboardMarkup([
            [back_inline("⬅️ رجوع", "nav:back_to_cats")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
        ])
        await q.edit_message_text(
            f"لا توجد منتجات حالياً في قسم *{category}*.",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_SHOP_CATEGORY

    rows = []
    for p in products:
        rows.append([
            InlineKeyboardButton(
                f"{p['name']} — {money(float(p['price_usdt']))}",
                callback_data=f"prod:{p['id']}"
            )
        ])
    rows.append([back_inline("⬅️ رجوع", "nav:back_to_cats")])

    context.user_data["shop_category"] = category

    await q.edit_message_text(
        f"📦 *{category}*\nاختر المنتج:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_SHOP_PRODUCT


async def shop_back_to_cats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    # Re-render categories
    cats = db.list_categories()
    buttons = [[InlineKeyboardButton(f"📦 {c}", callback_data=f"cat:{c}")] for c in cats]
    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")])
    await q.edit_message_text(
        "🛒 *اختر القسم:*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_SHOP_CATEGORY


async def shop_product_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    if data == "nav:back_to_cats":
        return await shop_back_to_cats(update, context)

    m = re.match(r"^prod:(\d+)$", data)
    if not m:
        await q.edit_message_text("اختيار غير صالح.")
        return ConversationHandler.END

    product_id = int(m.group(1))
    product = db.get_product(product_id)
    if not product or int(product.get("is_active", 0)) != 1:
        await q.edit_message_text("هذا المنتج غير متاح حالياً.")
        return ConversationHandler.END

    context.user_data["selected_product_id"] = product_id

    kb = InlineKeyboardMarkup([
        [back_inline("⬅️ رجوع", "nav:back_to_products")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
    ])

    await q.edit_message_text(
        f"✅ اخترت: *{product['name']}*\n"
        f"💲 السعر للوحدة: *{money(float(product['price_usdt']))}*\n\n"
        "✍️ الآن أرسل *الكمية* (رقم فقط).",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_QTY_INPUT


async def shop_back_to_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    category = context.user_data.get("shop_category")
    if not category:
        return await shop_back_to_cats(update, context)

    products = db.get_products_by_category(category)
    rows = []
    for p in products:
        rows.append([InlineKeyboardButton(
            f"{p['name']} — {money(float(p['price_usdt']))}",
            callback_data=f"prod:{p['id']}"
        )])
    rows.append([back_inline("⬅️ رجوع", "nav:back_to_cats")])

    await q.edit_message_text(
        f"📦 *{category}*\nاختر المنتج:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_SHOP_PRODUCT


async def qty_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = update.effective_user
    if not u or not update.message:
        return ConversationHandler.END

    qty = safe_int(update.message.text)
    if qty is None or qty <= 0 or qty > 9999:
        await update.message.reply_text("❌ الكمية غير صحيحة. أرسل رقم من 1 إلى 9999.")
        return ST_QTY_INPUT

    product_id = context.user_data.get("selected_product_id")
    if not product_id:
        await update.message.reply_text("حدث خطأ: لم يتم تحديد المنتج.")
        return ConversationHandler.END

    product = db.get_product(int(product_id))
    if not product or int(product.get("is_active", 0)) != 1:
        await update.message.reply_text("هذا المنتج غير متاح حالياً.")
        return ConversationHandler.END

    total = float(product["price_usdt"]) * qty
    context.user_data["pending_order"] = {
        "category": product["category"],
        "product_id": int(product["id"]),
        "qty": int(qty),
        "unit_price": float(product["price_usdt"]),
        "product_name": product["name"],
        "total": float(total),
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد الطلب", callback_data="order:confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="order:cancel"),
        ],
        [back_inline("⬅️ رجوع", "nav:back_to_products")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
    ])

    await update.message.reply_text(
        "🧾 *ملخص الطلب*\n"
        f"• المنتج: *{product['name']}*\n"
        f"• الكمية: *{qty}*\n"
        f"• سعر الوحدة: *{money(float(product['price_usdt']))}*\n"
        f"• الإجمالي: *{money(float(total))}*\n\n"
        "اضغط *تأكيد الطلب* للخصم من محفظتك.",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_QTY_INPUT


async def order_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    if data == "nav:home":
        await q.edit_message_text("🏠 عدنا للرئيسية. استخدم القائمة بالأسفل.")
        return ConversationHandler.END

    if data == "nav:back_to_products":
        return await shop_back_to_products(update, context)

    if data == "order:cancel":
        context.user_data.pop("pending_order", None)
        await q.edit_message_text("❌ تم إلغاء العملية.")
        return ConversationHandler.END

    if data != "order:confirm":
        await q.edit_message_text("طلب غير معروف.")
        return ConversationHandler.END

    u = q.from_user
    if not u:
        return ConversationHandler.END

    pending = context.user_data.get("pending_order")
    if not pending:
        await q.edit_message_text("لا يوجد طلب قيد التأكيد.")
        return ConversationHandler.END

    db.ensure_user(u.id, u.username, u.first_name)

    total = float(pending["total"])
    ok = db.deduct_balance(u.id, total)
    if not ok:
        bal = db.get_balance(u.id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ شحن USDT", callback_data="nav:goto_topup")],
            [InlineKeyboardButton("⬅️ رجوع للمنتجات", callback_data="nav:back_to_products")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
        ])
        await q.edit_message_text(
            "❌ رصيدك غير كافٍ.\n"
            f"رصيدك الحالي: *{money(bal)}*\n"
            f"مطلوب: *{money(total)}*\n\n"
            "يمكنك شحن المحفظة ثم إعادة الطلب.",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_SHOP_PRODUCT

    # Create order as PAID (wallet)
    product = db.get_product(int(pending["product_id"]))
    if not product:
        # Safety: refund if missing
        db.add_balance(u.id, total)
        await q.edit_message_text("حدث خطأ بالمنتج. تم إرجاع الرصيد.")
        return ConversationHandler.END

    order_id = db.create_order(u.id, product, int(pending["qty"]))
    db.update_order_status(order_id, "PAID_PROCESSING")

    context.user_data.pop("pending_order", None)

    # Notify admin
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🆕 *طلب جديد (مدفوع من المحفظة)*\n"
                    f"Order ID: `{order_id}`\n"
                    f"User: `{u.id}` @{u.username or '-'}\n"
                    f"Product: *{product['name']}*\n"
                    f"Qty: *{pending['qty']}*\n"
                    f"Total: *{money(total)}*\n\n"
                    "غيّر حالته لاحقاً عبر:\n"
                    f"`/order_done {order_id}` أو `/order_cancel {order_id}`"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning("Failed to notify admin: %s", e)

    await q.edit_message_text(
        "✅ تم تأكيد الطلب بنجاح!\n\n"
        f"📦 رقم الطلب: *{order_id}*\n"
        "الحالة: *قيد المعالجة*\n\n"
        "سيتواصل الدعم/سيتم التسليم حسب نظام متجرك.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def goto_topup_from_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("➕ شحن USDT: أرسل المبلغ المطلوب (مثال: 10).")
    return ST_TOPUP_AMOUNT


# ===== Wallet / Orders / Support =====
async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u or not update.message:
        return
    db.ensure_user(u.id, u.username, u.first_name)
    bal = db.get_balance(u.id)

    text = (
        "💼 *محفظتي*\n"
        f"الرصيد الحالي: *{money(bal)}*\n\n"
        "للشحن اضغط: ➕ شحن USDT"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u or not update.message:
        return
    db.ensure_user(u.id, u.username, u.first_name)
    orders = db.list_user_orders(u.id, limit=10)

    if not orders:
        await update.message.reply_text("📦 لا توجد طلبات بعد.")
        return

    lines = ["📦 *آخر طلباتك:*"]
    for o in orders:
        lines.append(
            f"• #{o['id']} — {o['product_name']} ×{o['qty']} — {money(float(o['total_usdt']))} — `{o['status']}`"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    txt = (
        "📞 *الدعم*\n"
        "اكتب مشكلتك هنا وسيصلنا.\n\n"
        "ملاحظة: يمكنك وضع آيدي الدعم/رابطه هنا لاحقاً."
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    txt = (
        f"ℹ️ *{STORE_NAME}*\n"
        "بوت متجر رقمي.\n\n"
        "• ادخل المتجر: 🛒 المتجر\n"
        "• راقب رصيدك: 💼 محفظتي\n"
        "• اشحن عبر USDT: ➕ شحن USDT\n"
        "• تابع طلباتك: 📦 طلباتي"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)


# ===== Topup Flow (USDT manual verification) =====
async def topup_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    await update.message.reply_text(
        "➕ *شحن USDT*\n"
        "أرسل *المبلغ* الذي تريد شحنه (مثال: 10 أو 15.5).",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_TOPUP_AMOUNT


async def topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = update.effective_user
    if not u or not update.message:
        return ConversationHandler.END

    amount = safe_float(update.message.text)
    if amount is None or amount <= 0 or amount > 100000:
        await update.message.reply_text("❌ مبلغ غير صحيح. أرسل رقم مثل: 10 أو 15.5")
        return ST_TOPUP_AMOUNT

    context.user_data["topup_amount"] = float(amount)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("TRC20", callback_data="net:TRC20")],
        [InlineKeyboardButton("BEP20", callback_data="net:BEP20")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="topup:back_amount")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
    ])
    await update.message.reply_text(
        f"اختر الشبكة لشحن *{money(amount)}*:",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_TOPUP_NETWORK


async def topup_network_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    if data == "nav:home":
        await q.edit_message_text("🏠 عدنا للرئيسية.")
        return ConversationHandler.END

    if data == "topup:back_amount":
        await q.edit_message_text("أرسل المبلغ من جديد:")
        return ST_TOPUP_AMOUNT

    m = re.match(r"^net:(TRC20|BEP20)$", data)
    if not m:
        await q.edit_message_text("اختيار شبكة غير صحيح.")
        return ConversationHandler.END

    net = m.group(1)
    context.user_data["topup_network"] = net
    amount = float(context.user_data.get("topup_amount", 0))

    addr = USDT_TRC20_ADDRESS if net == "TRC20" else USDT_BEP20_ADDRESS

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ أرسلت التحويل", callback_data="topup:sent")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="topup:back_network")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
    ])

    await q.edit_message_text(
        "📌 *عنوان الدفع*\n"
        f"الشبكة: *{net}*\n"
        f"المبلغ: *{money(amount)}*\n\n"
        f"`{addr}`\n\n"
        "بعد التحويل اضغط *أرسلت التحويل* ثم أرسل *TX Hash* (معرّف العملية).",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_TOPUP_NETWORK


async def topup_sent_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "nav:home":
        await q.edit_message_text("🏠 عدنا للرئيسية.")
        return ConversationHandler.END
    if q.data == "topup:back_network":
        # re-show network choices
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("TRC20", callback_data="net:TRC20")],
            [InlineKeyboardButton("BEP20", callback_data="net:BEP20")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="topup:back_amount")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")],
        ])
        await q.edit_message_text("اختر الشبكة:", reply_markup=kb)
        return ST_TOPUP_NETWORK

    if q.data != "topup:sent":
        await q.edit_message_text("أمر غير معروف.")
        return ConversationHandler.END

    await q.edit_message_text("✍️ الآن أرسل *TX Hash* (مثال يبدأ بـ 0x... أو أحرف/أرقام).", parse_mode=ParseMode.MARKDOWN)
    return ST_TOPUP_TX


async def topup_tx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = update.effective_user
    if not u or not update.message:
        return ConversationHandler.END

    tx = (update.message.text or "").strip()
    if len(tx) < 8:
        await update.message.reply_text("❌ TX Hash قصير جداً. أعد الإرسال.")
        return ST_TOPUP_TX

    amount = float(context.user_data.get("topup_amount", 0))
    net = str(context.user_data.get("topup_network", "")).strip()
    if amount <= 0 or net not in ("TRC20", "BEP20"):
        await update.message.reply_text("حدث خطأ في بيانات الشحن. ابدأ من جديد: ➕ شحن USDT")
        return ConversationHandler.END

    db.ensure_user(u.id, u.username, u.first_name)

    topup_id = db.create_topup(u.id, amount, net, tx)

    # Notify admin to approve
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "💰 *طلب شحن جديد (USDT)*\n"
                    f"Topup ID: `{topup_id}`\n"
                    f"User: `{u.id}` @{u.username or '-'}\n"
                    f"Amount: *{money(amount)}*\n"
                    f"Network: *{net}*\n"
                    f"TX: `{tx}`\n\n"
                    "للموافقة:\n"
                    f"`/approve_topup {topup_id}`\n"
                    "للرفض:\n"
                    f"`/reject_topup {topup_id}`"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning("Failed to notify admin: %s", e)

    context.user_data.pop("topup_amount", None)
    context.user_data.pop("topup_network", None)

    await update.message.reply_text(
        "✅ تم استلام طلب الشحن.\n"
        f"🧾 رقم العملية: {topup_id}\n"
        "الحالة: قيد المراجعة.\n\n"
        "سيتم إضافة الرصيد بعد التأكيد.",
        reply_markup=main_menu_kb()
    )
    return ConversationHandler.END


# ===== Admin Commands =====
async def admin_only(update: Update) -> bool:
    u = update.effective_user
    return bool(u and is_admin(u.id))


async def cmd_pending_topups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return

    pending = db.list_pending_topups(limit=20)
    if not pending:
        await update.message.reply_text("لا توجد عمليات شحن معلّقة.")
        return

    lines = ["🕒 *الشحنات المعلقة:*"]
    for t in pending:
        lines.append(
            f"• ID:{t['id']} user:{t['user_id']} {money(float(t['amount_usdt']))} {t['network']} — `{t['tx_hash']}`"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_approve_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /approve_topup TOPUP_ID")
        return
    tid = safe_int(context.args[0])
    if not tid:
        await update.message.reply_text("TOPUP_ID غير صحيح.")
        return

    t = db.get_topup(tid)
    if not t:
        await update.message.reply_text("العملية غير موجودة.")
        return
    if t["status"] != "PENDING":
        await update.message.reply_text(f"هذه العملية حالتها: {t['status']}")
        return

    db.set_topup_status(tid, "APPROVED")
    db.add_balance(int(t["user_id"]), float(t["amount_usdt"]))

    # Notify user
    try:
        await context.bot.send_message(
            chat_id=int(t["user_id"]),
            text=(
                "✅ تم تأكيد الشحن وإضافة الرصيد.\n"
                f"المبلغ: {money(float(t['amount_usdt']))}\n"
                f"الرصيد الحالي: {money(db.get_balance(int(t['user_id'])))}"
            )
        )
    except Exception as e:
        logger.warning("Failed to notify user for topup approval: %s", e)

    await update.message.reply_text("✅ تم اعتماد الشحن وإضافة الرصيد.")


async def cmd_reject_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /reject_topup TOPUP_ID")
        return
    tid = safe_int(context.args[0])
    if not tid:
        await update.message.reply_text("TOPUP_ID غير صحيح.")
        return

    t = db.get_topup(tid)
    if not t:
        await update.message.reply_text("العملية غير موجودة.")
        return
    if t["status"] != "PENDING":
        await update.message.reply_text(f"هذه العملية حالتها: {t['status']}")
        return

    db.set_topup_status(tid, "REJECTED")

    try:
        await context.bot.send_message(
            chat_id=int(t["user_id"]),
            text="❌ تم رفض عملية الشحن. إذا تعتقد أن هناك خطأ تواصل مع الدعم."
        )
    except Exception as e:
        logger.warning("Failed to notify user for topup rejection: %s", e)

    await update.message.reply_text("تم رفض الشحن.")


async def cmd_order_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /order_done ORDER_ID")
        return
    oid = safe_int(context.args[0])
    if not oid:
        await update.message.reply_text("ORDER_ID غير صحيح.")
        return
    o = db.get_order(oid)
    if not o:
        await update.message.reply_text("الطلب غير موجود.")
        return
    db.update_order_status(oid, "DONE")

    try:
        await context.bot.send_message(
            chat_id=int(o["user_id"]),
            text=f"✅ تم إكمال طلبك #{oid}. شكراً لك!"
        )
    except Exception as e:
        logger.warning("Failed to notify user order done: %s", e)

    await update.message.reply_text("✅ تم تحديث حالة الطلب إلى DONE.")


async def cmd_order_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /order_cancel ORDER_ID")
        return
    oid = safe_int(context.args[0])
    if not oid:
        await update.message.reply_text("ORDER_ID غير صحيح.")
        return
    o = db.get_order(oid)
    if not o:
        await update.message.reply_text("الطلب غير موجود.")
        return

    # Optional refund if already paid (wallet)
    # We refund for statuses that indicate paid/processing, not for pending payment.
    status = str(o["status"])
    if status in ("PAID_PROCESSING", "PAID"):
        db.add_balance(int(o["user_id"]), float(o["total_usdt"]))

    db.update_order_status(oid, "CANCELED")

    try:
        await context.bot.send_message(
            chat_id=int(o["user_id"]),
            text=f"❌ تم إلغاء طلبك #{oid}."
        )
    except Exception as e:
        logger.warning("Failed to notify user order canceled: %s", e)

    await update.message.reply_text("✅ تم إلغاء الطلب.")


async def cmd_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return
    # /add_product CATEGORY | NAME | PRICE
    text = update.message.text or ""
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3:
        await update.message.reply_text("الاستخدام:\n/add_product CATEGORY | NAME | PRICE\nمثال:\n/add_product PUBG | 60 UC | 0.99")
        return

    # First part includes command + category
    cat = parts[0].replace("/add_product", "").strip()
    name = parts[1]
    price = safe_float(parts[2])
    if not cat or not name or price is None or price <= 0:
        await update.message.reply_text("بيانات غير صحيحة.")
        return

    pid = db.upsert_product(cat, name, float(price), active=True)
    await update.message.reply_text(f"✅ تم إضافة المنتج ID={pid}")


async def cmd_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return
    # /set_price PRODUCT_ID PRICE
    if len(context.args) < 2:
        await update.message.reply_text("الاستخدام: /set_price PRODUCT_ID PRICE")
        return
    pid = safe_int(context.args[0])
    price = safe_float(context.args[1])
    if not pid or price is None or price <= 0:
        await update.message.reply_text("بيانات غير صحيحة.")
        return
    db.set_product_price(pid, float(price))
    await update.message.reply_text("✅ تم تحديث السعر.")


async def cmd_disable_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /disable_product PRODUCT_ID")
        return
    pid = safe_int(context.args[0])
    if not pid:
        await update.message.reply_text("PRODUCT_ID غير صحيح.")
        return
    db.set_product_active(pid, False)
    await update.message.reply_text("✅ تم تعطيل المنتج.")


async def cmd_enable_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update) or not update.message:
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /enable_product PRODUCT_ID")
        return
    pid = safe_int(context.args[0])
    if not pid:
        await update.message.reply_text("PRODUCT_ID غير صحيح.")
        return
    db.set_product_active(pid, True)
    await update.message.reply_text("✅ تم تفعيل المنتج.")


# ===== Router for Reply Menu texts =====
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    if not update.message:
        return ConversationHandler.END

    text = (update.message.text or "").strip()

    if text == "🛒 المتجر":
        return await shop_entry(update, context)
    if text == "💼 محفظتي":
        await wallet(update, context)
        return ConversationHandler.END
    if text == "➕ شحن USDT":
        return await topup_entry(update, context)
    if text == "📦 طلباتي":
        await my_orders(update, context)
        return ConversationHandler.END
    if text == "📞 الدعم":
        await support(update, context)
        return ConversationHandler.END
    if text == "ℹ️ معلومات":
        await info(update, context)
        return ConversationHandler.END

    # If user types something else while not in conversation
    await update.message.reply_text("اختر من القائمة بالأسفل 👇", reply_markup=main_menu_kb())
    return ConversationHandler.END


def build_app() -> Application:
    db.init_db()
    db.seed_default_products()

    app = Application.builder().token(BOT_TOKEN).build()

    # Start / Home
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("home", show_home))

    # Admin
    app.add_handler(CommandHandler("pending_topups", cmd_pending_topups))
    app.add_handler(CommandHandler("approve_topup", cmd_approve_topup))
    app.add_handler(CommandHandler("reject_topup", cmd_reject_topup))
    app.add_handler(CommandHandler("order_done", cmd_order_done))
    app.add_handler(CommandHandler("order_cancel", cmd_order_cancel))
    app.add_handler(CommandHandler("add_product", cmd_add_product))
    app.add_handler(CommandHandler("set_price", cmd_set_price))
    app.add_handler(CommandHandler("disable_product", cmd_disable_product))
    app.add_handler(CommandHandler("enable_product", cmd_enable_product))

    # Shop conversation
    shop_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router),
        ],
        states={
            ST_SHOP_CATEGORY: [
                CallbackQueryHandler(shop_cat_cb, pattern=r"^(cat:|nav:home).+|^nav:home$"),
                CallbackQueryHandler(shop_back_to_cats, pattern=r"^nav:back_to_cats$"),
            ],
            ST_SHOP_PRODUCT: [
                CallbackQueryHandler(shop_product_cb, pattern=r"^(prod:\d+|nav:back_to_cats)$"),
                CallbackQueryHandler(shop_back_to_products, pattern=r"^nav:back_to_products$"),
            ],
            ST_QTY_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, qty_input),
                CallbackQueryHandler(order_cb, pattern=r"^(order:confirm|order:cancel|nav:home|nav:back_to_products|nav:goto_topup)$"),
                CallbackQueryHandler(shop_back_to_products, pattern=r"^nav:back_to_products$"),
                CallbackQueryHandler(shop_back_to_cats, pattern=r"^nav:back_to_cats$"),
                CallbackQueryHandler(goto_topup_from_inline, pattern=r"^nav:goto_topup$"),
            ],
            ST_TOPUP_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, topup_amount),
            ],
            ST_TOPUP_NETWORK: [
                CallbackQueryHandler(topup_network_cb, pattern=r"^(net:(TRC20|BEP20)|topup:back_amount|nav:home)$"),
                CallbackQueryHandler(topup_sent_cb, pattern=r"^(topup:sent|topup:back_network|nav:home)$"),
            ],
            ST_TOPUP_TX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, topup_tx),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("home", show_home),
        ],
        allow_reentry=True,
    )

    app.add_handler(shop_conv)

    # If user sends random text not caught: route it
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    return app


def main():
    app = build_app()
    logger.info("Bot started.")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

import os
import logging
from typing import Dict

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from catalog import CATALOG, money
from db import DB
import keyboards as kb

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # ضع ايدي الأدمن
USDT_ADDRESS = os.environ.get("USDT_ADDRESS", "PUT_YOUR_USDT_TRC20_ADDRESS")
DB_PATH = os.environ.get("DB_PATH", "data.db")

db = DB(DB_PATH)

# --- user_data keys ---
UD_CAT = "cat"
UD_PID = "pid"
UD_QTY = "qty"
UD_AWAIT_DEPOSIT = "await_deposit"

# Build quick maps
CAT_BY_ID = {c.cid: c for c in CATALOG}
PROD_BY_ID = {p.pid: p for c in CATALOG for p in c.products}
PROD_TO_CAT = {p.pid: c.cid for c in CATALOG for p in c.products}

def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID

def ensure_stock_seed():
    # seed stock table once (if not exists)
    for c in CATALOG:
        for p in c.products:
            if db.stock_get(p.pid) is None:
                db.stock_set(p.pid, p.stock)

def cats_list():
    return [(c.cid, c.title) for c in CATALOG]

def products_list(cat_id: str):
    c = CAT_BY_ID[cat_id]
    out = []
    for p in c.products:
        stock = db.stock_get(p.pid) or 0
        label = f"{p.title} | {money(p.price)} | {stock}"
        out.append((p.pid, label))
    return out

async def home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(UD_AWAIT_DEPOSIT, None)
    text = (
        "🛒 *متجر الأكواد والبطاقات*\n\n"
        "اختر من القائمة بالأسفل أو اضغط *🛍 الأقسام*.\n"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=None, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb.main_menu_kb(), parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username or "", user.first_name or "")
    ensure_stock_seed()
    await update.message.reply_text(
        "✅ أهلاً بك!\nاختر من القائمة 👇",
        reply_markup=kb.main_menu_kb(),
    )
    await show_categories(update, context)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[UD_CAT] = None
    context.user_data[UD_PID] = None
    context.user_data[UD_QTY] = 1

    text = "📦 *الأقسام المتاحة:*\nاختر قسمًا:"
    markup = kb.cats_kb(cats_list())
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: str):
    context.user_data[UD_CAT] = cat_id
    context.user_data[UD_PID] = None
    context.user_data[UD_QTY] = 1
    c = CAT_BY_ID[cat_id]
    text = f"📦 *{c.title}*\nاختر المنتج:"
    markup = kb.products_kb(products_list(cat_id))
    await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")

async def show_qty(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: str):
    cat_id = PROD_TO_CAT[pid]
    p = PROD_BY_ID[pid]
    stock = db.stock_get(pid) or 0
    if stock <= 0:
        return await update.callback_query.edit_message_text(
            "❌ هذا المنتج غير متوفر حالياً.\nاختر منتجاً آخر:",
            reply_markup=kb.products_kb(products_list(cat_id)),
        )

    context.user_data[UD_PID] = pid
    context.user_data[UD_QTY] = 1
    text = (
        "🧾 *تفاصيل الطلب*\n\n"
        f"📦 القسم: {CAT_BY_ID[cat_id].title}\n"
        f"🔹 المنتج: {p.title}\n"
        f"💰 السعر: {money(p.price)}\n"
        f"📦 المتوفر: {stock}\n\n"
        "اختر الكمية ثم اضغط *تأكيد الطلب*."
    )
    await update.callback_query.edit_message_text(
        text,
        reply_markup=kb.qty_kb(pid, 1, cat_id),
        parse_mode="Markdown",
    )

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = db.get_balance(user_id)
    text = f"💳 *محفظتك*\n\nالرصيد الحالي: *{bal:.3f}$*"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb.wallet_kb(), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb.wallet_kb(), parse_mode="Markdown")

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = db.list_orders(user_id, limit=10)
    if not rows:
        msg = "📦 لا توجد طلبات حتى الآن."
    else:
        lines = ["📦 *آخر طلباتك:*"]
        for oid, title, qty, total, status, created_at in rows:
            lines.append(f"#{oid} • {title} x{qty} • {total:.3f}$ • *{status}* • {created_at}")
        msg = "\n".join(lines)

    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[UD_AWAIT_DEPOSIT] = True
    text = (
        "➕ *شحن USDT (يدوي)*\n\n"
        f"📮 العنوان (TRC20):\n`{USDT_ADDRESS}`\n\n"
        "أرسل الآن رسالة تحتوي على:\n"
        "- TxID أو Hash\n"
        "- ويفضل كتابة المبلغ بالدولار (مثال: 10)\n\n"
        "مثال:\n`TXID: abc...`\n`AMOUNT: 10`\n"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def handle_deposit_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ينتظر TxID/تفاصيل
    user_id = update.effective_user.id
    txt = (update.message.text or "").strip()
    if not txt:
        return

    dep_id = db.create_deposit(user_id=user_id, tx_ref=txt[:1000], amount=None)
    context.user_data[UD_AWAIT_DEPOSIT] = False

    await update.message.reply_text(
        f"✅ تم استلام طلب الشحن.\nرقم العملية: #{dep_id}\nسيتم المراجعة من الإدارة.",
        reply_markup=kb.main_menu_kb(),
    )

    # Notify admin
    if ADMIN_ID:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💰 *طلب شحن جديد*\n"
                f"Deposit ID: #{dep_id}\n"
                f"User: `{user_id}`\n"
                f"Ref:\n`{txt}`\n\n"
                "للاعتماد:\n"
                f"/approve {dep_id} 10.0\n"
                "للرفض:\n"
                f"/reject {dep_id}\n"
            ),
            parse_mode="Markdown",
        )

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ غير مصرح.")
    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام: /approve <deposit_id> <amount>")
    try:
        dep_id = int(context.args[0])
        amount = float(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ قيم غير صحيحة.")

    user_id = db.approve_deposit(dep_id, amount)
    if not user_id:
        return await update.message.reply_text("❌ لم يتم الاعتماد (قد يكون غير موجود أو تم التعامل معه).")

    await update.message.reply_text(f"✅ تم اعتماد الشحن #{dep_id} وإضافة {amount:.3f}$ للمستخدم {user_id}.")
    await context.bot.send_message(chat_id=user_id, text=f"✅ تم شحن محفظتك بمبلغ {amount:.3f}$ ✅")

async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ غير مصرح.")
    if len(context.args) < 1:
        return await update.message.reply_text("الاستخدام: /reject <deposit_id>")
    try:
        dep_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ رقم غير صحيح.")

    user_id = db.reject_deposit(dep_id)
    if not user_id:
        return await update.message.reply_text("❌ لم يتم الرفض (قد يكون غير موجود أو تم التعامل معه).")

    await update.message.reply_text(f"✅ تم رفض الشحن #{dep_id}.")
    await context.bot.send_message(chat_id=user_id, text=f"❌ تم رفض طلب الشحن #{dep_id}. تواصل مع الدعم إن كان هناك خطأ.")

async def admin_deliver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ غير مصرح.")
    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام: /deliver <order_id> <code_or_text>")
    try:
        order_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ order_id غير صحيح.")

    delivered_text = " ".join(context.args[1:]).strip()
    user_id = db.deliver_order(order_id, delivered_text)
    if not user_id:
        return await update.message.reply_text("❌ الطلب غير موجود.")

    await update.message.reply_text(f"✅ تم تسليم الطلب #{order_id}.")
    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ تم تسليم طلبك #{order_id}:\n\n{delivered_text}",
    )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "noop":
        return

    if data == "home":
        return await home(update, context)

    if data == "cats":
        return await show_categories(update, context)

    if data.startswith("cat:"):
        cat_id = data.split(":", 1)[1]
        if cat_id not in CAT_BY_ID:
            return await q.edit_message_text("❌ قسم غير موجود.")
        return await show_products(update, context, cat_id)

    if data.startswith("prod:"):
        pid = data.split(":", 1)[1]
        if pid not in PROD_BY_ID:
            return await q.edit_message_text("❌ منتج غير موجود.")
        return await show_qty(update, context, pid)

    if data.startswith("back:prods:"):
        cat_id = data.split(":", 2)[2]
        if cat_id not in CAT_BY_ID:
            return await q.edit_message_text("❌ قسم غير موجود.")
        return await show_products(update, context, cat_id)

    if data.startswith("cancel:"):
        cat_id = data.split(":", 1)[1]
        return await q.edit_message_text("✅ تم إلغاء الطلب.", reply_markup=kb.products_kb(products_list(cat_id)))

    if data == "deposit":
        return await deposit_start(update, context)

    # quantity adjustments
    if data.startswith("q:"):
        # q:+:pid or q:-:pid
        _, op, pid = data.split(":", 2)
        current_pid = context.user_data.get(UD_PID)
        if current_pid != pid:
            context.user_data[UD_PID] = pid
            context.user_data[UD_QTY] = 1

        qty = int(context.user_data.get(UD_QTY, 1))
        stock = db.stock_get(pid) or 0
        if op == "+":
            qty = min(stock, qty + 1)
        else:
            qty = max(1, qty - 1)

        context.user_data[UD_QTY] = qty
        cat_id = PROD_TO_CAT[pid]
        p = PROD_BY_ID[pid]
        text = (
            "🧾 *تفاصيل الطلب*\n\n"
            f"📦 القسم: {CAT_BY_ID[cat_id].title}\n"
            f"🔹 المنتج: {p.title}\n"
            f"💰 السعر: {money(p.price)}\n"
            f"📦 المتوفر: {stock}\n\n"
            "اختر الكمية ثم اضغط *تأكيد الطلب*."
        )
        return await q.edit_message_text(text, reply_markup=kb.qty_kb(pid, qty, cat_id), parse_mode="Markdown")

    # confirm order
    if data.startswith("confirm:"):
        pid = data.split(":", 1)[1]
        qty = int(context.user_data.get(UD_QTY, 1))
        stock = db.stock_get(pid) or 0
        if stock < qty:
            cat_id = PROD_TO_CAT[pid]
            return await q.edit_message_text(
                "❌ الكمية المطلوبة غير متوفرة.\nاختر كمية أقل.",
                reply_markup=kb.qty_kb(pid, min(stock, 1) if stock > 0 else 1, cat_id),
            )

        user_id = update.effective_user.id
        p = PROD_BY_ID[pid]
        total = qty * p.price

        # تحقق الرصيد
        if not db.charge_balance(user_id, total):
            bal = db.get_balance(user_id)
            return await q.edit_message_text(
                f"❌ رصيدك غير كافٍ.\nرصيدك: {bal:.3f}$\nالمطلوب: {total:.3f}$\n\nقم بشحن المحفظة.",
                reply_markup=kb.wallet_kb(),
            )

        # خصم من المخزون
        if not db.stock_dec(pid, qty):
            # رجع الرصيد إن فشل المخزون (احتياط)
            db.add_balance(user_id, total)
            return await q.edit_message_text("❌ حدث خطأ بالمخزون. حاول لاحقاً.")

        order_id = db.create_order(
            user_id=user_id,
            product_id=pid,
            product_title=p.title,
            unit_price=p.price,
            qty=qty,
            total=total,
        )

        await q.edit_message_text(
            "✅ *تم إنشاء الطلب بنجاح!*\n\n"
            f"رقم الطلب: *#{order_id}*\n"
            f"المنتج: {p.title}\n"
            f"الكمية: {qty}\n"
            f"الإجمالي: {total:.3f}$\n\n"
            "⏳ سيتم تسليم الطلب قريباً.",
            parse_mode="Markdown",
        )

        # notify admin
        if ADMIN_ID:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "📦 *طلب جديد*\n"
                    f"Order: #{order_id}\n"
                    f"User: `{user_id}`\n"
                    f"Product: {p.title}\n"
                    f"Qty: {qty}\n"
                    f"Total: {total:.3f}$\n\n"
                    "للتسليم:\n"
                    f"/deliver {order_id} CODE-XXXXX"
                ),
                parse_mode="Markdown",
            )
        return

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    user = update.effective_user
    db.upsert_user(user.id, user.username or "", user.first_name or "")
    ensure_stock_seed()

    # إذا ينتظر رسالة شحن
    if context.user_data.get(UD_AWAIT_DEPOSIT):
        return await handle_deposit_message(update, context)

    # Reply menu actions
    if txt == "🛍 الأقسام":
        return await show_categories(update, context)
    if txt == "💳 محفظتي":
        return await show_wallet(update, context)
    if txt == "➕ شحن USDT":
        return await deposit_start(update, context)
    if txt == "📦 طلباتي":
        return await show_orders(update, context)
    if txt == "📞 الدعم":
        return await update.message.reply_text("📞 للدعم: اكتب رسالتك هنا وسيتم الرد عليك قريباً ✅", reply_markup=kb.main_menu_kb())

    # افتراضي
    return await update.message.reply_text("استخدم القائمة بالأسفل ✅", reply_markup=kb.main_menu_kb())

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", admin_approve))
    app.add_handler(CommandHandler("reject", admin_reject))
    app.add_handler(CommandHandler("deliver", admin_deliver))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

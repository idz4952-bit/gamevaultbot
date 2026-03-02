# bot.py
import io
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import db
from config import (
    TOKEN,
    ADMIN_ID,
    CURRENCY,
    money,
    manual_open_now,
    manual_hours_text,
    new_client_ref,
)
from ui import (
    REPLY_MENU,
    MENU_BUTTONS,
    kb_categories,
    kb_products,
    kb_product_view,
    kb_balance_methods,
    kb_support,
    kb_topup_now,
    kb_orders_filters,
)

# =========================
# STATES
# =========================
ST_QTY = 10

UD_PID = "pid"
UD_CID = "cid"
UD_QTY_MAX = "qty_max"
UD_LAST_QTY = "last_qty"
UD_LAST_PID = "last_pid"
UD_ORDER_CLIENT_REF = "client_ref"


# =========================
# DELIVERY
# =========================
async def send_codes_delivery(chat_id, context, order_id, codes):
    if not codes:
        await context.bot.send_message(
            chat_id,
            f"✅ Order #{order_id} completed"
        )
        return

    text = (
        f"🎁 *Delivery Successful*\n"
        f"🧾 Order #{order_id}\n\n"
        f"`" + "\n".join(codes) + "`"
    )

    await context.bot.send_message(
        chat_id,
        text,
        parse_mode=ParseMode.MARKDOWN,
    )


# =========================
# START
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.upsert_user(update.effective_user)

    await update.message.reply_text(
        "✅ Bot Online 🚀",
        reply_markup=REPLY_MENU,
    )


# =========================
# MENU
# =========================
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.upsert_user(update.effective_user)

    text = update.message.text

    if text == "🛒 Our Products":
        return await show_categories(update, context)

    if text == "💰 My Balance":
        return await show_balance(update, context)

    if text == "📦 My Orders":
        return await show_orders(update, context)

    if text == "☎️ Contact Support":
        return await show_support(update, context)

    await update.message.reply_text(
        "Use menu 👇",
        reply_markup=REPLY_MENU,
    )


# =========================
# CATEGORIES
# =========================
async def show_categories(update, context):
    kb = kb_categories(db.is_admin_any(update.effective_user.id))

    if update.message:
        await update.message.reply_text(
            "🛒 Categories",
            reply_markup=kb,
        )
    else:
        await update.callback_query.edit_message_text(
            "🛒 Categories",
            reply_markup=kb,
        )


# =========================
# BALANCE
# =========================
async def show_balance(update, context):
    uid = update.effective_user.id
    bal = db.get_balance(uid)

    text = (
        "💰 Wallet\n\n"
        f"ID: {uid}\n"
        f"Balance: {bal:.3f} {CURRENCY}"
    )

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=kb_balance_methods(),
        )
    else:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=kb_balance_methods(),
        )


# =========================
# ORDERS
# =========================
async def show_orders(update, context):
    uid = update.effective_user.id

    db.cur.execute(
        """
        SELECT id,product_title,total,status,created_at
        FROM orders
        WHERE user_id=?
        ORDER BY id DESC LIMIT 5
        """,
        (uid,),
    )

    rows = db.cur.fetchall()

    if not rows:
        txt = "No orders yet."
    else:
        txt = "📦 Orders\n\n"
        for oid, title, total, status, created in rows:
            txt += (
                f"#{oid}\n"
                f"{title}\n"
                f"{total:.3f}{CURRENCY}\n"
                f"{status}\n\n"
            )

    if update.message:
        await update.message.reply_text(txt)
    else:
        await update.callback_query.edit_message_text(txt)


# =========================
# SUPPORT
# =========================
async def show_support(update, context):
    if update.message:
        await update.message.reply_text(
            "☎️ Support",
            reply_markup=kb_support(),
        )
    else:
        await update.callback_query.edit_message_text(
            "☎️ Support",
            reply_markup=kb_support(),
        )


# =========================
# CALLBACK
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data

    if data.startswith("cat:"):
        cid = int(data.split(":")[1])
        context.user_data[UD_CID] = cid

        return await q.edit_message_text(
            "Choose product:",
            reply_markup=kb_products(cid),
        )

    if data.startswith("view:"):
        pid = int(data.split(":")[1])

        db.cur.execute(
            "SELECT title,price,cid FROM products WHERE pid=?",
            (pid,),
        )
        title, price, cid = db.cur.fetchone()

        return await q.edit_message_text(
            f"{title}\n\nPrice: {price}",
            reply_markup=kb_product_view(pid, cid),
        )

    if data.startswith("buy:"):
        pid = int(data.split(":")[1])

        db.cur.execute(
            "SELECT title,cid FROM products WHERE pid=?",
            (pid,),
        )
        title, cid = db.cur.fetchone()

        db.cur.execute(
            "SELECT COUNT(*) FROM codes WHERE pid=? AND used=0",
            (pid,),
        )
        stock = db.cur.fetchone()[0]

        context.user_data[UD_PID] = pid
        context.user_data[UD_CID] = cid
        context.user_data[UD_QTY_MAX] = stock

        await q.edit_message_text(
            f"Buying {title}\nEnter qty 1-{stock}"
        )
        return ST_QTY


# =========================
# QTY INPUT
# =========================
async def qty_input(update, context):
    try:
        qty = int(update.message.text)
    except:
        return await update.message.reply_text("Numbers only")

    pid = context.user_data[UD_PID]

    db.cur.execute(
        "SELECT title,price FROM products WHERE pid=?",
        (pid,),
    )
    title, price = db.cur.fetchone()

    total = price * qty
    uid = update.effective_user.id

    if not db.charge_balance(uid, total):
        return await update.message.reply_text(
            "Not enough balance",
            reply_markup=kb_topup_now(),
        )

    db.cur.execute(
        """
        INSERT INTO orders(user_id,pid,product_title,qty,total,status)
        VALUES(?,?,?,?,?,'COMPLETED')
        """,
        (uid, pid, title, qty, total),
    )

    oid = db.cur.lastrowid

    db.cur.execute(
        "SELECT code_id,code_text FROM codes WHERE pid=? AND used=0 LIMIT ?",
        (pid, qty),
    )
    picked = db.cur.fetchall()

    codes = []

    for cid, code in picked:
        db.cur.execute(
            "UPDATE codes SET used=1 WHERE code_id=?",
            (cid,),
        )
        codes.append(code)

    db.con.commit()

    await send_codes_delivery(uid, context, oid, codes)

    return ConversationHandler.END


# =========================
# BUILD APP
# =========================
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_callback)],
        states={
            ST_QTY: [MessageHandler(filters.TEXT, qty_input)],
        },
        fallbacks=[CommandHandler("start", start_cmd)],
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(conv)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router)
    )

    return app


# =========================
# MAIN
# =========================
def main():
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()

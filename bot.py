import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
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
# ENV (Render)
# =========================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CURRENCY = os.getenv("CURRENCY", "$")

BYBIT_ID = os.getenv("BYBIT_ID", "YOUR_BYBIT_ID")
BINANCE_ID = os.getenv("BINANCE_ID", "YOUR_BINANCE_ID")
USDT_TRC20 = os.getenv("USDT_TRC20", "YOUR_USDT_TRC20")
USDT_BEP20 = os.getenv("USDT_BEP20", "YOUR_USDT_BEP20")

SUPPORT_CHAT = os.getenv("SUPPORT_CHAT", "@your_support")
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "@your_channel")

DB_PATH = os.getenv("DB_PATH", "shop.db")

if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")
if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID env var is missing or 0")

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def money(x: float) -> str:
    return f"{x:.3f} {CURRENCY}"

# =========================
# DB
# =========================
con = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = con.cursor()
cur.executescript(
    """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  balance REAL NOT NULL DEFAULT 0
);

-- Categories (فارغة، الأدمن يضيفها)
CREATE TABLE IF NOT EXISTS categories(
  cid INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL UNIQUE
);

-- Products (فارغة، الأدمن يضيفها)
CREATE TABLE IF NOT EXISTS products(
  pid INTEGER PRIMARY KEY AUTOINCREMENT,
  cid INTEGER NOT NULL,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  stock INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(cid) REFERENCES categories(cid)
);

CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  product_title TEXT NOT NULL,
  qty INTEGER NOT NULL,
  total REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/COMPLETED/CANCELLED
  player_id TEXT,
  delivered_text TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deposits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  method TEXT NOT NULL,   -- BYBIT/BINANCE/TRC20/BEP20
  ref TEXT NOT NULL,
  amount REAL,
  status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
)
con.commit()

# =========================
# Reply Menu (مثل الصور)
# =========================
REPLY_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🛒 Our Products"), KeyboardButton("💰 My Balance")],
        [KeyboardButton("📦 My Orders"), KeyboardButton("⚡ Manual Order")],
        [KeyboardButton("☎️ Contact Support")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# =========================
# Keyboards
# =========================
def kb_categories() -> InlineKeyboardMarkup:
    # عرض الأقسام + عدد المنتجات (مثل | 11)
    cur.execute(
        """
        SELECT c.cid, c.title, COUNT(p.pid)
        FROM categories c
        LEFT JOIN products p ON p.cid=c.cid AND p.active=1
        GROUP BY c.cid
        ORDER BY c.title
        """
    )
    rows = []
    data = cur.fetchall()
    if not data:
        rows.append([InlineKeyboardButton("⚠️ No categories yet", callback_data="noop")])
    else:
        for cid, title, cnt in data:
            rows.append([InlineKeyboardButton(f"{title} | {cnt}", callback_data=f"cat:{cid}")])
    return InlineKeyboardMarkup(rows)

def kb_products(cid: int) -> InlineKeyboardMarkup:
    cur.execute(
        "SELECT pid, title, price, stock FROM products WHERE cid=? AND active=1 ORDER BY title",
        (cid,),
    )
    rows = []
    items = cur.fetchall()
    if not items:
        rows.append([InlineKeyboardButton("⚠️ No products here", callback_data="noop")])
    else:
        for pid, title, price, stock in items:
            rows.append([InlineKeyboardButton(f"{title} | {money(price)} | {stock}", callback_data=f"prod:{pid}")])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back:cats")])
    return InlineKeyboardMarkup(rows)

def kb_qty(pid: int, qty: int, cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➖", callback_data=f"qty:-:{pid}"),
                InlineKeyboardButton(str(qty), callback_data="noop"),
                InlineKeyboardButton("➕", callback_data=f"qty:+:{pid}"),
            ],
            [InlineKeyboardButton("✅ Confirm Order", callback_data=f"confirm:{pid}")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cid}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{cid}"),
            ],
        ]
    )

def kb_balance_methods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌕 Bybit ID", callback_data="topup:BYBIT"),
                InlineKeyboardButton("🌕 Binance ID", callback_data="topup:BINANCE"),
            ],
            [
                InlineKeyboardButton("💎 USDT(TRC20)", callback_data="topup:TRC20"),
                InlineKeyboardButton("💎 USDT(BEP20)", callback_data="topup:BEP20"),
            ],
        ]
    )

def kb_orders_filters(page: int, total_pages: int) -> InlineKeyboardMarkup:
    nav_row = []
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"orders:next:{page+1}"))
    else:
        nav_row.append(InlineKeyboardButton("✅ End", callback_data="noop"))

    return InlineKeyboardMarkup(
        [
            nav_row,
            [
                InlineKeyboardButton("1 day 🎁", callback_data="orders:range:1d:0"),
                InlineKeyboardButton("1 week 🎁", callback_data="orders:range:7d:0"),
                InlineKeyboardButton("1 month 🎁", callback_data="orders:range:30d:0"),
                InlineKeyboardButton("All 🎁", callback_data="orders:range:all:0"),
            ],
        ]
    )

def kb_support() -> InlineKeyboardMarkup:
    def to_tme(x: str) -> str:
        x = (x or "").strip()
        if x.startswith("http://") or x.startswith("https://"):
            return x
        if x.startswith("@"):
            return f"https://t.me/{x[1:]}"
        return f"https://t.me/{x}"

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✉️ Contact Support", url=to_tme(SUPPORT_CHAT))],
            [InlineKeyboardButton("📣 Visit Support Channel", url=to_tme(SUPPORT_CHANNEL))],
        ]
    )

# =========================
# User helpers
# =========================
def upsert_user(u):
    cur.execute(
        """
        INSERT INTO users(user_id, username, first_name, balance)
        VALUES(?,?,?,0)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name
        """,
        (u.id, u.username or "", u.first_name or ""),
    )
    con.commit()

def get_balance(uid: int) -> float:
    cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0

def add_balance(uid: int, amount: float):
    cur.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
    con.commit()

def charge_balance(uid: int, amount: float) -> bool:
    bal = get_balance(uid)
    if bal + 1e-9 < amount:
        return False
    cur.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, uid))
    con.commit()
    return True

# =========================
# Pages (مثل الصور)
# =========================
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🛒 Here are our product categories!\nSelect a category to explore our offerings"
    if update.message:
        await update.message.reply_text(text, reply_markup=kb_categories())
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb_categories())

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = u.id
    bal = get_balance(uid)
    text = (
        "💵 Your Balance Information\n\n"
        f"Hello, {u.first_name or 'User'}! Here’s your current balance:\n\n"
        f"💎 Telegram ID: {uid}\n"
        f"💎 Current Balance: {bal:.3f} {CURRENCY}\n\n"
        "✨ What would you like to do next? You can top up your balance using one of the following methods:"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=kb_balance_methods())
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb_balance_methods())

def _orders_query(uid: int, rng: str) -> List[Tuple]:
    if rng == "all":
        cur.execute(
            "SELECT id,qty,product_title,total,status,player_id,created_at FROM orders WHERE user_id=? ORDER BY id DESC",
            (uid,),
        )
        return cur.fetchall()

    days = {"1d": 1, "7d": 7, "30d": 30}[rng]
    since = datetime.utcnow() - timedelta(days=days)
    cur.execute(
        """
        SELECT id,qty,product_title,total,status,player_id,created_at
        FROM orders
        WHERE user_id=? AND datetime(created_at) >= datetime(?)
        ORDER BY id DESC
        """,
        (uid, since.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return cur.fetchall()

def _format_orders_page(rows: List[Tuple], page: int, page_size: int = 4) -> Tuple[str, int]:
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = rows[page * page_size : (page + 1) * page_size]

    if not chunk:
        return ("📦 No orders found for this period.", 1)

    lines = []
    for oid, qty, title, total_price, status, player_id, created_at in chunk:
        lines.append(
            f"📦 Order ID: {oid} - Quantity: {qty}\n"
            f"#️⃣ Product : {title}\n"
            f"⭐ Order Status: {status}\n"
            f"💰 Total Price: {float(total_price):.3f} {CURRENCY}\n"
            f"🆔 Player ID: {player_id or '-'}\n"
            f"🕒 {created_at}\n"
        )
    footer = f"{page+1}/{total_pages}"
    return ("\n".join(lines) + f"\n{footer}", total_pages)

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, rng: str = "all", page: int = 0):
    uid = update.effective_user.id
    context.user_data["orders_rng"] = rng

    rows = _orders_query(uid, rng)
    text, total_pages = _format_orders_page(rows, page)

    if update.message:
        await update.message.reply_text(text, reply_markup=kb_orders_filters(page, total_pages))
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb_orders_filters(page, total_pages))

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "We're here to help! If you have any questions or need assistance, please choose an option below:\n\n"
        "💎 Contact Support: Reach out to our support team directly.\n"
        "💎 Visit Support Channel: Check out our support channel for FAQs and updates.\n\n"
        "✨ Feel free to ask anything!"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=kb_support())
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb_support())

# =========================
# /start + Reply buttons routing
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    await update.message.reply_text("✅ Bot is online!", reply_markup=REPLY_MENU)

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    t = (update.message.text or "").strip()

    if t == "🛒 Our Products":
        return await show_categories(update, context)
    if t == "💰 My Balance":
        return await show_balance(update, context)
    if t == "📦 My Orders":
        return await show_orders(update, context, rng=context.user_data.get("orders_rng") or "all", page=0)
    if t == "☎️ Contact Support":
        return await show_support(update, context)
    if t == "⚡ Manual Order":
        return await update.message.reply_text("⚡ Manual Order is enabled. (You can build its flow later.)", reply_markup=REPLY_MENU)

    await update.message.reply_text("Use the menu 👇", reply_markup=REPLY_MENU)

# =========================
# Shop callbacks (structure only)
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "noop":
        return

    if data == "back:cats":
        return await show_categories(update, context)

    if data.startswith("cat:"):
        cid = int(data.split(":", 1)[1])
        context.user_data["cid"] = cid
        context.user_data["qty"] = 1
        return await q.edit_message_text("Choose a product:", reply_markup=kb_products(cid))

    if data.startswith("back:prods:"):
        cid = int(data.split(":", 2)[2])
        return await q.edit_message_text("Choose a product:", reply_markup=kb_products(cid))

    if data.startswith("cancel:"):
        cid = int(data.split(":", 1)[1])
        context.user_data["qty"] = 1
        return await q.edit_message_text("✅ Cancelled.", reply_markup=kb_products(cid))

    if data.startswith("prod:"):
        pid = int(data.split(":", 1)[1])
        cur.execute("SELECT pid,title,price,cid,stock FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.")
        _, title, price, cid, stock = row
        if stock <= 0:
            return await q.edit_message_text("❌ Out of stock.", reply_markup=kb_products(cid))

        context.user_data["pid"] = pid
        context.user_data["cid"] = cid
        context.user_data["qty"] = 1

        text = (
            "🛒 Your Order\n\n"
            f"🔹 Product: {title}\n"
            f"💎 Price: {money(price)}\n"
            f"📦 In Stock: {stock}\n\n"
            "Choose quantity then Confirm."
        )
        return await q.edit_message_text(text, reply_markup=kb_qty(pid, 1, cid))

    if data.startswith("qty:"):
        _, op, pid_s = data.split(":", 2)
        pid = int(pid_s)
        cur.execute("SELECT title,price,cid,stock FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return
        title, price, cid, stock = row

        qty = int(context.user_data.get("qty", 1))
        if op == "+":
            qty = min(int(stock), qty + 1)
        else:
            qty = max(1, qty - 1)

        context.user_data["qty"] = qty
        text = (
            "🛒 Your Order\n\n"
            f"🔹 Product: {title}\n"
            f"💎 Price: {money(price)}\n"
            f"📦 In Stock: {stock}\n\n"
            "Choose quantity then Confirm."
        )
        return await q.edit_message_text(text, reply_markup=kb_qty(pid, qty, cid))

    if data.startswith("confirm:"):
        pid = int(data.split(":", 1)[1])
        qty = int(context.user_data.get("qty", 1))

        cur.execute("SELECT title,price,cid,stock FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.")
        title, price, cid, stock = row
        if stock < qty:
            return await q.edit_message_text("❌ Not enough stock.", reply_markup=kb_qty(pid, max(1, stock), cid))

        uid = update.effective_user.id
        total = float(price) * qty

        if not charge_balance(uid, total):
            bal = get_balance(uid)
            return await q.edit_message_text(
                f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total:.3f} {CURRENCY}"
            )

        # dec stock
        cur.execute("UPDATE products SET stock=stock-? WHERE pid=? AND stock>=?", (qty, pid, qty))
        if cur.rowcount == 0:
            add_balance(uid, total)
            return await q.edit_message_text("❌ Stock error. Try again.")
        con.commit()

        # create order
        cur.execute(
            "INSERT INTO orders(user_id,pid,product_title,qty,total,status) VALUES(?,?,?,?,?,?)",
            (uid, pid, title, qty, total, "PENDING"),
        )
        oid = cur.lastrowid
        con.commit()

        await q.edit_message_text(f"✅ Order created!\nOrder ID: {oid}\n{title} x{qty}\nTotal: {total:.3f} {CURRENCY}")

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🛒 NEW ORDER\nOrder ID: {oid}\nUser: {uid}\nProduct: {title}\nQty: {qty}\nTotal: {total:.3f} {CURRENCY}",
        )
        return

    # Orders pagination/filters
    if data.startswith("orders:range:"):
        _, _, rng, page = data.split(":")
        return await show_orders(update, context, rng=rng, page=int(page))
    if data.startswith("orders:next:"):
        _, _, page = data.split(":")
        rng = context.user_data.get("orders_rng") or "all"
        return await show_orders(update, context, rng=rng, page=int(page))

    # Topup selection (structure only)
    if data.startswith("topup:"):
        method = data.split(":", 1)[1]
        if method == "BYBIT":
            return await q.edit_message_text(f"🌕 Bybit ID:\n{BYBIT_ID}\n\nSend reference to admin manually.")
        if method == "BINANCE":
            return await q.edit_message_text(f"🌕 Binance ID:\n{BINANCE_ID}\n\nSend reference to admin manually.")
        if method == "TRC20":
            return await q.edit_message_text(f"💎 USDT(TRC20):\n{USDT_TRC20}\n\nSend TXID to admin manually.")
        if method == "BEP20":
            return await q.edit_message_text(f"💎 USDT(BEP20):\n{USDT_BEP20}\n\nSend TXID to admin manually.")

# =========================
# Admin: add categories/products بسهولة (بدون تعقيد)
# =========================
async def add_category_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    title = " ".join(context.args).strip()
    if not title:
        return await update.message.reply_text("Usage: /addcat <Category Title>")
    cur.execute("INSERT OR IGNORE INTO categories(title) VALUES(?)", (title,))
    con.commit()
    await update.message.reply_text("✅ Category added.")

async def add_product_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    # /addprod "Category Title" "Product Title" price stock
    txt = update.message.text
    m = re.match(r'^/addprod\s+"(.+?)"\s+"(.+?)"\s+([\d.]+)\s+(\d+)\s*$', txt)
    if not m:
        return await update.message.reply_text('Usage: /addprod "Category" "Product" price stock\nExample: /addprod "PUBG" "60 UC" 0.875 100')
    cat_title, prod_title, price_s, stock_s = m.groups()
    price = float(price_s)
    stock = int(stock_s)

    cur.execute("SELECT cid FROM categories WHERE title=?", (cat_title,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Category not found. Add it first with /addcat")
    cid = int(row[0])

    cur.execute(
        "INSERT INTO products(cid,title,price,stock,active) VALUES(?,?,?,?,1)",
        (cid, prod_title, price, stock),
    )
    con.commit()
    await update.message.reply_text("✅ Product added.")

# =========================
# RUN
# =========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("addcat", add_category_cmd))
    app.add_handler(CommandHandler("addprod", add_product_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

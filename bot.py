import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

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
    ConversationHandler,
    filters,
)

# =========================
# ENV
# =========================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # لازم
CURRENCY = os.getenv("CURRENCY", "$")

# طرق الشحن/الدعم (اختيارية لكن مطلوبة لنفس الشكل)
BYBIT_ID = os.getenv("BYBIT_ID", "YOUR_BYBIT_ID")
BINANCE_ID = os.getenv("BINANCE_ID", "YOUR_BINANCE_ID")
USDT_TRC20 = os.getenv("USDT_TRC20", "YOUR_USDT_TRC20_ADDRESS")
USDT_BEP20 = os.getenv("USDT_BEP20", "YOUR_USDT_BEP20_ADDRESS")

SUPPORT_CHAT = os.getenv("SUPPORT_CHAT", "@your_support")       # زر تواصل (يوزر/لينك)
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "@your_channel") # زر قناة دعم

DB_PATH = os.getenv("DB_PATH", "shop.db")

if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")
if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID env var is missing or 0")

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

CREATE TABLE IF NOT EXISTS stock(
  pid TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  category TEXT NOT NULL,
  stock INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  pid TEXT NOT NULL,
  title TEXT NOT NULL,
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
  method TEXT NOT NULL,  -- BYBIT/BINANCE/TRC20/BEP20
  ref TEXT NOT NULL,     -- txid or note
  amount REAL,           -- set on approval
  status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
)
con.commit()

# =========================
# Catalog like your earlier one
# =========================
CATALOG = {
    "🍏 ITUNES GIFTCARD (USA)": [
        ("it_5", "5$ iTunes US", 4.600, 217),
        ("it_10", "10$ iTunes US", 9.200, 124),
        ("it_20", "20$ iTunes US", 18.400, 21),
        ("it_25", "25$ iTunes US", 23.000, 13),
        ("it_50", "50$ iTunes US", 46.000, 9),
        ("it_100", "100$ iTunes US", 91.000, 31),
        ("it_200", "200$ iTunes US", 180.000, 0),
    ],
    "🪂 PUBG MOBILE UC VOUCHERS": [
        ("pubg_60", "60 UC", 0.875, 4690),
        ("pubg_325", "325 UC", 4.375, 0),
        ("pubg_660", "660 UC", 8.750, 0),
        ("pubg_1800", "1800 UC", 22.000, 0),
        ("pubg_3850", "3850 UC", 44.000, 0),
        ("pubg_8100", "8100 UC", 88.000, 0),
    ],
    "💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)": [
        ("ff_1", "1 USD - 100+10", 0.920, 196),
        ("ff_2", "2 USD - 210+21", 1.840, 0),
        ("ff_5", "5 USD - 530+53", 4.600, 0),
        ("ff_10", "10 USD - 1080+108", 9.200, 0),
        ("ff_20", "20 USD - 2200+220", 18.400, 0),
    ],
    "🎲 YALLA LUDO": [
        ("ludo_3_7", "3.7K Hearts + 10 RP", 9.000, 13),
        ("ludo_7_5", "7.5K Hearts + 20 RP", 18.000, 10),
        ("ludo_24", "24K Hearts + 60 RP", 54.000, 2),
        ("ludo_41", "41K Hearts + 100 RP", 90.000, 1),
    ],
    "🎮 PLAYSTATION USA GIFTCARDS": [
        ("ps_10", "10$ PSN USA", 8.900, 0),
        ("ps_25", "25$ PSN USA", 22.000, 10),
        ("ps_50", "50$ PSN USA", 44.000, 0),
        ("ps_100", "100$ PSN USA", 88.000, 5),
    ],
}

def seed_stock_once():
    # If stock empty, seed from CATALOG
    cur.execute("SELECT COUNT(*) FROM stock")
    n = cur.fetchone()[0]
    if n and n > 0:
        return
    for cat, items in CATALOG.items():
        for pid, title, price, st in items:
            cur.execute(
                "INSERT OR REPLACE INTO stock(pid,title,price,category,stock) VALUES(?,?,?,?,?)",
                (pid, title, float(price), cat, int(st)),
            )
    con.commit()

seed_stock_once()

# =========================
# Helpers
# =========================
def money(x: float) -> str:
    return f"{x:.3f} {CURRENCY}"

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

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

def get_product(pid: str) -> Optional[Tuple]:
    cur.execute("SELECT pid,title,price,category,stock FROM stock WHERE pid=?", (pid,))
    return cur.fetchone()

def dec_stock(pid: str, qty: int) -> bool:
    cur.execute("SELECT stock FROM stock WHERE pid=?", (pid,))
    row = cur.fetchone()
    if not row:
        return False
    st = int(row[0])
    if st < qty:
        return False
    cur.execute("UPDATE stock SET stock=stock-? WHERE pid=?", (qty, pid))
    con.commit()
    return True

# =========================
# UI (Reply menu like images)
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

def kb_categories() -> InlineKeyboardMarkup:
    rows = []
    for cat in CATALOG.keys():
        # count products in category
        count = len(CATALOG[cat])
        rows.append([InlineKeyboardButton(f"{cat} | {count}", callback_data=f"cat:{cat}")])
    return InlineKeyboardMarkup(rows)

def kb_products(cat: str) -> InlineKeyboardMarkup:
    rows = []
    cur.execute("SELECT pid,title,price,stock FROM stock WHERE category=? ORDER BY title", (cat,))
    for pid, title, price, st in cur.fetchall():
        rows.append([InlineKeyboardButton(f"{title} | {money(price)} | {st}", callback_data=f"prod:{pid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="home:cats")])
    return InlineKeyboardMarkup(rows)

def kb_qty(pid: str, qty: int, cat: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➖", callback_data=f"qty:-:{pid}"),
                InlineKeyboardButton(str(qty), callback_data="noop"),
                InlineKeyboardButton("➕", callback_data=f"qty:+:{pid}"),
            ],
            [InlineKeyboardButton("✅ Confirm Order", callback_data=f"confirm:{pid}")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cat}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{cat}"),
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
    rows = []
    # URL buttons require valid link or @username with https.
    # If user puts @username, we convert to https://t.me/username
    def to_tme(x: str) -> str:
        x = (x or "").strip()
        if not x:
            return "https://t.me/"
        if x.startswith("http://") or x.startswith("https://"):
            return x
        if x.startswith("@"):
            return f"https://t.me/{x[1:]}"
        return f"https://t.me/{x}"

    rows.append([InlineKeyboardButton("✉️ Contact Support", url=to_tme(SUPPORT_CHAT))])
    rows.append([InlineKeyboardButton("📣 Visit Support Channel", url=to_tme(SUPPORT_CHANNEL))])
    return InlineKeyboardMarkup(rows)

# =========================
# States
# =========================
ST_MANUAL_PLAYER, ST_MANUAL_DETAILS = range(2)
ST_TOPUP_REF = 10

# user_data keys
UD_CAT = "cat"
UD_PID = "pid"
UD_QTY = "qty"
UD_TOPUP_METHOD = "topup_method"
UD_ORDERS_RANGE = "orders_range"  # "1d" / "7d" / "30d" / "all"

# =========================
# Pages
# =========================
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "✅ Welcome!\nChoose an option from the menu below 👇"
    if update.message:
        await update.message.reply_text(text, reply_markup=REPLY_MENU)
    else:
        await update.callback_query.edit_message_text(text)

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
    # returns rows newest first
    if rng == "all":
        cur.execute(
            "SELECT id,qty,title,total,status,player_id,created_at FROM orders WHERE user_id=? ORDER BY id DESC",
            (uid,),
        )
        return cur.fetchall()

    days = {"1d": 1, "7d": 7, "30d": 30}[rng]
    since = datetime.utcnow() - timedelta(days=days)
    cur.execute(
        """
        SELECT id,qty,title,total,status,player_id,created_at
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
    start = page * page_size
    chunk = rows[start : start + page_size]

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

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, rng: Optional[str] = None, page: int = 0):
    uid = update.effective_user.id
    rng = rng or context.user_data.get(UD_ORDERS_RANGE) or "all"
    context.user_data[UD_ORDERS_RANGE] = rng

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
# Start + Menu Router
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
        return await show_orders(update, context, rng=context.user_data.get(UD_ORDERS_RANGE) or "all", page=0)
    if t == "☎️ Contact Support":
        return await show_support(update, context)
    if t == "⚡ Manual Order":
        # start conversation for manual order
        await update.message.reply_text("🆔 Please enter Player ID (or type - if not needed):")
        return ST_MANUAL_PLAYER

    # default
    await update.message.reply_text("Use the menu 👇", reply_markup=REPLY_MENU)
    return ConversationHandler.END

# =========================
# Manual Order flow
# =========================
async def manual_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = (update.message.text or "").strip()
    context.user_data["manual_player"] = pid
    await update.message.reply_text(
        "✍️ Now send your manual order details:\n"
        "- Product name\n"
        "- Quantity\n"
        "- Any notes\n\n"
        "Example:\nPUBG 8100 UC\nQty: 1"
    )
    return ST_MANUAL_DETAILS

async def manual_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    details = (update.message.text or "").strip()
    player = context.user_data.get("manual_player", "-")
    uid = update.effective_user.id

    # Create a 'manual order' record as PENDING with title "MANUAL ORDER"
    cur.execute(
        "INSERT INTO orders(user_id,pid,title,qty,total,status,player_id) VALUES(?,?,?,?,?,?,?)",
        (uid, "manual", "MANUAL ORDER", 1, 0.0, "PENDING", player),
    )
    oid = cur.lastrowid
    con.commit()

    await update.message.reply_text("✅ Manual order sent to admin. We will contact you soon.", reply_markup=REPLY_MENU)

    # Notify admin
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "⚡ MANUAL ORDER\n"
            f"Order ID: {oid}\n"
            f"User: {uid}\n"
            f"Player ID: {player}\n\n"
            f"Details:\n{details}\n\n"
            f"To complete:\n/deliver {oid} <your message/codes>"
        ),
    )
    return ConversationHandler.END

# =========================
# Shop callbacks (categories/products/qty/confirm)
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "noop":
        return

    if data == "home:cats":
        return await show_categories(update, context)

    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        context.user_data[UD_CAT] = cat
        context.user_data[UD_PID] = None
        context.user_data[UD_QTY] = 1
        return await q.edit_message_text(f"📦 {cat}\nChoose a product:", reply_markup=kb_products(cat))

    if data.startswith("back:prods:"):
        cat = data.split(":", 2)[2]
        return await q.edit_message_text(f"📦 {cat}\nChoose a product:", reply_markup=kb_products(cat))

    if data.startswith("cancel:"):
        cat = data.split(":", 1)[1]
        context.user_data[UD_PID] = None
        context.user_data[UD_QTY] = 1
        return await q.edit_message_text("✅ Order cancelled.", reply_markup=kb_products(cat))

    if data.startswith("prod:"):
        pid = data.split(":", 1)[1]
        prod = get_product(pid)
        if not prod:
            return await q.edit_message_text("❌ Product not found.")
        _, title, price, cat, st = prod
        if st <= 0:
            return await q.edit_message_text("❌ Out of stock. Choose another one.", reply_markup=kb_products(cat))

        context.user_data[UD_PID] = pid
        context.user_data[UD_CAT] = cat
        context.user_data[UD_QTY] = 1

        text = (
            "🛒 Your Order\n\n"
            f"📦 Category: {cat}\n"
            f"🔹 Product: {title}\n"
            f"💎 Price: {money(price)}\n"
            f"📦 In Stock: {st}\n\n"
            "Choose quantity then Confirm."
        )
        return await q.edit_message_text(text, reply_markup=kb_qty(pid, 1, cat))

    if data.startswith("qty:"):
        _, op, pid = data.split(":", 2)
        prod = get_product(pid)
        if not prod:
            return
        _, title, price, cat, st = prod

        qty = int(context.user_data.get(UD_QTY, 1))
        if op == "+":
            qty = min(int(st), qty + 1)
        else:
            qty = max(1, qty - 1)

        context.user_data[UD_QTY] = qty

        text = (
            "🛒 Your Order\n\n"
            f"📦 Category: {cat}\n"
            f"🔹 Product: {title}\n"
            f"💎 Price: {money(price)}\n"
            f"📦 In Stock: {st}\n\n"
            "Choose quantity then Confirm."
        )
        return await q.edit_message_text(text, reply_markup=kb_qty(pid, qty, cat))

    if data.startswith("confirm:"):
        pid = data.split(":", 1)[1]
        prod = get_product(pid)
        if not prod:
            return await q.edit_message_text("❌ Product not found.")
        _, title, price, cat, st = prod

        qty = int(context.user_data.get(UD_QTY, 1))
        if qty < 1:
            qty = 1
        if st < qty:
            return await q.edit_message_text("❌ Not enough stock.", reply_markup=kb_qty(pid, min(max(st, 1), 1), cat))

        uid = update.effective_user.id
        total = float(price) * qty

        # charge balance
        if not charge_balance(uid, total):
            bal = get_balance(uid)
            return await q.edit_message_text(
                f"❌ Insufficient balance.\n\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total:.3f} {CURRENCY}\n\nGo to My Balance to top up."
            )

        # decrement stock
        if not dec_stock(pid, qty):
            # rollback balance if stock failed
            add_balance(uid, total)
            return await q.edit_message_text("❌ Stock error. Try again.")

        # create order
        cur.execute(
            "INSERT INTO orders(user_id,pid,title,qty,total,status) VALUES(?,?,?,?,?,?)",
            (uid, pid, title, qty, total, "PENDING"),
        )
        oid = cur.lastrowid
        con.commit()

        await q.edit_message_text(
            "✅ Order created!\n\n"
            f"📦 Order ID: {oid}\n"
            f"Product: {title}\n"
            f"Qty: {qty}\n"
            f"Total: {total:.3f} {CURRENCY}\n\n"
            "⏳ Waiting for delivery."
        )

        # notify admin
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "🛒 NEW ORDER\n"
                f"Order ID: {oid}\n"
                f"User: {uid}\n"
                f"Product: {title}\n"
                f"Qty: {qty}\n"
                f"Total: {total:.3f} {CURRENCY}\n\n"
                f"Deliver:\n/deliver {oid} <codes/message>"
            ),
        )
        return

    # Topup flow: choose method then user sends ref/txid
    if data.startswith("topup:"):
        method = data.split(":", 1)[1]
        context.user_data[UD_TOPUP_METHOD] = method

        if method == "BYBIT":
            msg = f"🌕 Bybit ID top up\n\nSend transfer to Bybit ID:\n{BYBIT_ID}\n\nThen send your Bybit transfer reference here."
        elif method == "BINANCE":
            msg = f"🌕 Binance ID top up\n\nSend transfer to Binance Pay ID:\n{BINANCE_ID}\n\nThen send your Binance reference here."
        elif method == "TRC20":
            msg = f"💎 USDT (TRC20)\n\nSend USDT to:\n{USDT_TRC20}\n\nThen send TXID here."
        else:
            msg = f"💎 USDT (BEP20)\n\nSend USDT to:\n{USDT_BEP20}\n\nThen send TXID here."

        await q.edit_message_text(msg)
        return ST_TOPUP_REF

    # Orders callbacks
    if data.startswith("orders:range:"):
        _, _, rng, page = data.split(":")
        return await show_orders(update, context, rng=rng, page=int(page))

    if data.startswith("orders:next:"):
        _, _, page = data.split(":")
        rng = context.user_data.get(UD_ORDERS_RANGE) or "all"
        return await show_orders(update, context, rng=rng, page=int(page))

# =========================
# Topup TX/REF handler
# =========================
async def topup_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ref = (update.message.text or "").strip()
    method = context.user_data.get(UD_TOPUP_METHOD)
    if not method:
        await update.message.reply_text("❌ Choose a top up method from My Balance.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    uid = update.effective_user.id
    upsert_user(update.effective_user)

    cur.execute(
        "INSERT INTO deposits(user_id,method,ref,status) VALUES(?,?,?,?)",
        (uid, method, ref[:1500], "PENDING"),
    )
    dep_id = cur.lastrowid
    con.commit()

    await update.message.reply_text(
        f"✅ Top up request received.\nDeposit ID: {dep_id}\nStatus: PENDING\n\nWe will review it soon.",
        reply_markup=REPLY_MENU,
    )

    # Notify admin with inline approve/reject
    approve_btn = InlineKeyboardButton("✅ Approve", callback_data=f"admin:dep:approve:{dep_id}")
    reject_btn = InlineKeyboardButton("❌ Reject", callback_data=f"admin:dep:reject:{dep_id}")
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "💰 NEW TOPUP\n"
            f"Deposit ID: {dep_id}\n"
            f"User: {uid}\n"
            f"Method: {method}\n"
            f"Ref:\n{ref}\n\n"
            "Approve with amount:\n"
            f"/approve {dep_id} 10.0"
        ),
        reply_markup=InlineKeyboardMarkup([[approve_btn, reject_btn]]),
    )

    return ConversationHandler.END

# =========================
# Admin commands
# =========================
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /approve <deposit_id> <amount>")

    try:
        dep_id = int(context.args[0])
        amount = float(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ Invalid values.")

    cur.execute("SELECT user_id,status FROM deposits WHERE id=?", (dep_id,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Deposit not found.")
    user_id, status = int(row[0]), row[1]
    if status != "PENDING":
        return await update.message.reply_text("❌ Deposit already processed.")

    cur.execute("UPDATE deposits SET status='APPROVED', amount=? WHERE id=?", (amount, dep_id))
    con.commit()
    add_balance(user_id, amount)

    await update.message.reply_text(f"✅ Deposit #{dep_id} approved. Added {amount:.3f} {CURRENCY} to user {user_id}.")
    await context.bot.send_message(chat_id=user_id, text=f"✅ Your top up has been approved: +{amount:.3f} {CURRENCY}")

async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    if len(context.args) < 1:
        return await update.message.reply_text("Usage: /reject <deposit_id>")

    try:
        dep_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ Invalid deposit id.")

    cur.execute("SELECT user_id,status FROM deposits WHERE id=?", (dep_id,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Deposit not found.")
    user_id, status = int(row[0]), row[1]
    if status != "PENDING":
        return await update.message.reply_text("❌ Deposit already processed.")

    cur.execute("UPDATE deposits SET status='REJECTED' WHERE id=?", (dep_id,))
    con.commit()

    await update.message.reply_text(f"✅ Deposit #{dep_id} rejected.")
    await context.bot.send_message(chat_id=user_id, text=f"❌ Your top up #{dep_id} was rejected. Contact support if needed.")

async def deliver_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /deliver <order_id> <codes/message>")

    try:
        oid = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ Invalid order id.")

    delivered = " ".join(context.args[1:]).strip()

    cur.execute("SELECT user_id,status FROM orders WHERE id=?", (oid,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Order not found.")
    user_id, status = int(row[0]), row[1]
    if status == "COMPLETED":
        return await update.message.reply_text("❌ Order already completed.")

    cur.execute("UPDATE orders SET status='COMPLETED', delivered_text=? WHERE id=?", (delivered, oid))
    con.commit()

    await update.message.reply_text(f"✅ Order #{oid} delivered.")
    await context.bot.send_message(chat_id=user_id, text=f"✅ Order #{oid} COMPLETED:\n\n{delivered}")

# Inline admin quick reject/approve (without amount) -> we keep it as helper to reject quickly.
async def admin_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    # admin:dep:approve:ID or admin:dep:reject:ID
    if not is_admin(update.effective_user.id):
        return await q.edit_message_text("❌ Not allowed.")
    parts = data.split(":")
    if len(parts) != 4:
        return
    _, typ, action, dep_id_s = parts
    if typ != "dep":
        return
    dep_id = int(dep_id_s)

    if action == "reject":
        cur.execute("SELECT user_id,status FROM deposits WHERE id=?", (dep_id,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Deposit not found.")
        user_id, status = int(row[0]), row[1]
        if status != "PENDING":
            return await q.edit_message_text("❌ Already processed.")
        cur.execute("UPDATE deposits SET status='REJECTED' WHERE id=?", (dep_id,))
        con.commit()
        await context.bot.send_message(chat_id=user_id, text=f"❌ Your top up #{dep_id} was rejected.")
        return await q.edit_message_text(f"✅ Deposit #{dep_id} rejected.")

    # approve inline requires amount => we instruct to use /approve
    if action == "approve":
        return await q.edit_message_text(f"Use: /approve {dep_id} <amount>")

# =========================
# Build app
# =========================
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    # Conversations:
    manual_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^⚡ Manual Order$"), menu_router)],
        states={
            ST_MANUAL_PLAYER: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_player)],
            ST_MANUAL_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_details)],
        },
        fallbacks=[CommandHandler("start", start_cmd)],
        allow_reentry=True,
    )

    topup_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_callback, pattern=r"^topup:")],
        states={
            ST_TOPUP_REF: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_ref)],
        },
        fallbacks=[CommandHandler("start", start_cmd)],
        allow_reentry=True,
    )

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("reject", reject_cmd))
    app.add_handler(CommandHandler("deliver", deliver_cmd))

    # Admin inline callbacks
    app.add_handler(CallbackQueryHandler(admin_inline, pattern=r"^admin:"))

    # Conversations must be added before general callback/text routing
    app.add_handler(manual_conv)
    app.add_handler(topup_conv)

    # Callbacks for shop/orders/balance
    app.add_handler(CallbackQueryHandler(on_callback))

    # Text menu routing
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    return app

def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

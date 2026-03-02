# bot.py (SINGLE FILE - READY)
import os
import re
import io
import sqlite3
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
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
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("shopbot")

# =========================
# ENV
# =========================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "shop.db")

CURRENCY = os.getenv("CURRENCY", "$")

BINANCE_UID = os.getenv("BINANCE_ID", "YOUR_BINANCE_UID")
BYBIT_UID = os.getenv("BYBIT_UID", "YOUR_BYBIT_UID")
USDT_TRC20 = os.getenv("USDT_TRC20", "YOUR_USDT_TRC20_ADDRESS")
USDT_BEP20 = os.getenv("USDT_BEP20", "YOUR_USDT_BEP20_ADDRESS")

SUPPORT_CHAT = os.getenv("SUPPORT_CHAT", "@your_support")
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "@yourchannel")

if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")
if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID env var is missing or 0")

_db_dir = os.path.dirname(DB_PATH) if DB_PATH else ""
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

# =========================
# ROLES
# =========================
ROLE_OWNER = "OWNER"
ROLE_HELPER = "HELPER"

def to_tme(x: str) -> str:
    x = (x or "").strip()
    if not x:
        return "https://t.me/"
    if x.startswith("http://") or x.startswith("https://"):
        return x
    if x.startswith("@"):
        return f"https://t.me/{x[1:]}"
    return f"https://t.me/{x}"

def money(x: float) -> str:
    return f"{x:.3f} {CURRENCY}"

# =========================
# MANUAL HOURS (KSA)
# =========================
KSA_UTC_OFFSET_HOURS = 3
MANUAL_START_HOUR_KSA = 10
MANUAL_END_HOUR_KSA = 24

def now_ksa():
    return datetime.utcnow() + timedelta(hours=KSA_UTC_OFFSET_HOURS)

def manual_open_now() -> bool:
    t = now_ksa()
    return MANUAL_START_HOUR_KSA <= t.hour < MANUAL_END_HOUR_KSA

def manual_hours_text() -> str:
    gmt_start = (MANUAL_START_HOUR_KSA - KSA_UTC_OFFSET_HOURS) % 24
    gmt_end = (MANUAL_END_HOUR_KSA - KSA_UTC_OFFSET_HOURS) % 24
    return (
        "🕘 *Manual Working Hours*\n"
        f"🇸🇦 KSA: {MANUAL_START_HOUR_KSA:02d}:00 → 24:00\n"
        f"🌍 GMT: {gmt_start:02d}:00 → {gmt_end:02d}:00"
    )

# =========================
# SORT SMALL->BIG
# =========================
def extract_sort_value(title: str) -> float:
    t = (title or "").replace(",", ".")
    nums = re.findall(r"\d+(?:\.\d+)?", t)
    if not nums:
        return 1e18
    try:
        return float(nums[0])
    except Exception:
        return 1e18

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
  balance REAL NOT NULL DEFAULT 0,
  suspended INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admins(
  user_id INTEGER PRIMARY KEY,
  role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories(
  cid INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS products(
  pid INTEGER PRIMARY KEY AUTOINCREMENT,
  cid INTEGER NOT NULL,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  product_type TEXT NOT NULL DEFAULT 'CODE',
  active INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(cid) REFERENCES categories(cid)
);

CREATE TABLE IF NOT EXISTS codes(
  code_id INTEGER PRIMARY KEY AUTOINCREMENT,
  pid INTEGER NOT NULL,
  code_text TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  used_at TEXT,
  order_id INTEGER,
  FOREIGN KEY(pid) REFERENCES products(pid)
);

CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  product_title TEXT NOT NULL,
  qty INTEGER NOT NULL,
  total REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  delivered_text TEXT,
  client_ref TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deposits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  method TEXT NOT NULL,
  note TEXT NOT NULL,
  txid TEXT,
  amount REAL,
  status TEXT NOT NULL DEFAULT 'WAITING_PAYMENT',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manual_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  service TEXT NOT NULL,
  plan_title TEXT NOT NULL,
  price REAL NOT NULL,
  email TEXT,
  password TEXT,
  player_id TEXT,
  note TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING',
  delivered_text TEXT,
  approved_by INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manual_prices(
  pkey TEXT PRIMARY KEY,
  price REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_codes_unique ON codes(pid, code_text);
CREATE INDEX IF NOT EXISTS idx_codes_pid_used ON codes(pid, used);
CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_ref_unique ON orders(client_ref);
CREATE INDEX IF NOT EXISTS idx_deposits_user_status ON deposits(user_id, status);
CREATE INDEX IF NOT EXISTS idx_manual_user_status ON manual_orders(user_id, status);
"""
)
con.commit()

# seed owner admin
cur.execute("INSERT OR REPLACE INTO admins(user_id, role) VALUES(?,?)", (ADMIN_ID, ROLE_OWNER))
con.commit()

def admin_role(uid: int) -> Optional[str]:
    cur.execute("SELECT role FROM admins WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r[0] if r else None

def is_admin_any(uid: int) -> bool:
    return admin_role(uid) in (ROLE_OWNER, ROLE_HELPER)

def is_owner(uid: int) -> bool:
    return admin_role(uid) == ROLE_OWNER

def ensure_user_exists(user_id: int, username: str = "", first_name: str = ""):
    cur.execute(
        """
        INSERT INTO users(user_id, username, first_name, balance, suspended)
        VALUES(?,?,?,0,0)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id, username, first_name),
    )
    con.commit()

def upsert_user(u):
    cur.execute(
        """
        INSERT INTO users(user_id, username, first_name, balance, suspended)
        VALUES(?,?,?,0,0)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name
        """,
        (u.id, u.username or "", u.first_name or ""),
    )
    con.commit()

def is_suspended(uid: int) -> bool:
    ensure_user_exists(uid)
    cur.execute("SELECT suspended FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return bool(int(row[0] or 0)) if row else False

def set_suspended(uid: int, val: bool):
    ensure_user_exists(uid)
    cur.execute("UPDATE users SET suspended=? WHERE user_id=?", (1 if val else 0, uid))
    con.commit()

def get_balance(uid: int) -> float:
    ensure_user_exists(uid)
    cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return float(row[0] or 0.0) if row else 0.0

def add_balance(uid: int, amount: float):
    ensure_user_exists(uid)
    cur.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
    con.commit()

def charge_balance(uid: int, amount: float) -> bool:
    bal = get_balance(uid)
    if bal + 1e-9 < amount:
        return False
    cur.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, uid))
    con.commit()
    return True

def must_block_user(update: Update) -> bool:
    uid = update.effective_user.id
    if is_admin_any(uid):
        return False
    return is_suspended(uid)

# =========================
# DEFAULT DATA
# =========================
DEFAULT_CATEGORIES = [
    "🍎 ITUNES GIFTCARD (USA)",
    "🪂 PUBG MOBILE UC VOUCHERS",
    "💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)",
    "🎮 PLAYSTATION USA GIFTCARDS",
]
DEFAULT_PRODUCTS = [
    ("🪂 PUBG MOBILE UC VOUCHERS", "60 UC", 0.875),
    ("🪂 PUBG MOBILE UC VOUCHERS", "325 UC", 4.375),
    ("🪂 PUBG MOBILE UC VOUCHERS", "660 UC", 8.750),
    ("🪂 PUBG MOBILE UC VOUCHERS", "1800 UC", 22.000),
    ("🪂 PUBG MOBILE UC VOUCHERS", "3850 UC", 44.000),
    ("🪂 PUBG MOBILE UC VOUCHERS", "8100 UC", 88.000),
]

def seed_defaults():
    for cat in DEFAULT_CATEGORIES:
        cur.execute("INSERT OR IGNORE INTO categories(title) VALUES(?)", (cat,))
    con.commit()
    for cat, title, price in DEFAULT_PRODUCTS:
        cur.execute("SELECT cid FROM categories WHERE title=?", (cat,))
        row = cur.fetchone()
        if not row:
            continue
        cid = int(row[0])
        cur.execute("SELECT pid FROM products WHERE cid=? AND title=?", (cid, title))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO products(cid,title,price,product_type,active) VALUES(?,?,?,'CODE',1)",
            (cid, title, float(price)),
        )
    con.commit()

seed_defaults()

# =========================
# REPLY MENU
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

MENU_BUTTONS = {
    "🛒 Our Products", "💰 My Balance", "📦 My Orders",
    "⚡ Manual Order", "☎️ Contact Support",
}

# =========================
# STATES
# =========================
ST_QTY = 10
ST_TOPUP_DETAILS = 20
ST_ADMIN_INPUT = 99

UD_PID = "pid"
UD_CID = "cid"
UD_QTY_MAX = "qty_max"
UD_DEP_ID = "dep_id"
UD_LAST_QTY = "last_qty"
UD_LAST_PID = "last_pid"
UD_ORDER_CLIENT_REF = "order_client_ref"
UD_ADMIN_MODE = "admin_mode"

# =========================
# STOCK / KEYBOARDS
# =========================
def product_stock(pid: int) -> int:
    cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
    return int(cur.fetchone()[0] or 0)

def kb_categories(is_admin_user: bool) -> InlineKeyboardMarkup:
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
    for cid, title, cnt in cur.fetchall():
        rows.append([InlineKeyboardButton(f"{title} | {cnt}", callback_data=f"cat:{cid}")])
    if is_admin_user:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)

def kb_products(cid: int) -> InlineKeyboardMarkup:
    cur.execute("SELECT pid,title,price FROM products WHERE cid=? AND active=1", (cid,))
    items = cur.fetchall()
    items.sort(key=lambda r: extract_sort_value(r[1]))
    rows = []
    for pid, title, price in items:
        stock = product_stock(pid)
        label = f"{title} | {money(float(price))} | 📦{stock}"
        rows.append([InlineKeyboardButton(label[:62], callback_data=f"view:{pid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back:cats")])
    return InlineKeyboardMarkup(rows)

def kb_product_view(pid: int, cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 Buy Now", callback_data=f"buy:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cid}")],
        ]
    )

def kb_qty_cancel(cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")],
        ]
    )

def kb_balance_methods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌕 Bybit UID", callback_data="pay:BYBIT"),
                InlineKeyboardButton("🌕 Binance UID", callback_data="pay:BINANCE"),
            ],
            [
                InlineKeyboardButton("💎 USDT(TRC20)", callback_data="pay:TRC20"),
                InlineKeyboardButton("💎 USDT(BEP20)", callback_data="pay:BEP20"),
            ],
        ]
    )

def kb_have_paid(dep_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid:{dep_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="goto:balance")],
        ]
    )

def kb_topup_now() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 Top Up Now", callback_data="goto:topup")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back:cats")],
        ]
    )

def kb_support() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💬 Support Chat", url=to_tme(SUPPORT_CHAT))],
            [InlineKeyboardButton("📣 Support Channel", url=to_tme(SUPPORT_CHANNEL))],
        ]
    )

def kb_admin_panel(uid: int) -> InlineKeyboardMarkup:
    # owner فقط
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Products (PID)", callback_data="admin:listprod")],
            [InlineKeyboardButton("➕ Add Category", callback_data="admin:addcat"),
             InlineKeyboardButton("➕ Add Product", callback_data="admin:addprod")],
            [InlineKeyboardButton("➕ Add Codes", callback_data="admin:addcodes"),
             InlineKeyboardButton("💲 Set Price", callback_data="admin:setprice")],
            [InlineKeyboardButton("⛔ Toggle Product", callback_data="admin:toggle"),
             InlineKeyboardButton("➕ Add Balance", callback_data="admin:addbal")],
            [InlineKeyboardButton("➖ Take Balance", callback_data="admin:takebal"),
             InlineKeyboardButton("👑 Admins", callback_data="admin:admins")],
        ]
    )

# =========================
# DELIVERY
# =========================
MAX_CODES_IN_MESSAGE = 200
TELEGRAM_TEXT_LIMIT = 3800

async def send_codes_delivery(chat_id: int, context: ContextTypes.DEFAULT_TYPE, order_id: int, codes: List[str]):
    codes = [c.strip() for c in codes if c and c.strip()]
    count = len(codes)
    header = f"🎁 *Delivery Successful!*\n✅ Order *#{order_id}* COMPLETED\n📦 Codes: *{count}*\n\n"
    if count == 0:
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Order #{order_id} COMPLETED\n(No codes)")
        return

    if count > MAX_CODES_IN_MESSAGE:
        content = "\n".join(codes)
        bio = io.BytesIO(content.encode("utf-8"))
        bio.name = f"order_{order_id}_codes.txt"
        await context.bot.send_message(chat_id=chat_id, text=header + "📎 *Your codes are attached in a file:*", parse_mode=ParseMode.MARKDOWN)
        await context.bot.send_document(chat_id=chat_id, document=bio)
        return

    body = "\n".join(codes)
    text = header + f"`{body}`"
    if len(text) <= TELEGRAM_TEXT_LIMIT:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
        return

    await context.bot.send_message(chat_id=chat_id, text=header + "🎁 Codes (part 1):", parse_mode=ParseMode.MARKDOWN)
    chunk = ""
    for c in codes:
        line = c + "\n"
        if len(chunk) + len(line) > TELEGRAM_TEXT_LIMIT:
            await context.bot.send_message(chat_id=chat_id, text=f"`{chunk.rstrip()}`", parse_mode=ParseMode.MARKDOWN)
            chunk = line
        else:
            chunk += line
    if chunk.strip():
        await context.bot.send_message(chat_id=chat_id, text=f"`{chunk.rstrip()}`", parse_mode=ParseMode.MARKDOWN)

# =========================
# COMMANDS / PAGES
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    ensure_user_exists(ADMIN_ID)
    await update.message.reply_text("✅ Bot is online! 🚀", reply_markup=REPLY_MENU)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and must_block_user(update):
        if update.message:
            return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())
        return await update.callback_query.edit_message_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    text = "🛒 *Our Categories*\nاختر قسم 👇"
    kb = kb_categories(is_admin_any(update.effective_user.id))
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and must_block_user(update):
        if update.message:
            return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())
        return await update.callback_query.edit_message_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    u = update.effective_user
    uid = u.id
    bal = get_balance(uid)
    text = (
        "💰 *Wallet*\n\n"
        f"👤 Name: *{(u.first_name or 'User')}*\n"
        f"🆔 ID: `{uid}`\n"
        f"💎 Balance: *{bal:.3f}* {CURRENCY}\n\n"
        "✨ اختر طريقة الشحن:"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_balance_methods())
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_balance_methods())

def _orders_query(uid: int, rng: str) -> List[Tuple]:
    if rng == "all":
        cur.execute("SELECT id,qty,product_title,total,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC", (uid,))
        return cur.fetchall()
    days = {"1d": 1, "7d": 7, "30d": 30}[rng]
    since = datetime.utcnow() - timedelta(days=days)
    cur.execute(
        """
        SELECT id,qty,product_title,total,status,created_at
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
    chunk = rows[page * page_size: (page + 1) * page_size]
    if not chunk:
        return ("📦 No orders found for this period.", 1)

    lines = ["📦 *My Orders*\n"]
    for oid, qty, title, total_price, status, created_at in chunk:
        lines.append(
            f"🧾 *Order #{oid}*\n"
            f"🎮 Product: {title}\n"
            f"🔢 Qty: *{qty}*\n"
            f"💵 Total: *{float(total_price):.3f}* {CURRENCY}\n"
            f"⭐ Status: *{status}*\n"
            f"🕒 {created_at}\n"
        )
    footer = f"Page {page + 1}/{total_pages}"
    return ("\n".join(lines) + f"\n_{footer}_", total_pages)

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
                InlineKeyboardButton("1 day", callback_data="orders:range:1d:0"),
                InlineKeyboardButton("1 week", callback_data="orders:range:7d:0"),
                InlineKeyboardButton("1 month", callback_data="orders:range:30d:0"),
                InlineKeyboardButton("All", callback_data="orders:range:all:0"),
            ],
        ]
    )

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, rng: str = "all", page: int = 0):
    if update.effective_user and must_block_user(update):
        if update.message:
            return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())
        return await update.callback_query.edit_message_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    uid = update.effective_user.id
    rows = _orders_query(uid, rng)
    text, total_pages = _format_orders_page(rows, page)
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_orders_filters(page, total_pages))
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_orders_filters(page, total_pages))

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "☎️ *Support*\n\n"
        f"💬 Chat: {SUPPORT_CHAT}\n"
        f"📣 Channel: {SUPPORT_CHANNEL}\n\n"
        "اختر 👇"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_support())
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_support())

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)

    if must_block_user(update):
        t = (update.message.text or "").strip()
        if t == "☎️ Contact Support":
            return await show_support(update, context)
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    t = (update.message.text or "").strip()

    if t == "🛒 Our Products":
        return await show_categories(update, context)
    if t == "💰 My Balance":
        return await show_balance(update, context)
    if t == "📦 My Orders":
        return await show_orders(update, context, rng="all", page=0)
    if t == "☎️ Contact Support":
        return await show_support(update, context)
    if t == "⚡ Manual Order":
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await update.message.reply_text(
                "⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=REPLY_MENU,
            )
        return await update.message.reply_text(
            "⚡ Manual orders موجودة عندك في النسخة الكبيرة.\nهذه النسخة ركزت على المتجر + الشحن التلقائي.",
            reply_markup=REPLY_MENU,
        )

    await update.message.reply_text("Use the menu 👇", reply_markup=REPLY_MENU)

# =========================
# QTY INPUT
# =========================
async def qty_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if must_block_user(update):
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    if txt in MENU_BUTTONS:
        context.user_data.clear()
        return await menu_router(update, context)

    if txt.lower() in ("/cancel", "cancel"):
        context.user_data.clear()
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    try:
        qty = int(txt)
    except ValueError:
        return await update.message.reply_text("❌ Enter numbers only.")

    pid = int(context.user_data.get(UD_PID, 0))
    cid = int(context.user_data.get(UD_CID, 0))
    max_qty = int(context.user_data.get(UD_QTY_MAX, 0))

    if not pid or not cid or max_qty <= 0:
        await update.message.reply_text("❌ Session expired. Open Our Products again.")
        return ConversationHandler.END

    if qty < 1 or qty > max_qty:
        return await update.message.reply_text(f"❌ Enter a quantity between 1 and {max_qty}:")

    cur.execute("SELECT title, price FROM products WHERE pid=? AND active=1", (pid,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ Product not found.")
        return ConversationHandler.END

    title, price = row
    total = float(price) * qty

    client_ref = secrets.token_hex(10)
    context.user_data[UD_ORDER_CLIENT_REF] = client_ref
    context.user_data[UD_LAST_QTY] = qty
    context.user_data[UD_LAST_PID] = pid

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirm Purchase", callback_data=f"confirm:{pid}:{client_ref}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")],
        ]
    )
    await update.message.reply_text(
        f"🧾 *Confirm Order*\n\n"
        f"🎮 Product: *{title}*\n"
        f"🔢 Qty: *{qty}*\n"
        f"💵 Total: *{money(total)}*\n\n"
        "اضغط ✅ Confirm لإتمام العملية",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
    )
    return ConversationHandler.END

# =========================
# TOPUP DETAILS
# =========================
async def topup_details_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if must_block_user(update):
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    if txt in MENU_BUTTONS:
        context.user_data.pop(UD_DEP_ID, None)
        return await menu_router(update, context)

    if txt.lower() in ("/cancel", "cancel"):
        context.user_data.pop(UD_DEP_ID, None)
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    dep_id = int(context.user_data.get(UD_DEP_ID, 0))
    if not dep_id:
        await update.message.reply_text("❌ Session expired. Open My Balance again.")
        return ConversationHandler.END

    if "|" not in txt:
        return await update.message.reply_text("❌ Format: amount | txid\nExample: 10 | 2E38F3...")

    a, txid = [x.strip() for x in txt.split("|", 1)]
    try:
        amount = float(a)
    except ValueError:
        return await update.message.reply_text("❌ Amount must be a number.\nExample: 10 | TXID")

    cur.execute("SELECT user_id, status FROM deposits WHERE id=?", (dep_id,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ Deposit not found.")
        return ConversationHandler.END

    if row[1] not in ("WAITING_PAYMENT", "PAID", "PENDING_REVIEW"):
        await update.message.reply_text("❌ This deposit is already processed.")
        return ConversationHandler.END

    cur.execute(
        "UPDATE deposits SET txid=?, amount=?, status='PENDING_REVIEW' WHERE id=?",
        (txid[:1500], amount, dep_id),
    )
    con.commit()

    uid = update.effective_user.id
    await update.message.reply_text(
        f"✅ Received!\n🧾 Deposit ID: {dep_id}\n⏳ Status: PENDING_REVIEW\n\nWe will approve soon ✅",
        reply_markup=REPLY_MENU,
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💰 *DEPOSIT REVIEW*\n"
                f"🧾 Deposit ID: *{dep_id}*\n"
                f"👤 User: `{uid}`\n"
                f"💵 Amount: *{amount}*\n"
                f"🔗 TXID:\n`{txid}`\n\n"
                f"✅ Approve: /approvedep {dep_id}\n"
                f"🚫 Reject: /rejectdep {dep_id}"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.exception("Failed to notify admin about deposit %s: %s", dep_id, e)

    return ConversationHandler.END

# =========================
# ADMIN INPUT
# =========================
async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid_admin = update.effective_user.id
    if not is_owner(uid_admin):
        await update.message.reply_text("❌ Not allowed.")
        return ConversationHandler.END

    mode = context.user_data.get(UD_ADMIN_MODE)
    text = (update.message.text or "").strip()

    if text.lower() in ("/cancel", "cancel"):
        context.user_data.pop(UD_ADMIN_MODE, None)
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    try:
        if mode == "admins":
            m = re.match(r"^(addadmin|deladmin)\s*\|\s*(\d+)$", text.lower())
            if not m:
                await update.message.reply_text("❌ Format:\naddadmin | user_id\nor\ndeladmin | user_id")
                return ST_ADMIN_INPUT
            cmd, target_s = m.group(1), m.group(2)
            target = int(target_s)
            if cmd == "addadmin":
                if target == ADMIN_ID:
                    await update.message.reply_text("✅ Owner already admin.")
                    return ConversationHandler.END
                cur.execute("INSERT OR REPLACE INTO admins(user_id, role) VALUES(?,?)", (target, ROLE_HELPER))
                con.commit()
                await update.message.reply_text(f"✅ Added helper admin: {target}")
                return ConversationHandler.END
            if cmd == "deladmin":
                if target == ADMIN_ID:
                    await update.message.reply_text("❌ Cannot delete owner.")
                    return ConversationHandler.END
                cur.execute("DELETE FROM admins WHERE user_id=? AND role!=?", (target, ROLE_OWNER))
                con.commit()
                await update.message.reply_text(f"✅ Removed admin: {target}")
                return ConversationHandler.END

        if mode == "addcat":
            cur.execute("INSERT OR IGNORE INTO categories(title) VALUES(?)", (text,))
            con.commit()
            await update.message.reply_text("✅ Category added.")
            return ConversationHandler.END

        if mode == "addprod":
            m = re.match(r'^"(.+?)"\s*\|\s*"(.+?)"\s*\|\s*([\d.]+)\s*$', text)
            if not m:
                await update.message.reply_text('❌ Example:\n"CAT" | "TITLE" | 9.2')
                return ST_ADMIN_INPUT
            cat_title, prod_title, price_s = m.groups()
            cur.execute("SELECT cid FROM categories WHERE title=?", (cat_title,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Category not found.")
                return ConversationHandler.END
            cid = int(row[0])
            cur.execute(
                "INSERT INTO products(cid,title,price,product_type,active) VALUES(?,?,?,'CODE',1)",
                (cid, prod_title, float(price_s)),
            )
            con.commit()
            await update.message.reply_text("✅ Product added.")
            return ConversationHandler.END

        if mode == "addcodes":
            if "|" not in text:
                await update.message.reply_text("❌ Example:\n12 | CODE1\nCODE2")
                return ST_ADMIN_INPUT
            pid_s, codes_blob = [x.strip() for x in text.split("|", 1)]
            if not pid_s.isdigit():
                await update.message.reply_text("❌ PID must be a number.")
                return ST_ADMIN_INPUT
            pid = int(pid_s)
            codes = [c.strip().replace(" ", "") for c in codes_blob.splitlines() if c.strip()]
            if not codes:
                await update.message.reply_text("❌ No codes.")
                return ConversationHandler.END
            added = 0
            skipped = 0
            for ctext in codes:
                try:
                    cur.execute("INSERT INTO codes(pid,code_text,used) VALUES(?,?,0)", (pid, ctext))
                    added += 1
                except sqlite3.IntegrityError:
                    skipped += 1
            con.commit()
            await update.message.reply_text(f"✅ Added {added} codes to PID {pid}.\n♻️ Skipped duplicates: {skipped}")
            return ConversationHandler.END

        if mode == "setprice":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Example: 12 | 9.5")
                return ST_ADMIN_INPUT
            pid, price = int(m.group(1)), float(m.group(2))
            cur.execute("UPDATE products SET price=? WHERE pid=?", (price, pid))
            con.commit()
            await update.message.reply_text("✅ Price updated.")
            return ConversationHandler.END

        if mode == "toggle":
            if not text.isdigit():
                await update.message.reply_text("❌ Send PID number only.")
                return ST_ADMIN_INPUT
            pid = int(text)
            cur.execute("SELECT active FROM products WHERE pid=?", (pid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Product not found.")
                return ConversationHandler.END
            active = int(row[0])
            newv = 0 if active else 1
            cur.execute("UPDATE products SET active=? WHERE pid=?", (newv, pid))
            con.commit()
            await update.message.reply_text(f"✅ Product {'enabled ✅' if newv else 'disabled ⛔'}.")
            return ConversationHandler.END

        if mode == "addbal":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Example: user_id | amount")
                return ST_ADMIN_INPUT
            user_id, amount = int(m.group(1)), float(m.group(2))
            bal_before = get_balance(user_id)
            add_balance(user_id, amount)
            bal_after = get_balance(user_id)
            await update.message.reply_text(f"✅ Added +{money(amount)} to {user_id}")
            try:
                await context.bot.send_message(
                    user_id,
                    f"✅ Admin added balance: +{money(amount)}\n\n💳 Before: {bal_before:.3f}{CURRENCY}\n✅ After: {bal_after:.3f}{CURRENCY}",
                )
            except Exception:
                pass
            return ConversationHandler.END

        if mode == "takebal":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Example: user_id | amount")
                return ST_ADMIN_INPUT
            user_id, amount = int(m.group(1)), float(m.group(2))
            bal_before = get_balance(user_id)
            if not charge_balance(user_id, amount):
                await update.message.reply_text("❌ User has insufficient balance.")
                return ConversationHandler.END
            bal_after = get_balance(user_id)
            await update.message.reply_text(f"✅ Took {money(amount)} from {user_id}.")
            try:
                await context.bot.send_message(
                    user_id,
                    f"➖ Admin deducted: -{money(amount)}\n\n💳 Before: {bal_before:.3f}{CURRENCY}\n✅ After: {bal_after:.3f}{CURRENCY}",
                )
            except Exception:
                pass
            return ConversationHandler.END

        await update.message.reply_text("❌ Unknown admin mode. Use /admin")
        return ConversationHandler.END

    except Exception as e:
        logger.exception("Admin input error: %s", e)
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END

# =========================
# CALLBACK
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if must_block_user(update):
        if data in ("goto:cats", "goto:balance", "goto:topup", "back:cats"):
            await q.edit_message_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())
            return ConversationHandler.END
        await q.answer("Account suspended", show_alert=True)
        return ConversationHandler.END

    if data == "noop":
        return ConversationHandler.END

    # goto
    if data == "goto:cats":
        await show_categories(update, context)
        return ConversationHandler.END
    if data in ("goto:balance", "goto:topup"):
        await show_balance(update, context)
        return ConversationHandler.END

    # admin panel
    if data == "admin:panel":
        if not is_owner(update.effective_user.id):
            await q.edit_message_text("❌ Not allowed.")
            return ConversationHandler.END
        await q.edit_message_text("👑 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_panel(update.effective_user.id))
        return ConversationHandler.END

    if data.startswith("admin:"):
        if not is_owner(update.effective_user.id):
            await q.edit_message_text("❌ Not allowed.")
            return ConversationHandler.END

        mode = data.split(":", 1)[1]
        context.user_data[UD_ADMIN_MODE] = mode

        if mode == "listprod":
            cur.execute(
                """
                SELECT p.pid, c.title, p.title, p.price, p.active
                FROM products p JOIN categories c ON c.cid=p.cid
                ORDER BY c.title, p.title
                """
            )
            rows = cur.fetchall()
            if not rows:
                await q.edit_message_text("No products.")
                return ConversationHandler.END
            lines = [
                f"PID {pid} | {cat} | {title} | {float(price):.3f}{CURRENCY} | {'ON ✅' if act else 'OFF ⛔'}"
                for pid, cat, title, price, act in rows
            ]
            text = "\n".join(lines)
            await q.edit_message_text(text[:3800])
            return ConversationHandler.END

        prompts = {
            "addcat": 'Send category title:\nExample: 🪂 PUBG MOBILE UC VOUCHERS',
            "addprod": 'Send product:\n"Category Title" | "Product Title" | price\nExample:\n"🪂 PUBG MOBILE UC VOUCHERS" | "60 UC" | 0.875',
            "addcodes": 'Send codes:\npid | code1\\ncode2\\n...\nExample:\n12 | ABCD-1234\nEFGH-5678',
            "setprice": 'Send: pid | new_price\nExample: 12 | 9.5',
            "toggle": 'Send: pid\nExample: 12',
            "addbal": 'Send: user_id | amount\nExample: 1997968014 | 5',
            "takebal": 'Send: user_id | amount\nExample: 1997968014 | 5',
            "admins": "Send:\naddadmin | user_id\nor\ndeladmin | user_id",
        }
        await q.edit_message_text(prompts.get(mode, "Send input now..."))
        return ST_ADMIN_INPUT

    # navigation
    if data == "back:cats":
        await show_categories(update, context)
        return ConversationHandler.END

    if data.startswith("cat:"):
        cid = int(data.split(":", 1)[1])
        context.user_data[UD_CID] = cid
        await q.edit_message_text("🛒 Choose a product:", reply_markup=kb_products(cid))
        return ConversationHandler.END

    if data.startswith("back:prods:"):
        cid = int(data.split(":", 2)[2])
        await q.edit_message_text("🛒 Choose a product:", reply_markup=kb_products(cid))
        return ConversationHandler.END

    if data.startswith("view:"):
        pid = int(data.split(":", 1)[1])
        cur.execute("SELECT title, price, cid FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            await q.edit_message_text("❌ Product not found.")
            return ConversationHandler.END
        title, price, cid = row
        stock = product_stock(pid)
        text = (
            f"🎁 *{title}*\n\n"
            f"🆔 ID: `{pid}`\n"
            f"💵 Price: *{float(price):.3f}* {CURRENCY}\n"
            f"📦 Stock: *{stock}*"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_product_view(pid, cid))
        return ConversationHandler.END

    if data.startswith("buy:"):
        pid = int(data.split(":", 1)[1])
        cur.execute("SELECT title, cid FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            await q.edit_message_text("❌ Product not found.")
            return ConversationHandler.END
        title, cid = row
        stock = product_stock(pid)
        if stock <= 0:
            await q.edit_message_text("❌ Out of stock.", reply_markup=kb_products(cid))
            return ConversationHandler.END

        context.user_data[UD_PID] = pid
        context.user_data[UD_CID] = cid
        context.user_data[UD_QTY_MAX] = stock

        await q.edit_message_text(
            f"🛒 You are purchasing: *{title}*\n\n"
            f"📝 Enter quantity (1 → {stock}):\n"
            f"❌ /cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_qty_cancel(cid),
        )
        return ST_QTY

    # confirm purchase
    if data.startswith("confirm:"):
        parts = data.split(":")
        pid = int(parts[1]) if len(parts) > 1 else 0
        client_ref = parts[2] if len(parts) > 2 else ""

        qty = int(context.user_data.get(UD_LAST_QTY, 0))
        if qty <= 0 or pid <= 0 or not client_ref:
            await q.edit_message_text("❌ Quantity expired. Buy again.")
            return ConversationHandler.END

        cur.execute("SELECT id, delivered_text, status FROM orders WHERE client_ref=?", (client_ref,))
        already = cur.fetchone()
        if already:
            oid, delivered_text, status = already[0], already[1] or "", already[2]
            await q.edit_message_text(f"✅ Already processed.\nOrder ID: {oid}\nStatus: {status}\nDelivering again...")
            if delivered_text.strip():
                await send_codes_delivery(update.effective_user.id, context, oid, delivered_text.splitlines())
            return ConversationHandler.END

        cur.execute("SELECT title, price FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            await q.edit_message_text("❌ Product not found.")
            return ConversationHandler.END
        title, price = row
        total = float(price) * qty

        uid = update.effective_user.id
        bal_before = get_balance(uid)

        if not charge_balance(uid, total):
            bal = get_balance(uid)
            missing = total - bal
            await q.edit_message_text(
                f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
                reply_markup=kb_topup_now(),
            )
            return ConversationHandler.END

        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT code_id, code_text FROM codes WHERE pid=? AND used=0 ORDER BY code_id ASC LIMIT ?", (pid, qty))
            picked = cur.fetchall()
            if len(picked) < qty:
                cur.execute("ROLLBACK")
                add_balance(uid, total)
                await q.edit_message_text("❌ Stock error. Refunded. Try again.")
                return ConversationHandler.END

            cur.execute(
                "INSERT INTO orders(user_id,pid,product_title,qty,total,status,client_ref) VALUES(?,?,?,?,?,'PENDING',?)",
                (uid, pid, title, qty, total, client_ref),
            )
            oid = cur.lastrowid

            for code_id, _ in picked:
                cur.execute(
                    "UPDATE codes SET used=1, used_at=datetime('now'), order_id=? WHERE code_id=? AND used=0",
                    (oid, code_id),
                )

            codes_list = [c for _, c in picked]
            delivered_text = "\n".join(codes_list)
            cur.execute("UPDATE orders SET status='COMPLETED', delivered_text=? WHERE id=?", (delivered_text, oid))

            cur.execute("COMMIT")
        except Exception as e:
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
            add_balance(uid, total)
            logger.exception("Purchase transaction failed: %s", e)
            await q.edit_message_text("❌ Error while processing order. Refunded. Try again.")
            return ConversationHandler.END

        bal_after = get_balance(uid)

        await q.edit_message_text(
            f"✅ *Order Created Successfully!*\n"
            f"🧾 Order ID: *{oid}*\n"
            f"🎮 Product: {title}\n"
            f"🔢 Qty: *{qty}*\n"
            f"💵 Total: *{total:.3f} {CURRENCY}*\n\n"
            f"💳 Balance before: *{bal_before:.3f} {CURRENCY}*\n"
            f"✅ Balance after: *{bal_after:.3f} {CURRENCY}*\n\n"
            f"🚚 Delivering codes... 🎁",
            parse_mode=ParseMode.MARKDOWN,
        )
        await send_codes_delivery(chat_id=uid, context=context, order_id=oid, codes=codes_list)

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "✅ *NEW COMPLETED ORDER*\n"
                    f"🧾 Order ID: *{oid}*\n"
                    f"👤 User: `{uid}`\n"
                    f"🎮 Product: {title}\n"
                    f"🔢 Qty: *{qty}*\n"
                    f"💵 Total: *{total:.3f} {CURRENCY}*"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

        return ConversationHandler.END

    # orders pages
    if data.startswith("orders:range:"):
        _, _, rng, page = data.split(":")
        await show_orders(update, context, rng=rng, page=int(page))
        return ConversationHandler.END

    if data.startswith("orders:next:"):
        _, _, page = data.split(":")
        await show_orders(update, context, rng="all", page=int(page))
        return ConversationHandler.END

    # pay
    if data.startswith("pay:"):
        method = data.split(":", 1)[1]
        uid = update.effective_user.id
        note = secrets.token_hex(8).upper()
        cur.execute("INSERT INTO deposits(user_id,method,note,status) VALUES(?,?,?,'WAITING_PAYMENT')", (uid, method, note))
        dep_id = cur.lastrowid
        con.commit()

        if method == "BINANCE":
            dest_title, dest_value, extra = "UID", BINANCE_UID, "Send USDT only."
        elif method == "BYBIT":
            dest_title, dest_value, extra = "UID", BYBIT_UID, "Send USDT only."
        elif method == "TRC20":
            dest_title, dest_value, extra = "Address", USDT_TRC20, "Network: TRC20 only."
        else:
            dest_title, dest_value, extra = "Address", USDT_BEP20, "Network: BEP20 only."

        text = (
            f"🔑 *{method} Payment*\n\n"
            f"Send amount to this {dest_title} + include note:\n\n"
            f"*{dest_title}:*\n`{dest_value}`\n\n"
            f"*Note:*\n`{note}`\n\n"
            f"⚠️ {extra}\n\n"
            f"بعد الدفع اضغط ✅ I Have Paid"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_have_paid(dep_id))
        return ConversationHandler.END

    if data.startswith("paid:"):
        dep_id = int(data.split(":", 1)[1])
        context.user_data[UD_DEP_ID] = dep_id
        await q.edit_message_text(
            "✅ Great!\nNow send:\n`amount | txid`\nExample:\n`10 | 2E38F3A2...`\n\n/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_TOPUP_DETAILS

    return ConversationHandler.END

# =========================
# ADMIN COMMANDS
# =========================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    await update.message.reply_text("👑 Admin Panel", reply_markup=kb_admin_panel(update.effective_user.id))

async def approvedep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /approvedep <deposit_id>")
    dep_id = int(context.args[0])
    cur.execute("SELECT user_id, amount, status FROM deposits WHERE id=?", (dep_id,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Deposit not found.")
    user_id, amount, status = int(row[0]), row[1], row[2]
    if status != "PENDING_REVIEW":
        return await update.message.reply_text("❌ Deposit not ready for approval.")
    if amount is None:
        return await update.message.reply_text("❌ Amount missing.")
    bal_before = get_balance(user_id)
    cur.execute("UPDATE deposits SET status='APPROVED' WHERE id=?", (dep_id,))
    con.commit()
    add_balance(user_id, float(amount))
    bal_after = get_balance(user_id)
    await update.message.reply_text(f"✅ Deposit #{dep_id} approved. +{money(float(amount))}")
    try:
        await context.bot.send_message(
            user_id,
            f"✅ Top up approved: +{money(float(amount))}\n\n💳 Balance before: {bal_before:.3f}{CURRENCY}\n✅ Balance after: {bal_after:.3f}{CURRENCY}",
        )
    except Exception:
        pass

async def rejectdep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /rejectdep <deposit_id>")
    dep_id = int(context.args[0])
    cur.execute("SELECT user_id, status FROM deposits WHERE id=?", (dep_id,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Deposit not found.")
    user_id, status = int(row[0]), row[1]
    if status not in ("PENDING_REVIEW", "WAITING_PAYMENT"):
        return await update.message.reply_text("❌ Deposit already processed.")
    cur.execute("UPDATE deposits SET status='REJECTED' WHERE id=?", (dep_id,))
    con.commit()
    await update.message.reply_text(f"✅ Deposit #{dep_id} rejected.")
    try:
        await context.bot.send_message(user_id, f"❌ Top up #{dep_id} rejected. Contact support.")
    except Exception:
        pass

# =========================
# MAIN
# =========================
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    CB_PATTERN = r"^(cat:|view:|buy:|confirm:|pay:|paid:|admin:|orders:|back:|goto:)"

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
        states={
            ST_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, qty_input),
                     CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
            ST_TOPUP_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_details_input),
                               CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
            ST_ADMIN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_input),
                             CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
        },
        fallbacks=[CommandHandler("start", start_cmd)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("approvedep", approvedep_cmd))
    app.add_handler(CommandHandler("rejectdep", rejectdep_cmd))

    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    return app

def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

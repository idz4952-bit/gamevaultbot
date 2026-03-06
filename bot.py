
import os
import re
import io
import html
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
# Logging
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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Owner / Main Admin
DB_PATH = os.getenv("DB_PATH", "shop.db")

_db_dir = os.path.dirname(DB_PATH) if DB_PATH else ""
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

CURRENCY = os.getenv("CURRENCY", "$")

BINANCE_UID = os.getenv("BINANCE_ID", "YOUR_BINANCE_ID_ADDRESS")
BYBIT_UID = os.getenv("BYBIT_UID", "12345678")
USDT_TRC20 = os.getenv("USDT_TRC20", "YOUR_USDT_TRC20_ADDRESS")
USDT_BEP20 = os.getenv("USDT_BEP20", "YOUR_USDT_BEP20_ADDRESS")

SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+213xxxxxxxxx").strip()
SUPPORT_CHAT = os.getenv("SUPPORT_CHAT", "@your_support").strip()  # ✅ direct chat (not group)
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "@yourchannel").strip()

HIDDEN_CATEGORIES = {
    "🎲 YALLA LUDO",
    "🕹 ROBLOX (USA)",
    "🟦 STEAM (USA)",
}

if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")
if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID env var is missing or 0")


# =========================
# Admin roles
# =========================
ROLE_OWNER = "OWNER"
ROLE_HELPER = "HELPER"  # only manual orders


def is_owner(uid: int) -> bool:
    return uid == ADMIN_ID


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
# Working hours (Manual Orders) KSA
# 10:00 -> 24:00 (00:00)
# KSA = UTC+3
# =========================
KSA_UTC_OFFSET_HOURS = 3
MANUAL_START_HOUR_KSA = 10
MANUAL_END_HOUR_KSA = 24  # 12 ليلًا


def now_ksa():
    return datetime.utcnow() + timedelta(hours=KSA_UTC_OFFSET_HOURS)


def manual_open_now() -> bool:
    t = now_ksa()
    h = t.hour
    return MANUAL_START_HOUR_KSA <= h < MANUAL_END_HOUR_KSA


def manual_hours_text() -> str:
    # KSA 10->24, GMT 7->21
    gmt_start = (MANUAL_START_HOUR_KSA - KSA_UTC_OFFSET_HOURS) % 24
    gmt_end = (MANUAL_END_HOUR_KSA - KSA_UTC_OFFSET_HOURS) % 24
    return (
        "🕘 *Manual Working Hours*\n"
        f"🇸🇦 KSA: {MANUAL_START_HOUR_KSA:02d}:00 → 24:00\n"
        f"🌍 GMT: {gmt_start:02d}:00 → {gmt_end:02d}:00"
    )


# =========================
# SORT: صغير -> كبير
# =========================
def extract_sort_value(title: str) -> float:
    t = title.replace(",", ".")
    nums = re.findall(r"\d+(?:\.\d+)?", t)
    if not nums:
        return 1e18
    return float(nums[0])


# =========================
# DB
# =========================
con = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = con.cursor()

cur.executescript(
    """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  balance REAL NOT NULL DEFAULT 0
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
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manual_prices(
  pkey TEXT PRIMARY KEY,
  price REAL NOT NULL
);
"""
)
con.commit()


def ensure_schema():
    # unique code per product
    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_codes_unique ON codes(pid, code_text)")
        con.commit()
    except Exception:
        pass

    # helpful indexes
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_codes_pid_used ON codes(pid, used)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deposits_user_status ON deposits(user_id, status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_manual_user_status ON manual_orders(user_id, status)")
        con.commit()
    except Exception:
        pass

    # migrate manual_orders columns if missing
    for col, ctype in [("player_id", "TEXT"), ("note", "TEXT"), ("delivered_text", "TEXT")]:
        try:
            cur.execute(f"ALTER TABLE manual_orders ADD COLUMN {col} {ctype}")
            con.commit()
        except Exception:
            pass

    # ✅ Anti double-confirm: add client_ref unique to orders
    try:
        cur.execute("ALTER TABLE orders ADD COLUMN client_ref TEXT")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_ref_unique ON orders(client_ref)")
        con.commit()
    except Exception:
        pass

    # ✅ User suspend
    try:
        cur.execute("ALTER TABLE users ADD COLUMN suspended INTEGER NOT NULL DEFAULT 0")
        con.commit()
    except Exception:
        pass

    # ✅ Admins table (Owner + Helpers)
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admins(
              user_id INTEGER PRIMARY KEY,
              role TEXT NOT NULL
            )
            """
        )
        con.commit()
    except Exception:
        pass

    # ✅ manual_orders approved_by (admin id)
    try:
        cur.execute("ALTER TABLE manual_orders ADD COLUMN approved_by INTEGER")
        con.commit()
    except Exception:
        pass

    try:
        cur.execute("CREATE TABLE IF NOT EXISTS user_product_prices(user_id INTEGER NOT NULL, pid INTEGER NOT NULL, price REAL NOT NULL, PRIMARY KEY(user_id, pid))")
        cur.execute("CREATE TABLE IF NOT EXISTS user_manual_prices(user_id INTEGER NOT NULL, pkey TEXT NOT NULL, price REAL NOT NULL, PRIMARY KEY(user_id, pkey))")
        cur.execute("CREATE TABLE IF NOT EXISTS pos_agents(user_id INTEGER PRIMARY KEY, created_by INTEGER, profit_balance REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now'))) ")
        cur.execute("CREATE TABLE IF NOT EXISTS pos_customers(customer_id INTEGER PRIMARY KEY, pos_user_id INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))) ")
        cur.execute("CREATE TABLE IF NOT EXISTS pos_child_prices(pos_user_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, pid INTEGER NOT NULL, price REAL NOT NULL, PRIMARY KEY(pos_user_id, customer_id, pid))")
        con.commit()
    except Exception:
        pass


ensure_schema()


def seed_owner_admin():
    # Ensure owner exists as OWNER in admins table
    try:
        cur.execute("INSERT OR REPLACE INTO admins(user_id, role) VALUES(?,?)", (ADMIN_ID, ROLE_OWNER))
        con.commit()
    except Exception:
        pass


seed_owner_admin()


def admin_role(uid: int) -> Optional[str]:
    cur.execute("SELECT role FROM admins WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r[0] if r else None


def is_admin_any(uid: int) -> bool:
    return admin_role(uid) in (ROLE_OWNER, ROLE_HELPER)


def is_manual_admin(uid: int) -> bool:
    # helper can only manage manual orders; owner can do everything
    return admin_role(uid) in (ROLE_OWNER, ROLE_HELPER)


def is_pos_agent(uid: int) -> bool:
    cur.execute("SELECT 1 FROM pos_agents WHERE user_id=?", (uid,))
    return cur.fetchone() is not None


def pos_customer_owner(customer_id: int) -> Optional[int]:
    cur.execute("SELECT pos_user_id FROM pos_customers WHERE customer_id=?", (customer_id,))
    row = cur.fetchone()
    return int(row[0]) if row else None


def customer_is_under_pos(customer_id: int) -> bool:
    return pos_customer_owner(customer_id) is not None


def set_pos_agent(user_id: int, created_by: int):
    ensure_user_exists(user_id)
    cur.execute("INSERT OR IGNORE INTO pos_agents(user_id, created_by, profit_balance) VALUES(?,?,0)", (user_id, created_by))
    con.commit()


def remove_pos_agent(user_id: int):
    cur.execute("DELETE FROM pos_child_prices WHERE pos_user_id=?", (user_id,))
    cur.execute("DELETE FROM pos_customers WHERE pos_user_id=?", (user_id,))
    cur.execute("DELETE FROM pos_agents WHERE user_id=?", (user_id,))
    con.commit()


def pos_profit_balance(user_id: int) -> float:
    cur.execute("SELECT profit_balance FROM pos_agents WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


def add_pos_profit(user_id: int, amount: float):
    if amount <= 0:
        return
    cur.execute("UPDATE pos_agents SET profit_balance=profit_balance+? WHERE user_id=?", (float(amount), user_id))
    con.commit()


def cashout_pos_profit(user_id: int) -> float:
    amt = pos_profit_balance(user_id)
    if amt <= 0:
        return 0.0
    cur.execute("UPDATE pos_agents SET profit_balance=0 WHERE user_id=?", (user_id,))
    add_balance(user_id, amt)
    con.commit()
    return amt


def add_pos_customer(pos_user_id: int, customer_id: int):
    ensure_user_exists(customer_id)
    cur.execute("INSERT OR REPLACE INTO pos_customers(customer_id, pos_user_id) VALUES(?,?)", (customer_id, pos_user_id))
    con.commit()


def remove_pos_customer(pos_user_id: int, customer_id: int):
    cur.execute("DELETE FROM pos_child_prices WHERE pos_user_id=? AND customer_id=?", (pos_user_id, customer_id))
    cur.execute("DELETE FROM pos_customers WHERE customer_id=? AND pos_user_id=?", (customer_id, pos_user_id))
    con.commit()


def set_pos_child_price(pos_user_id: int, customer_id: int, pid: int, price: float):
    cur.execute(
        "INSERT INTO pos_child_prices(pos_user_id, customer_id, pid, price) VALUES(?,?,?,?) "
        "ON CONFLICT(pos_user_id, customer_id, pid) DO UPDATE SET price=excluded.price",
        (pos_user_id, customer_id, pid, float(price)),
    )
    con.commit()


def delete_pos_child_price(pos_user_id: int, customer_id: int, pid: int):
    cur.execute("DELETE FROM pos_child_prices WHERE pos_user_id=? AND customer_id=? AND pid=?", (pos_user_id, customer_id, pid))
    con.commit()


def get_user_product_price(user_id: int, pid: int) -> Optional[float]:
    cur.execute("SELECT price FROM user_product_prices WHERE user_id=? AND pid=?", (user_id, pid))
    row = cur.fetchone()
    if row is not None:
        return float(row[0])
    owner = pos_customer_owner(user_id)
    if owner is not None:
        cur.execute("SELECT price FROM pos_child_prices WHERE pos_user_id=? AND customer_id=? AND pid=?", (owner, user_id, pid))
        row = cur.fetchone()
        if row is not None:
            return float(row[0])
    return None


def get_effective_product_price(user_id: int, pid: int, default_price: float) -> float:
    custom = get_user_product_price(user_id, pid)
    return float(custom if custom is not None else default_price)


def get_effective_manual_price(user_id: int, key: str, default_price: float) -> float:
    cur.execute("SELECT price FROM user_manual_prices WHERE user_id=? AND pkey=?", (user_id, key))
    row = cur.fetchone()
    if row:
        return float(row[0])
    return get_manual_price(key, default_price)


# =========================
# Manual Prices (Defaults)
# =========================
MANUAL_PRICE_DEFAULTS = {
    "SHAHID_MENA_3M": 10.0,
    "SHAHID_MENA_12M": 35.0,
    "FF_100": 0.930,
    "FF_210": 1.860,
    "FF_530": 4.650,
    "FF_1080": 9.300,
    "FF_2200": 18.600,
}


def seed_manual_prices():
    for k, v in MANUAL_PRICE_DEFAULTS.items():
        cur.execute("INSERT OR IGNORE INTO manual_prices(pkey, price) VALUES(?,?)", (k, float(v)))
    con.commit()


def get_manual_price(key: str, default: float) -> float:
    cur.execute("SELECT price FROM manual_prices WHERE pkey=?", (key,))
    row = cur.fetchone()
    if not row:
        return float(default)
    try:
        return float(row[0])
    except Exception:
        return float(default)


seed_manual_prices()

# =========================
# SEED
# =========================
DEFAULT_CATEGORIES = [
    "🍎 ITUNES GIFTCARD (USA)",
    "🪂 PUBG MOBILE UC VOUCHERS",
    "💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)",
    "🎮 PLAYSTATION USA GIFTCARDS",
]

DEFAULT_PRODUCTS = [
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "1 USD 💎 PINS 100+10", 0.920),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "2 USD 💎 PINS 210+21", 1.840),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "5 USD 💎 PINS 530+53", 4.600),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "10 USD 💎 PINS 1080+108", 9.200),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "20 USD 💎 PINS 2200+220", 18.400),

    ("🪂 PUBG MOBILE UC VOUCHERS", "60 UC", 0.875),
    ("🪂 PUBG MOBILE UC VOUCHERS", "325 UC", 4.375),
    ("🪂 PUBG MOBILE UC VOUCHERS", "660 UC", 8.750),
    ("🪂 PUBG MOBILE UC VOUCHERS", "1800 UC", 22.000),
    ("🪂 PUBG MOBILE UC VOUCHERS", "3850 UC", 44.000),
    ("🪂 PUBG MOBILE UC VOUCHERS", "8100 UC", 88.000),

    ("🍎 ITUNES GIFTCARD (USA)", "5$ iTunes US", 4.600),
    ("🍎 ITUNES GIFTCARD (USA)", "10$ iTunes US", 9.200),
    ("🍎 ITUNES GIFTCARD (USA)", "20$ iTunes US", 18.400),
    ("🍎 ITUNES GIFTCARD (USA)", "25$ iTunes US", 23.000),
    ("🍎 ITUNES GIFTCARD (USA)", "50$ iTunes US", 46.000),
    ("🍎 ITUNES GIFTCARD (USA)", "100$ iTunes US", 92.000),

    ("🎮 PLAYSTATION USA GIFTCARDS", "10$ PSN USA", 8.900),
    ("🎮 PLAYSTATION USA GIFTCARDS", "25$ PSN USA", 22.000),
    ("🎮 PLAYSTATION USA GIFTCARDS", "50$ PSN USA", 44.000),
    ("🎮 PLAYSTATION USA GIFTCARDS", "100$ PSN USA", 88.000),
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
# Reply Menu
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
    "🛒 Our Products",
    "💰 My Balance",
    "📦 My Orders",
    "⚡ Manual Order",
    "☎️ Contact Support",
}

ADMIN_TEXT_EXIT = {
    "⬅️ رجوع",
    "⬅ رجوع",
    "رجوع",
    "❌ إلغاء العملية",
    "إلغاء العملية",
    "الغاء",
    "إلغاء",
}

# =========================
# States
# =========================
ST_QTY = 10
ST_TOPUP_DETAILS = 20
ST_ADMIN_INPUT = 99

ST_MANUAL_EMAIL = 30
ST_MANUAL_PASS = 31
ST_FF_PLAYERID = 32

UD_PID = "pid"
UD_CID = "cid"
UD_QTY_MAX = "qty_max"
UD_DEP_ID = "dep_id"
UD_ADMIN_MODE = "admin_mode"
UD_ORD_RNG = "orders_rng"

UD_MANUAL_SERVICE = "manual_service"
UD_MANUAL_PLAN = "manual_plan"
UD_MANUAL_PRICE = "manual_price"
UD_MANUAL_PLAN_TITLE = "manual_plan_title"
UD_MANUAL_EMAIL = "manual_email"

UD_FF_CART = "ff_cart"
UD_FF_TOTAL = "ff_total"

UD_ADMIN_MANUAL_ID = "admin_manual_id"
UD_ADMIN_CODES_PID = "admin_codes_pid"

UD_ORDER_CLIENT_REF = "order_client_ref"
UD_LAST_QTY = "last_qty"
UD_LAST_PID = "last_pid"

# =========================
# User helpers
# =========================
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
    return float(row[0]) if row else 0.0


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
# Delivery
# =========================
MAX_CODES_IN_MESSAGE = 200
TELEGRAM_TEXT_LIMIT = 3800


async def send_codes_delivery(chat_id: int, context: ContextTypes.DEFAULT_TYPE, order_id: int, codes: List[str]):
    codes = [c.strip() for c in codes if c and c.strip()]
    count = len(codes)

    header_html = (
        f"🎁 <b>Delivery Successful!</b>\n"
        f"✅ Order <b>#{order_id}</b> COMPLETED\n"
        f"📦 Codes: <b>{count}</b>\n\n"
    )
    if count == 0:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Order <b>#{order_id}</b> COMPLETED\n(No codes)",
            parse_mode=ParseMode.HTML,
        )
        return

    if count > MAX_CODES_IN_MESSAGE:
        content = "\n".join(codes)
        bio = io.BytesIO(content.encode("utf-8"))
        bio.name = f"order_{order_id}_codes.txt"
        await context.bot.send_message(
            chat_id=chat_id,
            text=header_html + "📎 <b>Your codes are attached in a file:</b>",
            parse_mode=ParseMode.HTML,
        )
        await context.bot.send_document(chat_id=chat_id, document=bio)
        return

    body = "\n".join(codes)
    text_html = header_html + f"<pre>{html.escape(body)}</pre>"
    if len(text_html) <= TELEGRAM_TEXT_LIMIT:
        await context.bot.send_message(chat_id=chat_id, text=text_html, parse_mode=ParseMode.HTML)
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=header_html + "🎁 <b>Codes (part 1):</b>",
        parse_mode=ParseMode.HTML,
    )
    chunk = ""
    for c in codes:
        line = c + "\n"
        if len(chunk) + len(line) > 3000:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"<pre>{html.escape(chunk.rstrip())}</pre>",
                parse_mode=ParseMode.HTML,
            )
            chunk = line
        else:
            chunk += line
    if chunk.strip():
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"<pre>{html.escape(chunk.rstrip())}</pre>",
            parse_mode=ParseMode.HTML,
        )


# =========================
# Keyboards
# =========================
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
        if title in HIDDEN_CATEGORIES:
            continue
        rows.append([InlineKeyboardButton(f"{title} | {cnt}", callback_data=f"cat:{cid}")])

    if is_admin_user:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin:panel")])

    return InlineKeyboardMarkup(rows)


def product_stock(pid: int) -> int:
    cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
    return int(cur.fetchone()[0])


def kb_products(cid: int, uid: int = 0) -> InlineKeyboardMarkup:
    cur.execute("SELECT pid,title,price FROM products WHERE cid=? AND active=1", (cid,))
    items = cur.fetchall()
    items.sort(key=lambda r: extract_sort_value(r[1]))

    rows = []
    for pid, title, price in items:
        stock = product_stock(pid)
        eff_price = get_effective_product_price(uid, pid, float(price)) if uid else float(price)
        label = f"{title} | {money(eff_price)} | 📦{stock}"
        rows.append([InlineKeyboardButton(label[:62], callback_data=f"view:{pid}")])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back:cats")])
    return InlineKeyboardMarkup(rows)


def kb_product_view(pid: int, cid: int) -> InlineKeyboardMarkup:
    # ✅ Official Website removed
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 Buy Now", callback_data=f"buy:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cid}")],
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


def kb_support() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💬 Support Chat", url=to_tme(SUPPORT_CHAT))],
        [InlineKeyboardButton("📣 Support Channel", url=to_tme(SUPPORT_CHANNEL))],
    ]
    return InlineKeyboardMarkup(rows)


def kb_admin_panel(uid: int) -> InlineKeyboardMarkup:
    if admin_role(uid) == ROLE_HELPER:
        rows = [[InlineKeyboardButton("📥 Manual Orders", callback_data="admin:manuallist:0")]]
        if is_pos_agent(uid):
            rows.append([InlineKeyboardButton("🏪 POS Panel", callback_data="admin:pos:self")])
        return InlineKeyboardMarkup(rows)

    rows = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin:dash"), InlineKeyboardButton("👥 Customers", callback_data="admin:users:0")],
        [InlineKeyboardButton("📥 Manual Orders", callback_data="admin:manuallist:0"), InlineKeyboardButton("🧮 Daily Audit", callback_data="admin:dailyauditday:today")],
        [InlineKeyboardButton("🛍 Products Control", callback_data="admin:products"), InlineKeyboardButton("🛠 Manual Control", callback_data="admin:manualprices")],
        [InlineKeyboardButton("🏪 POS Manager", callback_data="admin:pos:owner"), InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcastall")],
        [InlineKeyboardButton("➕ Add Balance", callback_data="admin:addbal"), InlineKeyboardButton("➖ Take Balance", callback_data="admin:takebal")],
        [InlineKeyboardButton("👑 Admins", callback_data="admin:admins")],
    ]
    if is_pos_agent(uid):
        rows.insert(3, [InlineKeyboardButton("🏪 POS Panel", callback_data="admin:pos:self")])
    return InlineKeyboardMarkup(rows)


def kb_admin_products_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 List Products", callback_data="admin:listprod"), InlineKeyboardButton("💲 Set Price", callback_data="admin:setprice")],
        [InlineKeyboardButton("🎯 User Price", callback_data="admin:userprice"), InlineKeyboardButton("📌 User Prices", callback_data="admin:userpricelist")],
        [InlineKeyboardButton("⛔ Toggle Product", callback_data="admin:toggle"), InlineKeyboardButton("🗑 Delete Product", callback_data="admin:delprod")],
        [InlineKeyboardButton("➕ Add Category", callback_data="admin:addcat"), InlineKeyboardButton("➕ Add Product", callback_data="admin:addprod")],
        [InlineKeyboardButton("➕ Add Codes (text)", callback_data="admin:addcodes"), InlineKeyboardButton("📄 Add Codes (file)", callback_data="admin:addcodesfile")],
        [InlineKeyboardButton("🗑 Delete Category (FULL)", callback_data="admin:delcatfull")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")],
    ])


def kb_manual_prices_panel() -> InlineKeyboardMarkup:
    s1 = "ON ✅" if manual_flag_enabled("MANUAL_SHAHID_ENABLED") else "OFF ⛔"
    s2 = "ON ✅" if manual_flag_enabled("MANUAL_FF_ENABLED") else "OFF ⛔"
    s3 = "ON ✅" if manual_flag_enabled("SHAHID_MENA_3M_ENABLED") else "OFF ⛔"
    s4 = "ON ✅" if manual_flag_enabled("SHAHID_MENA_12M_ENABLED") else "OFF ⛔"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Edit Manual Prices", callback_data="admin:manualprices:edit")],
        [InlineKeyboardButton("🎯 User Manual Price", callback_data="admin:usermanualprice"), InlineKeyboardButton("📌 User Manual Prices", callback_data="admin:usermanualpricelist")],
        [InlineKeyboardButton(f"📺 Shahid {s1}", callback_data="admin:manualtoggle:MANUAL_SHAHID_ENABLED"), InlineKeyboardButton(f"💎 Free Fire {s2}", callback_data="admin:manualtoggle:MANUAL_FF_ENABLED")],
        [InlineKeyboardButton(f"Shahid 3M {s3}", callback_data="admin:manualtoggle:SHAHID_MENA_3M_ENABLED"), InlineKeyboardButton(f"Shahid 12M {s4}", callback_data="admin:manualtoggle:SHAHID_MENA_12M_ENABLED")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")],
    ])


def manual_prices_text() -> str:
    cur.execute("SELECT pkey, price FROM manual_prices ORDER BY pkey")
    rows = cur.fetchall()
    lines = ["🛠 *Manual Control*", "", "الأسعار الحالية:"]
    for k, p in rows:
        lines.append(f"• `{k}` = *{float(p):.3f}{CURRENCY}*")
    lines.append("")
    lines.append("Edit: `KEY | PRICE`")
    lines.append("User manual price: `user_id | KEY | PRICE`")
    return "\n".join(lines)[:3800]


def kb_pos_owner_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add POS", callback_data="admin:pos:add"), InlineKeyboardButton("➖ Remove POS", callback_data="admin:pos:remove")],
        [InlineKeyboardButton("📋 POS List", callback_data="admin:pos:list")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")],
    ])


def kb_pos_self_panel(uid: int) -> InlineKeyboardMarkup:
    profit = pos_profit_balance(uid)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Customer", callback_data="admin:pos:self:addcust"), InlineKeyboardButton("➖ Remove Customer", callback_data="admin:pos:self:delcust")],
        [InlineKeyboardButton("🎯 Child Price", callback_data="admin:pos:self:setprice"), InlineKeyboardButton("📌 Child Prices", callback_data="admin:pos:self:pricelist")],
        [InlineKeyboardButton("💳 Charge Child", callback_data="admin:pos:self:topup"), InlineKeyboardButton(f"💸 Profit Cashout {profit:.3f}{CURRENCY}", callback_data="admin:pos:self:cashout")],
        [InlineKeyboardButton("👥 My Customers", callback_data="admin:pos:self:listcust")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")],
    ])


def kb_admin_manual_view(mid: int, service: str, has_email: bool, has_pass: bool, has_player: bool) -> InlineKeyboardMarkup:
    rows = []

    copy_row = []
    if has_player:
        copy_row.append(InlineKeyboardButton("📋 Copy Player ID", callback_data=f"admin:copy:player:{mid}"))
    if has_email:
        copy_row.append(InlineKeyboardButton("📋 Copy Email", callback_data=f"admin:copy:email:{mid}"))
    if has_pass:
        copy_row.append(InlineKeyboardButton("📋 Copy Password", callback_data=f"admin:copy:pass:{mid}"))
    if copy_row:
        rows.append(copy_row)

    rows.append(
        [
            InlineKeyboardButton("✅ Approve ✅", callback_data=f"admin:manual:approve:{mid}"),
            InlineKeyboardButton("🚫 Reject 🚫", callback_data=f"admin:manual:rejectmenu:{mid}"),
        ]
    )

    if service == "FREEFIRE_MENA":
        rows.append(
            [
                InlineKeyboardButton("🟥 Wrong ID", callback_data=f"admin:manual:reject:{mid}:WRONG_ID"),
                InlineKeyboardButton("🟦 Other Server", callback_data=f"admin:manual:reject:{mid}:OTHER_SERVER"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("🟨 Not Available", callback_data=f"admin:manual:reject:{mid}:NOT_AVAILABLE"),
                InlineKeyboardButton("✍️ Custom", callback_data=f"admin:manual:reject:{mid}:CUSTOM"),
            ]
        )
    else:
        rows.append([InlineKeyboardButton("✍️ Custom Reject", callback_data=f"admin:manual:reject:{mid}:CUSTOM")])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:manuallist:0")])
    rows.append([InlineKeyboardButton("👑 Admin Home", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)


def kb_admin_users_page(page: int, total_pages: int, rows: List[Tuple[int, str, str, float, int, float, int, float, float, int]]) -> InlineKeyboardMarkup:
    buttons = []
    for uid, username, first_name, bal, oc, osp, mc, msp, dep, suspended in rows:
        uname = f"@{username}" if username else ""
        name = first_name or ""
        sflag = " ⛔" if int(suspended) == 1 else ""
        label = f"👤 {uid}{sflag} {uname} {name}".strip()
        sub = f" | 💰{bal:.3f}{CURRENCY} | 🧾{oc} | 🔥{osp:.3f}{CURRENCY}"
        text = (label + sub)[:58]
        buttons.append([InlineKeyboardButton(text, callback_data=f"admin:user:view:{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:users:{page-1}"))
    nav.append(InlineKeyboardButton(f"Page {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️ Next", callback_data=f"admin:users:{page+1}"))
    buttons.append(nav)

    buttons.append([InlineKeyboardButton("👑 Admin Home", callback_data="admin:panel")])
    return InlineKeyboardMarkup(buttons)


def kb_admin_user_view(uid: int, suspended: int) -> InlineKeyboardMarkup:
    # ✅ Suspend/Unsuspend (cannot for admins)
    can_suspend = (not is_admin_any(uid)) and (uid != ADMIN_ID)

    rows = [
        [
            InlineKeyboardButton("➕ Add Balance", callback_data=f"admin:user:addbal:{uid}"),
            InlineKeyboardButton("➖ Take Balance", callback_data=f"admin:user:takebal:{uid}"),
        ],
        [
            InlineKeyboardButton("📄 Export Report", callback_data=f"admin:user:export:{uid}"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin:users:0"),
        ],
    ]

    if can_suspend:
        if int(suspended) == 1:
            rows.insert(1, [InlineKeyboardButton("✅ Unsuspend User", callback_data=f"admin:user:unsuspend:{uid}")])
        else:
            rows.insert(1, [InlineKeyboardButton("⛔ Suspend User", callback_data=f"admin:user:suspend:{uid}")])

    rows.append([InlineKeyboardButton("👑 Admin Home", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)


def kb_qty_cancel(cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")],
        ]
    )


# =========================
# Manual Order (Shahid + FreeFire MENA Cart)
# =========================
FF_PACKS = [
    ("FF_100", "100+10", 110),
    ("FF_210", "210+21", 231),
    ("FF_530", "530+53", 583),
    ("FF_1080", "1080+108", 1188),
    ("FF_2200", "2200+220", 2420),
]


def _ff_pack(sku: str):
    for x in FF_PACKS:
        if x[0] == sku:
            return x
    return None


def _ff_cart_get(context):
    cart = context.user_data.get(UD_FF_CART)
    if not isinstance(cart, dict):
        cart = {}
        context.user_data[UD_FF_CART] = cart
    return cart


def _ff_calc_totals(cart: Dict[str, int]):
    total_price = 0.0
    total_diamonds = 0
    lines = []
    for sku, qty in cart.items():
        if qty <= 0:
            continue
        pack = _ff_pack(sku)
        if not pack:
            continue
        _, title, diamonds = pack
        price = get_effective_manual_price(uid, sku, MANUAL_PRICE_DEFAULTS.get(sku, 0.0))
        total_price += float(price) * qty
        total_diamonds += diamonds * qty
        lines.append((title, qty, float(price), diamonds))

    order_map = {t: i for i, (_, t, _) in enumerate(FF_PACKS)}
    lines.sort(key=lambda x: order_map.get(x[0], 999))
    return total_price, total_diamonds, lines


def kb_manual_services() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📺 Shahid", callback_data="manual:shahid")],
            [InlineKeyboardButton("💎 Free Fire (MENA)", callback_data="manual:ff")],
            [InlineKeyboardButton("⬅️ Back", callback_data="goto:cats")],
        ]
    )


def kb_shahid_plans() -> InlineKeyboardMarkup:
    p3 = get_manual_price("SHAHID_MENA_3M", MANUAL_PRICE_DEFAULTS["SHAHID_MENA_3M"])
    p12 = get_manual_price("SHAHID_MENA_12M", MANUAL_PRICE_DEFAULTS["SHAHID_MENA_12M"])
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"Shahid [MENA] | 3 Month | {p3:.3f}{CURRENCY}", callback_data="manual:shahid:MENA_3M")],
            [InlineKeyboardButton(f"Shahid [MENA] | 12 Month | {p12:.3f}{CURRENCY}", callback_data="manual:shahid:MENA_12M")],
            [InlineKeyboardButton("⬅️ Back", callback_data="manual:services")],
            [InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")],
        ]
    )


def ff_menu_text() -> str:
    return (
        "💎 *Free Fire (MENA)*\n\n"
        "🛒 Add packs to cart ثم Checkout.\n"
        "⏱ Delivery: *1-5 minutes*\n\n"
        "✅ تقدر تمسح السلة أو تكمل الدفع\n\n"
        + manual_hours_text()
    )


def kb_ff_menu(context) -> InlineKeyboardMarkup:
    cart = _ff_cart_get(context)
    uid = context.user_data.get("_manual_uid", 0)
    rows = []
    for sku, title, _ in FF_PACKS:
        qty = int(cart.get(sku, 0))
        suffix = f"  🧺[{qty}]" if qty > 0 else ""
        price = get_effective_manual_price(uid, sku, MANUAL_PRICE_DEFAULTS.get(sku, 0.0))
        rows.append([InlineKeyboardButton(f"{title} 💎 | {float(price):.3f}{CURRENCY}{suffix}", callback_data=f"manual:ff:add:{sku}")])

    rows.append([InlineKeyboardButton("🗑 Clear Cart", callback_data="manual:ff:clear")])
    rows.append([InlineKeyboardButton("✅ Proceed to Checkout", callback_data="manual:ff:checkout")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="manual:services")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")])
    return InlineKeyboardMarkup(rows)


def ff_checkout_text(context) -> str:
    cart = _ff_cart_get(context)
    context._user_id_for_manual = context.user_data.get("_manual_uid", 0)
    total_price, total_diamonds, lines = _ff_calc_totals(cart)
    if not lines:
        return "🛒 Cart is empty.\nAdd items first."

    text_lines = ["🧺 *Your Cart — Free Fire* ⚡\n"]
    for title, qty, _, _ in lines:
        text_lines.append(f"💎 {title} (x{qty})")

    text_lines.append("")
    text_lines.append(f"💎 Total Diamonds: *{total_diamonds}*")
    text_lines.append(f"💰 Total: *{total_price:.3f}{CURRENCY}*")
    text_lines.append("")
    text_lines.append("🆔 Send Player ID (NUMBERS only)\n❌ /cancel to stop")
    return "\n".join(text_lines)


# =========================
# Pages
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    ensure_user_exists(ADMIN_ID)
    await update.message.reply_text("✅ Bot is online! 🚀", reply_markup=REPLY_MENU)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and must_block_user(update):
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

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
    owner_pos = pos_customer_owner(uid)
    if owner_pos is not None:
        text = (
            "💰 *Wallet*\n\n"
            f"👤 Name: *{(u.first_name or 'User')}*\n"
            f"🆔 ID: `{uid}`\n"
            f"💎 Balance: *{bal:.3f}* {CURRENCY}\n\n"
            f"🏪 This account is under POS: `{owner_pos}`\n"
            "لشحن الرصيد تواصل مع نقطة البيع التابعة لك."
        )
        if update.message:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=REPLY_MENU)
        else:
            await update.callback_query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="goto:cats")]]),
            )
        return

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
        cur.execute(
            "SELECT id,qty,product_title,total,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC",
            (uid,),
        )
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


async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, rng: str = "all", page: int = 0):
    if update.effective_user and must_block_user(update):
        if update.message:
            return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())
        return await update.callback_query.edit_message_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    uid = update.effective_user.id
    context.user_data[UD_ORD_RNG] = rng

    rows = _orders_query(uid, rng)
    text, total_pages = _format_orders_page(rows, page)

    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_orders_filters(page, total_pages))
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_orders_filters(page, total_pages))


from telegram.helpers import escape_markdown

def md(x: str) -> str:
    return escape_markdown(x or "", version=1)

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "☎️ *Support*\n\n"
        f"📞 Phone: `{md(SUPPORT_PHONE)}`\n"
        f"💬 Chat: {md(SUPPORT_CHAT)}\n"
        f"📣 Channel: {md(SUPPORT_CHANNEL)}\n\n"
        "اختر 👇"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_support())
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_support())


def smart_reply(msg: str) -> Optional[str]:
    m = msg.lower()
    if any(x in m for x in ["price", "سعر", "كم", "ثمن"]):
        return "💡 الأسعار تظهر داخل 🛒 Our Products → اختر القسم."
    if any(x in m for x in ["balance", "رصيد", "wallet", "محفظة"]):
        return "💡 اضغط 💰 My Balance لمشاهدة الرصيد وطرق الشحن."
    if any(x in m for x in ["order", "طلب", "orders", "طلباتي"]):
        return "💡 اضغط 📦 My Orders لمشاهدة الطلبات."
    if any(x in m for x in ["usdt", "trc20", "bep20", "txid"]):
        return "💡 من 💰 My Balance اختر طريقة الشحن ثم اضغط ✅ I Have Paid وأرسل Amount | TXID."
    return None


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)

    # block suspended users (except admins)
    if must_block_user(update):
        t = (update.message.text or "").strip()
        if t in ("☎️ Contact Support",):
            return await show_support(update, context)
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    t = (update.message.text or "").strip()

    if t == "🛒 Our Products":
        return await show_categories(update, context)
    if t == "💰 My Balance":
        return await show_balance(update, context)
    if t == "📦 My Orders":
        return await show_orders(update, context, rng=context.user_data.get(UD_ORD_RNG) or "all", page=0)
    if t == "☎️ Contact Support":
        return await show_support(update, context)
    if t == "⚡ Manual Order":
        # ✅ enforce hours
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await update.message.reply_text(
                "⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=REPLY_MENU,
            )
        return await update.message.reply_text("⚡ *MANUAL ORDER*\nSelect a service:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_services())

    hint = smart_reply(t)
    if hint:
        return await update.message.reply_text(hint, reply_markup=REPLY_MENU)

    await update.message.reply_text("Use the menu 👇", reply_markup=REPLY_MENU)


# =========================
# Qty input
# =========================
async def qty_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if must_block_user(update):
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    if txt in MENU_BUTTONS:
        context.user_data.pop(UD_PID, None)
        context.user_data.pop(UD_CID, None)
        context.user_data.pop(UD_QTY_MAX, None)
        context.user_data.pop(UD_ORDER_CLIENT_REF, None)
        return await menu_router(update, context)

    if txt.lower() in ("/cancel", "cancel") or txt in ADMIN_TEXT_EXIT:
        context.user_data.pop(UD_PID, None)
        context.user_data.pop(UD_CID, None)
        context.user_data.pop(UD_QTY_MAX, None)
        context.user_data.pop(UD_ORDER_CLIENT_REF, None)
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
    price = get_effective_product_price(update.effective_user.id, pid, float(price))
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
# Topup details
# =========================
async def topup_details_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if must_block_user(update):
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    if txt in MENU_BUTTONS:
        context.user_data.pop(UD_DEP_ID, None)
        return await menu_router(update, context)

    if txt.lower() in ("/cancel", "cancel") or txt in ADMIN_TEXT_EXIT:
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
# Manual: Shahid Email/Pass
# =========================
async def manual_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if must_block_user(update):
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    if txt in MENU_BUTTONS:
        for k in [UD_MANUAL_SERVICE, UD_MANUAL_PLAN, UD_MANUAL_PRICE, UD_MANUAL_PLAN_TITLE, UD_MANUAL_EMAIL]:
            context.user_data.pop(k, None)
        return await menu_router(update, context)

    if txt.lower() in ("/cancel", "cancel") or txt in ADMIN_TEXT_EXIT:
        for k in [UD_MANUAL_SERVICE, UD_MANUAL_PLAN, UD_MANUAL_PRICE, UD_MANUAL_PLAN_TITLE, UD_MANUAL_EMAIL]:
            context.user_data.pop(k, None)
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", txt):
        return await update.message.reply_text("❌ Send a valid Gmail.\nExample: example@gmail.com")

    context.user_data[UD_MANUAL_EMAIL] = txt
    await update.message.reply_text("🔐 Now send temporary password:\n\n/cancel to stop")
    return ST_MANUAL_PASS


async def manual_pass_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = (update.message.text or "").strip()

    if must_block_user(update):
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    if pwd in MENU_BUTTONS:
        for k in [UD_MANUAL_SERVICE, UD_MANUAL_PLAN, UD_MANUAL_PRICE, UD_MANUAL_PLAN_TITLE, UD_MANUAL_EMAIL]:
            context.user_data.pop(k, None)
        return await menu_router(update, context)

    if pwd.lower() in ("/cancel", "cancel") or pwd in ADMIN_TEXT_EXIT:
        for k in [UD_MANUAL_SERVICE, UD_MANUAL_PLAN, UD_MANUAL_PRICE, UD_MANUAL_PLAN_TITLE, UD_MANUAL_EMAIL]:
            context.user_data.pop(k, None)
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    uid = update.effective_user.id
    service = context.user_data.get(UD_MANUAL_SERVICE)
    price = float(context.user_data.get(UD_MANUAL_PRICE, 0))
    email = context.user_data.get(UD_MANUAL_EMAIL)
    plan_title = context.user_data.get(UD_MANUAL_PLAN_TITLE, "")

    if service != "SHAHID" or price <= 0 or not email or not plan_title:
        await update.message.reply_text("❌ Session expired. Open Manual Order again.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    bal_before = get_balance(uid)

    if not charge_balance(uid, price):
        bal = get_balance(uid)
        missing = price - bal
        await update.message.reply_text(
            f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {price:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
            reply_markup=kb_topup_now(),
        )
        return ConversationHandler.END

    bal_after = get_balance(uid)

    cur.execute(
        """
        INSERT INTO manual_orders(user_id,service,plan_title,price,email,password,status)
        VALUES(?,?,?,?,?,?,'PENDING')
        """,
        (uid, "SHAHID", plan_title, price, email, pwd[:250]),
    )
    con.commit()
    mid = cur.lastrowid

    await update.message.reply_text(
        f"✅ Manual order created!\n"
        f"🧾 Order ID: {mid}\n"
        f"📺 Service: {plan_title}\n"
        f"💵 Paid: {price:.3f} {CURRENCY}\n\n"
        f"💳 Balance before: {bal_before:.3f} {CURRENCY}\n"
        f"✅ Balance after: {bal_after:.3f} {CURRENCY}\n\n"
        f"⏳ سيتم التنفيذ قريباً ✅",
        reply_markup=REPLY_MENU,
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "⚡ *MANUAL ORDER (SHAHID)*\n"
                f"🧾 Manual ID: *{mid}*\n"
                f"👤 User: `{uid}`\n"
                f"📦 Plan: *{plan_title}*\n"
                f"💵 Price: *{price:.3f} {CURRENCY}*\n"
                f"🟨 Email: `{email}`\n"
                f"🟥 Password: `{pwd}`\n"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.exception("Failed to notify admin about Shahid manual order %s: %s", mid, e)

    for k in [UD_MANUAL_SERVICE, UD_MANUAL_PLAN, UD_MANUAL_PRICE, UD_MANUAL_PLAN_TITLE, UD_MANUAL_EMAIL]:
        context.user_data.pop(k, None)
    return ConversationHandler.END


# =========================
# Manual: FreeFire PlayerID
# =========================
async def ff_playerid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if must_block_user(update):
        return await update.message.reply_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())

    if txt in MENU_BUTTONS:
        context.user_data.pop(UD_FF_CART, None)
        context.user_data.pop(UD_FF_TOTAL, None)
        context.user_data.pop("ff_total_diamonds", None)
        return await menu_router(update, context)

    if txt.lower() in ("/cancel", "cancel") or txt in ADMIN_TEXT_EXIT:
        context.user_data.pop(UD_FF_CART, None)
        context.user_data.pop(UD_FF_TOTAL, None)
        context.user_data.pop("ff_total_diamonds", None)
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    player_id = txt.replace(" ", "")
    if not player_id.isdigit():
        return await update.message.reply_text("❌ Player ID must be NUMBERS only.\nExample: 123456789")
    if len(player_id) < 6:
        return await update.message.reply_text("❌ Player ID is too short.\nExample: 123456789")

    uid = update.effective_user.id
    cart = _ff_cart_get(context)
    total_price, total_diamonds, lines = _ff_calc_totals(cart)

    if not lines or total_price <= 0:
        await update.message.reply_text("🛒 Cart is empty. Open Manual Order again.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    bal_before = get_balance(uid)

    if not charge_balance(uid, total_price):
        bal = get_balance(uid)
        missing = total_price - bal
        await update.message.reply_text(
            f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total_price:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
            reply_markup=kb_topup_now(),
        )
        return ConversationHandler.END

    bal_after = get_balance(uid)

    note_lines = []
    for title, qty, price, diamonds in lines:
        note_lines.append(f"{title} x{qty} | {price:.3f}{CURRENCY} | diamonds_each={diamonds}")
    note = "\n".join(note_lines)

    plan_title = f"Free Fire (MENA) | Total Diamonds: {total_diamonds}"
    cur.execute(
        """
        INSERT INTO manual_orders(user_id,service,plan_title,price,player_id,note,status)
        VALUES(?,?,?,?,?,?,'PENDING')
        """,
        (uid, "FREEFIRE_MENA", plan_title, float(total_price), player_id[:120], note[:4000]),
    )
    con.commit()
    mid = cur.lastrowid

    await update.message.reply_text(
        f"✅ Manual order created!\n"
        f"🧾 Order ID: {mid}\n"
        f"🆔 Player ID: {player_id}\n"
        f"💎 Diamonds: {total_diamonds}\n"
        f"💵 Paid: {total_price:.3f} {CURRENCY}\n\n"
        f"💳 Balance before: {bal_before:.3f} {CURRENCY}\n"
        f"✅ Balance after: {bal_after:.3f} {CURRENCY}\n\n"
        f"⏳ سيتم الشحن قريباً ✅",
        reply_markup=REPLY_MENU,
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "⚡ *MANUAL ORDER (FREE FIRE MENA)*\n"
                f"🧾 Manual ID: *{mid}*\n"
                f"👤 User ID: `{uid}`\n"
                f"🆔 Player ID: `{player_id}`\n"
                f"💎 Diamonds: *{total_diamonds}*\n"
                f"💵 Total: *{total_price:.3f} {CURRENCY}*\n\n"
                f"🧺 Cart:\n`{note}`"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.exception("Failed to notify admin about FF manual order %s: %s", mid, e)

    context.user_data.pop(UD_FF_CART, None)
    context.user_data.pop(UD_FF_TOTAL, None)
    context.user_data.pop("ff_total_diamonds", None)
    return ConversationHandler.END


# =========================
# Admin: Customers helpers
# =========================
def _users_page(page: int, page_size: int = 10) -> Tuple[List[Tuple], int]:
    cur.execute("SELECT COUNT(*) FROM users")
    total = int(cur.fetchone()[0])
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    off = page * page_size

    cur.execute(
        "SELECT user_id, username, first_name, balance, suspended FROM users ORDER BY user_id LIMIT ? OFFSET ?",
        (page_size, off),
    )
    base_rows = cur.fetchall()

    out = []
    for uid, username, first_name, bal, suspended in base_rows:
        uid = int(uid)
        cur.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders WHERE user_id=? AND status='COMPLETED'", (uid,))
        oc, osp = cur.fetchone()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(price),0) FROM manual_orders WHERE user_id=? AND status='COMPLETED'", (uid,))
        mc, msp = cur.fetchone()
        cur.execute("SELECT COALESCE(SUM(amount),0) FROM deposits WHERE user_id=? AND status='APPROVED'", (uid,))
        dep = cur.fetchone()[0] or 0.0
        out.append((uid, username or "", first_name or "", float(bal or 0), int(oc or 0), float(osp or 0), int(mc or 0), float(msp or 0), float(dep or 0), int(suspended or 0)))
    return out, total_pages


def _user_report_text(uid: int, limit_each: int = 10) -> str:
    ensure_user_exists(uid)
    cur.execute("SELECT username, first_name, balance, suspended FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone() or ("", "", 0.0, 0)
    username, first_name, bal, suspended = row[0] or "", row[1] or "", float(row[2] or 0.0), int(row[3] or 0)

    cur.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders WHERE user_id=? AND status='COMPLETED'", (uid,))
    oc, osp = cur.fetchone()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(price),0) FROM manual_orders WHERE user_id=? AND status='COMPLETED'", (uid,))
    mc, msp = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM deposits WHERE user_id=? AND status='APPROVED'", (uid,))
    dep = cur.fetchone()[0] or 0.0

    lines = []
    lines.append("👥 CUSTOMER REPORT")
    lines.append(f"🆔 User ID: {uid}")
    lines.append(f"⛔ Suspended: {'YES' if suspended else 'NO'}")
    if username:
        lines.append(f"👤 Username: @{username}")
    if first_name:
        lines.append(f"🧾 Name: {first_name}")
    lines.append(f"💰 Balance: {bal:.3f}{CURRENCY}")
    lines.append("")
    lines.append(f"🧾 Orders Completed: {int(oc or 0)} | Spent: {float(osp or 0):.3f}{CURRENCY}")
    lines.append(f"⚡ Manual Completed: {int(mc or 0)} | Spent: {float(msp or 0):.3f}{CURRENCY}")
    lines.append(f"💳 Deposits Approved: {float(dep or 0):.3f}{CURRENCY}")
    lines.append("\n--- LAST ORDERS ---")
    cur.execute(
        "SELECT id, product_title, total, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit_each),
    )
    for oid, title, total, status, created_at in cur.fetchall():
        lines.append(f"#{oid} | {status} | {float(total):.3f}{CURRENCY} | {created_at} | {title}")

    lines.append("\n--- LAST MANUAL ---")
    cur.execute(
        "SELECT id, service, plan_title, price, status, created_at, COALESCE(approved_by,'') FROM manual_orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit_each),
    )
    for mid, service, plan_title, price, status, created_at, approved_by in cur.fetchall():
        ab = f" | approved_by={approved_by}" if approved_by else ""
        lines.append(f"M#{mid} | {status} | {float(price):.3f}{CURRENCY} | {created_at} | {service} | {plan_title}{ab}")

    lines.append("\n--- LAST DEPOSITS ---")
    cur.execute(
        "SELECT id, method, amount, status, created_at, txid FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit_each),
    )
    for did, method, amount, status, created_at, txid in cur.fetchall():
        a = "None" if amount is None else f"{float(amount):.3f}{CURRENCY}"
        t = (txid or "")[:18] + ("..." if (txid and len(txid) > 18) else "")
        lines.append(f"D#{did} | {status} | {a} | {created_at} | {method} | {t}")

    return "\n".join(lines)


def _dashboard_text() -> str:
    cur.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders WHERE status='COMPLETED'")
    oc, osp = cur.fetchone()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(price),0) FROM manual_orders WHERE status='COMPLETED'")
    mc, msp = cur.fetchone()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM deposits WHERE status='APPROVED'")
    dc, dep_sum = cur.fetchone()

    cur.execute("SELECT COALESCE(SUM(CASE WHEN used=0 THEN 1 ELSE 0 END),0) FROM codes")
    stock_all = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT product_title, COALESCE(SUM(total),0) as rev
        FROM orders
        WHERE status='COMPLETED'
        GROUP BY product_title
        ORDER BY rev DESC
        LIMIT 5
        """
    )
    top = cur.fetchall()

    lines = []
    lines.append("📊 *Dashboard*")
    lines.append("")
    lines.append(f"🧾 Completed Orders: *{int(oc or 0)}*  | 💰 Revenue: *{float(osp or 0):.3f}{CURRENCY}*")
    lines.append(f"⚡ Completed Manual: *{int(mc or 0)}*  | 💰 Revenue: *{float(msp or 0):.3f}{CURRENCY}*")
    lines.append(f"💳 Approved Deposits: *{int(dc or 0)}* | 💵 Total: *{float(dep_sum or 0):.3f}{CURRENCY}*")
    lines.append("")
    lines.append(f"📦 Total Stock Codes (unused): *{stock_all}*")
    lines.append("")
    lines.append("🏆 *Top Products (Revenue)*")
    if not top:
        lines.append("— No data yet.")
    else:
        for title, rev in top:
            lines.append(f"• {title[:40]} — *{float(rev):.3f}{CURRENCY}*")
    lines.append("")
    lines.append("✅ Everything running smooth 🚀")
    return "\n".join(lines)[:3800]


# =========================
# Code validation rules for Admin adding codes
# =========================
FF_CODE_RE = re.compile(r"^\d{16}$")
PUBG_CODE_RE = re.compile(r"^[A-Za-z0-9]{18}$")


def _pid_code_rule(pid: int) -> Optional[str]:
    """
    Return 'FF16' or 'PUBG18' or None based on category/product title.
    """
    cur.execute(
        """
        SELECT p.title, c.title
        FROM products p
        JOIN categories c ON c.cid=p.cid
        WHERE p.pid=?
        """,
        (pid,),
    )
    row = cur.fetchone()
    if not row:
        return None
    ptitle = (row[0] or "").upper()
    ctitle = (row[1] or "").upper()

    blob = f"{ptitle} {ctitle}"
    if "FREE FIRE" in blob or "GARENA" in blob:
        return "FF16"
    if "PUBG" in blob:
        return "PUBG18"
    return None


def validate_codes_for_pid(pid: int, codes: List[str]) -> Tuple[bool, str]:
    rule = _pid_code_rule(pid)
    if not rule:
        return True, ""

    bad = []
    for c in codes:
        cc = (c or "").strip().replace(" ", "")
        if rule == "FF16":
            if not FF_CODE_RE.match(cc):
                bad.append(cc)
        elif rule == "PUBG18":
            if not PUBG_CODE_RE.match(cc):
                bad.append(cc)

    if not bad:
        return True, ""

    # determine if too short/long for first bad
    sample = bad[0]
    if rule == "FF16":
        msg = f"❌ Free Fire code must be 16 digits فقط.\nمثال: 1234567890123456\nBad sample: {sample}"
    else:
        msg = f"❌ PUBG code must be 18 characters (A-Z a-z 0-9).\nBad sample: {sample}"
    return False, msg


# =========================
# Callback handler
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    # block suspended users (except admins)
    if update.effective_user and must_block_user(update):
        if data in ("goto:cats", "goto:balance", "goto:topup", "back:cats"):
            return await q.edit_message_text("⛔ حسابك موقوف. تواصل مع الدعم.", reply_markup=kb_support())
        return await q.answer("Account suspended", show_alert=True)

    if data == "noop":
        return

    if data == "goto:cats":
        return await show_categories(update, context)
    if data == "goto:balance" or data == "goto:topup":
        return await show_balance(update, context)

    # Manual nav
    if data == "manual:back" or data == "manual:services":
        return await q.edit_message_text("⚡ *MANUAL ORDER*\nSelect a service:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_services())

    if data == "manual:shahid":
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(), parse_mode=ParseMode.MARKDOWN)
        text = (
            "📺 *Shahid*\n\n"
            "📩 المطلوب منك:\n"
            "➡️ Gmail جديد\n"
            "➡️ Password مؤقت\n\n"
            + manual_hours_text()
        )
        return await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_shahid_plans())

    if data.startswith("manual:shahid:"):
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(), parse_mode=ParseMode.MARKDOWN)

        plan = data.split(":")[2]
        if plan == "MENA_3M":
            plan_title = "Shahid [MENA] | 3 Month"
            price = get_effective_manual_price(update.effective_user.id, "SHAHID_MENA_3M", MANUAL_PRICE_DEFAULTS["SHAHID_MENA_3M"])
        elif plan == "MENA_12M":
            plan_title = "Shahid [MENA] | 12 Month"
            price = get_effective_manual_price(update.effective_user.id, "SHAHID_MENA_12M", MANUAL_PRICE_DEFAULTS["SHAHID_MENA_12M"])
        else:
            return await q.edit_message_text("❌ Unknown plan.")

        uid = update.effective_user.id
        bal = get_balance(uid)
        if bal + 1e-9 < price:
            missing = price - bal
            return await q.edit_message_text(
                f"❌ Insufficient balance.\n\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {price:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}\n\nClick below to top up 👇",
                reply_markup=kb_topup_now(),
            )

        context.user_data[UD_MANUAL_SERVICE] = "SHAHID"
        context.user_data[UD_MANUAL_PLAN] = plan
        context.user_data[UD_MANUAL_PRICE] = float(price)
        context.user_data[UD_MANUAL_PLAN_TITLE] = plan_title

        await q.edit_message_text(
            f"✅ Selected: *{plan_title}*\n💵 Price: *{float(price):.3f} {CURRENCY}*\n\n📩 Send NEW Gmail now:\n\n/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_MANUAL_EMAIL

    if data == "manual:ff":
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(), parse_mode=ParseMode.MARKDOWN)
        context.user_data["_manual_uid"] = update.effective_user.id
        return await q.edit_message_text(ff_menu_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_ff_menu(context))

    if data.startswith("manual:ff:add:"):
        sku = data.split(":")[3]
        if not _ff_pack(sku):
            return await q.edit_message_text("❌ Unknown pack.", reply_markup=kb_ff_menu(context))
        cart = _ff_cart_get(context)
        cart[sku] = int(cart.get(sku, 0)) + 1
        context.user_data[UD_FF_CART] = cart
        context.user_data["_manual_uid"] = update.effective_user.id
        return await q.edit_message_text(ff_menu_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_ff_menu(context))

    if data == "manual:ff:clear":
        context.user_data[UD_FF_CART] = {}
        context.user_data.pop(UD_FF_TOTAL, None)
        context.user_data.pop("ff_total_diamonds", None)
        context.user_data["_manual_uid"] = update.effective_user.id
        return await q.edit_message_text(ff_menu_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_ff_menu(context))

    if data == "manual:ff:checkout":
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(), parse_mode=ParseMode.MARKDOWN)

        cart = _ff_cart_get(context)
        total_price, _, lines = _ff_calc_totals(cart)
        if not lines:
            return await q.edit_message_text("🛒 Your Cart is empty.\nAdd items first.", reply_markup=kb_ff_menu(context))

        uid = update.effective_user.id
        bal = get_balance(uid)
        if bal + 1e-9 < total_price:
            missing = total_price - bal
            return await q.edit_message_text(
                f"❌ Insufficient balance.\n\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total_price:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}\n\nClick below to top up 👇",
                reply_markup=kb_topup_now(),
            )

        await q.edit_message_text(ff_checkout_text(context), parse_mode=ParseMode.MARKDOWN)
        return ST_FF_PLAYERID

    # =========================
    # Admin panel
    # =========================
    if data == "admin:panel":
        if not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("👑 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_panel(update.effective_user.id))

    if data == "admin:dash":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text(_dashboard_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_panel(update.effective_user.id))

    if data == "admin:admins":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute("SELECT user_id, role FROM admins ORDER BY role DESC, user_id ASC")
        rows = cur.fetchall()
        lines = ["👑 *Admins*\n", "Send:\n`addadmin | user_id`\n`deladmin | user_id`\n"]
        for uid, role in rows:
            lines.append(f"• `{uid}` — *{role}*")
        context.user_data[UD_ADMIN_MODE] = "admins_manage"
        await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:broadcastall":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "broadcast_all"
        await q.edit_message_text("📢 *Broadcast to all users*\n\nSend the message now.\n\n/cancel to stop", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:products":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("🛍 *Products Control*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_products_panel())

    if data == "admin:userprice":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "userprice"
        await q.edit_message_text("🎯 *User Custom Price*\n\nSet/Update:\n`user_id | pid | price`\n\nDelete:\n`del | user_id | pid`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:userpricelist":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute("SELECT user_id, pid, price FROM user_product_prices ORDER BY user_id, pid LIMIT 200")
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("📌 No custom user prices found.", reply_markup=kb_admin_products_panel())
        lines = ["📌 *User Prices*", ""]
        for u1, pid, price in rows:
            lines.append(f"• User `{u1}` | PID `{pid}` | *{float(price):.3f}{CURRENCY}*")
        return await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_products_panel())

    if data == "admin:manualprices":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text(manual_prices_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_prices_panel())

    if data == "admin:manualprices:edit":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "setmanualprice"
        await q.edit_message_text(manual_prices_text() + "\n\nSend now: `KEY | PRICE`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:usermanualprice":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "usermanualprice"
        await q.edit_message_text("🎯 *User Manual Price*\n\nSet/Update:\n`user_id | KEY | price`\n\nDelete:\n`del | user_id | KEY`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:usermanualpricelist":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute("SELECT user_id, pkey, price FROM user_manual_prices ORDER BY user_id, pkey LIMIT 200")
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("📌 No user manual prices found.", reply_markup=kb_manual_prices_panel())
        lines = ["📌 *User Manual Prices*", ""]
        for u1, key, price in rows:
            lines.append(f"• User `{u1}` | `{key}` | *{float(price):.3f}{CURRENCY}*")
        return await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_prices_panel())

    if data.startswith("admin:manualtoggle:"):
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        key = data.split(":", 2)[2]
        set_manual_flag(key, not manual_flag_enabled(key))
        return await q.edit_message_text(manual_prices_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_prices_panel())

    if data == "admin:pos:owner":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("🏪 *POS Manager*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_owner_panel())

    if data == "admin:pos:add":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "pos_add"
        await q.edit_message_text("Send admin user id to make him POS:\n`user_id`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:pos:remove":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "pos_remove"
        await q.edit_message_text("Send POS user id to remove:\n`user_id`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:pos:list":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute("SELECT user_id, profit_balance FROM pos_agents ORDER BY user_id")
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("No POS found.", reply_markup=kb_pos_owner_panel())
        lines = ["🏪 *POS List*", ""]
        for puid, pbal in rows:
            cur.execute("SELECT COUNT(*) FROM pos_customers WHERE pos_user_id=?", (puid,))
            cc = int(cur.fetchone()[0])
            lines.append(f"• POS `{puid}` | customers={cc} | profit={float(pbal):.3f}{CURRENCY}")
        return await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_owner_panel())

    if data == "admin:pos:self":
        if not is_pos_agent(update.effective_user.id):
            return await q.edit_message_text("❌ You are not POS.")
        return await q.edit_message_text("🏪 *POS Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_self_panel(update.effective_user.id))

    if data == "admin:pos:self:addcust":
        if not is_pos_agent(update.effective_user.id):
            return await q.edit_message_text("❌ You are not POS.")
        context.user_data[UD_ADMIN_MODE] = "pos_self_addcust"
        await q.edit_message_text("Send customer id to attach under you:\n`user_id`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:pos:self:delcust":
        if not is_pos_agent(update.effective_user.id):
            return await q.edit_message_text("❌ You are not POS.")
        context.user_data[UD_ADMIN_MODE] = "pos_self_delcust"
        await q.edit_message_text("Send customer id to remove from your POS:\n`user_id`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:pos:self:setprice":
        if not is_pos_agent(update.effective_user.id):
            return await q.edit_message_text("❌ You are not POS.")
        context.user_data[UD_ADMIN_MODE] = "pos_self_price"
        await q.edit_message_text("Set child product price:\n`customer_id | pid | price`\nDelete:\n`del | customer_id | pid`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:pos:self:topup":
        if not is_pos_agent(update.effective_user.id):
            return await q.edit_message_text("❌ You are not POS.")
        context.user_data[UD_ADMIN_MODE] = "pos_self_topup"
        await q.edit_message_text("Charge child from your balance:\n`customer_id | amount`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT

    if data == "admin:pos:self:pricelist":
        if not is_pos_agent(update.effective_user.id):
            return await q.edit_message_text("❌ You are not POS.")
        cur.execute("SELECT customer_id, pid, price FROM pos_child_prices WHERE pos_user_id=? ORDER BY customer_id, pid LIMIT 200", (update.effective_user.id,))
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("No child prices found.", reply_markup=kb_pos_self_panel(update.effective_user.id))
        lines = ["📌 *Child Prices*", ""]
        for cuid, pid, price in rows:
            lines.append(f"• Customer `{cuid}` | PID `{pid}` | *{float(price):.3f}{CURRENCY}*")
        return await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_self_panel(update.effective_user.id))

    if data == "admin:pos:self:listcust":
        if not is_pos_agent(update.effective_user.id):
            return await q.edit_message_text("❌ You are not POS.")
        cur.execute("SELECT customer_id FROM pos_customers WHERE pos_user_id=? ORDER BY customer_id", (update.effective_user.id,))
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("No customers under you.", reply_markup=kb_pos_self_panel(update.effective_user.id))
        lines = ["👥 *My Customers*", ""]
        for (cuid,) in rows:
            lines.append(f"• `{cuid}`")
        return await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_self_panel(update.effective_user.id))

    if data == "admin:pos:self:cashout":
        if not is_pos_agent(update.effective_user.id):
            return await q.edit_message_text("❌ You are not POS.")
        amount = cashout_pos_profit(update.effective_user.id)
        return await q.edit_message_text(f"✅ POS profit cashed out: {amount:.3f}{CURRENCY}", reply_markup=kb_pos_self_panel(update.effective_user.id))

    # Customers list
    if data.startswith("admin:users:"):
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        page = int(data.split(":")[2])
        rows, total_pages = _users_page(page=page, page_size=10)
        text = "👥 *Customers*\nTap a user to view details:"
        return await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_users_page(page, total_pages, rows))

    if data.startswith("admin:user:view:"):
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        uid = int(data.split(":")[3])
        rep = _user_report_text(uid, limit_each=7)[:3800]
        cur.execute("SELECT suspended FROM users WHERE user_id=?", (uid,))
        s = int((cur.fetchone() or (0,))[0] or 0)
        return await q.edit_message_text(rep, reply_markup=kb_admin_user_view(uid, s))

    if data.startswith("admin:user:suspend:"):
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        uid = int(data.split(":")[3])
        if is_admin_any(uid) or uid == ADMIN_ID:
            return await q.edit_message_text("❌ لا يمكن تعليق الأدمن.")
        set_suspended(uid, True)
        try:
            await context.bot.send_message(uid, "⛔ تم تعليق حسابك. تواصل مع الدعم.")
        except Exception:
            pass
        return await q.edit_message_text(f"✅ User {uid} suspended.", reply_markup=kb_admin_panel(update.effective_user.id))

    if data.startswith("admin:user:unsuspend:"):
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        uid = int(data.split(":")[3])
        set_suspended(uid, False)
        try:
            await context.bot.send_message(uid, "✅ تم فك تعليق حسابك. يمكنك استخدام البوت الآن.")
        except Exception:
            pass
        return await q.edit_message_text(f"✅ User {uid} unsuspended.", reply_markup=kb_admin_panel(update.effective_user.id))

    if data.startswith("admin:user:export:"):
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        uid = int(data.split(":")[3])
        rep = _user_report_text(uid, limit_each=30)
        bio = io.BytesIO(rep.encode("utf-8"))
        bio.name = f"user_{uid}_report.txt"
        try:
            await context.bot.send_document(chat_id=ADMIN_ID, document=bio)
        except Exception as e:
            logger.exception("Failed to send export report: %s", e)
        await q.answer("Sent ✅", show_alert=False)
        return

    # Manual Orders list (Owner + Helper)
    if data.startswith("admin:manuallist:"):
        if not is_manual_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        page = int(data.split(":")[2])
        page_size = 8
        cur.execute("SELECT COUNT(*) FROM manual_orders WHERE status='PENDING'")
        total = int(cur.fetchone()[0])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        off = page * page_size

        cur.execute(
            """
            SELECT id, user_id, service, plan_title, price, created_at
            FROM manual_orders
            WHERE status='PENDING'
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, off),
        )
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("📥 No pending manual orders.", reply_markup=kb_admin_panel(update.effective_user.id))

        buttons = []
        for mid, uid, service, plan_title, price, created_at in rows:
            label = f"🧾 M#{mid} | {service} | {float(price):.3f}{CURRENCY}"
            buttons.append([InlineKeyboardButton(label[:60], callback_data=f"admin:manual:view:{mid}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:manuallist:{page-1}"))
        nav.append(InlineKeyboardButton(f"Page {page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("➡️ Next", callback_data=f"admin:manuallist:{page+1}"))
        buttons.append(nav)
        buttons.append([InlineKeyboardButton("👑 Admin Home", callback_data="admin:panel")])

        return await q.edit_message_text("📥 *Pending Manual Orders:*", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))

    if data.startswith("admin:manual:view:"):
        if not is_manual_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        mid = int(data.split(":")[3])
        cur.execute(
            """
            SELECT id, user_id, service, plan_title, price, email, password, player_id, note, status, created_at
            FROM manual_orders WHERE id=?
            """,
            (mid,),
        )
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Manual order not found.")
        (_mid, uid, service, plan_title, price, email, password, player_id, note, status, created_at) = row

        text_lines = []
        text_lines.append(f"🧾 *Manual Order #{_mid}*")
        text_lines.append(f"⭐ Status: *{status}*")
        text_lines.append(f"🔧 Service: *{service}*")
        text_lines.append(f"📦 Plan: {plan_title}")
        text_lines.append(f"💵 Price: *{float(price):.3f} {CURRENCY}*")
        text_lines.append(f"👤 User: `{uid}`")
        text_lines.append(f"🕒 Created: {created_at}")
        text_lines.append("")

        if player_id:
            text_lines.append(f"🟦 Player ID: `{player_id}`")
        if email:
            text_lines.append(f"🟨 Email: `{email}`")
        if password:
            text_lines.append(f"🟥 Password: `{password}`")

        if note:
            text_lines.append("\n📝 Note:")
            text_lines.append(f"`{str(note)}`")

        text = "\n".join(text_lines)[:3800]
        kb = kb_admin_manual_view(
            _mid,
            service,
            has_email=bool(email),
            has_pass=bool(password),
            has_player=bool(player_id),
        )
        return await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    # Copy buttons
    if data.startswith("admin:copy:"):
        if not is_manual_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        _, _, kind, mid_s = data.split(":")
        mid = int(mid_s)
        cur.execute("SELECT user_id, email, password, player_id FROM manual_orders WHERE id=?", (mid,))
        row = cur.fetchone()
        if not row:
            await q.answer("Not found", show_alert=True)
            return
        uid, email, password, player_id = int(row[0]), row[1] or "", row[2] or "", row[3] or ""

        if kind == "player":
            val = player_id
            label = "PLAYER ID"
        elif kind == "email":
            val = email
            label = "EMAIL"
        else:
            val = password
            label = "PASSWORD"

        if not val:
            await q.answer("Empty", show_alert=True)
            return

        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"📋 COPY {label} (Manual #{mid})\n`{val}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            await q.answer("Sent ✅", show_alert=False)
        except Exception as e:
            logger.exception("Copy send failed: %s", e)
            await q.answer("Failed", show_alert=True)
        return

    # Manual approve
    if data.startswith("admin:manual:approve:"):
        if not is_manual_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        mid = int(data.split(":")[3])
        cur.execute("SELECT user_id, price, status, service, plan_title FROM manual_orders WHERE id=?", (mid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Manual order not found.")
        uid, price, status, service, plan_title = int(row[0]), float(row[1]), row[2], row[3], row[4]
        if status != "PENDING":
            return await q.edit_message_text("❌ This manual order is not pending.")

        # ✅ store approved_by (admin id) but do not show to client
        approver_id = update.effective_user.id
        cur.execute("UPDATE manual_orders SET status='COMPLETED', approved_by=? WHERE id=?", (approver_id, mid))
        con.commit()

        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "✅ *تم شحن بنجاح!*\n"
                    f"🧾 Manual Order: *#{mid}*\n"
                    f"📦 Service: {plan_title}\n"
                    f"💵 Paid: *{price:.3f} {CURRENCY}*\n\n"
                    f"Approved by admin: `{approver_id}`\n\n"
                    "شكراً لك ❤️"
                    "شكراً لك ❤️"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.exception("Failed to notify user %s about manual approve %s: %s", uid, mid, e)

        # notify owner (optional)
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"✅ Manual #{mid} approved by admin `{approver_id}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

        return await q.edit_message_text(f"✅ Manual order #{mid} approved.", reply_markup=kb_admin_panel(update.effective_user.id))

    # Manual reject menu + reason (same as before)
    if data.startswith("admin:manual:rejectmenu:"):
        if not is_manual_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        mid = int(data.split(":")[3])

        return await q.edit_message_text(
            "Choose reject reason (or custom):",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🟥 Wrong ID", callback_data=f"admin:manual:reject:{mid}:WRONG_ID")],
                    [InlineKeyboardButton("🟦 Other Server", callback_data=f"admin:manual:reject:{mid}:OTHER_SERVER")],
                    [InlineKeyboardButton("🟨 Not Available", callback_data=f"admin:manual:reject:{mid}:NOT_AVAILABLE")],
                    [InlineKeyboardButton("✍️ Custom", callback_data=f"admin:manual:reject:{mid}:CUSTOM")],
                    [InlineKeyboardButton("⬅️ Back", callback_data=f"admin:manual:view:{mid}")],
                ]
            ),
        )

    if data.startswith("admin:manual:reject:"):
        if not is_manual_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        _, _, _, mid_s, reason = data.split(":")
        mid = int(mid_s)

        if reason == "CUSTOM":
            context.user_data[UD_ADMIN_MODE] = "manual_reject_custom"
            context.user_data[UD_ADMIN_MANUAL_ID] = mid
            await q.edit_message_text("✍️ Send custom reject reason text now:")
            return ST_ADMIN_INPUT

        reason_map = {
            "WRONG_ID": "❌ تم الرفض: 🟥 الايدي خطأ.",
            "OTHER_SERVER": "❌ تم الرفض: 🟦 الايدي من سيرفر/منطقة أخرى.",
            "NOT_AVAILABLE": "❌ تم الرفض: 🟨 الخدمة غير متاحة حالياً.",
        }
        reason_text = reason_map.get(reason, "❌ Rejected.")

        cur.execute("SELECT user_id, price, status FROM manual_orders WHERE id=?", (mid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Manual order not found.")
        uid, price, status = int(row[0]), float(row[1]), row[2]
        if status != "PENDING":
            return await q.edit_message_text("❌ This manual order is not pending.")

        bal_before = get_balance(uid)
        add_balance(uid, price)
        pos_owner = pos_customer_owner(uid)
        if pos_owner is not None and price > base_price:
            add_pos_profit(pos_owner, (price - base_price) * qty)
        bal_after = get_balance(uid)

        cur.execute("UPDATE manual_orders SET status='REJECTED', delivered_text=? WHERE id=?", (reason_text, mid))
        con.commit()

        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"{reason_text}\n"
                    f"🧾 Manual Order #{mid}\n"
                    f"💰 Refunded: +{price:.3f} {CURRENCY}\n\n"
                    f"💳 Balance before: {bal_before:.3f} {CURRENCY}\n"
                    f"✅ Balance after: {bal_after:.3f} {CURRENCY}\n"
                ),
            )
        except Exception as e:
            logger.exception("Failed to notify user %s about manual reject %s: %s", uid, mid, e)

        return await q.edit_message_text(f"✅ Manual order #{mid} rejected + refunded.", reply_markup=kb_admin_panel(update.effective_user.id))

    # Admin generic modes entry (Owner only)
    if data.startswith("admin:"):
        if not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")

        # helpers are limited
        if admin_role(update.effective_user.id) == ROLE_HELPER:
            # allowed only manuallist/manual view/approve/reject/copy/panel
            allowed_prefixes = (
                "admin:panel",
                "admin:manuallist",
                "admin:manual:",
                "admin:copy:",
            )
            if not any(data.startswith(x) for x in allowed_prefixes):
                return await q.edit_message_text("❌ Not allowed.")

        # Owner-only actions below
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")

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
                return await q.edit_message_text("No products.")
            lines = [
                f"PID {pid} | {cat} | {title} | {float(price):.3f}{CURRENCY} | {'ON ✅' if act else 'OFF ⛔'}"
                for pid, cat, title, price, act in rows
            ]
            text = "\n".join(lines)
            if len(text) > 3800:
                text = text[:3800] + "\n..."
            return await q.edit_message_text(text)

        prompts = {
            "addcat": 'Send category title:\nExample: 🪂 PUBG MOBILE UC VOUCHERS',
            "addprod": 'Send product:\nFormat: "Category Title" | "Product Title" | price\nExample:\n"🍎 ITUNES GIFTCARD (USA)" | "10$ iTunes US" | 9.2',
            "addcodes": 'Send codes:\nFormat: pid | code1\\ncode2\\n...\nExample:\n12 | ABCD-1234\nEFGH-5678',
            "addcodesfile": "✅ Send PID first (example: 12), then send .txt file.\nOR send file with caption PID.",
            "setprice": 'Send: pid | new_price\nExample: 12 | 9.5',
            "toggle": 'Send: pid (toggle ON/OFF)\nExample: 12',
            "approvedep": 'Send: deposit_id\nExample: 10',
            "rejectdep": 'Send: deposit_id\nExample: 10',
            "addbal": 'Send: user_id | amount\nExample: 1997968014 | 5',
            "takebal": 'Send: user_id | amount\nExample: 1997968014 | 5',
            "delprod": "🗑 Delete Product\nSend PID\nExample: 12",
            "delcatfull": "🗑 Delete Category (FULL)\nSend CID or Title\nExample:\n12\nor\n🍎 ITUNES GIFTCARD (USA)",
        }
        await q.edit_message_text(prompts.get(mode, "Send input now..."))
        return ST_ADMIN_INPUT

    # Navigation
    if data == "back:cats":
        return await show_categories(update, context)

    if data.startswith("cat:"):
        cid = int(data.split(":", 1)[1])
        context.user_data[UD_CID] = cid
        return await q.edit_message_text("🛒 Choose a product:", reply_markup=kb_products(cid, update.effective_user.id))

    if data.startswith("back:prods:"):
        cid = int(data.split(":", 2)[2])
        return await q.edit_message_text("🛒 Choose a product:", reply_markup=kb_products(cid, update.effective_user.id))

    # View
    if data.startswith("view:"):
        pid = int(data.split(":", 1)[1])
        cur.execute("SELECT title, price, cid FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.")
        title, price, cid = row
        stock = product_stock(pid)

        text = (
            f"🎁 *{title}*\n\n"
            f"🆔 ID: `{pid}`\n"
            f"💵 Price: *{float(price):.3f}* {CURRENCY}\n"
            f"📦 Stock: *{stock}*"
        )
        return await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_product_view(pid, cid))

    # Buy -> qty
    if data.startswith("buy:"):
        pid = int(data.split(":", 1)[1])
        cur.execute("SELECT title, cid FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.")
        title, cid = row
        stock = product_stock(pid)
        if stock <= 0:
            return await q.edit_message_text("❌ Out of stock.", reply_markup=kb_products(cid, update.effective_user.id))

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

    # Confirm purchase
    if data.startswith("confirm:"):
        parts = data.split(":")
        pid = int(parts[1]) if len(parts) > 1 else 0
        client_ref = parts[2] if len(parts) > 2 else ""

        qty = int(context.user_data.get(UD_LAST_QTY, 0))
        if qty <= 0 or pid <= 0 or not client_ref:
            return await q.edit_message_text("❌ Quantity expired. Buy again.")

        cur.execute("SELECT id, delivered_text, status FROM orders WHERE client_ref=?", (client_ref,))
        already = cur.fetchone()
        if already:
            oid, delivered_text, status = already[0], already[1] or "", already[2]
            await q.edit_message_text(f"✅ Already processed.\nOrder ID: {oid}\nStatus: {status}\nDelivering again...")
            if delivered_text.strip():
                await send_codes_delivery(update.effective_user.id, context, oid, delivered_text.splitlines())
            return

        cur.execute("SELECT title, price FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.")
        title, price = row
        base_price = float(price)
        price = get_effective_product_price(uid, pid, base_price)
        total = float(price) * qty

        uid = update.effective_user.id
        bal_before = get_balance(uid)

        if not charge_balance(uid, total):
            bal = get_balance(uid)
            missing = total - bal
            return await q.edit_message_text(
                f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
                reply_markup=kb_topup_now(),
            )

        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT code_id, code_text FROM codes WHERE pid=? AND used=0 ORDER BY code_id ASC LIMIT ?", (pid, qty))
            picked = cur.fetchall()
            if len(picked) < qty:
                cur.execute("ROLLBACK")
                add_balance(uid, total)
                return await q.edit_message_text("❌ Stock error. Refunded. Try again.")

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
            return await q.edit_message_text("❌ Error while processing order. Refunded. Try again.")

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
        except Exception as e:
            logger.exception("Failed to notify admin about completed order %s: %s", oid, e)
        return

    # Orders pagination
    if data.startswith("orders:range:"):
        _, _, rng, page = data.split(":")
        return await show_orders(update, context, rng=rng, page=int(page))

    if data.startswith("orders:next:"):
        _, _, page = data.split(":")
        rng = context.user_data.get(UD_ORD_RNG) or "all"
        return await show_orders(update, context, rng=rng, page=int(page))

    # Payment
    if data.startswith("pay:"):
        method = data.split(":", 1)[1]
        uid = update.effective_user.id
        note = secrets.token_hex(8).upper()

        cur.execute(
            "INSERT INTO deposits(user_id,method,note,status) VALUES(?,?,?,'WAITING_PAYMENT')",
            (uid, method, note),
        )
        dep_id = cur.lastrowid
        con.commit()

        if method == "BINANCE":
            dest_title = "UID"
            dest_value = BINANCE_UID
            extra = "Send USDT only."
        elif method == "BYBIT":
            dest_title = "UID"
            dest_value = BYBIT_UID
            extra = "Send USDT only."
        elif method == "TRC20":
            dest_title = "Address"
            dest_value = USDT_TRC20
            extra = "Network: TRC20 only."
        else:
            dest_title = "Address"
            dest_value = USDT_BEP20
            extra = "Network: BEP20 only."

        text = (
            f"🔑 *{method} Payment*\n\n"
            f"Send amount to this {dest_title} + include note:\n\n"
            f"*{dest_title}:*\n`{dest_value}`\n\n"
            f"*Note:*\n`{note}`\n\n"
            f"⚠️ {extra}\n\n"
            f"بعد الدفع اضغط ✅ I Have Paid"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_have_paid(dep_id))
        return

    if data.startswith("paid:"):
        dep_id = int(data.split(":", 1)[1])
        context.user_data[UD_DEP_ID] = dep_id
        await q.edit_message_text(
            "✅ Great!\nNow send:\n`amount | txid`\nExample:\n`10 | 2E38F3A2...`\n\n/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_TOPUP_DETAILS


# =========================
# Admin input (text + file)
# =========================
async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid_admin = update.effective_user.id
    if not is_admin_any(uid_admin):
        return ConversationHandler.END

    mode = context.user_data.get(UD_ADMIN_MODE)

    # allow exit + menu
    if update.message and update.message.text:
        t = update.message.text.strip()
        if t in MENU_BUTTONS:
            context.user_data.pop(UD_ADMIN_MODE, None)
            context.user_data.pop(UD_ADMIN_CODES_PID, None)
            context.user_data.pop(UD_ADMIN_MANUAL_ID, None)
            return await menu_router(update, context)

        if t.lower() in ("/cancel", "cancel") or t in ADMIN_TEXT_EXIT:
            context.user_data.pop(UD_ADMIN_MODE, None)
            context.user_data.pop(UD_ADMIN_CODES_PID, None)
            context.user_data.pop(UD_ADMIN_MANUAL_ID, None)
            await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

    # helper limitation
    if admin_role(uid_admin) == ROLE_HELPER and not is_pos_agent(uid_admin):
        if mode not in ("manual_reject_custom",):
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END

    text = (update.message.text or "").strip() if update.message else ""

    try:
        if mode == "admins_manage":
            if admin_role(uid_admin) != ROLE_OWNER:
                await update.message.reply_text("❌ Not allowed.")
                return ConversationHandler.END

            m = re.match(r"^(addadmin|deladmin)\s*\|\s*(\d+)$", text.strip().lower())
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

        if mode == "manual_reject_custom":
            mid = int(context.user_data.get(UD_ADMIN_MANUAL_ID, 0))
            reason_text = (update.message.text or "").strip()
            if not mid or not reason_text:
                await update.message.reply_text("❌ Missing manual id or reason.")
                return ConversationHandler.END

            cur.execute("SELECT user_id, price, status FROM manual_orders WHERE id=?", (mid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Manual order not found.")
                return ConversationHandler.END
            uid, price, status = int(row[0]), float(row[1]), row[2]
            if status != "PENDING":
                await update.message.reply_text("❌ This manual order is not pending.")
                return ConversationHandler.END

            bal_before = get_balance(uid)
            add_balance(uid, price)
            bal_after = get_balance(uid)

            cur.execute("UPDATE manual_orders SET status='REJECTED', delivered_text=? WHERE id=?", (reason_text[:3500], mid))
            con.commit()

            await update.message.reply_text(f"✅ Manual order #{mid} rejected + refunded.", reply_markup=REPLY_MENU)

            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=(
                        f"❌ Your manual order #{mid} was rejected.\n"
                        f"Reason: {reason_text}\n\n"
                        f"Refunded: +{price:.3f} {CURRENCY}\n"
                        f"💳 Balance before: {bal_before:.3f} {CURRENCY}\n"
                        f"✅ Balance after: {bal_after:.3f} {CURRENCY}\n"
                    ),
                )
            except Exception as e:
                logger.exception("Failed to notify user %s about custom manual reject %s: %s", uid, mid, e)

            context.user_data.pop(UD_ADMIN_MANUAL_ID, None)
            return ConversationHandler.END
        if mode == "userprice":
            m = re.match(r"^(del)\s*\|\s*(\d+)\s*\|\s*(\d+)$", text, flags=re.I)
            if m:
                user_id, pid = int(m.group(2)), int(m.group(3))
                cur.execute("DELETE FROM user_product_prices WHERE user_id=? AND pid=?", (user_id, pid))
                con.commit()
                await update.message.reply_text(f"✅ Deleted custom price for user {user_id} pid {pid}")
                return ConversationHandler.END
            m = re.match(r"^(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: user_id | pid | price\nOr: del | user_id | pid")
                return ST_ADMIN_INPUT
            user_id, pid, price = int(m.group(1)), int(m.group(2)), float(m.group(3))
            cur.execute(
                "INSERT INTO user_product_prices(user_id, pid, price) VALUES(?,?,?) ON CONFLICT(user_id, pid) DO UPDATE SET price=excluded.price",
                (user_id, pid, price),
            )
            con.commit()
            await update.message.reply_text(f"✅ Custom price saved for user {user_id} pid {pid} = {price:.3f}{CURRENCY}")
            return ConversationHandler.END

        if mode == "usermanualprice":
            m = re.match(r"^(del)\s*\|\s*(\d+)\s*\|\s*([A-Z0-9_]+)$", text, flags=re.I)
            if m:
                user_id, key = int(m.group(2)), m.group(3)
                cur.execute("DELETE FROM user_manual_prices WHERE user_id=? AND pkey=?", (user_id, key))
                con.commit()
                await update.message.reply_text(f"✅ Deleted user manual price for {user_id} {key}")
                return ConversationHandler.END
            m = re.match(r"^(\d+)\s*\|\s*([A-Z0-9_]+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: user_id | KEY | PRICE\nOr: del | user_id | KEY")
                return ST_ADMIN_INPUT
            user_id, key, price = int(m.group(1)), m.group(2), float(m.group(3))
            cur.execute(
                "INSERT INTO user_manual_prices(user_id, pkey, price) VALUES(?,?,?) ON CONFLICT(user_id, pkey) DO UPDATE SET price=excluded.price",
                (user_id, key, price),
            )
            con.commit()
            await update.message.reply_text(f"✅ User manual price saved: {user_id} | {key} = {price:.3f}{CURRENCY}")
            return ConversationHandler.END

        if mode == "broadcast_all":
            sent = 0
            failed = 0
            cur.execute("SELECT user_id FROM users ORDER BY user_id")
            for (to_uid,) in cur.fetchall():
                try:
                    await context.bot.send_message(chat_id=int(to_uid), text=text)
                    sent += 1
                except Exception:
                    failed += 1
            await update.message.reply_text(f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        if mode == "pos_add":
            if not text.isdigit():
                await update.message.reply_text("❌ Send user id only.")
                return ST_ADMIN_INPUT
            target = int(text)
            if not is_admin_any(target):
                await update.message.reply_text("❌ POS must already be admin/helper.")
                return ConversationHandler.END
            set_pos_agent(target, uid_admin)
            await update.message.reply_text(f"✅ Admin {target} is now POS.")
            return ConversationHandler.END

        if mode == "pos_remove":
            if not text.isdigit():
                await update.message.reply_text("❌ Send user id only.")
                return ST_ADMIN_INPUT
            target = int(text)
            remove_pos_agent(target)
            await update.message.reply_text(f"✅ POS removed: {target}")
            return ConversationHandler.END

        if mode == "pos_self_addcust":
            if not text.isdigit():
                await update.message.reply_text("❌ Send customer id only.")
                return ST_ADMIN_INPUT
            add_pos_customer(uid_admin, int(text))
            await update.message.reply_text(f"✅ Customer {text} added under your POS.")
            return ConversationHandler.END

        if mode == "pos_self_delcust":
            if not text.isdigit():
                await update.message.reply_text("❌ Send customer id only.")
                return ST_ADMIN_INPUT
            remove_pos_customer(uid_admin, int(text))
            await update.message.reply_text(f"✅ Customer {text} removed from your POS.")
            return ConversationHandler.END

        if mode == "pos_self_price":
            m = re.match(r"^(del)\s*\|\s*(\d+)\s*\|\s*(\d+)$", text, flags=re.I)
            if m:
                cuid, pid = int(m.group(2)), int(m.group(3))
                delete_pos_child_price(uid_admin, cuid, pid)
                await update.message.reply_text(f"✅ Child price deleted for customer {cuid} pid {pid}")
                return ConversationHandler.END
            m = re.match(r"^(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: customer_id | pid | price\nOr: del | customer_id | pid")
                return ST_ADMIN_INPUT
            cuid, pid, price = int(m.group(1)), int(m.group(2)), float(m.group(3))
            if pos_customer_owner(cuid) != uid_admin:
                await update.message.reply_text("❌ هذا العميل ليس تابعًا لك.")
                return ConversationHandler.END
            set_pos_child_price(uid_admin, cuid, pid, price)
            await update.message.reply_text(f"✅ Child price saved: customer {cuid} | pid {pid} = {price:.3f}{CURRENCY}")
            return ConversationHandler.END

        if mode == "pos_self_topup":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: customer_id | amount")
                return ST_ADMIN_INPUT
            cuid, amount = int(m.group(1)), float(m.group(2))
            if pos_customer_owner(cuid) != uid_admin:
                await update.message.reply_text("❌ هذا العميل ليس تابعًا لك.")
                return ConversationHandler.END
            if not charge_balance(uid_admin, amount):
                await update.message.reply_text("❌ رصيدك غير كافٍ.")
                return ConversationHandler.END
            add_balance(cuid, amount)
            await update.message.reply_text(f"✅ Charged customer {cuid} with +{amount:.3f}{CURRENCY}")
            try:
                await context.bot.send_message(cuid, f"✅ POS charged your balance: +{amount:.3f}{CURRENCY}\nPOS ID: {uid_admin}")
            except Exception:
                pass
            return ConversationHandler.END

            m = re.match(r"^([A-Z0-9_]+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: KEY | PRICE\nExample: FF_100 | 0.95")
                return ST_ADMIN_INPUT
            key, price_s = m.group(1), m.group(2)
            price = float(price_s)
            cur.execute(
                "INSERT INTO manual_prices(pkey, price) VALUES(?,?) "
                "ON CONFLICT(pkey) DO UPDATE SET price=excluded.price",
                (key, price),
            )
            con.commit()
            await update.message.reply_text(f"✅ Manual price updated: {key} = {price:.3f}{CURRENCY}")
            return ConversationHandler.END

        if admin_role(uid_admin) != ROLE_OWNER:
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END

        if mode == "delprod":
            if not text.isdigit():
                await update.message.reply_text("❌ Send PID number only.\nExample: 12")
                return ST_ADMIN_INPUT
            pid = int(text)
            cur.execute("SELECT title FROM products WHERE pid=?", (pid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Product not found.")
                return ConversationHandler.END
            title = row[0]
            cur.execute("DELETE FROM codes WHERE pid=?", (pid,))
            cur.execute("DELETE FROM products WHERE pid=?", (pid,))
            con.commit()
            await update.message.reply_text(f"✅ Deleted product PID {pid}\nTitle: {title}")
            return ConversationHandler.END

        if mode == "delcatfull":
            inp = text
            if not inp:
                await update.message.reply_text("❌ Send CID or Category Title.")
                return ST_ADMIN_INPUT

            cid = None
            cat_title = None
            if inp.isdigit():
                cid = int(inp)
                cur.execute("SELECT title FROM categories WHERE cid=?", (cid,))
                row = cur.fetchone()
                if not row:
                    await update.message.reply_text("❌ Category not found.")
                    return ConversationHandler.END
                cat_title = row[0]
            else:
                cat_title = inp
                cur.execute("SELECT cid FROM categories WHERE title=?", (cat_title,))
                row = cur.fetchone()
                if not row:
                    await update.message.reply_text("❌ Category not found.")
                    return ConversationHandler.END
                cid = int(row[0])

            cur.execute("SELECT pid FROM products WHERE cid=?", (cid,))
            pids = [int(r[0]) for r in cur.fetchall()]
            deleted_codes = 0
            deleted_products = 0

            for pid in pids:
                cur.execute("SELECT COUNT(*) FROM codes WHERE pid=?", (pid,))
                deleted_codes += int(cur.fetchone()[0])
                cur.execute("DELETE FROM codes WHERE pid=?", (pid,))
                cur.execute("DELETE FROM products WHERE pid=?", (pid,))
                deleted_products += 1

            cur.execute("DELETE FROM categories WHERE cid=?", (cid,))
            con.commit()

            await update.message.reply_text(
                f"✅ Category deleted (FULL)\n"
                f"Title: {cat_title}\nCID: {cid}\n"
                f"Deleted products: {deleted_products}\n"
                f"Deleted codes: {deleted_codes}\n\n"
                f"📝 Orders history kept as archive."
            )
            return ConversationHandler.END

        if mode == "addcat":
            cur.execute("INSERT OR IGNORE INTO categories(title) VALUES(?)", (text,))
            con.commit()
            await update.message.reply_text("✅ Category added.")
            return ConversationHandler.END

        if mode == "addprod":
            m = re.match(r'^"(.+?)"\s*\|\s*"(.+?)"\s*\|\s*([\d.]+)\s*$', text)
            if not m:
                await update.message.reply_text("❌ Format invalid.\nExample:\n\"CAT\" | \"TITLE\" | 9.2")
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
                await update.message.reply_text("❌ Missing '|'.\nExample:\n12 | CODE1\nCODE2")
                return ST_ADMIN_INPUT
            pid_s, codes_blob = [x.strip() for x in text.split("|", 1)]
            if not pid_s.isdigit():
                await update.message.reply_text("❌ PID must be a number.")
                return ST_ADMIN_INPUT
            pid = int(pid_s)
            codes = [c.strip() for c in codes_blob.splitlines() if c.strip()]
            if not codes:
                await update.message.reply_text("❌ No codes.")
                return ConversationHandler.END

            ok, msg = validate_codes_for_pid(pid, codes)
            if not ok:
                await update.message.reply_text(msg)
                return ConversationHandler.END

            added = 0
            skipped = 0
            for ctext in codes:
                ctext = ctext.strip().replace(" ", "")
                try:
                    cur.execute("INSERT INTO codes(pid,code_text,used) VALUES(?,?,0)", (pid, ctext))
                    added += 1
                except sqlite3.IntegrityError:
                    skipped += 1
            con.commit()
            await update.message.reply_text(f"✅ Added {added} codes to PID {pid}.\n♻️ Skipped duplicates: {skipped}")
            return ConversationHandler.END

        if mode == "addcodesfile":
            if update.message.text and not update.message.document:
                pid_txt = update.message.text.strip()
                if pid_txt.isdigit():
                    context.user_data[UD_ADMIN_CODES_PID] = int(pid_txt)
                    await update.message.reply_text("✅ PID saved. Now send the .txt file (one code per line).")
                    return ST_ADMIN_INPUT
                await update.message.reply_text("❌ Send PID as a number, then send the .txt file.")
                return ST_ADMIN_INPUT

            if not update.message.document:
                await update.message.reply_text("❌ Please send a .txt file (document).")
                return ST_ADMIN_INPUT

            pid = None
            caption = (update.message.caption or "").strip()
            m = re.search(r"(\d+)", caption)
            if m:
                pid = int(m.group(1))
            else:
                pid = context.user_data.get(UD_ADMIN_CODES_PID)

            if not pid:
                await update.message.reply_text("❌ Missing PID. Send PID number first, then send file.")
                return ST_ADMIN_INPUT

            file = await update.message.document.get_file()
            raw = await file.download_as_bytearray()
            content = raw.decode("utf-8", errors="ignore")

            codes = [c.strip().replace(" ", "") for c in content.splitlines() if c.strip()]
            if not codes:
                await update.message.reply_text("❌ File has no codes.")
                return ConversationHandler.END

            ok, msg = validate_codes_for_pid(pid, codes)
            if not ok:
                await update.message.reply_text(msg)
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

            context.user_data.pop(UD_ADMIN_CODES_PID, None)
            await update.message.reply_text(f"✅ Added {added} codes to PID {pid} from file.\n♻️ Skipped duplicates: {skipped}")
            return ConversationHandler.END

        if mode == "setprice":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: pid | price\nExample: 12 | 9.5")
                return ST_ADMIN_INPUT
            pid, price = int(m.group(1)), float(m.group(2))
            cur.execute("UPDATE products SET price=? WHERE pid=?", (price, pid))
            con.commit()
            await update.message.reply_text("✅ Price updated.")
            return ConversationHandler.END

        if mode == "toggle":
            if not text.isdigit():
                await update.message.reply_text("❌ Send PID number only.\nExample: 12")
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

        if mode == "approvedep":
            if not text.isdigit():
                await update.message.reply_text("❌ Send deposit_id number only.\nExample: 10")
                return ST_ADMIN_INPUT
            dep_id = int(text)
            cur.execute("SELECT user_id, amount, status FROM deposits WHERE id=?", (dep_id,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Deposit not found.")
                return ConversationHandler.END
            user_id, amount, status = int(row[0]), row[1], row[2]
            if status != "PENDING_REVIEW":
                await update.message.reply_text("❌ Deposit not ready for approval.")
                return ConversationHandler.END
            if amount is None:
                await update.message.reply_text("❌ Amount missing.")
                return ConversationHandler.END
            bal_before = get_balance(user_id)
            cur.execute("UPDATE deposits SET status='APPROVED' WHERE id=?", (dep_id,))
            con.commit()
            add_balance(user_id, float(amount))
            bal_after = get_balance(user_id)
            await update.message.reply_text(f"✅ Deposit #{dep_id} approved. +{money(float(amount))}")
            await context.bot.send_message(
                user_id,
                f"✅ Top up approved: +{money(float(amount))}\n\n💳 Balance before: {bal_before:.3f} {CURRENCY}\n✅ Balance after: {bal_after:.3f} {CURRENCY}",
            )
            return ConversationHandler.END

        if mode == "rejectdep":
            if not text.isdigit():
                await update.message.reply_text("❌ Send deposit_id number only.\nExample: 10")
                return ST_ADMIN_INPUT
            dep_id = int(text)
            cur.execute("SELECT user_id, status FROM deposits WHERE id=?", (dep_id,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Deposit not found.")
                return ConversationHandler.END
            user_id, status = int(row[0]), row[1]
            if status not in ("PENDING_REVIEW", "WAITING_PAYMENT"):
                await update.message.reply_text("❌ Deposit already processed.")
                return ConversationHandler.END
            cur.execute("UPDATE deposits SET status='REJECTED' WHERE id=?", (dep_id,))
            con.commit()
            await update.message.reply_text(f"✅ Deposit #{dep_id} rejected.")
            await context.bot.send_message(user_id, f"❌ Top up #{dep_id} rejected. Contact support.")
            return ConversationHandler.END

        if mode == "addbal":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: user_id | amount\nExample: 1997968014 | 5")
                return ST_ADMIN_INPUT
            user_id, amount = int(m.group(1)), float(m.group(2))
            bal_before = get_balance(user_id)
            add_balance(user_id, amount)
            bal_after = get_balance(user_id)
            await update.message.reply_text(f"✅ Added +{money(amount)} to {user_id}")
            await context.bot.send_message(
                user_id,
                f"✅ Admin added balance: +{money(amount)}\n\n💳 Balance before: {bal_before:.3f} {CURRENCY}\n✅ Balance after: {bal_after:.3f} {CURRENCY}",
            )
            return ConversationHandler.END

        if mode == "takebal":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: user_id | amount\nExample: 1997968014 | 5")
                return ST_ADMIN_INPUT
            user_id, amount = int(m.group(1)), float(m.group(2))
            bal_before = get_balance(user_id)
            if not charge_balance(user_id, amount):
                bal = get_balance(user_id)
                await update.message.reply_text(f"❌ User has insufficient balance. User balance: {bal:.3f} {CURRENCY}")
                return ConversationHandler.END
            add_balance(ADMIN_ID, amount)
            bal_after = get_balance(user_id)
            await update.message.reply_text(f"✅ Took {money(amount)} from {user_id} → added to Admin.")
            await context.bot.send_message(
                user_id,
                f"➖ Admin deducted: -{money(amount)}\n\n💳 Balance before: {bal_before:.3f} {CURRENCY}\n✅ Balance after: {bal_after:.3f} {CURRENCY}",
            )
            return ConversationHandler.END

        await update.message.reply_text("✅ Done.")
        return ConversationHandler.END

    except Exception as e:
        logger.exception("Admin input error: %s", e)
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END


# =========================
# Admin commands
# =========================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_any(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    await update.message.reply_text("👑 Admin Panel", reply_markup=kb_admin_panel(update.effective_user.id))


async def approvedep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if admin_role(update.effective_user.id) != ROLE_OWNER:
        return
    if not context.args:
        return await update.message.reply_text("Usage: /approvedep <deposit_id>")
    context.user_data[UD_ADMIN_MODE] = "approvedep"
    update.message.text = context.args[0]
    return await admin_input(update, context)


async def rejectdep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if admin_role(update.effective_user.id) != ROLE_OWNER:
        return
    if not context.args:
        return await update.message.reply_text("Usage: /rejectdep <deposit_id>")
    context.user_data[UD_ADMIN_MODE] = "rejectdep"
    update.message.text = context.args[0]
    return await admin_input(update, context)


# =========================
# Main
# =========================
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    CB_PATTERN = r"^(cat:|view:|buy:|confirm:|pay:|paid:|manual:|admin:|orders:|back:|goto:)"

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(on_callback, pattern=CB_PATTERN)
        ],
        states={
            ST_QTY: [MessageHandler(filters.TEXT, qty_input), CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
            ST_TOPUP_DETAILS: [MessageHandler(filters.TEXT, topup_details_input), CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
            ST_ADMIN_INPUT: [MessageHandler(filters.TEXT | filters.Document.ALL, admin_input), CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
            ST_MANUAL_EMAIL: [MessageHandler(filters.TEXT, manual_email_input), CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
            ST_MANUAL_PASS: [MessageHandler(filters.TEXT, manual_pass_input), CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
            ST_FF_PLAYERID: [MessageHandler(filters.TEXT, ff_playerid_input), CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
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

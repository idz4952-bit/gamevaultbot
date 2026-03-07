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
    "🪂 PUBG MOBILE UC",
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
        cur.execute("ALTER TABLE deposits ADD COLUMN approved_at TEXT")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS balance_ledger(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              delta REAL NOT NULL,
              balance_before REAL NOT NULL,
              balance_after REAL NOT NULL,
              source_type TEXT NOT NULL,
              source_id TEXT,
              note TEXT,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_user_created ON balance_ledger(user_id, created_at)")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_flags(
              fkey TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_alerts(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              audit_date TEXT NOT NULL,
              user_id INTEGER NOT NULL,
              issue_key TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_alert_unique ON audit_alerts(audit_date, user_id, issue_key)")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_product_prices(
              user_id INTEGER NOT NULL,
              pid INTEGER NOT NULL,
              price REAL NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY(user_id, pid)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_product_prices_pid ON user_product_prices(pid)")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_manual_prices(
              user_id INTEGER NOT NULL,
              pkey TEXT NOT NULL,
              price REAL NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY(user_id, pkey)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_manual_prices_pkey ON user_manual_prices(pkey)")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pos_product_prices(
              reseller_id INTEGER NOT NULL,
              client_user_id INTEGER NOT NULL,
              pid INTEGER NOT NULL,
              price REAL NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY(reseller_id, client_user_id, pid)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pos_product_prices_client ON pos_product_prices(client_user_id)")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pos_manual_prices(
              reseller_id INTEGER NOT NULL,
              client_user_id INTEGER NOT NULL,
              pkey TEXT NOT NULL,
              price REAL NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY(reseller_id, client_user_id, pkey)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pos_manual_prices_client ON pos_manual_prices(client_user_id)")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS resellers(
              user_id INTEGER PRIMARY KEY,
              active INTEGER NOT NULL DEFAULT 1,
              profit_balance REAL NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_resellers_active ON resellers(active)")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS reseller_clients(
              client_user_id INTEGER PRIMARY KEY,
              reseller_id INTEGER NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_reseller_clients_reseller ON reseller_clients(reseller_id)")
        con.commit()
    except Exception:
        pass
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS reseller_profit_log(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              reseller_id INTEGER NOT NULL,
              amount REAL NOT NULL,
              source_type TEXT NOT NULL,
              source_id TEXT,
              note TEXT,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_reseller_profit_log_reseller_created ON reseller_profit_log(reseller_id, created_at)")
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
MANUAL_FLAG_DEFAULTS = {
    "MANUAL_SHAHID_ENABLED": 1,
    "MANUAL_FF_ENABLED": 1,
    "SHAHID_MENA_3M_ENABLED": 1,
    "SHAHID_MENA_12M_ENABLED": 1,
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
def seed_manual_flags():
    for k, v in MANUAL_FLAG_DEFAULTS.items():
        cur.execute("INSERT OR IGNORE INTO manual_flags(fkey, enabled) VALUES(?,?)", (k, int(v)))
    con.commit()
def manual_flag_enabled(key: str, default: int = 1) -> bool:
    cur.execute("SELECT enabled FROM manual_flags WHERE fkey=?", (key,))
    row = cur.fetchone()
    return bool(int(row[0])) if row else bool(default)
def set_manual_flag(key: str, enabled: bool):
    cur.execute("INSERT INTO manual_flags(fkey, enabled) VALUES(?,?) ON CONFLICT(fkey) DO UPDATE SET enabled=excluded.enabled", (key, 1 if enabled else 0))
    con.commit()
seed_manual_prices()
seed_manual_flags()
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
        [KeyboardButton("🏪 POS Panel"), KeyboardButton("☎️ Contact Support")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)
MENU_BUTTONS = {
    "🛒 Our Products",
    "💰 My Balance",
    "📦 My Orders",
    "⚡ Manual Order",
    "🏪 POS Panel",
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

def get_user_brief(user_id: int) -> str:
    ensure_user_exists(user_id)
    cur.execute("SELECT username, first_name FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        return f"{user_id}"
    username = (row[0] or "").strip()
    first_name = (row[1] or "").strip()
    label = f"{user_id}"
    if username:
        label += f" (@{username})"
    elif first_name:
        label += f" ({first_name})"
    return label
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
def record_ledger(uid: int, delta: float, balance_before: float, balance_after: float, source_type: str, source_id: Optional[str] = None, note: str = ""):
    cur.execute(
        "INSERT INTO balance_ledger(user_id, delta, balance_before, balance_after, source_type, source_id, note) VALUES(?,?,?,?,?,?,?)",
        (uid, float(delta), float(balance_before), float(balance_after), source_type, str(source_id or ""), note[:1000]),
    )
    con.commit()
def add_balance_logged(uid: int, amount: float, source_type: str, source_id: Optional[str] = None, note: str = "") -> Tuple[float, float]:
    bal_before = get_balance(uid)
    add_balance(uid, amount)
    bal_after = get_balance(uid)
    record_ledger(uid, amount, bal_before, bal_after, source_type, source_id, note)
    return bal_before, bal_after
def charge_balance_logged(uid: int, amount: float, source_type: str, source_id: Optional[str] = None, note: str = "") -> Tuple[bool, float, float]:
    bal_before = get_balance(uid)
    if not charge_balance(uid, amount):
        return False, bal_before, bal_before
    bal_after = get_balance(uid)
    record_ledger(uid, -amount, bal_before, bal_after, source_type, source_id, note)
    return True, bal_before, bal_after
def all_admin_ids() -> List[int]:
    cur.execute("SELECT user_id FROM admins")
    return sorted({int(r[0]) for r in cur.fetchall()} | {ADMIN_ID})
async def notify_manual_order_admins(context: ContextTypes.DEFAULT_TYPE, message_text: str):
    for aid in all_admin_ids():
        try:
            await context.bot.send_message(chat_id=aid, text=message_text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            logger.exception("Failed notifying admin %s", aid)
async def broadcast_to_all_users(context: ContextTypes.DEFAULT_TYPE, message_text: str) -> Tuple[int, int]:
    cur.execute("SELECT user_id FROM users ORDER BY user_id")
    rows = cur.fetchall()
    sent = 0
    failed = 0
    for row in rows:
        uid = int(row[0])
        try:
            await context.bot.send_message(chat_id=uid, text=message_text)
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Broadcast failed to %s", uid)
    return sent, failed

async def send_audit_alert(context: ContextTypes.DEFAULT_TYPE, audit_date: str, uid: int, issue_key: str, message_text: str):
    try:
        cur.execute("INSERT INTO audit_alerts(audit_date, user_id, issue_key) VALUES(?,?,?)", (audit_date, uid, issue_key[:180]))
        con.commit()
    except sqlite3.IntegrityError:
        return
    except Exception:
        logger.exception("Failed to save audit alert")
        return
    for aid in all_admin_ids():
        try:
            await context.bot.send_message(
                chat_id=aid,
                text=(
                    "🚨 *Audit Alert*\n"
                    f"📅 Date: `{audit_date}`\n"
                    f"👤 User: `{uid}`\n"
                    f"📝 Reason: {message_text}"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            logger.exception("Failed sending audit alert to %s", aid)
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
def get_base_product_price(pid: int) -> float:
    cur.execute("SELECT price FROM products WHERE pid=?", (pid,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


def get_admin_product_price(uid: int, pid: int, default_price: Optional[float] = None) -> float:
    cur.execute("SELECT price FROM user_product_prices WHERE user_id=? AND pid=?", (uid, pid))
    row = cur.fetchone()
    if row:
        return float(row[0])
    if default_price is not None:
        return float(default_price)
    return get_base_product_price(pid)


def get_pos_product_price(reseller_id: Optional[int], client_uid: int, pid: int) -> Optional[float]:
    if not reseller_id:
        return None
    cur.execute(
        "SELECT price FROM pos_product_prices WHERE reseller_id=? AND client_user_id=? AND pid=?",
        (reseller_id, client_uid, pid),
    )
    row = cur.fetchone()
    return float(row[0]) if row else None


def get_user_product_price(uid: int, pid: int, default_price: Optional[float] = None) -> float:
    base = get_admin_product_price(uid, pid, default_price)
    reseller_id = get_client_reseller_id(uid)
    pos_price = get_pos_product_price(reseller_id, uid, pid)
    return float(pos_price) if pos_price is not None else float(base)


def get_effective_product_base_for_pos(client_uid: int, pid: int) -> float:
    return get_admin_product_price(client_uid, pid, get_base_product_price(pid))


def has_user_product_price(uid: int, pid: int) -> bool:
    cur.execute("SELECT 1 FROM user_product_prices WHERE user_id=? AND pid=?", (uid, pid))
    return cur.fetchone() is not None


def set_user_product_price(uid: int, pid: int, price: float):
    cur.execute(
        "INSERT INTO user_product_prices(user_id, pid, price) VALUES(?,?,?) ON CONFLICT(user_id, pid) DO UPDATE SET price=excluded.price",
        (uid, pid, float(price)),
    )
    con.commit()


def clear_user_product_price(uid: int, pid: int):
    cur.execute("DELETE FROM user_product_prices WHERE user_id=? AND pid=?", (uid, pid))
    con.commit()


def has_pos_product_price(reseller_id: int, client_uid: int, pid: int) -> bool:
    cur.execute(
        "SELECT 1 FROM pos_product_prices WHERE reseller_id=? AND client_user_id=? AND pid=?",
        (reseller_id, client_uid, pid),
    )
    return cur.fetchone() is not None


def set_pos_product_price(reseller_id: int, client_uid: int, pid: int, price: float):
    cur.execute(
        "INSERT INTO pos_product_prices(reseller_id, client_user_id, pid, price) VALUES(?,?,?,?) ON CONFLICT(reseller_id, client_user_id, pid) DO UPDATE SET price=excluded.price, created_at=datetime('now')",
        (reseller_id, client_uid, pid, float(price)),
    )
    con.commit()


def clear_pos_product_price(reseller_id: int, client_uid: int, pid: int):
    cur.execute(
        "DELETE FROM pos_product_prices WHERE reseller_id=? AND client_user_id=? AND pid=?",
        (reseller_id, client_uid, pid),
    )
    con.commit()


def get_admin_manual_price(uid: Optional[int], key: str, default_price: Optional[float] = None) -> float:
    if uid is not None:
        cur.execute("SELECT price FROM user_manual_prices WHERE user_id=? AND pkey=?", (uid, key))
        row = cur.fetchone()
        if row:
            return float(row[0])
    if default_price is not None:
        return float(default_price)
    return get_manual_price(key, MANUAL_PRICE_DEFAULTS.get(key, 0.0))


def get_pos_manual_price(reseller_id: Optional[int], client_uid: int, key: str) -> Optional[float]:
    if not reseller_id:
        return None
    cur.execute(
        "SELECT price FROM pos_manual_prices WHERE reseller_id=? AND client_user_id=? AND pkey=?",
        (reseller_id, client_uid, key),
    )
    row = cur.fetchone()
    return float(row[0]) if row else None


def get_user_manual_price(uid: Optional[int], key: str, default_price: Optional[float] = None) -> float:
    admin_price = get_admin_manual_price(uid, key, default_price)
    if uid is not None:
        reseller_id = get_client_reseller_id(uid)
        pos_price = get_pos_manual_price(reseller_id, uid, key)
        if pos_price is not None:
            return float(pos_price)
    return float(admin_price)


def get_effective_manual_base_for_pos(client_uid: int, key: str) -> float:
    return get_admin_manual_price(client_uid, key, get_manual_price(key, MANUAL_PRICE_DEFAULTS.get(key, 0.0)))


def has_user_manual_price(uid: int, key: str) -> bool:
    cur.execute("SELECT 1 FROM user_manual_prices WHERE user_id=? AND pkey=?", (uid, key))
    return cur.fetchone() is not None


def set_user_manual_price(uid: int, key: str, price: float):
    cur.execute(
        "INSERT INTO user_manual_prices(user_id, pkey, price) VALUES(?,?,?) ON CONFLICT(user_id, pkey) DO UPDATE SET price=excluded.price",
        (uid, key, float(price)),
    )
    con.commit()


def clear_user_manual_price(uid: int, key: str):
    cur.execute("DELETE FROM user_manual_prices WHERE user_id=? AND pkey=?", (uid, key))
    con.commit()


def has_pos_manual_price(reseller_id: int, client_uid: int, key: str) -> bool:
    cur.execute(
        "SELECT 1 FROM pos_manual_prices WHERE reseller_id=? AND client_user_id=? AND pkey=?",
        (reseller_id, client_uid, key),
    )
    return cur.fetchone() is not None


def set_pos_manual_price(reseller_id: int, client_uid: int, key: str, price: float):
    cur.execute(
        "INSERT INTO pos_manual_prices(reseller_id, client_user_id, pkey, price) VALUES(?,?,?,?) ON CONFLICT(reseller_id, client_user_id, pkey) DO UPDATE SET price=excluded.price, created_at=datetime('now')",
        (reseller_id, client_uid, key, float(price)),
    )
    con.commit()


def clear_pos_manual_price(reseller_id: int, client_uid: int, key: str):
    cur.execute(
        "DELETE FROM pos_manual_prices WHERE reseller_id=? AND client_user_id=? AND pkey=?",
        (reseller_id, client_uid, key),
    )
    con.commit()


def pos_all_products_text(client_uid: Optional[int] = None) -> str:
    cur.execute(
        """
        SELECT p.pid, p.title, p.price, c.title
        FROM products p
        JOIN categories c ON c.cid = p.cid
        WHERE p.active=1
        ORDER BY c.title, p.pid
        """
    )
    rows = cur.fetchall()
    lines = ["📦 *Available Auto Products*", ""]
    if not rows:
        lines.append("لا توجد منتجات تلقائية نشطة.")
    else:
        last_cat = None
        for pid, title, price, cat_title in rows:
            if cat_title != last_cat:
                lines.append(f"*{cat_title}*")
                last_cat = cat_title
            effective = get_effective_product_base_for_pos(client_uid, int(pid)) if client_uid else float(price)
            lines.append(f"• PID `{pid}` | {title} | Base *{float(effective):.3f}{CURRENCY}*")
    return "\n".join(lines)[:3800]


def pos_all_manual_keys_text(client_uid: Optional[int] = None) -> str:
    keys = ["SHAHID_MENA_3M", "SHAHID_MENA_12M", "FF_100", "FF_210", "FF_530", "FF_1080", "FF_2200"]
    lines = ["🛠 *Available Manual Keys*", ""]
    for key in keys:
        effective = get_effective_manual_base_for_pos(client_uid, key) if client_uid else get_manual_price(key, MANUAL_PRICE_DEFAULTS.get(key, 0.0))
        lines.append(f"• `{key}` | Base *{float(effective):.3f}{CURRENCY}*")
    return "\n".join(lines)[:3800]


def shahid_plan_to_price_key(plan_title: str) -> Optional[str]:
    t = (plan_title or "").upper()
    if "12" in t:
        return "SHAHID_MENA_12M"
    if "3" in t:
        return "SHAHID_MENA_3M"
    return None


def ff_title_to_sku(title: str) -> Optional[str]:
    t = (title or "").strip()
    for sku, pack_title, _ in FF_PACKS:
        if pack_title == t:
            return sku
    return None


def calculate_pos_manual_profit(reseller_id: Optional[int], client_uid: int, service: str, plan_title: str, note: str = "") -> Tuple[float, str]:
    if not reseller_id:
        return 0.0, ""
    total_margin = 0.0
    details = []
    service = (service or "").upper().strip()
    if service == "SHAHID":
        key = shahid_plan_to_price_key(plan_title)
        if key and has_pos_manual_price(reseller_id, client_uid, key):
            sell = get_pos_manual_price(reseller_id, client_uid, key) or 0.0
            base = get_effective_manual_base_for_pos(client_uid, key)
            margin = max(0.0, float(sell) - float(base))
            if margin > 1e-9:
                total_margin += margin
                details.append(f"{key} +{margin:.3f}{CURRENCY}")
        return total_margin, " | ".join(details)
    if service == "FREEFIRE_MENA":
        for raw_line in (note or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            m = re.match(r"^(.+?)\s+x(\d+)\s*\|", line)
            if not m:
                continue
            title = m.group(1).strip()
            qty = int(m.group(2))
            if qty <= 0:
                continue
            sku = ff_title_to_sku(title)
            if not sku or not has_pos_manual_price(reseller_id, client_uid, sku):
                continue
            sell = get_pos_manual_price(reseller_id, client_uid, sku) or 0.0
            base = get_effective_manual_base_for_pos(client_uid, sku)
            margin_each = max(0.0, float(sell) - float(base))
            if margin_each <= 1e-9:
                continue
            margin = margin_each * qty
            total_margin += margin
            details.append(f"{sku} x{qty} +{margin:.3f}{CURRENCY}")
        return total_margin, " | ".join(details)
    return 0.0, ""

def is_reseller(uid: int) -> bool:
    cur.execute("SELECT active FROM resellers WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return bool(int(row[0])) if row else False

def add_reseller(uid: int):
    ensure_user_exists(uid)
    cur.execute("INSERT INTO resellers(user_id, active, profit_balance) VALUES(?,1,COALESCE((SELECT profit_balance FROM resellers WHERE user_id=?),0)) ON CONFLICT(user_id) DO UPDATE SET active=1", (uid, uid))
    con.commit()

def remove_reseller(uid: int):
    cur.execute("DELETE FROM reseller_clients WHERE reseller_id=?", (uid,))
    cur.execute("DELETE FROM pos_product_prices WHERE reseller_id=?", (uid,))
    cur.execute("DELETE FROM pos_manual_prices WHERE reseller_id=?", (uid,))
    cur.execute("DELETE FROM reseller_profit_log WHERE reseller_id=?", (uid,))
    cur.execute("DELETE FROM resellers WHERE user_id=?", (uid,))
    con.commit()

def reseller_profit_balance(uid: int) -> float:
    cur.execute("SELECT profit_balance FROM resellers WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0

def add_reseller_profit(uid: int, amount: float, source_type: str, source_id: Optional[str] = None, note: str = ""):
    if amount <= 0:
        return
    add_reseller(uid)
    cur.execute("UPDATE resellers SET profit_balance=profit_balance+? WHERE user_id=?", (float(amount), uid))
    cur.execute(
        "INSERT INTO reseller_profit_log(reseller_id, amount, source_type, source_id, note) VALUES(?,?,?,?,?)",
        (uid, float(amount), source_type[:80], str(source_id or "")[:80], note[:1000]),
    )
    con.commit()

def transfer_reseller_profit_to_balance(uid: int) -> float:
    amount = reseller_profit_balance(uid)
    if amount <= 0:
        return 0.0
    cur.execute("UPDATE resellers SET profit_balance=0 WHERE user_id=?", (uid,))
    con.commit()
    add_balance_logged(uid, amount, "POS_PROFIT_TRANSFER", note="Transfer reseller profit to balance")
    return float(amount)

def get_client_reseller_id(uid: int) -> Optional[int]:
    cur.execute("SELECT reseller_id FROM reseller_clients WHERE client_user_id=?", (uid,))
    row = cur.fetchone()
    return int(row[0]) if row else None

def reseller_can_manage_client(reseller_id: int, client_uid: int) -> bool:
    cur.execute("SELECT 1 FROM reseller_clients WHERE reseller_id=? AND client_user_id=?", (reseller_id, client_uid))
    return cur.fetchone() is not None

def assign_client_to_reseller(reseller_id: int, client_uid: int) -> Tuple[bool, str]:
    if reseller_id == client_uid:
        return False, "لا يمكن إضافة نفسك كعميل."
    if is_admin_any(client_uid):
        return False, "لا يمكن ربط أدمن كنقطة بيع فرعية."
    if is_reseller(client_uid):
        return False, "هذا المستخدم نقطة بيع بالفعل."
    ensure_user_exists(client_uid)
    cur.execute("SELECT reseller_id FROM reseller_clients WHERE client_user_id=?", (client_uid,))
    row = cur.fetchone()
    if row and int(row[0]) == reseller_id:
        return False, "العميل مضاف بالفعل لهذه النقطة."
    if row and int(row[0]) != reseller_id:
        return False, f"العميل تابع لنقطة بيع أخرى: {int(row[0])}"
    cur.execute("INSERT OR REPLACE INTO reseller_clients(client_user_id, reseller_id) VALUES(?,?)", (client_uid, reseller_id))
    con.commit()
    return True, "تم ربط العميل بنقطة البيع."

def remove_client_from_reseller(reseller_id: int, client_uid: int) -> bool:
    cur.execute("DELETE FROM reseller_clients WHERE reseller_id=? AND client_user_id=?", (reseller_id, client_uid))
    ch = cur.rowcount
    if ch:
        cur.execute(
            "DELETE FROM pos_product_prices WHERE reseller_id=? AND client_user_id=?",
            (reseller_id, client_uid),
        )
        cur.execute(
            "DELETE FROM pos_manual_prices WHERE reseller_id=? AND client_user_id=?",
            (reseller_id, client_uid),
        )
    con.commit()
    return bool(ch)

def effective_topup_allowed(uid: int) -> bool:
    return get_client_reseller_id(uid) is None

def pos_clients_text(reseller_id: int) -> str:
    cur.execute(
        """
        SELECT u.user_id, u.username, u.first_name, u.balance
        FROM reseller_clients rc
        JOIN users u ON u.user_id=rc.client_user_id
        WHERE rc.reseller_id=?
        ORDER BY rc.client_user_id DESC
        LIMIT 100
        """,
        (reseller_id,),
    )
    rows = cur.fetchall()
    lines = ["🏪 *POS Clients*", f"POS ID: `{reseller_id}`", ""]
    if not rows:
        lines.append("لا يوجد عملاء تابعون لهذه النقطة بعد.")
    else:
        for uid, username, first_name, bal in rows:
            uname = f" @{username}" if username else ""
            name = f" {first_name}" if first_name else ""
            lines.append(f"• `{uid}`{uname}{name} | 💰 {float(bal or 0):.3f}{CURRENCY}")
    return "\n".join(lines)[:3800]


def kb_pos_panel(uid: int) -> InlineKeyboardMarkup:
    profit = reseller_profit_balance(uid)
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Client", callback_data="pos:addclient"), InlineKeyboardButton("➖ Remove Client", callback_data="pos:removeclient")],
            [InlineKeyboardButton("🎯 Auto Price", callback_data="pos:setprice"), InlineKeyboardButton("🛠 Manual Price", callback_data="pos:setmanual")],
            [InlineKeyboardButton("📦 Auto Products", callback_data="pos:catalog:auto"), InlineKeyboardButton("🧾 Manual Keys", callback_data="pos:catalog:manual")],
            [InlineKeyboardButton("📌 Clients", callback_data="pos:clients"), InlineKeyboardButton("💸 Charge Client", callback_data="pos:charge")],
            [InlineKeyboardButton("📋 Auto Prices", callback_data="pos:prices:auto"), InlineKeyboardButton("📋 Manual Prices", callback_data="pos:prices:manual")],
            [InlineKeyboardButton("📢 Notify My Clients", callback_data="pos:notify"), InlineKeyboardButton(f"💰 Profit {profit:.3f}{CURRENCY}", callback_data="pos:profit")],
            [InlineKeyboardButton("🔄 Transfer Profit to Balance", callback_data="pos:profit:transfer")],
            [InlineKeyboardButton("⬅️ Back", callback_data="goto:cats")],
        ]
    )

def pos_panel_text(uid: int) -> str:
    cur.execute("SELECT COUNT(*) FROM reseller_clients WHERE reseller_id=?", (uid,))
    client_count = int(cur.fetchone()[0] or 0)
    profit = reseller_profit_balance(uid)
    return (
        "🏪 *POS Panel*\n\n"
        f"🆔 POS ID: `{uid}`\n"
        f"👥 Clients: *{client_count}*\n"
        f"💰 Pending Profit: *{profit:.3f}{CURRENCY}*\n\n"
        "من هنا يمكنك:\n"
        "• إضافة/حذف عملاء تابعين لك فقط\n"
        "• عرض قائمة المنتجات التلقائية ومفاتيح اليدوي\n"
        "• تحديد سعر POS خاص لعملائك فقط\n"
        "• شحن رصيد عملائك من رصيدك\n"
        "• إرسال إشعار لعملائك فقط\n"
        "• تحويل أرباحك المجمعة إلى رصيدك"
    )

def pos_product_prices_text(reseller_id: int) -> str:
    cur.execute(
        """
        SELECT ppp.client_user_id, ppp.pid, ppp.price, p.title
        FROM pos_product_prices ppp
        LEFT JOIN products p ON p.pid = ppp.pid
        WHERE ppp.reseller_id=?
        ORDER BY ppp.client_user_id ASC, ppp.pid ASC
        LIMIT 250
        """,
        (reseller_id,),
    )
    rows = cur.fetchall()
    lines = ["📋 *POS Auto Prices*", ""]
    if not rows:
        lines.append("لا توجد أسعار تلقائية خاصة محفوظة لعملائك.")
    else:
        for xuid, pid, price, ptitle in rows:
            base = get_effective_product_base_for_pos(int(xuid), int(pid))
            lines.append(f"• Client `{xuid}` | PID `{pid}` | Base *{float(base):.3f}{CURRENCY}* → Sell *{float(price):.3f}{CURRENCY}* | {ptitle or '-'}")
    lines.append("")
    lines.append("استخدم 🎯 Auto Price للتعديل أو الحذف.")
    lines.append("")
    lines.append(pos_all_products_text())
    return "\n".join(lines)[:3800]

def pos_manual_prices_text(reseller_id: int) -> str:
    cur.execute(
        """
        SELECT pmp.client_user_id, pmp.pkey, pmp.price
        FROM pos_manual_prices pmp
        WHERE pmp.reseller_id=?
        ORDER BY pmp.client_user_id ASC, pmp.pkey ASC
        LIMIT 250
        """,
        (reseller_id,),
    )
    rows = cur.fetchall()
    lines = ["📋 *POS Manual Prices*", ""]
    if not rows:
        lines.append("لا توجد أسعار يدوية خاصة محفوظة لعملائك.")
    else:
        for xuid, pkey, price in rows:
            base = get_effective_manual_base_for_pos(int(xuid), pkey)
            lines.append(f"• Client `{xuid}` | `{pkey}` | Base *{float(base):.3f}{CURRENCY}* → Sell *{float(price):.3f}{CURRENCY}*")
    lines.append("")
    lines.append("استخدم 🛠 Manual Price للتعديل أو الحذف.")
    lines.append("")
    lines.append(pos_all_manual_keys_text())
    return "\n".join(lines)[:3800]

def kb_reseller_admin_panel() -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add POS", callback_data="admin:resellers:add"), InlineKeyboardButton("➖ Remove POS", callback_data="admin:resellers:del")],
            [InlineKeyboardButton("📌 POS List", callback_data="admin:resellers:list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")],
        ]
    )


def reseller_admin_text() -> str:
    cur.execute(
        """
        SELECT r.user_id, r.profit_balance, COUNT(rc.client_user_id)
        FROM resellers r
        LEFT JOIN reseller_clients rc ON rc.reseller_id=r.user_id
        WHERE r.active=1
        GROUP BY r.user_id, r.profit_balance
        ORDER BY r.user_id
        """
    )
    rows = cur.fetchall()
    lines = ["🏪 *POS Control*", ""]
    if not rows:
        lines.append("No active POS yet.")
    else:
        for uid, profit, cnt in rows:
            lines.append(f"• `{uid}` | clients={int(cnt or 0)} | profit={float(profit or 0):.3f}{CURRENCY}")
    lines.append("")
    lines.append("Use buttons below to add/remove POS.")
    return "\n".join(lines)[:3800]


def kb_products(cid: int, viewer_uid: Optional[int] = None) -> InlineKeyboardMarkup:

    cur.execute("SELECT pid,title,price FROM products WHERE cid=? AND active=1", (cid,))
    items = cur.fetchall()
    items.sort(key=lambda r: extract_sort_value(r[1]))
    rows = []
    for pid, title, price in items:
        stock = product_stock(pid)
        show_price = get_user_product_price(viewer_uid, pid, float(price)) if viewer_uid else float(price)
        label = f"{title} | {money(float(show_price))} | 📦{stock}"
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
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📥 Manual Orders", callback_data="admin:manuallist:0")],
            ]
        )
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Dashboard", callback_data="admin:dash"), InlineKeyboardButton("👥 Customers", callback_data="admin:users:0")],
            [InlineKeyboardButton("🧮 Daily Audit", callback_data="admin:dailyauditday:today"), InlineKeyboardButton("📥 Manual Orders", callback_data="admin:manuallist:0")],
            [InlineKeyboardButton("🛍 Products Control", callback_data="admin:products"), InlineKeyboardButton("🛠 Manual Control", callback_data="admin:manualprices")],
            [InlineKeyboardButton("➕ Add Balance", callback_data="admin:addbal"), InlineKeyboardButton("➖ Take Balance", callback_data="admin:takebal")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcastall"), InlineKeyboardButton("🏪 POS Control", callback_data="admin:resellers")],
            [InlineKeyboardButton("👑 Admins", callback_data="admin:admins")],
        ]
    )
def kb_admin_products_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 List Products", callback_data="admin:listprod"), InlineKeyboardButton("💲 Set Price", callback_data="admin:setprice")],
            [InlineKeyboardButton("🎯 User Price", callback_data="admin:userprice"), InlineKeyboardButton("📌 User Prices", callback_data="admin:userpricelist")],
            [InlineKeyboardButton("⛔ Toggle Product", callback_data="admin:toggle"), InlineKeyboardButton("🗑 Delete Product", callback_data="admin:delprod")],
            [InlineKeyboardButton("➕ Add Category", callback_data="admin:addcat"), InlineKeyboardButton("➕ Add Product", callback_data="admin:addprod")],
            [InlineKeyboardButton("➕ Add Codes (text)", callback_data="admin:addcodes"), InlineKeyboardButton("📄 Add Codes (file)", callback_data="admin:addcodesfile")],
            [InlineKeyboardButton("🗑 Delete Category (FULL)", callback_data="admin:delcatfull")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")],
        ]
    )
def kb_manual_prices_panel() -> InlineKeyboardMarkup:
    s1 = "ON ✅" if manual_flag_enabled("MANUAL_SHAHID_ENABLED") else "OFF ⛔"
    s2 = "ON ✅" if manual_flag_enabled("MANUAL_FF_ENABLED") else "OFF ⛔"
    s3 = "ON ✅" if manual_flag_enabled("SHAHID_MENA_3M_ENABLED") else "OFF ⛔"
    s4 = "ON ✅" if manual_flag_enabled("SHAHID_MENA_12M_ENABLED") else "OFF ⛔"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✍️ Edit Manual Prices", callback_data="admin:manualprices:edit")],
            [InlineKeyboardButton("🎯 User Manual Price", callback_data="admin:usermanualprice"), InlineKeyboardButton("📌 User Manual Prices", callback_data="admin:usermanualpricelist")],
            [InlineKeyboardButton(f"📺 Shahid {s1}", callback_data="admin:manualtoggle:MANUAL_SHAHID_ENABLED"), InlineKeyboardButton(f"💎 Free Fire {s2}", callback_data="admin:manualtoggle:MANUAL_FF_ENABLED")],
            [InlineKeyboardButton(f"Shahid 3M {s3}", callback_data="admin:manualtoggle:SHAHID_MENA_3M_ENABLED"), InlineKeyboardButton(f"Shahid 12M {s4}", callback_data="admin:manualtoggle:SHAHID_MENA_12M_ENABLED")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")],
        ]
    )
def manual_prices_text() -> str:
    cur.execute("SELECT pkey, price FROM manual_prices ORDER BY pkey")
    rows = cur.fetchall()
    lines = ["🛠 *Manual Control*", "", "الأسعار الحالية:"]
    for k, p in rows:
        lines.append(f"• `{k}` = *{float(p):.3f}{CURRENCY}*")
    lines.append("")
    lines.append("المفاتيح المتاحة للتعديل:")
    lines.append("`SHAHID_MENA_3M`, `SHAHID_MENA_12M`, `FF_100`, `FF_210`, `FF_530`, `FF_1080`, `FF_2200`")
    lines.append("")
    lines.append("للتعديل اضغط: ✍️ Edit Manual Prices")
    lines.append("ثم أرسل الصيغة: `KEY | PRICE`")
    lines.append("مثال: `FF_100 | 0.95`")
    return "\n".join(lines)[:3800]
def kb_daily_audit(target_date: Optional[str] = None) -> InlineKeyboardMarkup:
    td = target_date or datetime.utcnow().strftime("%Y-%m-%d")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📅 Today", callback_data="admin:dailyauditday:today"), InlineKeyboardButton("🕘 Yesterday", callback_data="admin:dailyauditday:yesterday")],
            [InlineKeyboardButton(f"🔄 Refresh {td}", callback_data=f"admin:dailyauditday:{td}")],
            [InlineKeyboardButton("✍️ Custom Date", callback_data="admin:dailyauditcustom")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")],
        ]
    )
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
def _ff_calc_totals(cart: Dict[str, int], uid: Optional[int] = None):
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
        price = get_user_manual_price(uid, sku, get_manual_price(sku, MANUAL_PRICE_DEFAULTS.get(sku, 0.0)))
        total_price += float(price) * qty
        total_diamonds += diamonds * qty
        lines.append((title, qty, float(price), diamonds))
    order_map = {t: i for i, (_, t, _) in enumerate(FF_PACKS)}
    lines.sort(key=lambda x: order_map.get(x[0], 999))
    return total_price, total_diamonds, lines
def kb_manual_services() -> InlineKeyboardMarkup:
    rows = []
    if manual_flag_enabled("MANUAL_SHAHID_ENABLED"):
        rows.append([InlineKeyboardButton("📺 Shahid", callback_data="manual:shahid")])
    if manual_flag_enabled("MANUAL_FF_ENABLED"):
        rows.append([InlineKeyboardButton("💎 Free Fire (MENA)", callback_data="manual:ff")])
    if not rows:
        rows.append([InlineKeyboardButton("⛔ No manual services available", callback_data="noop")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="goto:cats")])
    return InlineKeyboardMarkup(rows)
def kb_shahid_plans(uid: Optional[int] = None) -> InlineKeyboardMarkup:
    p3 = get_user_manual_price(uid, "SHAHID_MENA_3M", get_manual_price("SHAHID_MENA_3M", MANUAL_PRICE_DEFAULTS["SHAHID_MENA_3M"]))
    p12 = get_user_manual_price(uid, "SHAHID_MENA_12M", get_manual_price("SHAHID_MENA_12M", MANUAL_PRICE_DEFAULTS["SHAHID_MENA_12M"]))
    rows = []
    if manual_flag_enabled("SHAHID_MENA_3M_ENABLED"):
        rows.append([InlineKeyboardButton(f"Shahid [MENA] | 3 Month | {p3:.3f}{CURRENCY}", callback_data="manual:shahid:MENA_3M")])
    if manual_flag_enabled("SHAHID_MENA_12M_ENABLED"):
        rows.append([InlineKeyboardButton(f"Shahid [MENA] | 12 Month | {p12:.3f}{CURRENCY}", callback_data="manual:shahid:MENA_12M")])
    if not rows:
        rows.append([InlineKeyboardButton("⛔ All Shahid plans disabled", callback_data="noop")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="manual:services")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")])
    return InlineKeyboardMarkup(rows)
def ff_menu_text(uid: Optional[int] = None) -> str:
    return (
        "💎 *Free Fire (MENA)*\n\n"
        "🛒 Add packs to cart ثم Checkout.\n"
        "⏱ Delivery: *1-5 minutes*\n\n"
        "✅ تقدر تمسح السلة أو تكمل الدفع\n\n"
        + manual_hours_text()
    )
def kb_ff_menu(context, uid: Optional[int] = None) -> InlineKeyboardMarkup:
    cart = _ff_cart_get(context)
    rows = []
    for sku, title, _ in FF_PACKS:
        qty = int(cart.get(sku, 0))
        suffix = f"  🧺[{qty}]" if qty > 0 else ""
        price = get_user_manual_price(uid, sku, get_manual_price(sku, MANUAL_PRICE_DEFAULTS.get(sku, 0.0)))
        rows.append([InlineKeyboardButton(f"{title} 💎 | {float(price):.3f}{CURRENCY}{suffix}", callback_data=f"manual:ff:add:{sku}")])
    rows.append([InlineKeyboardButton("🗑 Clear Cart", callback_data="manual:ff:clear")])
    rows.append([InlineKeyboardButton("✅ Proceed to Checkout", callback_data="manual:ff:checkout")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="manual:services")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")])
    return InlineKeyboardMarkup(rows)
def ff_checkout_text(context, uid: Optional[int] = None) -> str:
    cart = _ff_cart_get(context)
    total_price, total_diamonds, lines = _ff_calc_totals(cart, uid=uid)
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
async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    await update.message.reply_text(f"🆔 Your ID: `{update.effective_user.id}`", parse_mode=ParseMode.MARKDOWN, reply_markup=REPLY_MENU)
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
    reseller_id = get_client_reseller_id(uid)
    if reseller_id is not None:
        text = (
            "💰 *Wallet*\n\n"
            f"👤 Name: *{(u.first_name or 'User')}*\n"
            f"🆔 ID: `{uid}`\n"
            f"💎 Balance: *{bal:.3f}* {CURRENCY}\n\n"
            "🏪 هذا الحساب تابع لنقطة بيع.\n"
            f"📌 POS ID: `{reseller_id}`\n"
            "لشحن الرصيد تواصل مع نقطة البيع الخاصة بك."
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
    if t.lower() == "id":
        return await update.message.reply_text(f"🆔 Your ID: `{update.effective_user.id}`", parse_mode=ParseMode.MARKDOWN, reply_markup=REPLY_MENU)
    if t == "🛒 Our Products":
        return await show_categories(update, context)
    if t == "💰 My Balance":
        return await show_balance(update, context)
    if t == "📦 My Orders":
        return await show_orders(update, context, rng=context.user_data.get(UD_ORD_RNG) or "all", page=0)
    if t == "☎️ Contact Support":
        return await show_support(update, context)
    if t == "🏪 POS Panel":
        if not is_reseller(update.effective_user.id):
            return await update.message.reply_text("❌ هذه القائمة متاحة لنقاط البيع فقط.", reply_markup=REPLY_MENU)
        return await update.message.reply_text(pos_panel_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
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
    title, base_price = row
    price = get_user_product_price(update.effective_user.id, pid, float(base_price))
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
    ok_charge, bal_before, bal_after = charge_balance_logged(uid, price, "MANUAL_SHAHID_CHARGE", note=plan_title)
    if not ok_charge:
        bal = get_balance(uid)
        missing = price - bal
        await update.message.reply_text(
            f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {price:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
            reply_markup=kb_topup_now(),
        )
        return ConversationHandler.END
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
        await notify_manual_order_admins(
            context,
            (
                "⚡ *MANUAL ORDER (SHAHID)*\n"
                f"🧾 Manual ID: *{mid}*\n"
                f"👤 User: `{uid}`\n"
                f"📦 Plan: *{plan_title}*\n"
                f"💵 Price: *{price:.3f} {CURRENCY}*\n"
                f"🟨 Email: `{email}`\n"
                f"🟥 Password: `{pwd}`\n"
            ),
        )
    except Exception as e:
        logger.exception("Failed to notify admins about Shahid manual order %s: %s", mid, e)
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
    total_price, total_diamonds, lines = _ff_calc_totals(cart, uid=uid)
    if not lines or total_price <= 0:
        await update.message.reply_text("🛒 Cart is empty. Open Manual Order again.", reply_markup=REPLY_MENU)
        return ConversationHandler.END
    ok_charge, bal_before, bal_after = charge_balance_logged(uid, total_price, "MANUAL_FF_CHARGE", note=f"diamonds={total_diamonds}")
    if not ok_charge:
        bal = get_balance(uid)
        missing = total_price - bal
        await update.message.reply_text(
            f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total_price:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
            reply_markup=kb_topup_now(),
        )
        return ConversationHandler.END
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
        await notify_manual_order_admins(
            context,
            (
                "⚡ *MANUAL ORDER (FREE FIRE MENA)*\n"
                f"🧾 Manual ID: *{mid}*\n"
                f"👤 User ID: `{uid}`\n"
                f"🆔 Player ID: `{player_id}`\n"
                f"💎 Diamonds: *{total_diamonds}*\n"
                f"💵 Total: *{total_price:.3f} {CURRENCY}*\n\n"
                f"🧺 Cart:\n`{note}`"
            ),
        )
    except Exception as e:
        logger.exception("Failed to notify admins about FF manual order %s: %s", mid, e)
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
def _resolve_audit_date(raw: Optional[str] = None) -> str:
    if not raw or raw == "today":
        return datetime.utcnow().strftime("%Y-%m-%d")
    if raw == "yesterday":
        return (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    return raw
def _daily_audit_report(target_date: str) -> Tuple[str, List[Tuple[int, str, str]]]:
    target_date = _resolve_audit_date(target_date)
    day_start_s = f"{target_date} 00:00:00"
    day_end_s = f"{target_date} 23:59:59"
    users = set()
    for sql in [
        "SELECT DISTINCT user_id FROM balance_ledger WHERE datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)",
        "SELECT DISTINCT user_id FROM orders WHERE status='COMPLETED' AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)",
        "SELECT DISTINCT user_id FROM manual_orders WHERE datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)",
        "SELECT DISTINCT user_id FROM deposits WHERE status='APPROVED' AND datetime(COALESCE(approved_at, created_at)) >= datetime(?) AND datetime(COALESCE(approved_at, created_at)) <= datetime(?)",
    ]:
        cur.execute(sql, (day_start_s, day_end_s))
        users.update(int(r[0]) for r in cur.fetchall())
    if not users:
        return (f"🧮 *Daily Audit* — `{target_date}`\n\nNo accounting activity found.", [])
    mismatches = 0
    alerts: List[Tuple[int, str, str]] = []
    lines = [f"🧮 *Daily Audit* — `{target_date}`", ""]
    for uid in sorted(users):
        cur.execute(
            "SELECT balance_before FROM balance_ledger WHERE user_id=? AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?) ORDER BY id ASC LIMIT 1",
            (uid, day_start_s, day_end_s),
        )
        row_first = cur.fetchone()
        cur.execute(
            "SELECT COALESCE(SUM(delta),0), COALESCE(SUM(CASE WHEN delta>0 THEN delta ELSE 0 END),0), COALESCE(SUM(CASE WHEN delta<0 THEN -delta ELSE 0 END),0), COUNT(*) FROM balance_ledger WHERE user_id=? AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)",
            (uid, day_start_s, day_end_s),
        )
        net_delta, total_in, total_out, tx_count = cur.fetchone()
        net_delta = float(net_delta or 0)
        total_in = float(total_in or 0)
        total_out = float(total_out or 0)
        tx_count = int(tx_count or 0)
        actual = get_balance(uid)
        opening = float(row_first[0]) if row_first else float(actual - net_delta)
        expected = opening + net_delta
        diff = actual - expected
        cur.execute(
            "SELECT COALESCE(SUM(total),0) FROM orders WHERE user_id=? AND status='COMPLETED' AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)",
            (uid, day_start_s, day_end_s),
        )
        orders_total = float(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COALESCE(SUM(price),0) FROM manual_orders WHERE user_id=? AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?) AND status != 'REJECTED'",
            (uid, day_start_s, day_end_s),
        )
        manual_total = float(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM deposits WHERE user_id=? AND status='APPROVED' AND datetime(COALESCE(approved_at, created_at)) >= datetime(?) AND datetime(COALESCE(approved_at, created_at)) <= datetime(?)",
            (uid, day_start_s, day_end_s),
        )
        dep_total = float(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COALESCE(SUM(-delta),0) FROM balance_ledger WHERE user_id=? AND source_type='ORDER_PURCHASE' AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)",
            (uid, day_start_s, day_end_s),
        )
        order_ledger_total = float(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COALESCE(SUM(-delta),0) FROM balance_ledger WHERE user_id=? AND source_type IN ('MANUAL_SHAHID_CHARGE','MANUAL_FF_CHARGE') AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)",
            (uid, day_start_s, day_end_s),
        )
        manual_ledger_total = float(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COALESCE(SUM(delta),0) FROM balance_ledger WHERE user_id=? AND source_type='DEPOSIT_APPROVED' AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)",
            (uid, day_start_s, day_end_s),
        )
        dep_ledger_total = float(cur.fetchone()[0] or 0)
        orders_gap = orders_total - order_ledger_total
        manual_gap = manual_total - manual_ledger_total
        deposits_gap = dep_total - dep_ledger_total
        has_issue = abs(diff) > 0.009 or abs(orders_gap) > 0.009 or abs(manual_gap) > 0.009 or abs(deposits_gap) > 0.009
        status_icon = "⚠️ MISMATCH" if has_issue else "✅ OK"
        if has_issue:
            mismatches += 1
        lines.append(f"👤 `{uid}` | {status_icon}")
        lines.append(f"Open: {opening:.3f}{CURRENCY} | In: +{total_in:.3f} | Out: -{total_out:.3f}")
        lines.append(f"Expected: {expected:.3f}{CURRENCY} | Actual: {actual:.3f}{CURRENCY} | Diff: {diff:+.3f}{CURRENCY} | Tx: {tx_count}")
        if abs(orders_gap) > 0.009:
            lines.append(f"ordersvsledger={orders_gap:+.3f}{CURRENCY}")
            alerts.append((uid, f"orders_gap_{target_date}", f"Orders total and ledger differ by {orders_gap:+.3f}{CURRENCY}."))
        if abs(manual_gap) > 0.009:
            lines.append(f"manualvsledger={manual_gap:+.3f}{CURRENCY}")
            alerts.append((uid, f"manual_gap_{target_date}", f"Manual charges and ledger differ by {manual_gap:+.3f}{CURRENCY}."))
        if abs(deposits_gap) > 0.009:
            lines.append(f"depositsvsledger={deposits_gap:+.3f}{CURRENCY}")
            alerts.append((uid, f"deposit_gap_{target_date}", f"Approved deposits and ledger differ by {deposits_gap:+.3f}{CURRENCY}."))
        if abs(diff) > 0.009:
            alerts.append((uid, f"balance_gap_{target_date}", f"Actual balance differs from expected by {diff:+.3f}{CURRENCY}."))
        lines.append("")
    lines.append(f"⚠️ Mismatches: {mismatches}")
    if mismatches:
        lines.append("🚨 يوجد فرق أو خطأ محاسبي، راجع العملاء المشار إليهم.")
    else:
        lines.append("✅ لا يوجد فرق محاسبي في هذا اليوم.")
    return ("\n".join(lines)[:3900], alerts)
async def _daily_audit_report_with_alerts(context: ContextTypes.DEFAULT_TYPE, target_date: str) -> str:
    text, alerts = _daily_audit_report(target_date)
    audit_date = _resolve_audit_date(target_date)
    for uid, issue_key, message_text in alerts:
        await send_audit_alert(context, audit_date, uid, issue_key, message_text)
    return text
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
    if data == "pos:panel":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        return await q.edit_message_text(pos_panel_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
    if data == "pos:clients":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        return await q.edit_message_text(pos_clients_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
    if data == "pos:profit":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        amount = reseller_profit_balance(update.effective_user.id)
        text_profit = (
            "💰 *POS Profit*\n\n"
            f"Pending Profit: *{amount:.3f}{CURRENCY}*\n\n"
            "اضغط الزر لتحويل الربح المتجمع إلى رصيدك."
        )
        return await q.edit_message_text(text_profit, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
    if data == "pos:profit:transfer":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        amount = transfer_reseller_profit_to_balance(update.effective_user.id)
        if amount <= 0:
            await q.answer("No profit yet", show_alert=True)
            return await q.edit_message_text(pos_panel_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
        await q.answer("Profit transferred ✅", show_alert=False)
        return await q.edit_message_text(
            f"✅ تم تحويل أرباح نقطة البيع إلى الرصيد.\n\nAmount: *{amount:.3f}{CURRENCY}*\nBalance now: *{get_balance(update.effective_user.id):.3f}{CURRENCY}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_pos_panel(update.effective_user.id),
        )
    if data == "pos:addclient":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        context.user_data[UD_ADMIN_MODE] = "pos_add_client"
        await q.edit_message_text("➕ Send client user_id to attach under your POS.\n\n/cancel to stop")
        return ST_ADMIN_INPUT
    if data == "pos:removeclient":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        context.user_data[UD_ADMIN_MODE] = "pos_remove_client"
        await q.edit_message_text("➖ Send client user_id to remove from your POS.\n\n/cancel to stop")
        return ST_ADMIN_INPUT
    if data == "pos:setprice":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        context.user_data[UD_ADMIN_MODE] = "pos_set_price"
        await q.edit_message_text(
            (
                "🎯 *Set Client Price*\n\nFormat:\n`client_user_id | pid | price`\nExample:\n`1997968014 | 12 | 10`\n\nDelete custom price:\n`del | client_user_id | pid`\n\n⚠️ لا يمكن أقل من سعر البوت الأساسي.\n\n"
                + pos_all_products_text()
                + "\n\n/cancel to stop"
            )[:3900],
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_ADMIN_INPUT
    if data == "pos:charge":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        context.user_data[UD_ADMIN_MODE] = "pos_charge_client"
        await q.edit_message_text(
            "💸 *Charge Client from POS Balance*\n\nFormat:\n`client_user_id | amount`\nExample:\n`1997968014 | 50`\n\nسيتم خصم المبلغ من رصيد نقطة البيع وإضافته لرصيد العميل.\n\n/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_ADMIN_INPUT
    if data == "pos:setmanual":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        context.user_data[UD_ADMIN_MODE] = "pos_set_manual_price"
        await q.edit_message_text(
            (
                "🛠 *Set Client Manual Price*\n\n"
                "Set/Update:\n"
                "`client_user_id | KEY | price`\n"
                "Example:\n"
                "`1997968014 | FF_100 | 0.95`\n\n"
                "Delete custom manual price:\n"
                "`del | client_user_id | KEY`\n\n"
                + pos_all_manual_keys_text()
                + "\n\n/cancel to stop"
            )[:3900],
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_ADMIN_INPUT
    if data == "pos:prices:auto":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        return await q.edit_message_text(pos_product_prices_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
    if data == "pos:prices:manual":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        return await q.edit_message_text(pos_manual_prices_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
    if data == "pos:catalog:auto":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        return await q.edit_message_text(pos_all_products_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
    if data == "pos:catalog:manual":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        return await q.edit_message_text(pos_all_manual_keys_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_pos_panel(update.effective_user.id))
    if data == "pos:notify":
        if not is_reseller(update.effective_user.id):
            return await q.edit_message_text("❌ POS only.")
        context.user_data[UD_ADMIN_MODE] = "pos_broadcast_clients"
        await q.edit_message_text(
            "📢 *Notify My Clients*\n\n"
            "أرسل الرسالة الآن وسيتم إرسالها إلى عملائك فقط.\n\n"
            "/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_ADMIN_INPUT
    # Manual nav
    # Manual nav
    if data == "manual:back" or data == "manual:services":
        return await q.edit_message_text("⚡ *MANUAL ORDER*\nSelect a service:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_services())
    if data == "manual:shahid":
        if not manual_flag_enabled("MANUAL_SHAHID_ENABLED"):
            return await q.edit_message_text("⛔ خدمة Shahid معطلة حالياً.", reply_markup=kb_manual_services())
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(), parse_mode=ParseMode.MARKDOWN)
        text = (
            "📺 *Shahid*\n\n"
            "📩 المطلوب منك:\n"
            "➡️ Gmail جديد\n"
            "➡️ Password مؤقت\n\n"
            + manual_hours_text()
        )
        return await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_shahid_plans(update.effective_user.id))
    if data.startswith("manual:shahid:"):
        if not manual_flag_enabled("MANUAL_SHAHID_ENABLED"):
            return await q.edit_message_text("⛔ خدمة Shahid معطلة حالياً.", reply_markup=kb_manual_services())
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(), parse_mode=ParseMode.MARKDOWN)
        plan = data.split(":")[2]
        if plan == "MENA_3M":
            if not manual_flag_enabled("SHAHID_MENA_3M_ENABLED"):
                return await q.edit_message_text("⛔ باقة Shahid 3M معطلة حالياً.", reply_markup=kb_shahid_plans(update.effective_user.id))
            plan_title = "Shahid [MENA] | 3 Month"
            price = get_user_manual_price(update.effective_user.id, "SHAHID_MENA_3M", get_manual_price("SHAHID_MENA_3M", MANUAL_PRICE_DEFAULTS["SHAHID_MENA_3M"]))
        elif plan == "MENA_12M":
            if not manual_flag_enabled("SHAHID_MENA_12M_ENABLED"):
                return await q.edit_message_text("⛔ باقة Shahid 12M معطلة حالياً.", reply_markup=kb_shahid_plans(update.effective_user.id))
            plan_title = "Shahid [MENA] | 12 Month"
            price = get_user_manual_price(update.effective_user.id, "SHAHID_MENA_12M", get_manual_price("SHAHID_MENA_12M", MANUAL_PRICE_DEFAULTS["SHAHID_MENA_12M"]))
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
        if not manual_flag_enabled("MANUAL_FF_ENABLED"):
            return await q.edit_message_text("⛔ خدمة Free Fire معطلة حالياً.", reply_markup=kb_manual_services())
        if not manual_open_now() and not is_admin_any(update.effective_user.id):
            return await q.edit_message_text("⛔ الشحن اليدوي مغلق الآن.\n\n" + manual_hours_text(), parse_mode=ParseMode.MARKDOWN)
        return await q.edit_message_text(ff_menu_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_ff_menu(context, update.effective_user.id))
    if data.startswith("manual:ff:add:"):
        sku = data.split(":")[3]
        if not _ff_pack(sku):
            return await q.edit_message_text("❌ Unknown pack.", reply_markup=kb_ff_menu(context))
        cart = _ff_cart_get(context)
        cart[sku] = int(cart.get(sku, 0)) + 1
        context.user_data[UD_FF_CART] = cart
        return await q.edit_message_text(ff_menu_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_ff_menu(context, update.effective_user.id))
    if data == "manual:ff:clear":
        context.user_data[UD_FF_CART] = {}
        context.user_data.pop(UD_FF_TOTAL, None)
        context.user_data.pop("ff_total_diamonds", None)
        return await q.edit_message_text(ff_menu_text(update.effective_user.id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_ff_menu(context, update.effective_user.id))
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
        await q.edit_message_text(ff_checkout_text(context, update.effective_user.id), parse_mode=ParseMode.MARKDOWN)
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
        await q.edit_message_text(
            "📢 *Broadcast to all users*\n\nSend the message now. It will be sent to all users.\n\n/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_ADMIN_INPUT
    if data == "admin:resellers":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text(reseller_admin_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_reseller_admin_panel())
    if data == "admin:resellers:list":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text(reseller_admin_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_reseller_admin_panel())
    if data == "admin:resellers:add":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "reseller_add"
        await q.edit_message_text("🏪 Send user_id to add as POS.\n\n/cancel to stop")
        return ST_ADMIN_INPUT
    if data == "admin:resellers:del":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "reseller_del"
        await q.edit_message_text("🏪 Send user_id to remove from POS.\n\n/cancel to stop")
        return ST_ADMIN_INPUT
    if data == "admin:products":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("🛍 *Products Control*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_products_panel())
    if data == "admin:userprice":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "userprice"
        await q.edit_message_text(
            "🎯 *User Custom Price*\n\nSet special price for one customer only.\n\nSet/Update:\n`user_id | pid | price`\nExample:\n`1997968014 | 12 | 8.5`\n\nDelete custom price:\n`del | user_id | pid`\nExample:\n`del | 1997968014 | 12`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_ADMIN_INPUT
    if data == "admin:userpricelist":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute(
            """
            SELECT upp.user_id, upp.pid, upp.price, p.title
            FROM user_product_prices upp
            LEFT JOIN products p ON p.pid=upp.pid
            ORDER BY upp.user_id ASC, upp.pid ASC
            LIMIT 100
            """
        )
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("📌 No custom user prices found.", reply_markup=kb_admin_products_panel())
        lines = ["📌 *User Custom Prices*", ""]
        for xuid, pid, price, ptitle in rows:
            lines.append(f"• User `{xuid}` | PID `{pid}` | *{float(price):.3f}{CURRENCY}* | {ptitle or '-'}")
        lines.append("")
        lines.append("Use 🎯 User Price to add/update/delete.")
        return await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_products_panel())
    if data == "admin:usermanualprice":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "usermanualprice"
        await q.edit_message_text(
            "🎯 *User Manual Price*\n\n"
            "Set special manual price for one customer only.\n\n"
            "Set/Update:\n"
            "`user_id | KEY | price`\n"
            "Example:\n"
            "`1997968014 | FF_100 | 0.80`\n\n"
            "Delete custom manual price:\n"
            "`del | user_id | KEY`\n"
            "Example:\n"
            "`del | 1997968014 | FF_100`\n\n"
            "Allowed keys:\n"
            "`SHAHID_MENA_3M`, `SHAHID_MENA_12M`, `FF_100`, `FF_210`, `FF_530`, `FF_1080`, `FF_2200`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ST_ADMIN_INPUT
    if data == "admin:usermanualpricelist":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute(
            """
            SELECT ump.user_id, ump.pkey, ump.price
            FROM user_manual_prices ump
            ORDER BY ump.user_id ASC, ump.pkey ASC
            LIMIT 200
            """
        )
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("📌 No custom manual prices found.", reply_markup=kb_manual_prices_panel())
        lines = ["📌 *User Manual Prices*", ""]
        for xuid, pkey, price in rows:
            lines.append(f"• User `{xuid}` | `{pkey}` | *{float(price):.3f}{CURRENCY}*")
        lines.append("")
        lines.append("Use 🎯 User Manual Price to add/update/delete.")
        return await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_prices_panel())
    if data == "admin:manualprices":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text(manual_prices_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_prices_panel())
    if data == "admin:manualprices:edit":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "setmanualprice"
        return await q.edit_message_text(manual_prices_text() + "\n\nSend now: `KEY | PRICE`", parse_mode=ParseMode.MARKDOWN)
    if data.startswith("admin:manualtoggle:"):
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        key = data.split(":", 2)[2]
        set_manual_flag(key, not manual_flag_enabled(key))
        return await q.edit_message_text(manual_prices_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_manual_prices_panel())
    if data.startswith("admin:dailyauditday:"):
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        target_date = _resolve_audit_date(data.split(":", 2)[2])
        report = await _daily_audit_report_with_alerts(context, target_date)
        return await q.edit_message_text(report, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_daily_audit(target_date))
    if data == "admin:dailyauditcustom":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "dailyaudit_date"
        await q.edit_message_text("📅 Send date as: `YYYY-MM-DD`\nExample: `2026-03-06`", parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT
    # Manual prices view
    if data == "admin:manualprices_legacy_unused":
        if admin_role(update.effective_user.id) != ROLE_OWNER:
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute("SELECT pkey, price FROM manual_prices ORDER BY pkey")
        rows = cur.fetchall()
        lines = ["🛠 *Manual Prices*\nSend: `key | price`\nExample: `FF_100 | 0.95`\n"]
        for k, p in rows:
            lines.append(f"• `{k}` = *{float(p):.3f}{CURRENCY}*")
        lines.append("\nKeys: SHAHID_MENA_3M, SHAHID_MENA_12M, FF_100, FF_210, FF_530, FF_1080, FF_2200")
        context.user_data[UD_ADMIN_MODE] = "setmanualprice"
        await q.edit_message_text("\n".join(lines)[:3800], parse_mode=ParseMode.MARKDOWN)
        return ST_ADMIN_INPUT
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
        cur.execute("SELECT user_id, price, status, service, plan_title, note FROM manual_orders WHERE id=?", (mid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Manual order not found.")
        uid, price, status, service, plan_title, manual_note = int(row[0]), float(row[1]), row[2], row[3], row[4], (row[5] or "")
        if status != "PENDING":
            return await q.edit_message_text("❌ This manual order is not pending.")
        approver_id = update.effective_user.id
        delivered_note = f"APPROVED_BY:{approver_id}"
        cur.execute("UPDATE manual_orders SET status='COMPLETED', approved_by=?, delivered_text=? WHERE id=?", (approver_id, delivered_note, mid))
        con.commit()
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "✅ *تم شحن بنجاح!*\n"
                    f"🧾 Manual Order: *#{mid}*\n"
                    f"📦 Service: {plan_title}\n"
                    f"💵 Paid: *{price:.3f} {CURRENCY}*\n"
                    f"🆔 Admin Approver ID: `{approver_id}`\n\n"
                    "شكراً لك ❤️"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.exception("Failed to notify user %s about manual approve %s: %s", uid, mid, e)
        reseller_id = get_client_reseller_id(uid)
        manual_margin, manual_margin_details = calculate_pos_manual_profit(
            reseller_id,
            uid,
            service,
            plan_title,
            manual_note,
        )
        if reseller_id and manual_margin > 1e-9:
            add_reseller_profit(reseller_id, manual_margin, "POS_MANUAL_MARGIN", str(mid), f"client={uid} service={service} details={manual_margin_details}")
            try:
                detail_text = f"\nDetails: {manual_margin_details}" if manual_margin_details else ""
                await context.bot.send_message(
                    chat_id=reseller_id,
                    text=(
                        "💰 *POS Profit Added*\n"
                        f"Client: `{uid}`\n"
                        f"Manual Order: *#{mid}*\n"
                        f"Margin added: *{manual_margin:.3f}{CURRENCY}*{detail_text}\n"
                        f"Pending profit: *{reseller_profit_balance(reseller_id):.3f}{CURRENCY}*"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                logger.exception("Failed notifying reseller %s about manual margin", reseller_id)
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
        bal_before, bal_after = add_balance_logged(uid, price, 'MANUAL_REFUND', source_id=str(mid), note=reason_text)
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
        title, base_price, cid = row
        stock = product_stock(pid)
        show_price = get_user_product_price(update.effective_user.id, pid, float(base_price))
        custom_note = "\n🏷 Special customer price applied" if abs(float(show_price) - float(base_price)) > 1e-9 else ""
        text = (
            f"🎁 *{title}*\n\n"
            f"🆔 ID: `{pid}`\n"
            f"💵 Price: *{float(show_price):.3f}* {CURRENCY}{custom_note}\n"
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
        title, base_price = row
        uid = update.effective_user.id
        price = get_user_product_price(uid, pid, float(base_price))
        total = float(price) * qty
        ok_charge, bal_before, bal_after = charge_balance_logged(uid, total, "ORDER_PURCHASE", note=title)
        if not ok_charge:
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
                add_balance_logged(uid, total, 'ORDER_PURCHASE_REFUND', note='stock error refund')
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
            add_balance_logged(uid, total, 'ORDER_PURCHASE_REFUND', note='exception refund')
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
        reseller_id = get_client_reseller_id(uid)
        admin_base_price = get_effective_product_base_for_pos(uid, pid)
        margin = (float(price) - float(admin_base_price)) * qty
        if reseller_id and margin > 1e-9 and has_pos_product_price(reseller_id, uid, pid):
            add_reseller_profit(reseller_id, margin, "POS_ORDER_MARGIN", str(oid), f"client={uid} pid={pid} qty={qty}")
            try:
                await context.bot.send_message(
                    chat_id=reseller_id,
                    text=(
                        "💰 *POS Profit Added*\n"
                        f"Client: `{uid}`\n"
                        f"Order: *#{oid}*\n"
                        f"Margin added: *{margin:.3f}{CURRENCY}*\n"
                        f"Pending profit: *{reseller_profit_balance(reseller_id):.3f}{CURRENCY}*"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                logger.exception("Failed notifying reseller %s about margin", reseller_id)
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
        reseller_id = get_client_reseller_id(uid)
        if reseller_id and not is_admin_any(uid):
            return await q.edit_message_text(f"⛔ هذا الحساب تابع لنقطة بيع `{reseller_id}`.\nشحن الرصيد يتم من خلال نقطة البيع فقط.", parse_mode=ParseMode.MARKDOWN)
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
    if not is_admin_any(uid_admin) and not is_reseller(uid_admin):
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
    text = (update.message.text or "").strip() if update.message else ""
    if mode == "pos_add_client":
        if not is_reseller(uid_admin):
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
        if not text.isdigit():
            await update.message.reply_text("❌ Send client user_id only.")
            return ST_ADMIN_INPUT
        client_uid = int(text)
        ok, msg = assign_client_to_reseller(uid_admin, client_uid)
        if ok:
            try:
                await context.bot.send_message(
                    client_uid,
                    (
                        "✅ تم ربط حسابك بنقطة بيع داخل البوت.\n"
                        f"🏪 POS ID: `{uid_admin}`\n"
                        "🛒 الآن أي أسعار خاصة بنقطة البيع ستظهر لك تلقائياً داخل المنتجات والخدمات اليدوية."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=REPLY_MENU,
                )
            except Exception:
                logger.exception("Failed notifying client %s about POS attach", client_uid)
        await update.message.reply_text(("✅ " if ok else "❌ ") + msg, reply_markup=REPLY_MENU)
        return ConversationHandler.END
    if mode == "pos_remove_client":
        if not is_reseller(uid_admin):
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
        if not text.isdigit():
            await update.message.reply_text("❌ Send client user_id only.")
            return ST_ADMIN_INPUT
        client_uid = int(text)
        ok = remove_client_from_reseller(uid_admin, client_uid)
        if ok:
            try:
                await context.bot.send_message(
                    client_uid,
                    "ℹ️ تم فك ربطك من نقطة البيع داخل البوت. عادت أسعارك الافتراضية.",
                    reply_markup=REPLY_MENU,
                )
            except Exception:
                logger.exception("Failed notifying client %s about POS detach", client_uid)
        await update.message.reply_text(("✅ Client removed." if ok else "❌ Client not found under your POS."), reply_markup=REPLY_MENU)
        return ConversationHandler.END
    if mode == "pos_set_price":
        if not is_reseller(uid_admin):
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
        m_del = re.match(r"^del\s*\|\s*(\d+)\s*\|\s*(\d+)$", text, re.I)
        if m_del:
            client_uid = int(m_del.group(1)); pid = int(m_del.group(2))
            if not reseller_can_manage_client(uid_admin, client_uid):
                await update.message.reply_text("❌ هذا العميل ليس تابعاً لك.")
                return ConversationHandler.END
            clear_pos_product_price(uid_admin, client_uid, pid)
            await update.message.reply_text("✅ تم حذف سعر POS الخاص للمنتج لهذا العميل.", reply_markup=REPLY_MENU)
            return ConversationHandler.END
        m = re.match(r"^(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)$", text)
        if not m:
            await update.message.reply_text("❌ Format: client_user_id | pid | price")
            return ST_ADMIN_INPUT
        client_uid, pid, price = int(m.group(1)), int(m.group(2)), float(m.group(3))
        if not reseller_can_manage_client(uid_admin, client_uid):
            await update.message.reply_text("❌ هذا العميل ليس تابعاً لك.")
            return ConversationHandler.END
        base_price = get_effective_product_base_for_pos(client_uid, pid)
        if base_price <= 0:
            await update.message.reply_text("❌ Product not found.")
            return ConversationHandler.END
        if price + 1e-9 < base_price:
            await update.message.reply_text(f"❌ لا يمكن أقل من السعر الأساسي الفعلي للعميل: {base_price:.3f}{CURRENCY}")
            return ConversationHandler.END
        set_pos_product_price(uid_admin, client_uid, pid, price)
        margin = float(price) - float(base_price)
        await update.message.reply_text(
            f"✅ تم حفظ سعر POS للمنتج.\nClient: {client_uid}\nPID: {pid}\nBase: {base_price:.3f}{CURRENCY}\nSell: {price:.3f}{CURRENCY}\nProfit per item: {margin:.3f}{CURRENCY}",
            reply_markup=REPLY_MENU,
        )
        return ConversationHandler.END
    if mode == "pos_set_manual_price":
        if not is_reseller(uid_admin):
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
        allowed_keys = {"SHAHID_MENA_3M", "SHAHID_MENA_12M", "FF_100", "FF_210", "FF_530", "FF_1080", "FF_2200"}
        m_del = re.match(r"^del\s*\|\s*(\d+)\s*\|\s*([A-Za-z0-9_]+)$", text, re.I)
        if m_del:
            client_uid = int(m_del.group(1)); key = m_del.group(2).upper()
            if key not in allowed_keys:
                await update.message.reply_text("❌ Invalid KEY.")
                return ST_ADMIN_INPUT
            if not reseller_can_manage_client(uid_admin, client_uid):
                await update.message.reply_text("❌ هذا العميل ليس تابعاً لك.")
                return ConversationHandler.END
            clear_pos_manual_price(uid_admin, client_uid, key)
            await update.message.reply_text("✅ تم حذف سعر POS اليدوي لهذا العميل.", reply_markup=REPLY_MENU)
            return ConversationHandler.END
        m = re.match(r"^(\d+)\s*\|\s*([A-Za-z0-9_]+)\s*\|\s*([\d.]+)$", text)
        if not m:
            await update.message.reply_text("❌ Format: client_user_id | KEY | price")
            return ST_ADMIN_INPUT
        client_uid, key, price = int(m.group(1)), m.group(2).upper(), float(m.group(3))
        if key not in allowed_keys:
            await update.message.reply_text("❌ Invalid KEY.")
            return ST_ADMIN_INPUT
        if not reseller_can_manage_client(uid_admin, client_uid):
            await update.message.reply_text("❌ هذا العميل ليس تابعاً لك.")
            return ConversationHandler.END
        base_price = get_effective_manual_base_for_pos(client_uid, key)
        if price + 1e-9 < base_price:
            await update.message.reply_text(f"❌ لا يمكن أقل من السعر الأساسي الفعلي للعميل: {base_price:.3f}{CURRENCY}")
            return ConversationHandler.END
        set_pos_manual_price(uid_admin, client_uid, key, price)
        margin = float(price) - float(base_price)
        await update.message.reply_text(
            f"✅ تم حفظ سعر POS اليدوي.\nClient: {client_uid}\nKEY: {key}\nBase: {base_price:.3f}{CURRENCY}\nSell: {price:.3f}{CURRENCY}\nProfit per order: {margin:.3f}{CURRENCY}",
            reply_markup=REPLY_MENU,
        )
        return ConversationHandler.END
    if mode == "pos_broadcast_clients":
        if not is_reseller(uid_admin):
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
        cur.execute("SELECT client_user_id FROM reseller_clients WHERE reseller_id=? ORDER BY client_user_id", (uid_admin,))
        targets = [int(r[0]) for r in cur.fetchall()]
        if not targets:
            await update.message.reply_text("❌ لا يوجد عملاء تابعون لك.", reply_markup=REPLY_MENU)
            return ConversationHandler.END
        sent = 0
        failed = 0
        for target_uid in targets:
            try:
                await context.bot.send_message(target_uid, f"📢 رسالة من نقطة البيع الخاصة بك:\n\n{text}")
                sent += 1
            except Exception:
                failed += 1
                logger.exception("POS broadcast failed to %s", target_uid)
        await update.message.reply_text(f"✅ تم إرسال الإشعار إلى عملائك فقط.\nنجح: {sent}\nفشل: {failed}", reply_markup=REPLY_MENU)
        return ConversationHandler.END
    if mode == "pos_charge_client":
        if not is_reseller(uid_admin):
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
        m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
        if not m:
            await update.message.reply_text("❌ Format: client_user_id | amount")
            return ST_ADMIN_INPUT
        client_uid, amount = int(m.group(1)), float(m.group(2))
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return ST_ADMIN_INPUT
        if not reseller_can_manage_client(uid_admin, client_uid):
            await update.message.reply_text("❌ هذا العميل ليس تابعاً لك.")
            return ConversationHandler.END
        ok, rb_before, rb_after = charge_balance_logged(uid_admin, amount, "POS_TOPUP_TO_CLIENT", str(client_uid), f"POS topup to client {client_uid}")
        if not ok:
            await update.message.reply_text(f"❌ رصيد نقطة البيع غير كافٍ.\nرصيدك: {rb_before:.3f}{CURRENCY}", reply_markup=REPLY_MENU)
            return ConversationHandler.END
        cb_before, cb_after = add_balance_logged(client_uid, amount, "POS_TOPUP_FROM_RESELLER", str(uid_admin), f"POS {uid_admin} topup")
        try:
            await context.bot.send_message(client_uid, f"✅ تم شحن رصيدك من نقطة البيع التابعة لك.\n+{amount:.3f}{CURRENCY}\n\n💳 Before: {cb_before:.3f}{CURRENCY}\n✅ After: {cb_after:.3f}{CURRENCY}")
        except Exception:
            logger.exception("Failed notifying client %s about POS topup", client_uid)
        await update.message.reply_text(f"✅ تم شحن العميل {client_uid} بمبلغ {amount:.3f}{CURRENCY}\nرصيدك الآن: {rb_after:.3f}{CURRENCY}", reply_markup=REPLY_MENU)
        return ConversationHandler.END
    if mode == "reseller_add":
        if admin_role(uid_admin) != ROLE_OWNER:
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
        if not text.isdigit():
            await update.message.reply_text("❌ Send user_id only.")
            return ST_ADMIN_INPUT
        target = int(text)
        add_reseller(target)
        try:
            await context.bot.send_message(target, "✅ تم تفعيلك كنقطة بيع.\nاستخدم زر 🏪 POS Panel للدخول إلى لوحة نقطة البيع.", reply_markup=REPLY_MENU)
        except Exception:
            logger.exception("Failed notifying reseller %s", target)
        await update.message.reply_text(f"✅ Added POS: {target}", reply_markup=REPLY_MENU)
        return ConversationHandler.END
    if mode == "reseller_del":
        if admin_role(uid_admin) != ROLE_OWNER:
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
        if not text.isdigit():
            await update.message.reply_text("❌ Send user_id only.")
            return ST_ADMIN_INPUT
        target = int(text)
        remove_reseller(target)
        try:
            await context.bot.send_message(target, "ℹ️ تم إلغاء تفعيل نقطة البيع الخاصة بك.", reply_markup=REPLY_MENU)
        except Exception:
            logger.exception("Failed notifying reseller %s about removal", target)
        await update.message.reply_text(f"✅ Removed POS: {target}", reply_markup=REPLY_MENU)
        return ConversationHandler.END
    # helper limitation
    if admin_role(uid_admin) == ROLE_HELPER:
        if mode not in ("manual_reject_custom",):
            await update.message.reply_text("❌ Not allowed.")
            return ConversationHandler.END
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
            bal_before, bal_after = add_balance_logged(uid, price, 'MANUAL_REFUND', source_id=str(mid), note=reason_text)
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
        if mode == "usermanualprice":
            if admin_role(uid_admin) != ROLE_OWNER:
                await update.message.reply_text("❌ Not allowed.")
                return ConversationHandler.END
            raw = (update.message.text or "").strip()
            allowed_keys = {"SHAHID_MENA_3M", "SHAHID_MENA_12M", "FF_100", "FF_210", "FF_530", "FF_1080", "FF_2200"}
            m_del = re.match(r"^del\s*\|\s*(\d+)\s*\|\s*([A-Z0-9_]+)\s*$", raw, flags=re.IGNORECASE)
            if m_del:
                user_id = int(m_del.group(1))
                key = m_del.group(2).upper()
                if key not in allowed_keys:
                    await update.message.reply_text("❌ Unknown key.")
                    return ST_ADMIN_INPUT
                if not has_user_manual_price(user_id, key):
                    await update.message.reply_text("❌ No custom manual price exists for this user/key.")
                    return ConversationHandler.END
                clear_user_manual_price(user_id, key)
                await update.message.reply_text(
                    f"✅ Custom manual price deleted.\nUser: {user_id}\nKey: {key}",
                    reply_markup=REPLY_MENU,
                )
                return ConversationHandler.END
            m_set = re.match(r"^(\d+)\s*\|\s*([A-Z0-9_]+)\s*\|\s*([\d.]+)\s*$", raw)
            if not m_set:
                await update.message.reply_text(
                    "❌ Format:\n"
                    "user_id | KEY | price\n"
                    "Example:\n"
                    "1997968014 | FF_100 | 0.80\n\n"
                    "Delete:\n"
                    "del | user_id | KEY"
                )
                return ST_ADMIN_INPUT
            user_id = int(m_set.group(1))
            key = m_set.group(2).upper()
            if key not in allowed_keys:
                await update.message.reply_text("❌ Unknown key. Use supported manual keys only.")
                return ST_ADMIN_INPUT
            price = float(m_set.group(3))
            if price < 0:
                await update.message.reply_text("❌ Price must be >= 0")
                return ST_ADMIN_INPUT
            ensure_user_exists(user_id)
            set_user_manual_price(user_id, key, price)
            await update.message.reply_text(
                f"✅ Custom manual price saved.\nUser: {user_id}\nKey: {key}\nPrice: {price:.3f}{CURRENCY}",
                reply_markup=REPLY_MENU,
            )
            return ConversationHandler.END
        if mode == "setmanualprice":
            if admin_role(uid_admin) != ROLE_OWNER:
                await update.message.reply_text("❌ Not allowed.")
                return ConversationHandler.END
            m = re.match(r"^([A-Za-z0-9_]+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text(
                    "❌ Format: KEY | PRICE\n"
                    "Example: FF_100 | 0.95\n\n"
                    "Allowed keys:\n"
                    "SHAHID_MENA_3M\nSHAHID_MENA_12M\nFF_100\nFF_210\nFF_530\nFF_1080\nFF_2200"
                )
                return ST_ADMIN_INPUT
            key, price_s = m.group(1).upper(), m.group(2)
            allowed_keys = {"SHAHID_MENA_3M", "SHAHID_MENA_12M", "FF_100", "FF_210", "FF_530", "FF_1080", "FF_2200"}
            if key not in allowed_keys:
                await update.message.reply_text("❌ Unknown manual price key. Use one of the supported keys only.")
                return ST_ADMIN_INPUT
            price = float(price_s)
            if price < 0:
                await update.message.reply_text("❌ Price must be >= 0")
                return ST_ADMIN_INPUT
            cur.execute(
                "INSERT INTO manual_prices(pkey, price) VALUES(?,?) "
                "ON CONFLICT(pkey) DO UPDATE SET price=excluded.price",
                (key, price),
            )
            con.commit()
            await update.message.reply_text(
                f"✅ Manual price updated: {key} = {price:.3f}{CURRENCY}",
                reply_markup=kb_manual_prices_panel(),
            )
            return ConversationHandler.END
        if mode == "dailyaudit_date":
            if admin_role(uid_admin) != ROLE_OWNER:
                await update.message.reply_text("❌ Not allowed.")
                return ConversationHandler.END
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                await update.message.reply_text("❌ Format: YYYY-MM-DD\nExample: 2026-03-06")
                return ST_ADMIN_INPUT
            report = await _daily_audit_report_with_alerts(context, text)
            await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_daily_audit(text))
            return ConversationHandler.END
        if mode == "broadcast_all":
            if admin_role(uid_admin) != ROLE_OWNER:
                await update.message.reply_text("❌ Not allowed.")
                return ConversationHandler.END
            msg = (update.message.text or "").strip()
            if not msg:
                await update.message.reply_text("❌ Send message text first.")
                return ST_ADMIN_INPUT
            sent, failed = await broadcast_to_all_users(context, f"📢 إشعار من الإدارة\n\n{msg}")
            await update.message.reply_text(f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}", reply_markup=REPLY_MENU)
            return ConversationHandler.END
        if mode == "userprice":
            if admin_role(uid_admin) != ROLE_OWNER:
                await update.message.reply_text("❌ Not allowed.")
                return ConversationHandler.END
            raw = (update.message.text or "").strip()
            m_del = re.match(r"^del\s*\|\s*(\d+)\s*\|\s*(\d+)\s*$", raw, flags=re.IGNORECASE)
            if m_del:
                user_id = int(m_del.group(1))
                pid = int(m_del.group(2))
                cur.execute("SELECT title FROM products WHERE pid=?", (pid,))
                prow = cur.fetchone()
                if not prow:
                    await update.message.reply_text("❌ Product PID not found.")
                    return ST_ADMIN_INPUT
                if not has_user_product_price(user_id, pid):
                    await update.message.reply_text("❌ No custom price exists for this user/product.")
                    return ConversationHandler.END
                clear_user_product_price(user_id, pid)
                await update.message.reply_text(
                    f"✅ Custom price deleted.\nUser: {user_id}\nPID: {pid}\nProduct: {prow[0]}",
                    reply_markup=REPLY_MENU,
                )
                return ConversationHandler.END
            m_set = re.match(r"^(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*$", raw)
            if not m_set:
                await update.message.reply_text(
                    "❌ Format:\n"
                    "Set/Update: user_id | pid | price\n"
                    "Example: 1997968014 | 12 | 8.5\n\n"
                    "Delete: del | user_id | pid\n"
                    "Example: del | 1997968014 | 12"
                )
                return ST_ADMIN_INPUT
            user_id = int(m_set.group(1))
            pid = int(m_set.group(2))
            price = float(m_set.group(3))
            if price < 0:
                await update.message.reply_text("❌ Price must be >= 0")
                return ST_ADMIN_INPUT
            cur.execute("SELECT title, price FROM products WHERE pid=?", (pid,))
            prow = cur.fetchone()
            if not prow:
                await update.message.reply_text("❌ Product PID not found.")
                return ST_ADMIN_INPUT
            ensure_user_exists(user_id)
            set_user_product_price(user_id, pid, price)
            await update.message.reply_text(
                f"✅ Custom price saved.\n"
                f"User: {user_id}\n"
                f"PID: {pid}\n"
                f"Product: {prow[0]}\n"
                f"Base price: {float(prow[1]):.3f}{CURRENCY}\n"
                f"User price: {float(price):.3f}{CURRENCY}",
                reply_markup=REPLY_MENU,
            )
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
            cur.execute("UPDATE deposits SET status='APPROVED', approved_at=datetime('now') WHERE id=?", (dep_id,))
            con.commit()
            bal_before, bal_after = add_balance_logged(user_id, float(amount), 'DEPOSIT_APPROVED', source_id=str(dep_id), note='approved deposit')
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
            bal_before, bal_after = add_balance_logged(user_id, amount, 'ADMIN_ADD_BALANCE', source_id=str(uid_admin), note='admin add balance')
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
            ok_take, bal_before, bal_after = charge_balance_logged(user_id, amount, 'ADMIN_TAKE_BALANCE', source_id=str(uid_admin), note='admin take balance')
            if not ok_take:
                bal = get_balance(user_id)
                await update.message.reply_text(f"❌ User has insufficient balance. User balance: {bal:.3f} {CURRENCY}")
                return ConversationHandler.END
            add_balance_logged(ADMIN_ID, amount, 'ADMIN_OWNER_COLLECTION', source_id=str(user_id), note='collected from user')
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
    CB_PATTERN = r"^(cat:|view:|buy:|confirm:|pay:|paid:|manual:|admin:|orders:|back:|goto:|pos:)"
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
    app.add_handler(CommandHandler("id", id_cmd))
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

# bot.py
import os
import re
import io
import csv
import zipfile
import sqlite3
import secrets
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputFile,
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
logger = logging.getLogger("gamevaultbot")

# =========================
# ENV
# =========================
TOKEN = os.getenv("TOKEN")

# Supports either ADMIN_ID or ADMIN_IDS="id1,id2,id3"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS: set[int] = set()
if ADMIN_IDS_RAW:
    for x in ADMIN_IDS_RAW.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))
if ADMIN_ID:
    ADMIN_IDS.add(ADMIN_ID)

DB_PATH = os.getenv("DB_PATH", "shop.db")

CURRENCY = os.getenv("CURRENCY", "$")

BINANCE_UID = os.getenv("BINANCE_ID", "YOUR_BINANCE_ID_ADDRESS")
BYBIT_UID = os.getenv("BYBIT_UID", "12345678")
USDT_TRC20 = os.getenv("USDT_TRC20", "YOUR_USDT_TRC20_ADDRESS")
USDT_BEP20 = os.getenv("USDT_BEP20", "YOUR_USDT_BEP20_ADDRESS")

SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+213xxxxxxxxx")
SUPPORT_GROUP = os.getenv("SUPPORT_GROUP", "@yourgroup")
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "@yourchannel")

# اخفاء اقسام (اختياري)
HIDDEN_CATEGORIES = {
    "🎲 YALLA LUDO",
    "🕹 ROBLOX (USA)",
    "🟦 STEAM (USA)",
}

if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_ID/ADMIN_IDS env var is missing")

# ensure DB dir exists (important for /var/data)
db_dir = os.path.dirname(DB_PATH) or "."
os.makedirs(db_dir, exist_ok=True)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def money(x: float) -> str:
    return f"{x:.3f} {CURRENCY}"


def to_tme(x: str) -> str:
    x = (x or "").strip()
    if x.startswith("http://") or x.startswith("https://"):
        return x
    if x.startswith("@"):
        return f"https://t.me/{x[1:]}"
    return f"https://t.me/{x}"


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

CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  balance REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categories(
  cid INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL UNIQUE,
  hidden INTEGER NOT NULL DEFAULT 0
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
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
)
con.commit()


def ensure_schema():
    # منع تكرار الأكواد داخل نفس المنتج
    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_codes_unique ON codes(pid, code_text)")
        con.commit()
    except Exception:
        pass

    # ضمان وجود أعمدة لو DB قديم
    for col, ctype in [("player_id", "TEXT"), ("note", "TEXT")]:
        try:
            cur.execute(f"ALTER TABLE manual_orders ADD COLUMN {col} {ctype}")
            con.commit()
        except Exception:
            pass

    # add hidden column if old db
    try:
        cur.execute("ALTER TABLE categories ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        con.commit()
    except Exception:
        pass


ensure_schema()

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
        cur.execute("INSERT OR IGNORE INTO categories(title, hidden) VALUES(?,0)", (cat,))
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

# =========================
# States
# =========================
ST_QTY = 10
ST_TOPUP_DETAILS = 20

ST_MANUAL_EMAIL = 30
ST_MANUAL_PASS = 31
ST_FF_PLAYERID = 32

# Admin conversational states
ST_ADMIN_TEXT = 99
ST_ADMIN_DOC = 98

# user_data keys
UD_PID = "pid"
UD_CID = "cid"
UD_QTY_MAX = "qty_max"
UD_DEP_ID = "dep_id"
UD_ORD_RNG = "orders_rng"

UD_MANUAL_SERVICE = "manual_service"
UD_MANUAL_PLAN = "manual_plan"
UD_MANUAL_PRICE = "manual_price"
UD_MANUAL_PLAN_TITLE = "manual_plan_title"
UD_MANUAL_EMAIL = "manual_email"

UD_FF_CART = "ff_cart"
UD_FF_TOTAL = "ff_total"

# admin user_data
AD_MODE = "ad_mode"
AD_TMP = "ad_tmp"
AD_PAGE = "ad_page"


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


def ensure_user_exists(user_id: int, username: str = "", first_name: str = ""):
    cur.execute(
        """
        INSERT INTO users(user_id, username, first_name, balance)
        VALUES(?,?,?,0)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id, username, first_name),
    )
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


# =========================
# Delivery
# =========================
MAX_CODES_IN_MESSAGE = 200
TELEGRAM_TEXT_LIMIT = 3800


async def send_codes_delivery(chat_id: int, context: ContextTypes.DEFAULT_TYPE, order_id: int, codes: List[str]):
    codes = [c.strip() for c in codes if c and c.strip()]
    count = len(codes)

    header = f"✅ Order #{order_id} COMPLETED\n🎁 Codes count: {count}\n\n"

    if count == 0:
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Order #{order_id} COMPLETED\n(No codes)")
        return

    if count > MAX_CODES_IN_MESSAGE:
        content = "\n".join(codes)
        bio = io.BytesIO(content.encode("utf-8"))
        bio.name = f"order_{order_id}_codes.txt"
        await context.bot.send_message(chat_id=chat_id, text=header + "📎 Your codes are attached as a file:")
        await context.bot.send_document(chat_id=chat_id, document=bio)
        return

    body = "\n".join(codes)
    text = header + body
    if len(text) <= TELEGRAM_TEXT_LIMIT:
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    await context.bot.send_message(chat_id=chat_id, text=header + "🎁 Codes (part 1):")
    chunk = ""
    part = 1
    for c in codes:
        line = c + "\n"
        if len(chunk) + len(line) > TELEGRAM_TEXT_LIMIT:
            await context.bot.send_message(chat_id=chat_id, text=chunk.rstrip())
            part += 1
            chunk = f"🎁 Codes (part {part}):\n" + line
        else:
            chunk += line
    if chunk.strip():
        await context.bot.send_message(chat_id=chat_id, text=chunk.rstrip())


# =========================
# Keyboards (Shop)
# =========================
def kb_categories() -> InlineKeyboardMarkup:
    cur.execute(
        """
        SELECT c.cid, c.title, c.hidden, COUNT(p.pid)
        FROM categories c
        LEFT JOIN products p ON p.cid=c.cid AND p.active=1
        GROUP BY c.cid
        ORDER BY c.title
        """
    )
    rows = []
    for cid, title, hidden, cnt in cur.fetchall():
        if title in HIDDEN_CATEGORIES:
            continue
        if int(hidden) == 1:
            continue
        rows.append([InlineKeyboardButton(f"{title} | {cnt}", callback_data=f"cat:{cid}")])
    rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin:home")])
    return InlineKeyboardMarkup(rows)


def product_stock(pid: int) -> int:
    cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
    return int(cur.fetchone()[0])


def kb_products(cid: int) -> InlineKeyboardMarkup:
    cur.execute("SELECT pid,title,price FROM products WHERE cid=? AND active=1", (cid,))
    items = cur.fetchall()
    items.sort(key=lambda r: extract_sort_value(r[1]))

    rows = []
    for pid, title, price in items:
        stock = product_stock(pid)
        label = f"{title} | {money(float(price))} | {stock}"
        rows.append([InlineKeyboardButton(label, callback_data=f"view:{pid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back:cats")])
    return InlineKeyboardMarkup(rows)


def kb_product_view(pid: int, cid: int) -> InlineKeyboardMarkup:
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
        [[InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid:{dep_id}")]]
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
                InlineKeyboardButton("1 day 🎁", callback_data="orders:range:1d:0"),
                InlineKeyboardButton("1 week 🎁", callback_data="orders:range:7d:0"),
                InlineKeyboardButton("1 month 🎁", callback_data="orders:range:30d:0"),
                InlineKeyboardButton("All 🎁", callback_data="orders:range:all:0"),
            ],
        ]
    )


def kb_support() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✉️ Contact Support", url=to_tme(SUPPORT_GROUP))],
            [InlineKeyboardButton("📣 Visit Support Channel", url=to_tme(SUPPORT_CHANNEL))],
        ]
    )


# =========================
# Manual Order (Shahid + FreeFire MENA Cart)
# =========================
FF_PACKS = [
    ("FF_100", "100+10", 110, 0.930),
    ("FF_210", "210+21", 231, 1.860),
    ("FF_530", "530+53", 583, 4.650),
    ("FF_1080", "1080+108", 1188, 9.300),
    ("FF_2200", "2200+220", 2420, 18.600),
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
        _, title, diamonds, price = pack
        total_price += price * qty
        total_diamonds += diamonds * qty
        lines.append((title, qty, price, diamonds))
    order_map = {t: i for i, (_, t, _, _) in enumerate(FF_PACKS)}
    lines.sort(key=lambda x: order_map.get(x[0], 999))
    return total_price, total_diamonds, lines


def kb_manual_services() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📺 Shahid", callback_data="manual:shahid")],
            [InlineKeyboardButton("💎 Free Fire (MENA)", callback_data="manual:ff")],
            [InlineKeyboardButton("⬅️ Back", callback_data="manual:back")],
        ]
    )


def kb_shahid_plans() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Shahid [MENA] | 3 Month | 10.000$", callback_data="manual:shahid:MENA_3M")],
            [InlineKeyboardButton("Shahid [MENA] | 12 Month | 35.000$", callback_data="manual:shahid:MENA_12M")],
            [InlineKeyboardButton("⬅️ Back", callback_data="manual:services")],
        ]
    )


def ff_menu_text() -> str:
    return (
        "💎 Free Fire (MENA)\n\n"
        "How to Place a Free Fire Diamonds Order:\n"
        "Simply send the player ID code to place your order.\n\n"
        "📦 Delivery Time: 1-5 minutes"
    )


def kb_ff_menu(context) -> InlineKeyboardMarkup:
    cart = _ff_cart_get(context)
    rows = []
    for sku, title, _, price in FF_PACKS:
        qty = int(cart.get(sku, 0))
        suffix = f" [{qty}]" if qty > 0 else ""
        rows.append([InlineKeyboardButton(f"{title} 💎 | {price:.3f}$" + suffix, callback_data=f"manual:ff:add:{sku}")])

    rows.append([InlineKeyboardButton("🗑 Clear Cart", callback_data="manual:ff:clear")])
    rows.append([InlineKeyboardButton("✅ Proceed to Checkout", callback_data="manual:ff:checkout")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="manual:services")])
    return InlineKeyboardMarkup(rows)


def ff_checkout_text(context) -> str:
    cart = _ff_cart_get(context)
    total_price, total_diamonds, lines = _ff_calc_totals(cart)
    if not lines:
        return "🛒 Your Cart is empty.\nAdd items first."

    text_lines = ["🛒 Your Cart — Free Fire ⚡\n"]
    for title, qty, _, _ in lines:
        text_lines.append(f"💎 {title} (x{qty})")

    text_lines.append("")
    text_lines.append(f"💎 Total Diamonds: {total_diamonds}")
    text_lines.append(f"💰 Total: ${total_price:.3f}")
    text_lines.append("")
    text_lines.append("🆔 Enter Player ID (NUMBERS only) to proceed:\n❌ /cancel to stop")

    context.user_data[UD_FF_TOTAL] = float(total_price)
    context.user_data["ff_total_diamonds"] = int(total_diamonds)
    return "\n".join(text_lines)


# =========================
# Admin Panel (PRO UI)
# =========================
def kb_admin_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📁 Categories", callback_data="ad:cats:0"),
             InlineKeyboardButton("🧩 Products", callback_data="ad:prods:0")],
            [InlineKeyboardButton("🔑 Codes / Stock", callback_data="ad:codes:home"),
             InlineKeyboardButton("📦 Orders", callback_data="ad:orders:0")],
            [InlineKeyboardButton("💰 Deposits", callback_data="ad:deps:0"),
             InlineKeyboardButton("👤 Users", callback_data="ad:users:home")],
            [InlineKeyboardButton("📊 Stats", callback_data="ad:stats"),
             InlineKeyboardButton("⬅️ Back", callback_data="back:cats")],
        ]
    )


def kb_admin_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Home", callback_data="admin:home")]])


def _paginate(items: List[Tuple], page: int, page_size: int = 8):
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * page_size:(page + 1) * page_size]
    return chunk, page, total_pages


def kb_admin_pager(prefix: str, page: int, total_pages: int, extra_row: Optional[List[InlineKeyboardButton]] = None):
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}:{page-1}"))
    else:
        row.append(InlineKeyboardButton("—", callback_data="noop"))
    row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}:{page+1}"))
    else:
        row.append(InlineKeyboardButton("—", callback_data="noop"))

    rows = [row]
    if extra_row:
        rows.append(extra_row)
    rows.append([InlineKeyboardButton("⬅️ Admin Home", callback_data="admin:home")])
    return InlineKeyboardMarkup(rows)


async def admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.edit_message_text("👑 Admin Panel", reply_markup=kb_admin_home())
    else:
        await update.message.reply_text("👑 Admin Panel", reply_markup=kb_admin_home())


# ---- Categories ----
def _cats_all():
    cur.execute("SELECT cid,title,hidden FROM categories ORDER BY title")
    return cur.fetchall()


def kb_admin_cats(page: int):
    items = _cats_all()
    chunk, page, total_pages = _paginate(items, page, page_size=8)
    rows = []
    for cid, title, hidden in chunk:
        eye = "🙈" if int(hidden) else "👁"
        rows.append([InlineKeyboardButton(f"{eye} {title}", callback_data=f"ad:cat:menu:{cid}")])
    extra = [
        InlineKeyboardButton("➕ Add", callback_data="ad:cat:add"),
        InlineKeyboardButton("🔎 Search", callback_data="ad:cat:search"),
    ]
    return kb_admin_pager("ad:cats", page, total_pages, extra_row=extra)


def kb_admin_cat_menu(cid: int):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Rename", callback_data=f"ad:cat:rename:{cid}")],
            [InlineKeyboardButton("👁/🙈 Toggle Hide", callback_data=f"ad:cat:toggle:{cid}")],
            [InlineKeyboardButton("🗑 Delete", callback_data=f"ad:cat:del:{cid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="ad:cats:0")],
        ]
    )


# ---- Products ----
def _prods_all():
    cur.execute(
        """
        SELECT p.pid, p.title, p.price, p.active, c.title, p.cid
        FROM products p JOIN categories c ON c.cid=p.cid
        ORDER BY c.title, p.title
        """
    )
    return cur.fetchall()


def kb_admin_prods(page: int):
    items = _prods_all()
    chunk, page, total_pages = _paginate(items, page, page_size=7)
    rows = []
    for pid, title, price, active, cat_title, cid in chunk:
        st = "🟢" if int(active) else "🔴"
        rows.append([InlineKeyboardButton(f"{st} PID {pid} | {cat_title} | {title} | {float(price):.3f}",
                                          callback_data=f"ad:prod:menu:{pid}")])
    extra = [
        InlineKeyboardButton("➕ Add", callback_data="ad:prod:add"),
        InlineKeyboardButton("🔎 Search PID", callback_data="ad:prod:search"),
    ]
    return kb_admin_pager("ad:prods", page, total_pages, extra_row=extra)


def kb_admin_prod_menu(pid: int):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💲 Set Price", callback_data=f"ad:prod:price:{pid}")],
            [InlineKeyboardButton("✏️ Rename", callback_data=f"ad:prod:rename:{pid}")],
            [InlineKeyboardButton("⛔ Toggle ON/OFF", callback_data=f"ad:prod:toggle:{pid}")],
            [InlineKeyboardButton("📦 Stock", callback_data=f"ad:prod:stock:{pid}")],
            [InlineKeyboardButton("🗑 Delete", callback_data=f"ad:prod:del:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="ad:prods:0")],
        ]
    )


# ---- Codes ----
def kb_admin_codes_home():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add codes (Text)", callback_data="ad:codes:addtxt")],
            [InlineKeyboardButton("📄 Upload codes .txt", callback_data="ad:codes:upload")],
            [InlineKeyboardButton("🧹 Delete a code", callback_data="ad:codes:del")],
            [InlineKeyboardButton("📤 Export unused by PID", callback_data="ad:codes:export")],
            [InlineKeyboardButton("⬅️ Admin Home", callback_data="admin:home")],
        ]
    )


# ---- Orders ----
def _orders_recent(limit: int = 50):
    cur.execute(
        """
        SELECT id,user_id,product_title,qty,total,status,created_at
        FROM orders ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    )
    return cur.fetchall()


def kb_admin_orders(page: int):
    items = _orders_recent(80)
    chunk, page, total_pages = _paginate(items, page, page_size=6)
    rows = []
    for oid, uid, title, qty, total, status, created_at in chunk:
        rows.append([InlineKeyboardButton(f"#{oid} | {status} | {uid} | {qty} | {float(total):.3f}",
                                          callback_data=f"ad:ord:menu:{oid}")])
    extra = [
        InlineKeyboardButton("🔎 Find by ID", callback_data="ad:ord:find"),
        InlineKeyboardButton("♻️ Resend codes", callback_data="ad:ord:resend"),
    ]
    return kb_admin_pager("ad:orders", page, total_pages, extra_row=extra)


def kb_admin_order_menu(oid: int):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("❌ Cancel + refund (if not completed)", callback_data=f"ad:ord:cancel:{oid}")],
            [InlineKeyboardButton("♻️ Resend delivery", callback_data=f"ad:ord:resendone:{oid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="ad:orders:0")],
        ]
    )


# ---- Deposits ----
def _deps_pending():
    cur.execute(
        """
        SELECT id,user_id,method,note,txid,amount,status,created_at
        FROM deposits
        WHERE status IN ('PENDING_REVIEW','WAITING_PAYMENT')
        ORDER BY id DESC
        """
    )
    return cur.fetchall()


def kb_admin_deps(page: int):
    items = _deps_pending()
    chunk, page, total_pages = _paginate(items, page, page_size=6)
    rows = []
    for dep_id, uid, method, note, txid, amount, status, created_at in chunk:
        short_tx = (txid or "")[:12] + ("..." if txid and len(txid) > 12 else "")
        rows.append([InlineKeyboardButton(f"DEP {dep_id} | {status} | {uid} | {method} | {amount} | {short_tx}",
                                          callback_data=f"ad:dep:menu:{dep_id}")])
    extra = [
        InlineKeyboardButton("🔎 Find by ID", callback_data="ad:dep:find"),
        InlineKeyboardButton("↻ Refresh", callback_data="ad:deps:0"),
    ]
    return kb_admin_pager("ad:deps", page, total_pages, extra_row=extra)


def kb_admin_dep_menu(dep_id: int):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Approve", callback_data=f"ad:dep:approve:{dep_id}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"ad:dep:reject:{dep_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="ad:deps:0")],
        ]
    )


# ---- Users ----
def kb_admin_users_home():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔎 Find user by ID", callback_data="ad:user:find")],
            [InlineKeyboardButton("➕ Add balance", callback_data="ad:user:addbal")],
            [InlineKeyboardButton("➖ Deduct balance", callback_data="ad:user:takebal")],
            [InlineKeyboardButton("⬅️ Admin Home", callback_data="admin:home")],
        ]
    )


# ---- Stats ----
def get_stats():
    cur.execute("SELECT COUNT(*) FROM users")
    users = int(cur.fetchone()[0])

    cur.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders WHERE status='COMPLETED'")
    orders_count, revenue = cur.fetchone()
    orders_count = int(orders_count or 0)
    revenue = float(revenue or 0)

    cur.execute(
        """
        SELECT product_title, COUNT(*), SUM(total)
        FROM orders WHERE status='COMPLETED'
        GROUP BY product_title
        ORDER BY SUM(total) DESC
        LIMIT 5
        """
    )
    top = cur.fetchall()
    return users, orders_count, revenue, top


# =========================
# Pages (Shop)
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    for aid in ADMIN_IDS:
        ensure_user_exists(aid)
    await update.message.reply_text("✅ Bot is online!", reply_markup=REPLY_MENU)


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
        f"💎 Telegram ID: `{uid}`\n"
        f"💎 Current Balance: *{bal:.3f}* {CURRENCY}\n\n"
        "✨ Choose a top up method:"
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

    lines = []
    for oid, qty, title, total_price, status, created_at in chunk:
        lines.append(
            f"📦 Order ID: {oid} - Quantity: {qty}\n"
            f"#️⃣ Product : {title}\n"
            f"⭐ Order Status: {status}\n"
            f"💰 Total Price: {float(total_price):.3f} {CURRENCY}\n"
            f"🕒 {created_at}\n"
        )
    footer = f"{page+1}/{total_pages}"
    return ("\n".join(lines) + f"\n{footer}", total_pages)


async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, rng: str = "all", page: int = 0):
    uid = update.effective_user.id
    context.user_data[UD_ORD_RNG] = rng

    rows = _orders_query(uid, rng)
    text, total_pages = _format_orders_page(rows, page)

    if update.message:
        await update.message.reply_text(text, reply_markup=kb_orders_filters(page, total_pages))
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb_orders_filters(page, total_pages))


async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "We're here to help!\n\n"
        f"📞 Phone: `{SUPPORT_PHONE}`\n"
        f"👥 Support Group: {SUPPORT_GROUP}\n\n"
        "Choose an option below:"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_support())
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_support())


def smart_reply(msg: str) -> Optional[str]:
    m = msg.lower()
    if any(x in m for x in ["price", "سعر", "كم", "ثمن"]):
        return "💡 الأسعار تظهر داخل Our Products → اختر القسم."
    if any(x in m for x in ["balance", "رصيد", "wallet", "محفظة"]):
        return "💡 اضغط My Balance لمشاهدة الرصيد وطرق الشحن."
    if any(x in m for x in ["order", "طلب", "orders", "طلباتي"]):
        return "💡 اضغط My Orders لمشاهدة الطلبات."
    if any(x in m for x in ["usdt", "trc20", "bep20", "txid"]):
        return "💡 من My Balance اختر طريقة الشحن ثم اضغط ✅ I Have Paid وأرسل Amount | TXID."
    return None


# =========================
# Router
# =========================
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
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
        return await update.message.reply_text("⚡ MANUAL ORDER\nSelect a service:", reply_markup=kb_manual_services())

    hint = smart_reply(t)
    if hint:
        return await update.message.reply_text(hint, reply_markup=REPLY_MENU)

    await update.message.reply_text("Use the menu 👇", reply_markup=REPLY_MENU)


# =========================
# Qty input
# =========================
async def qty_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if txt.lower() in ("/cancel", "cancel"):
        for k in [UD_PID, UD_CID, UD_QTY_MAX, "qty_value"]:
            context.user_data.pop(k, None)
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
    context.user_data["qty_value"] = qty

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cid}")],
        ]
    )
    await update.message.reply_text(
        f"🧾 Confirm Order\n\nProduct: {title}\nQty: {qty}\nTotal: {money(total)}\n\nPress Confirm ✅",
        reply_markup=kb,
    )
    return ConversationHandler.END


# =========================
# Topup details
# =========================
async def topup_details_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

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
        f"✅ Received.\nDeposit ID: {dep_id}\nStatus: PENDING_REVIEW\n\nWe will approve soon.",
        reply_markup=REPLY_MENU,
    )
    for aid in ADMIN_IDS:
        await context.bot.send_message(
            aid,
            f"💰 DEPOSIT REVIEW\nDeposit ID: {dep_id}\nUser: {uid}\nAmount: {amount}\nTXID:\n{txid}\n\n(Use Admin Panel → Deposits)",
        )
    return ConversationHandler.END


# =========================
# Manual: Shahid Email/Pass
# =========================
async def manual_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if txt.lower() in ("/cancel", "cancel"):
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

    if pwd.lower() in ("/cancel", "cancel"):
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

    if not charge_balance(uid, price):
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
        f"✅ Manual order created!\nService: {plan_title}\nOrder ID: {mid}\nPaid: {price:.3f} {CURRENCY}\n\nWe will process it soon ✅",
        reply_markup=REPLY_MENU,
    )

    for aid in ADMIN_IDS:
        await context.bot.send_message(
            aid,
            f"⚡ MANUAL ORDER (SHAHID)\nManual ID: {mid}\nUser: {uid}\nPlan: {plan_title}\nPrice: {price:.3f} {CURRENCY}\nGmail: {email}\nPassword: {pwd}\n"
        )

    for k in [UD_MANUAL_SERVICE, UD_MANUAL_PLAN, UD_MANUAL_PRICE, UD_MANUAL_PLAN_TITLE, UD_MANUAL_EMAIL]:
        context.user_data.pop(k, None)
    return ConversationHandler.END


# =========================
# Manual: FreeFire PlayerID
# =========================
async def ff_playerid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if txt.lower() in ("/cancel", "cancel"):
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

    if not charge_balance(uid, total_price):
        bal = get_balance(uid)
        missing = total_price - bal
        await update.message.reply_text(
            f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total_price:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
            reply_markup=kb_topup_now(),
        )
        return ConversationHandler.END

    note_lines = []
    for title, qty, price, diamonds in lines:
        note_lines.append(f"{title} x{qty} | ${price:.3f} | diamonds_each={diamonds}")
    note = "\n".join(note_lines)

    plan_title = f"Free Fire (MENA) | Total Diamonds: {total_diamonds}"
    cur.execute(
        """
        INSERT INTO manual_orders(user_id,service,plan_title,price,player_id,note,status)
        VALUES(?,?,?,?,?,?,'PENDING')
        """,
        (uid, "FREEFIRE_MENA", plan_title, total_price, player_id[:120], note[:4000]),
    )
    con.commit()
    mid = cur.lastrowid

    await update.message.reply_text(
        f"✅ Manual order created!\n"
        f"Service: Free Fire (MENA)\n"
        f"Order ID: {mid}\n"
        f"Player ID: {player_id}\n"
        f"Total Diamonds: {total_diamonds}\n"
        f"Paid: {total_price:.3f} {CURRENCY}\n\n"
        f"We will process it soon ✅",
        reply_markup=REPLY_MENU,
    )

    for aid in ADMIN_IDS:
        await context.bot.send_message(
            aid,
            f"⚡ MANUAL ORDER (FREE FIRE MENA)\n"
            f"Manual ID: {mid}\nUser: {uid}\nPlayer ID: {player_id}\n"
            f"Total Diamonds: {total_diamonds}\nTotal: {total_price:.3f} {CURRENCY}\n\n"
            f"Cart:\n{note}"
        )

    context.user_data.pop(UD_FF_CART, None)
    context.user_data.pop(UD_FF_TOTAL, None)
    context.user_data.pop("ff_total_diamonds", None)
    return ConversationHandler.END


# =========================
# Admin Text Handler (one state for many actions)
# =========================
def _ad_set(context, mode: str, tmp: dict):
    context.user_data[AD_MODE] = mode
    context.user_data[AD_TMP] = tmp or {}


def _ad_get(context):
    return context.user_data.get(AD_MODE), (context.user_data.get(AD_TMP) or {})


async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    txt = (update.message.text or "").strip()
    if txt.lower() in ("/cancel", "cancel"):
        _ad_set(context, "", {})
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    mode, tmp = _ad_get(context)

    try:
        # Category add
        if mode == "cat_add":
            title = txt
            cur.execute("INSERT OR IGNORE INTO categories(title,hidden) VALUES(?,0)", (title,))
            con.commit()
            _ad_set(context, "", {})
            await update.message.reply_text("✅ Category added.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        # Category rename
        if mode == "cat_rename":
            cid = int(tmp["cid"])
            cur.execute("UPDATE categories SET title=? WHERE cid=?", (txt, cid))
            con.commit()
            _ad_set(context, "", {})
            await update.message.reply_text("✅ Category renamed.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        # Category search
        if mode == "cat_search":
            q = txt.lower()
            cur.execute("SELECT cid,title,hidden FROM categories WHERE lower(title) LIKE ? ORDER BY title", (f"%{q}%",))
            rows = cur.fetchall()
            if not rows:
                await update.message.reply_text("No categories found.", reply_markup=REPLY_MENU)
                return ConversationHandler.END
            lines = [f"{cid} | {'HIDDEN' if int(h) else 'SHOW'} | {t}" for cid, t, h in rows[:50]]
            await update.message.reply_text("\n".join(lines), reply_markup=REPLY_MENU)
            _ad_set(context, "", {})
            return ConversationHandler.END

        # Product add: CAT_ID | TITLE | PRICE
        if mode == "prod_add":
            # Format: cid | title | price
            if "|" not in txt:
                await update.message.reply_text("❌ Format: cid | title | price\nExample: 3 | 10$ PSN USA | 8.9")
                return ConversationHandler.END
            parts = [x.strip() for x in txt.split("|")]
            if len(parts) != 3:
                await update.message.reply_text("❌ Format: cid | title | price")
                return ConversationHandler.END
            cid = int(parts[0])
            title = parts[1]
            price = float(parts[2])
            cur.execute("SELECT cid FROM categories WHERE cid=?", (cid,))
            if not cur.fetchone():
                await update.message.reply_text("❌ Category id not found.")
                return ConversationHandler.END
            cur.execute("INSERT INTO products(cid,title,price,product_type,active) VALUES(?,?,?,'CODE',1)", (cid, title, price))
            con.commit()
            _ad_set(context, "", {})
            await update.message.reply_text("✅ Product added.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        # Product search PID
        if mode == "prod_search":
            pid = int(txt)
            cur.execute(
                """
                SELECT p.pid,p.title,p.price,p.active,c.title
                FROM products p JOIN categories c ON c.cid=p.cid
                WHERE p.pid=?
                """,
                (pid,),
            )
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Product not found.", reply_markup=REPLY_MENU)
                return ConversationHandler.END
            pid, title, price, active, cat = row
            stock = product_stock(pid)
            await update.message.reply_text(
                f"PID {pid}\nCategory: {cat}\nTitle: {title}\nPrice: {float(price):.3f}\nActive: {active}\nStock: {stock}",
                reply_markup=REPLY_MENU,
            )
            _ad_set(context, "", {})
            return ConversationHandler.END

        # Product price set
        if mode == "prod_price":
            pid = int(tmp["pid"])
            price = float(txt)
            cur.execute("UPDATE products SET price=? WHERE pid=?", (price, pid))
            con.commit()
            _ad_set(context, "", {})
            await update.message.reply_text("✅ Price updated.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        # Product rename
        if mode == "prod_rename":
            pid = int(tmp["pid"])
            cur.execute("UPDATE products SET title=? WHERE pid=?", (txt, pid))
            con.commit()
            _ad_set(context, "", {})
            await update.message.reply_text("✅ Product renamed.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        # Codes add via text: pid | codes lines...
        if mode == "codes_addtxt":
            if "|" not in txt:
                await update.message.reply_text("❌ Format: pid | CODE1\\nCODE2...\nExample:\n12 | AAAA-1111\nBBBB-2222")
                return ConversationHandler.END
            pid_s, blob = [x.strip() for x in txt.split("|", 1)]
            pid = int(pid_s)
            codes = [c.strip() for c in blob.splitlines() if c.strip()]
            if not codes:
                await update.message.reply_text("❌ No codes found.")
                return ConversationHandler.END
            added, skipped = 0, 0
            for ctext in codes:
                try:
                    cur.execute("INSERT INTO codes(pid,code_text,used) VALUES(?,?,0)", (pid, ctext))
                    added += 1
                except sqlite3.IntegrityError:
                    skipped += 1
            con.commit()
            _ad_set(context, "", {})
            await update.message.reply_text(f"✅ Added {added} codes to PID {pid}.\n♻️ Skipped duplicates: {skipped}", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        # Codes delete: pid | code
        if mode == "codes_del":
            if "|" not in txt:
                await update.message.reply_text("❌ Format: pid | code_text")
                return ConversationHandler.END
            pid_s, code_text = [x.strip() for x in txt.split("|", 1)]
            pid = int(pid_s)
            cur.execute("DELETE FROM codes WHERE pid=? AND code_text=? AND used=0", (pid, code_text))
            con.commit()
            _ad_set(context, "", {})
            await update.message.reply_text("✅ Deleted (only if unused).", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        # Codes export: pid
        if mode == "codes_export":
            pid = int(txt)
            cur.execute("SELECT code_text FROM codes WHERE pid=? AND used=0 ORDER BY code_id ASC", (pid,))
            rows = cur.fetchall()
            codes = [r[0] for r in rows]
            if not codes:
                await update.message.reply_text("No unused codes.", reply_markup=REPLY_MENU)
                _ad_set(context, "", {})
                return ConversationHandler.END
            content = "\n".join(codes)
            bio = io.BytesIO(content.encode("utf-8"))
            bio.name = f"pid_{pid}_unused_codes.txt"
            await update.message.reply_document(document=bio, caption=f"Unused codes for PID {pid}")
            _ad_set(context, "", {})
            return ConversationHandler.END

        # Orders find by id
        if mode == "ord_find":
            oid = int(txt)
            cur.execute("SELECT id,user_id,pid,product_title,qty,total,status,delivered_text,created_at FROM orders WHERE id=?", (oid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Order not found.", reply_markup=REPLY_MENU)
                return ConversationHandler.END
            oid, uid, pid, title, qty, total, status, delivered, created = row
            await update.message.reply_text(
                f"Order #{oid}\nUser: {uid}\nProduct: {title}\nQty: {qty}\nTotal: {float(total):.3f}\nStatus: {status}\nCreated: {created}",
                reply_markup=REPLY_MENU,
            )
            _ad_set(context, "", {})
            return ConversationHandler.END

        # Orders resend codes by id
        if mode == "ord_resend":
            oid = int(txt)
            cur.execute("SELECT user_id, delivered_text, status FROM orders WHERE id=?", (oid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Order not found.", reply_markup=REPLY_MENU)
                return ConversationHandler.END
            uid, delivered_text, status = row
            codes = (delivered_text or "").splitlines()
            await send_codes_delivery(chat_id=int(uid), context=context, order_id=oid, codes=codes)
            await update.message.reply_text("✅ Resent delivery to user.", reply_markup=REPLY_MENU)
            _ad_set(context, "", {})
            return ConversationHandler.END

        # Deposit find by id
        if mode == "dep_find":
            dep_id = int(txt)
            cur.execute("SELECT id,user_id,method,note,txid,amount,status,created_at FROM deposits WHERE id=?", (dep_id,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Deposit not found.", reply_markup=REPLY_MENU)
                return ConversationHandler.END
            dep_id, uid, method, note, txid, amount, status, created = row
            await update.message.reply_text(
                f"DEP {dep_id}\nUser: {uid}\nMethod: {method}\nAmount: {amount}\nStatus: {status}\nNote: {note}\nTXID: {txid}\nCreated: {created}",
                reply_markup=REPLY_MENU,
            )
            _ad_set(context, "", {})
            return ConversationHandler.END

        # User find
        if mode == "user_find":
            uid = int(txt)
            ensure_user_exists(uid)
            cur.execute("SELECT username, first_name, balance FROM users WHERE user_id=?", (uid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("User not found.", reply_markup=REPLY_MENU)
                return ConversationHandler.END
            username, first_name, bal = row
            await update.message.reply_text(
                f"User {uid}\nUsername: @{username}\nName: {first_name}\nBalance: {float(bal):.3f} {CURRENCY}",
                reply_markup=REPLY_MENU,
            )
            _ad_set(context, "", {})
            return ConversationHandler.END

        # User add balance: user_id | amount
        if mode == "user_addbal":
            if "|" not in txt:
                await update.message.reply_text("❌ Format: user_id | amount")
                return ConversationHandler.END
            uid_s, amount_s = [x.strip() for x in txt.split("|", 1)]
            uid = int(uid_s)
            amount = float(amount_s)
            add_balance(uid, amount)
            await update.message.reply_text(f"✅ Added +{money(amount)} to {uid}", reply_markup=REPLY_MENU)
            await context.bot.send_message(uid, f"✅ Admin added balance: +{money(amount)}")
            _ad_set(context, "", {})
            return ConversationHandler.END

        # User take balance: user_id | amount
        if mode == "user_takebal":
            if "|" not in txt:
                await update.message.reply_text("❌ Format: user_id | amount")
                return ConversationHandler.END
            uid_s, amount_s = [x.strip() for x in txt.split("|", 1)]
            uid = int(uid_s)
            amount = float(amount_s)
            if not charge_balance(uid, amount):
                bal = get_balance(uid)
                await update.message.reply_text(f"❌ Insufficient. User balance: {bal:.3f} {CURRENCY}", reply_markup=REPLY_MENU)
                return ConversationHandler.END
            # optionally add to first admin
            any_admin = next(iter(ADMIN_IDS))
            add_balance(any_admin, amount)
            await update.message.reply_text(f"✅ Deducted -{money(amount)} from {uid}", reply_markup=REPLY_MENU)
            await context.bot.send_message(uid, f"➖ Admin deducted: -{money(amount)}")
            _ad_set(context, "", {})
            return ConversationHandler.END

        await update.message.reply_text("❌ Admin action expired. Open Admin Panel again.", reply_markup=REPLY_MENU)
        _ad_set(context, "", {})
        return ConversationHandler.END

    except Exception as e:
        logger.exception("admin_text_input error")
        await update.message.reply_text(f"❌ Error: {e}", reply_markup=REPLY_MENU)
        _ad_set(context, "", {})
        return ConversationHandler.END


# =========================
# Admin Doc Handler (upload codes TXT)
# =========================
async def admin_doc_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    mode, tmp = _ad_get(context)
    if mode != "codes_upload":
        await update.message.reply_text("❌ No upload session. Open Admin → Codes → Upload.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Send a .txt document.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    if doc.file_size and doc.file_size > 3_000_000:
        await update.message.reply_text("❌ File too large (max ~3MB).", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    pid = int(tmp.get("pid", 0))
    if pid <= 0:
        await update.message.reply_text("❌ Missing PID. Open Upload again.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    tg_file = await doc.get_file()
    content = (await tg_file.download_as_bytearray()).decode("utf-8", errors="ignore")
    codes = [c.strip() for c in content.splitlines() if c.strip()]
    if not codes:
        await update.message.reply_text("❌ No codes found in file.", reply_markup=REPLY_MENU)
        _ad_set(context, "", {})
        return ConversationHandler.END

    added, skipped = 0, 0
    for ctext in codes:
        try:
            cur.execute("INSERT INTO codes(pid,code_text,used) VALUES(?,?,0)", (pid, ctext))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1
    con.commit()

    await update.message.reply_text(f"✅ Uploaded.\nPID {pid}\nAdded: {added}\nSkipped duplicates: {skipped}", reply_markup=REPLY_MENU)
    _ad_set(context, "", {})
    return ConversationHandler.END


# =========================
# Callback handler (Shop + Admin)
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "noop":
        return

    # goto topup
    if data == "goto:topup":
        return await show_balance(update, context)

    # Manual navigation
    if data == "manual:back" or data == "manual:services":
        return await q.edit_message_text("⚡ MANUAL ORDER\nSelect a service:", reply_markup=kb_manual_services())

    # Manual Shahid
    if data == "manual:shahid":
        text = (
            "📺 Shahid — Select a product:\n\n"
            "📩 What we need from you:\n"
            "➡️ New Gmail address\n"
            "➡️ Password (temporary)\n"
        )
        return await q.edit_message_text(text, reply_markup=kb_shahid_plans())

    if data.startswith("manual:shahid:"):
        plan = data.split(":")[2]
        if plan == "MENA_3M":
            plan_title = "Shahid [MENA] | 3 Month"
            price = 10.0
        elif plan == "MENA_12M":
            plan_title = "Shahid [MENA] | 12 Month"
            price = 35.0
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
            f"✅ Selected: {plan_title}\nPrice: {price:.3f} {CURRENCY}\n\n📩 Send NEW Gmail address now:\n\n/cancel to stop"
        )
        return ST_MANUAL_EMAIL

    # Manual FreeFire
    if data == "manual:ff":
        return await q.edit_message_text(ff_menu_text(), reply_markup=kb_ff_menu(context))

    if data.startswith("manual:ff:add:"):
        sku = data.split(":")[3]
        if not _ff_pack(sku):
            return await q.edit_message_text("❌ Unknown pack.", reply_markup=kb_ff_menu(context))
        cart = _ff_cart_get(context)
        cart[sku] = int(cart.get(sku, 0)) + 1
        context.user_data[UD_FF_CART] = cart
        return await q.edit_message_text(ff_menu_text(), reply_markup=kb_ff_menu(context))

    if data == "manual:ff:clear":
        context.user_data[UD_FF_CART] = {}
        context.user_data.pop(UD_FF_TOTAL, None)
        context.user_data.pop("ff_total_diamonds", None)
        return await q.edit_message_text(ff_menu_text(), reply_markup=kb_ff_menu(context))

    if data == "manual:ff:checkout":
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

        await q.edit_message_text(ff_checkout_text(context))
        return ST_FF_PLAYERID

    # =========================
    # ADMIN PANEL (PRO)
    # =========================
    if data == "admin:home":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await admin_home(update, context)

    # Admin categories list page
    if data.startswith("ad:cats:"):
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        page = int(data.split(":")[2])
        return await q.edit_message_text("📁 Categories", reply_markup=kb_admin_cats(page))

    if data.startswith("ad:cat:menu:"):
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        cid = int(data.split(":")[3])
        cur.execute("SELECT title, hidden FROM categories WHERE cid=?", (cid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Category not found.", reply_markup=kb_admin_back_home())
        title, hidden = row
        st = "HIDDEN 🙈" if int(hidden) else "VISIBLE 👁"
        return await q.edit_message_text(f"📁 Category #{cid}\n{title}\nStatus: {st}", reply_markup=kb_admin_cat_menu(cid))

    if data == "ad:cat:add":
        _ad_set(context, "cat_add", {})
        return await q.edit_message_text("➕ Send category title now:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data.startswith("ad:cat:rename:"):
        cid = int(data.split(":")[3])
        _ad_set(context, "cat_rename", {"cid": cid})
        return await q.edit_message_text("✏️ Send new category title:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data.startswith("ad:cat:toggle:"):
        cid = int(data.split(":")[3])
        cur.execute("SELECT hidden FROM categories WHERE cid=?", (cid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Not found.", reply_markup=kb_admin_back_home())
        newv = 0 if int(row[0]) else 1
        cur.execute("UPDATE categories SET hidden=? WHERE cid=?", (newv, cid))
        con.commit()
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_admin_cats(0))

    if data.startswith("ad:cat:del:"):
        cid = int(data.split(":")[3])
        # prevent delete if products exist
        cur.execute("SELECT COUNT(*) FROM products WHERE cid=?", (cid,))
        cnt = int(cur.fetchone()[0])
        if cnt > 0:
            return await q.edit_message_text("❌ Can't delete. Category has products. Delete/move products first.", reply_markup=kb_admin_cat_menu(cid))
        cur.execute("DELETE FROM categories WHERE cid=?", (cid,))
        con.commit()
        return await q.edit_message_text("✅ Deleted.", reply_markup=kb_admin_cats(0))

    if data == "ad:cat:search":
        _ad_set(context, "cat_search", {})
        return await q.edit_message_text("🔎 Send search text for categories:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    # Admin products list
    if data.startswith("ad:prods:"):
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        page = int(data.split(":")[2])
        return await q.edit_message_text("🧩 Products", reply_markup=kb_admin_prods(page))

    if data.startswith("ad:prod:menu:"):
        pid = int(data.split(":")[3])
        cur.execute(
            """
            SELECT p.pid,p.title,p.price,p.active,c.title
            FROM products p JOIN categories c ON c.cid=p.cid
            WHERE p.pid=?
            """,
            (pid,),
        )
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.", reply_markup=kb_admin_back_home())
        pid, title, price, active, cat = row
        stock = product_stock(pid)
        st = "ON 🟢" if int(active) else "OFF 🔴"
        return await q.edit_message_text(
            f"🧩 PID {pid}\nCategory: {cat}\nTitle: {title}\nPrice: {float(price):.3f} {CURRENCY}\nStatus: {st}\nStock: {stock}",
            reply_markup=kb_admin_prod_menu(pid),
        )

    if data == "ad:prod:add":
        # show how to get cid list quickly
        cur.execute("SELECT cid,title FROM categories ORDER BY title")
        rows = cur.fetchall()
        sample = "\n".join([f"{cid} | {t}" for cid, t in rows[:12]])
        _ad_set(context, "prod_add", {})
        return await q.edit_message_text(
            "➕ Add Product\nSend:\n`cid | title | price`\n\nExample:\n`3 | 10$ PSN USA | 8.9`\n\nCategories sample:\n"
            + sample
            + "\n\n/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_admin_back_home(),
        )

    if data == "ad:prod:search":
        _ad_set(context, "prod_search", {})
        return await q.edit_message_text("🔎 Send PID number:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data.startswith("ad:prod:price:"):
        pid = int(data.split(":")[3])
        _ad_set(context, "prod_price", {"pid": pid})
        return await q.edit_message_text("💲 Send new price:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data.startswith("ad:prod:rename:"):
        pid = int(data.split(":")[3])
        _ad_set(context, "prod_rename", {"pid": pid})
        return await q.edit_message_text("✏️ Send new product title:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data.startswith("ad:prod:toggle:"):
        pid = int(data.split(":")[3])
        cur.execute("SELECT active FROM products WHERE pid=?", (pid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.", reply_markup=kb_admin_back_home())
        newv = 0 if int(row[0]) else 1
        cur.execute("UPDATE products SET active=? WHERE pid=?", (newv, pid))
        con.commit()
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_admin_prods(0))

    if data.startswith("ad:prod:stock:"):
        pid = int(data.split(":")[3])
        stock = product_stock(pid)
        return await q.edit_message_text(f"📦 Stock for PID {pid}: {stock} unused codes", reply_markup=kb_admin_prod_menu(pid))

    if data.startswith("ad:prod:del:"):
        pid = int(data.split(":")[3])
        # delete product only if no orders? allow but keep safe
        cur.execute("SELECT COUNT(*) FROM orders WHERE pid=?", (pid,))
        cnt = int(cur.fetchone()[0])
        if cnt > 0:
            return await q.edit_message_text("❌ Can't delete. Product has orders history.", reply_markup=kb_admin_prod_menu(pid))
        cur.execute("DELETE FROM codes WHERE pid=?", (pid,))
        cur.execute("DELETE FROM products WHERE pid=?", (pid,))
        con.commit()
        return await q.edit_message_text("✅ Product deleted.", reply_markup=kb_admin_prods(0))

    # Codes home
    if data == "ad:codes:home":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("🔑 Codes / Stock", reply_markup=kb_admin_codes_home())

    if data == "ad:codes:addtxt":
        _ad_set(context, "codes_addtxt", {})
        return await q.edit_message_text(
            "➕ Add Codes (Text)\nSend:\n`pid | CODE1\\nCODE2...`\nExample:\n`12 | AAAA-1111\\nBBBB-2222`\n\n/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_admin_back_home(),
        )

    if data == "ad:codes:upload":
        # first ask PID by text input
        _ad_set(context, "codes_upload_pid", {})
        return await q.edit_message_text(
            "📄 Upload codes .txt\nStep 1: Send PID number first.\n\n/cancel to stop",
            reply_markup=kb_admin_back_home(),
        )

    if data == "ad:codes:del":
        _ad_set(context, "codes_del", {})
        return await q.edit_message_text(
            "🧹 Delete Code (unused only)\nSend:\n`pid | code_text`\n\n/cancel to stop",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_admin_back_home(),
        )

    if data == "ad:codes:export":
        _ad_set(context, "codes_export", {})
        return await q.edit_message_text("📤 Export unused codes\nSend PID number:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    # Orders
    if data.startswith("ad:orders:"):
        page = int(data.split(":")[2])
        return await q.edit_message_text("📦 Orders (recent)", reply_markup=kb_admin_orders(page))

    if data.startswith("ad:ord:menu:"):
        oid = int(data.split(":")[3])
        cur.execute("SELECT id,user_id,product_title,qty,total,status,created_at FROM orders WHERE id=?", (oid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Order not found.", reply_markup=kb_admin_back_home())
        oid, uid, title, qty, total, status, created = row
        return await q.edit_message_text(
            f"📦 Order #{oid}\nUser: {uid}\nProduct: {title}\nQty: {qty}\nTotal: {float(total):.3f}\nStatus: {status}\nCreated: {created}",
            reply_markup=kb_admin_order_menu(oid),
        )

    if data == "ad:ord:find":
        _ad_set(context, "ord_find", {})
        return await q.edit_message_text("🔎 Send order id:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data == "ad:ord:resend":
        _ad_set(context, "ord_resend", {})
        return await q.edit_message_text("♻️ Resend delivery\nSend order id:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data.startswith("ad:ord:cancel:"):
        oid = int(data.split(":")[3])
        cur.execute("SELECT user_id,total,status FROM orders WHERE id=?", (oid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Order not found.", reply_markup=kb_admin_back_home())
        user_id, total, status = int(row[0]), float(row[1]), row[2]
        if status == "COMPLETED":
            return await q.edit_message_text("❌ Cannot cancel completed order.", reply_markup=kb_admin_order_menu(oid))
        if status == "CANCELLED":
            return await q.edit_message_text("❌ Already cancelled.", reply_markup=kb_admin_order_menu(oid))
        add_balance(user_id, total)
        cur.execute("UPDATE orders SET status='CANCELLED' WHERE id=?", (oid,))
        con.commit()
        await context.bot.send_message(user_id, f"❌ Order #{oid} cancelled.\nRefunded: +{money(total)}")
        return await q.edit_message_text("✅ Cancelled + refunded.", reply_markup=kb_admin_orders(0))

    if data.startswith("ad:ord:resendone:"):
        oid = int(data.split(":")[3])
        cur.execute("SELECT user_id, delivered_text FROM orders WHERE id=?", (oid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Not found.", reply_markup=kb_admin_back_home())
        uid, delivered = row
        codes = (delivered or "").splitlines()
        await send_codes_delivery(chat_id=int(uid), context=context, order_id=oid, codes=codes)
        return await q.edit_message_text("✅ Resent delivery.", reply_markup=kb_admin_order_menu(oid))

    # Deposits
    if data.startswith("ad:deps:"):
        page = int(data.split(":")[2])
        return await q.edit_message_text("💰 Deposits (pending)", reply_markup=kb_admin_deps(page))

    if data.startswith("ad:dep:menu:"):
        dep_id = int(data.split(":")[3])
        cur.execute("SELECT id,user_id,method,note,txid,amount,status,created_at FROM deposits WHERE id=?", (dep_id,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Deposit not found.", reply_markup=kb_admin_back_home())
        dep_id, uid, method, note, txid, amount, status, created = row
        return await q.edit_message_text(
            f"💰 Deposit #{dep_id}\nUser: {uid}\nMethod: {method}\nAmount: {amount}\nStatus: {status}\nNote: {note}\nTXID: {txid}\nCreated: {created}",
            reply_markup=kb_admin_dep_menu(dep_id),
        )

    if data == "ad:dep:find":
        _ad_set(context, "dep_find", {})
        return await q.edit_message_text("🔎 Send deposit id:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data.startswith("ad:dep:approve:"):
        dep_id = int(data.split(":")[3])
        cur.execute("SELECT user_id, amount, status FROM deposits WHERE id=?", (dep_id,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Deposit not found.", reply_markup=kb_admin_back_home())
        user_id, amount, status = int(row[0]), row[1], row[2]
        if status != "PENDING_REVIEW":
            return await q.edit_message_text("❌ Not ready for approval (need PENDING_REVIEW).", reply_markup=kb_admin_dep_menu(dep_id))
        if amount is None:
            return await q.edit_message_text("❌ Amount missing.", reply_markup=kb_admin_dep_menu(dep_id))
        cur.execute("UPDATE deposits SET status='APPROVED' WHERE id=?", (dep_id,))
        con.commit()
        add_balance(user_id, float(amount))
        await context.bot.send_message(user_id, f"✅ Top up approved: +{money(float(amount))}")
        return await q.edit_message_text(f"✅ Approved +{money(float(amount))}", reply_markup=kb_admin_deps(0))

    if data.startswith("ad:dep:reject:"):
        dep_id = int(data.split(":")[3])
        cur.execute("SELECT user_id, status FROM deposits WHERE id=?", (dep_id,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Deposit not found.", reply_markup=kb_admin_back_home())
        user_id, status = int(row[0]), row[1]
        if status not in ("PENDING_REVIEW", "WAITING_PAYMENT"):
            return await q.edit_message_text("❌ Already processed.", reply_markup=kb_admin_dep_menu(dep_id))
        cur.execute("UPDATE deposits SET status='REJECTED' WHERE id=?", (dep_id,))
        con.commit()
        await context.bot.send_message(user_id, f"❌ Top up #{dep_id} rejected. Contact support.")
        return await q.edit_message_text("✅ Rejected.", reply_markup=kb_admin_deps(0))

    # Users
    if data == "ad:users:home":
        return await q.edit_message_text("👤 Users", reply_markup=kb_admin_users_home())

    if data == "ad:user:find":
        _ad_set(context, "user_find", {})
        return await q.edit_message_text("🔎 Send user_id:\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data == "ad:user:addbal":
        _ad_set(context, "user_addbal", {})
        return await q.edit_message_text("➕ Send: user_id | amount\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    if data == "ad:user:takebal":
        _ad_set(context, "user_takebal", {})
        return await q.edit_message_text("➖ Send: user_id | amount\n\n/cancel to stop", reply_markup=kb_admin_back_home())

    # Stats
    if data == "ad:stats":
        users, orders_count, revenue, top = get_stats()
        lines = [
            "📊 Stats",
            f"👤 Users: {users}",
            f"✅ Completed Orders: {orders_count}",
            f"💰 Revenue: {money(revenue)}",
            "",
            "🏆 Top Products:",
        ]
        if top:
            for title, cnt, sm in top:
                lines.append(f"- {title} | {int(cnt)} orders | {float(sm or 0):.3f}")
        else:
            lines.append("- (No data)")
        return await q.edit_message_text("\n".join(lines), reply_markup=kb_admin_back_home())

    # Special: codes upload PID step handled in admin_text_input
    mode, tmp = _ad_get(context)
    if mode == "codes_upload_pid":
        # this happens via text input, not callback
        pass

    # =========================
    # Navigation (Shop)
    # =========================
    if data == "back:cats":
        return await show_categories(update, context)

    if data.startswith("cat:"):
        cid = int(data.split(":", 1)[1])
        context.user_data[UD_CID] = cid
        return await q.edit_message_text("Choose a product:", reply_markup=kb_products(cid))

    if data.startswith("back:prods:"):
        cid = int(data.split(":", 2)[2])
        return await q.edit_message_text("Choose a product:", reply_markup=kb_products(cid))

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
            f"🎁 {title}\n\n"
            f"- ID: {pid}\n"
            f"- Description: N/A\n"
            f"- Price: {float(price):.3f} {CURRENCY}\n"
            f"- In Stock: {stock} items available"
        )
        return await q.edit_message_text(text, reply_markup=kb_product_view(pid, cid))

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
            return await q.edit_message_text("❌ Out of stock.", reply_markup=kb_products(cid))

        context.user_data[UD_PID] = pid
        context.user_data[UD_CID] = cid
        context.user_data[UD_QTY_MAX] = stock

        await q.edit_message_text(
            f"You are purchasing {title}\n\n📝 Enter a quantity between 1 and {stock}:\n\n❌ If you want to cancel the process, send /cancel"
        )
        return ST_QTY

    # Confirm purchase
    if data.startswith("confirm:"):
        pid = int(data.split(":", 1)[1])
        qty = int(context.user_data.get("qty_value", 0))
        if qty <= 0:
            return await q.edit_message_text("❌ Quantity expired. Buy again.")

        cur.execute("SELECT title, price, cid FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.")
        title, price, cid = row
        total = float(price) * qty

        uid = update.effective_user.id
        if not charge_balance(uid, total):
            bal = get_balance(uid)
            missing = total - bal
            return await q.edit_message_text(
                f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
                reply_markup=kb_topup_now(),
            )

        cur.execute("SELECT code_id, code_text FROM codes WHERE pid=? AND used=0 LIMIT ?", (pid, qty))
        picked = cur.fetchall()
        if len(picked) < qty:
            add_balance(uid, total)
            return await q.edit_message_text("❌ Stock error. Try again.")

        cur.execute(
            "INSERT INTO orders(user_id,pid,product_title,qty,total,status) VALUES(?,?,?,?,?,'PENDING')",
            (uid, pid, title, qty, total),
        )
        oid = cur.lastrowid

        for code_id, _ in picked:
            cur.execute(
                "UPDATE codes SET used=1, used_at=datetime('now'), order_id=? WHERE code_id=?",
                (oid, code_id),
            )

        codes_list = [c for _, c in picked]
        delivered_text = "\n".join(codes_list)
        cur.execute("UPDATE orders SET status='COMPLETED', delivered_text=? WHERE id=?", (delivered_text, oid))
        con.commit()

        await q.edit_message_text(f"✅ Order created!\nOrder ID: {oid}\nTotal: {total:.3f} {CURRENCY}\nDelivering codes...")
        await send_codes_delivery(chat_id=uid, context=context, order_id=oid, codes=codes_list)

        for aid in ADMIN_IDS:
            await context.bot.send_message(
                aid,
                f"✅ NEW COMPLETED ORDER\nOrder ID: {oid}\nUser: {uid}\nProduct: {title}\nQty: {qty}\nTotal: {total:.3f} {CURRENCY}",
            )
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
            extra = "Make sure you are sending only USDT."
        elif method == "BYBIT":
            dest_title = "UID"
            dest_value = BYBIT_UID
            extra = "Make sure you are sending only USDT."
        elif method == "TRC20":
            dest_title = "Address"
            dest_value = USDT_TRC20
            extra = "Network: TRC20 only."
        else:
            dest_title = "Address"
            dest_value = USDT_BEP20
            extra = "Network: BEP20 only."

        text = (
            f"🔑 {method} Payment\n\n"
            f"Please send the amount to this {dest_title} and include the note\n\n"
            f"{dest_title}:\n`{dest_value}`\n\n"
            f"Note:\n`{note}`\n\n"
            f"⚠️ {extra}\n\n"
            f"After that, click the ✅ I Have Paid button."
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

    # default
    return


# =========================
# Special: codes upload PID step (handled in text input by checking mode)
# =========================
async def admin_pid_step_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler is merged into admin_text_input by switching mode.
    return ConversationHandler.END


# =========================
# Admin commands
# =========================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    await update.message.reply_text("👑 Admin Panel", reply_markup=kb_admin_home())


# =========================
# Extra: small bridge in admin_text_input for upload PID step
# =========================
async def admin_text_bridge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    mode, tmp = _ad_get(context)
    txt = (update.message.text or "").strip()

    if mode == "codes_upload_pid":
        if txt.lower() in ("/cancel", "cancel"):
            _ad_set(context, "", {})
            await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
            return ConversationHandler.END
        try:
            pid = int(txt)
        except ValueError:
            await update.message.reply_text("❌ Send PID as a number.\n/cancel to stop", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        _ad_set(context, "codes_upload", {"pid": pid})
        await update.message.reply_text("✅ Now send the .txt file (each code on new line).", reply_markup=REPLY_MENU)
        return ST_ADMIN_DOC

    # otherwise proceed normal admin_text_input
    return await admin_text_input(update, context)


# =========================
# Main
# =========================
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    # Conversations
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_callback)],
        states={
            ST_QTY: [MessageHandler(filters.TEXT, qty_input)],
            ST_TOPUP_DETAILS: [MessageHandler(filters.TEXT, topup_details_input)],
            ST_MANUAL_EMAIL: [MessageHandler(filters.TEXT, manual_email_input)],
            ST_MANUAL_PASS: [MessageHandler(filters.TEXT, manual_pass_input)],
            ST_FF_PLAYERID: [MessageHandler(filters.TEXT, ff_playerid_input)],

            # Admin text/doc flows
            ST_ADMIN_TEXT: [MessageHandler(filters.TEXT, admin_text_bridge)],
            ST_ADMIN_DOC: [MessageHandler(filters.Document.ALL, admin_doc_input)],
        },
        fallbacks=[CommandHandler("start", start_cmd)],
        allow_reentry=True,
    )

    # Commands first
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))

    # Conversation
    app.add_handler(conv)

    # Menu last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    return app


def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

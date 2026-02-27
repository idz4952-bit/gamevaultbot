# bot.py
import os
import re
import io
import json
import sqlite3
import secrets
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
# ENV
# =========================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ✅ Persist DB on Render Disk if available
DEFAULT_DB = "shop.db"
MOUNT_PATH = os.getenv("DB_MOUNT_PATH", "/var/data")  # Render Disk mount
if os.path.isdir(MOUNT_PATH):
    os.makedirs(MOUNT_PATH, exist_ok=True)
    DB_PATH = os.getenv("DB_PATH", os.path.join(MOUNT_PATH, DEFAULT_DB))
else:
    DB_PATH = os.getenv("DB_PATH", DEFAULT_DB)

CURRENCY = os.getenv("CURRENCY", "$")

BINANCE_UID = os.getenv("BINANCE_ID", "YOUR_BINANCE_ID_ADDRESS")
BYBIT_UID = os.getenv("BYBIT_UID", "12345678")
USDT_TRC20 = os.getenv("USDT_TRC20", "YOUR_USDT_TRC20_ADDRESS")
USDT_BEP20 = os.getenv("USDT_BEP20", "YOUR_USDT_BEP20_ADDRESS")

SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+213xxxxxxxxx")
SUPPORT_GROUP = os.getenv("SUPPORT_GROUP", "@yourgroup")
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "@yourchannel")

# اخفاء اقسام
HIDDEN_CATEGORIES = {
    "🎲 YALLA LUDO",
    "🕹 ROBLOX (USA)",
    "🟦 STEAM (USA)",
}

if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")
if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID env var is missing or 0")


def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


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
# ✅ timeout + WAL
con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
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
  balance_before REAL,
  balance_after REAL,
  admin_reason TEXT,
  processed_by INTEGER,
  processed_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Manual items (pricing + active)
CREATE TABLE IF NOT EXISTS manual_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service TEXT NOT NULL,           -- SHAHID | FREEFIRE_MENA
  sku TEXT NOT NULL,               -- MENA_3M | FF_2200 etc
  title TEXT NOT NULL,             -- display title
  price REAL NOT NULL,
  meta TEXT,                       -- JSON: {"diamonds":2420}
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(service, sku)
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

    # ضمان أعمدة قديمة (لو DB قديم)
    for col, ctype in [
        ("player_id", "TEXT"),
        ("note", "TEXT"),
        ("balance_before", "REAL"),
        ("balance_after", "REAL"),
        ("admin_reason", "TEXT"),
        ("processed_by", "INTEGER"),
        ("processed_at", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE manual_orders ADD COLUMN {col} {ctype}")
            con.commit()
        except Exception:
            pass


ensure_schema()


# =========================
# SEED (Categories & Products)
# =========================
DEFAULT_CATEGORIES = [
    "🍎 ITUNES GIFTCARD (USA)",
    "🪂 PUBG MOBILE UC VOUCHERS",
    "💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)",
    "🎮 PLAYSTATION USA GIFTCARDS",
]

DEFAULT_PRODUCTS = [
    # Free Fire (OFFICIAL PINS)
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "1 USD 💎 PINS 100+10", 0.920),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "2 USD 💎 PINS 210+21", 1.840),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "5 USD 💎 PINS 530+53", 4.600),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "10 USD 💎 PINS 1080+108", 9.200),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "20 USD 💎 PINS 2200+220", 18.400),

    # PUBG
    ("🪂 PUBG MOBILE UC VOUCHERS", "60 UC", 0.875),
    ("🪂 PUBG MOBILE UC VOUCHERS", "325 UC", 4.375),
    ("🪂 PUBG MOBILE UC VOUCHERS", "660 UC", 8.750),
    ("🪂 PUBG MOBILE UC VOUCHERS", "1800 UC", 22.000),
    ("🪂 PUBG MOBILE UC VOUCHERS", "3850 UC", 44.000),
    ("🪂 PUBG MOBILE UC VOUCHERS", "8100 UC", 88.000),

    # iTunes
    ("🍎 ITUNES GIFTCARD (USA)", "5$ iTunes US", 4.600),
    ("🍎 ITUNES GIFTCARD (USA)", "10$ iTunes US", 9.200),
    ("🍎 ITUNES GIFTCARD (USA)", "20$ iTunes US", 18.400),
    ("🍎 ITUNES GIFTCARD (USA)", "25$ iTunes US", 23.000),
    ("🍎 ITUNES GIFTCARD (USA)", "50$ iTunes US", 46.000),
    ("🍎 ITUNES GIFTCARD (USA)", "100$ iTunes US", 92.000),

    # PlayStation
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
# SEED Manual Items (pricing + active)
# =========================
def seed_manual_items():
    items = [
        # Shahid
        ("SHAHID", "MENA_3M", "Shahid [MENA] | 3 Month", 10.000, {}),
        ("SHAHID", "MENA_12M", "Shahid [MENA] | 12 Month", 35.000, {}),
        # Free Fire MENA
        ("FREEFIRE_MENA", "FF_100", "100+10", 0.930, {"diamonds": 110}),
        ("FREEFIRE_MENA", "FF_210", "210+21", 1.860, {"diamonds": 231}),
        ("FREEFIRE_MENA", "FF_530", "530+53", 4.650, {"diamonds": 583}),
        ("FREEFIRE_MENA", "FF_1080", "1080+108", 9.300, {"diamonds": 1188}),
        ("FREEFIRE_MENA", "FF_2200", "2200+220", 18.600, {"diamonds": 2420}),
    ]
    for service, sku, title, price, meta in items:
        cur.execute(
            "INSERT OR IGNORE INTO manual_items(service,sku,title,price,meta,active) VALUES(?,?,?,?,?,1)",
            (service, sku, title, float(price), json.dumps(meta)),
        )
    con.commit()


seed_manual_items()

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
ST_ADMIN_INPUT = 99
ST_ADMIN_CODES_FILE = 98
ST_ADMIN_MANUAL_REASON = 97

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

UD_PENDING_MANUAL_REJECT_ID = "pending_manual_reject_id"

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
    rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin:panel")])
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
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid:{dep_id}")]])


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
# Admin Panel (Professional Grid)
# =========================
def kb_admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📁 Categories", callback_data="ap:cats"),
                InlineKeyboardButton("🧩 Products", callback_data="ap:prods"),
            ],
            [
                InlineKeyboardButton("🔑 Codes / Stock", callback_data="ap:codes"),
                InlineKeyboardButton("📦 Orders", callback_data="ap:orders"),
            ],
            [
                InlineKeyboardButton("💰 Deposits", callback_data="ap:deps"),
                InlineKeyboardButton("👤 Users", callback_data="ap:users"),
            ],
            [
                InlineKeyboardButton("⚡ Manual Orders", callback_data="ap:manual"),
                InlineKeyboardButton("🛠 Manual Prices", callback_data="ap:manual_items"),
            ],
            [
                InlineKeyboardButton("📊 Stats", callback_data="ap:stats"),
                InlineKeyboardButton("⬅️ Back", callback_data="back:cats"),
            ],
        ]
    )


def kb_ap_categories() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Category", callback_data="admin:addcat")],
            [InlineKeyboardButton("🔎 Search", callback_data="admin:searchcat")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:panel")],
        ]
    )


def kb_ap_products() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 List Products (PID)", callback_data="admin:listprod")],
            [InlineKeyboardButton("➕ Add Product", callback_data="admin:addprod")],
            [InlineKeyboardButton("💲 Set Price", callback_data="admin:setprice")],
            [InlineKeyboardButton("⛔ Toggle Product", callback_data="admin:toggle")],
            [InlineKeyboardButton("🔎 Search PID", callback_data="admin:searchpid")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:panel")],
        ]
    )


def kb_ap_codes() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Codes (Text)", callback_data="admin:addcodes")],
            [InlineKeyboardButton("📥 Add Codes (File)", callback_data="admin:addcodesfile")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:panel")],
        ]
    )


def kb_ap_orders() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("❌ Cancel Order (refund)", callback_data="admin:cancelorder")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:panel")],
        ]
    )


def kb_ap_deposits() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Approve Deposit", callback_data="admin:approvedep")],
            [InlineKeyboardButton("🚫 Reject Deposit", callback_data="admin:rejectdep")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:panel")],
        ]
    )


def kb_ap_users() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Balance to User", callback_data="admin:addbal")],
            [InlineKeyboardButton("➖ Take Balance (to Admin)", callback_data="admin:takebal")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:panel")],
        ]
    )


def kb_ap_manual() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 List Pending", callback_data="man:list:pending")],
            [InlineKeyboardButton("🔎 Search Manual ID", callback_data="man:search")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:panel")],
        ]
    )


def kb_ap_manual_items() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 List Items", callback_data="mi:list")],
            [InlineKeyboardButton("💲 Set Price", callback_data="mi:setprice")],
            [InlineKeyboardButton("⛔ Toggle Item", callback_data="mi:toggle")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:panel")],
        ]
    )


def kb_manual_action(manual_id: int, service: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"man:approve:{manual_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"man:reject:{manual_id}:GENERIC"),
        ]
    ]
    if service == "FREEFIRE_MENA":
        rows += [
            [InlineKeyboardButton("🆔 ID Wrong", callback_data=f"man:reject:{manual_id}:ID_WRONG")],
            [InlineKeyboardButton("🌍 Wrong Server", callback_data=f"man:reject:{manual_id}:WRONG_SERVER")],
            [InlineKeyboardButton("⏳ Available Later", callback_data=f"man:reject:{manual_id}:LATER")],
            [InlineKeyboardButton("✍️ Custom Reason", callback_data=f"man:reject_custom:{manual_id}")],
        ]
    else:
        rows += [[InlineKeyboardButton("✍️ Custom Reason", callback_data=f"man:reject_custom:{manual_id}")]]
    return InlineKeyboardMarkup(rows)


def reason_text(code: str) -> str:
    mp = {
        "GENERIC": "لم يتم الشحن. يرجى التواصل مع الدعم.",
        "ID_WRONG": "لم يتم الشحن: الآيدي خطأ.",
        "WRONG_SERVER": "لم يتم الشحن: حسابك في سيرفر آخر.",
        "LATER": "لم يتم الشحن: سيتم توفيره في المستقبل.",
    }
    return mp.get(code, mp["GENERIC"])


# =========================
# Manual Order (Dynamic from DB)
# =========================
def kb_manual_services() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📺 Shahid", callback_data="manual:shahid")],
            [InlineKeyboardButton("💎 Free Fire (MENA)", callback_data="manual:ff")],
            [InlineKeyboardButton("⬅️ Back", callback_data="manual:back")],
        ]
    )


def kb_shahid_plans() -> InlineKeyboardMarkup:
    cur.execute("SELECT sku,title,price FROM manual_items WHERE service='SHAHID' AND active=1 ORDER BY price ASC")
    rows = []
    for sku, title, price in cur.fetchall():
        rows.append([InlineKeyboardButton(f"{title} | {float(price):.3f}{CURRENCY}", callback_data=f"manual:shahid:{sku}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="manual:services")])
    return InlineKeyboardMarkup(rows)


def ff_menu_text() -> str:
    return (
        "💎 Free Fire (MENA)\n\n"
        "How to Place a Free Fire Diamonds Order:\n"
        "Add packs to cart then checkout.\n\n"
        "📦 Delivery Time: 1-5 minutes"
    )


def _ff_cart_get(context):
    cart = context.user_data.get(UD_FF_CART)
    if not isinstance(cart, dict):
        cart = {}
        context.user_data[UD_FF_CART] = cart
    return cart


def _ff_items_active():
    cur.execute("SELECT sku,title,price,meta FROM manual_items WHERE service='FREEFIRE_MENA' AND active=1 ORDER BY price ASC")
    out = []
    for sku, title, price, meta in cur.fetchall():
        diamonds = 0
        try:
            d = json.loads(meta or "{}")
            diamonds = int(d.get("diamonds", 0))
        except Exception:
            diamonds = 0
        out.append((sku, title, diamonds, float(price)))
    return out


def _ff_pack_lookup(sku: str):
    cur.execute("SELECT sku,title,price,meta,active FROM manual_items WHERE service='FREEFIRE_MENA' AND sku=?", (sku,))
    row = cur.fetchone()
    if not row:
        return None
    sku, title, price, meta, active = row
    if int(active) != 1:
        return None
    diamonds = 0
    try:
        diamonds = int(json.loads(meta or "{}").get("diamonds", 0))
    except Exception:
        diamonds = 0
    return (sku, title, diamonds, float(price))


def _ff_calc_totals(cart: Dict[str, int]):
    total_price = 0.0
    total_diamonds = 0
    lines = []
    for sku, qty in cart.items():
        if qty <= 0:
            continue
        pack = _ff_pack_lookup(sku)
        if not pack:
            continue
        _, title, diamonds, price = pack
        total_price += price * qty
        total_diamonds += diamonds * qty
        lines.append((title, qty, price, diamonds, sku))
    return total_price, total_diamonds, lines


def kb_ff_menu(context) -> InlineKeyboardMarkup:
    cart = _ff_cart_get(context)
    rows = []
    items = _ff_items_active()
    for sku, title, diamonds, price in items:
        qty = int(cart.get(sku, 0))
        suffix = f" [{qty}]" if qty > 0 else ""
        rows.append([InlineKeyboardButton(f"{title} 💎 ({diamonds}) | {price:.3f}{CURRENCY}{suffix}", callback_data=f"manual:ff:add:{sku}")])

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
    for title, qty, _, _, _ in lines:
        text_lines.append(f"💎 {title} (x{qty})")

    text_lines.append("")
    text_lines.append(f"💎 Total Diamonds: {total_diamonds}")
    text_lines.append(f"💰 Total: {total_price:.3f}{CURRENCY}")
    text_lines.append("")
    text_lines.append("🆔 Enter Player ID (NUMBERS only) to proceed:\n❌ /cancel to stop")

    context.user_data[UD_FF_TOTAL] = float(total_price)
    context.user_data["ff_total_diamonds"] = int(total_diamonds)
    return "\n".join(text_lines)


# =========================
# Pages
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    ensure_user_exists(ADMIN_ID)
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
        f"🆔 Telegram ID: `{uid}`\n"
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
    chunk = rows[page * page_size : (page + 1) * page_size]

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
# Qty input (Shop)
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
        await update.message.reply_text("❌ Enter numbers only.")
        return ST_QTY

    pid = int(context.user_data.get(UD_PID, 0))
    cid = int(context.user_data.get(UD_CID, 0))
    max_qty = int(context.user_data.get(UD_QTY_MAX, 0))

    if not pid or not cid or max_qty <= 0:
        await update.message.reply_text("❌ Session expired. Open Our Products again.")
        return ConversationHandler.END

    if qty < 1 or qty > max_qty:
        await update.message.reply_text(f"❌ Enter a quantity between 1 and {max_qty}:")
        return ST_QTY

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
        await update.message.reply_text("❌ Format: amount | txid\nExample: 10 | 2E38F3...")
        return ST_TOPUP_DETAILS

    a, txid = [x.strip() for x in txt.split("|", 1)]
    try:
        amount = float(a)
    except ValueError:
        await update.message.reply_text("❌ Amount must be a number.\nExample: 10 | TXID")
        return ST_TOPUP_DETAILS

    cur.execute("SELECT user_id, status FROM deposits WHERE id=?", (dep_id,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ Deposit not found.")
        return ConversationHandler.END

    if row[1] not in ("WAITING_PAYMENT", "PAID"):
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
    await context.bot.send_message(
        ADMIN_ID,
        f"💰 DEPOSIT REVIEW\nDeposit ID: {dep_id}\nUser: {uid}\nAmount: {amount}\nTXID:\n{txid}\n\nApprove: /approvedep {dep_id}\nReject: /rejectdep {dep_id}",
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
        await update.message.reply_text("❌ Send a valid Gmail.\nExample: example@gmail.com")
        return ST_MANUAL_EMAIL

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
        INSERT INTO manual_orders(user_id,service,plan_title,price,email,password,status,balance_before,balance_after)
        VALUES(?,?,?,?,?,?,'PENDING',?,?)
        """,
        (uid, "SHAHID", plan_title, price, email, pwd[:250], float(bal_before), float(bal_after)),
    )
    con.commit()
    mid = cur.lastrowid

    await update.message.reply_text(
        "✅ Manual order created!\n"
        f"Service: {plan_title}\n"
        f"Order ID: {mid}\n"
        f"Paid: {price:.3f} {CURRENCY}\n\n"
        f"💳 Balance before: {bal_before:.3f} {CURRENCY}\n"
        f"✅ Balance after:  {bal_after:.3f} {CURRENCY}\n\n"
        "We will process it soon ✅",
        reply_markup=REPLY_MENU,
    )

    await context.bot.send_message(
        ADMIN_ID,
        (
            "⚡ MANUAL ORDER (SHAHID)\n"
            f"Manual ID: {mid}\n"
            f"User ID:\n`{uid}`\n"
            f"Plan: {plan_title}\n"
            f"Price: {price:.3f} {CURRENCY}\n"
            f"Balance before: {bal_before:.3f} {CURRENCY}\n"
            f"Balance after:  {bal_after:.3f} {CURRENCY}\n"
            f"Gmail: {email}\n"
        ),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_manual_action(mid, "SHAHID"),
    )

    for k in [UD_MANUAL_SERVICE, UD_MANUAL_PLAN, UD_MANUAL_PRICE, UD_MANUAL_PLAN_TITLE, UD_MANUAL_EMAIL]:
        context.user_data.pop(k, None)
    return ConversationHandler.END


# =========================
# Manual: FreeFire PlayerID (Digits Only + /cancel works)
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
        await update.message.reply_text("❌ Player ID must be NUMBERS only.\nExample: 123456789")
        return ST_FF_PLAYERID

    if len(player_id) < 6:
        await update.message.reply_text("❌ Player ID is too short.\nExample: 123456789")
        return ST_FF_PLAYERID

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
    for title, qty, price, diamonds, _sku in lines:
        note_lines.append(f"{title} x{qty} | {price:.3f}{CURRENCY} | diamonds_each={diamonds}")
    note = "\n".join(note_lines)

    plan_title = f"Free Fire (MENA) | Total Diamonds: {total_diamonds}"
    cur.execute(
        """
        INSERT INTO manual_orders(user_id,service,plan_title,price,player_id,note,status,balance_before,balance_after)
        VALUES(?,?,?,?,?,?,'PENDING',?,?)
        """,
        (uid, "FREEFIRE_MENA", plan_title, float(total_price), player_id[:120], note[:4000], float(bal_before), float(bal_after)),
    )
    con.commit()
    mid = cur.lastrowid

    await update.message.reply_text(
        "✅ Manual order created!\n"
        "Service: Free Fire (MENA)\n"
        f"Order ID: {mid}\n"
        f"Player ID: {player_id}\n"
        f"Total Diamonds: {total_diamonds}\n"
        f"Paid: {total_price:.3f} {CURRENCY}\n\n"
        f"💳 Balance before: {bal_before:.3f} {CURRENCY}\n"
        f"✅ Balance after:  {bal_after:.3f} {CURRENCY}\n\n"
        "We will process it soon ✅",
        reply_markup=REPLY_MENU,
    )

    await context.bot.send_message(
        ADMIN_ID,
        (
            "⚡ MANUAL ORDER (FREE FIRE MENA)\n"
            f"Manual ID: {mid}\n"
            f"User ID:\n`{uid}`\n"
            f"Player ID:\n`{player_id}`\n"
            f"Total Diamonds: {total_diamonds}\n"
            f"Total: {total_price:.3f} {CURRENCY}\n"
            f"Balance before: {bal_before:.3f} {CURRENCY}\n"
            f"Balance after:  {bal_after:.3f} {CURRENCY}\n\n"
            f"Cart:\n{note}"
        ),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_manual_action(mid, "FREEFIRE_MENA"),
    )

    context.user_data.pop(UD_FF_CART, None)
    context.user_data.pop(UD_FF_TOTAL, None)
    context.user_data.pop("ff_total_diamonds", None)
    return ConversationHandler.END


# =========================
# Admin: Manual reject custom reason input
# =========================
async def admin_manual_reason_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    txt = (update.message.text or "").strip()
    if txt.lower() in ("/cancel", "cancel"):
        context.user_data.pop(UD_PENDING_MANUAL_REJECT_ID, None)
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    mid = int(context.user_data.get(UD_PENDING_MANUAL_REJECT_ID, 0))
    if not mid:
        await update.message.reply_text("❌ Session expired.")
        return ConversationHandler.END

    reason = txt[:800]
    context.user_data.pop(UD_PENDING_MANUAL_REJECT_ID, None)

    # process reject with custom reason
    await process_manual_reject(update, context, mid, reason)
    return ConversationHandler.END


# =========================
# Manual approve/reject processors
# =========================
async def process_manual_approve(update: Update, context: ContextTypes.DEFAULT_TYPE, mid: int):
    cur.execute("SELECT user_id, service, plan_title, price, status FROM manual_orders WHERE id=?", (mid,))
    row = cur.fetchone()
    if not row:
        await context.bot.send_message(ADMIN_ID, "❌ Manual order not found.")
        return
    user_id, service, plan_title, price, status = int(row[0]), row[1], row[2], float(row[3]), row[4]
    if status != "PENDING":
        await context.bot.send_message(ADMIN_ID, f"ℹ️ Manual #{mid} already processed ({status}).")
        return

    cur.execute(
        "UPDATE manual_orders SET status='COMPLETED', processed_by=?, processed_at=datetime('now') WHERE id=?",
        (ADMIN_ID, mid),
    )
    con.commit()

    await context.bot.send_message(
        user_id,
        "✅ تم الشحن بنجاح!\n\n"
        f"Service: {plan_title}\n"
        f"Manual ID: {mid}\n"
        f"Amount: {price:.3f} {CURRENCY}\n\n"
        "شكراً لاستخدامك متجرنا ❤️",
    )
    await context.bot.send_message(ADMIN_ID, f"✅ Approved Manual #{mid} and notified user {user_id}.")


async def process_manual_reject(update: Update, context: ContextTypes.DEFAULT_TYPE, mid: int, reason: str):
    cur.execute("SELECT user_id, plan_title, price, status FROM manual_orders WHERE id=?", (mid,))
    row = cur.fetchone()
    if not row:
        await context.bot.send_message(ADMIN_ID, "❌ Manual order not found.")
        return
    user_id, plan_title, price, status = int(row[0]), row[1], float(row[2]), row[3]
    if status != "PENDING":
        await context.bot.send_message(ADMIN_ID, f"ℹ️ Manual #{mid} already processed ({status}).")
        return

    # refund
    bal_before_refund = get_balance(user_id)
    add_balance(user_id, price)
    bal_after_refund = get_balance(user_id)

    cur.execute(
        "UPDATE manual_orders SET status='REJECTED', admin_reason=?, processed_by=?, processed_at=datetime('now') WHERE id=?",
        (reason[:800], ADMIN_ID, mid),
    )
    con.commit()

    await context.bot.send_message(
        user_id,
        "❌ لم يتم الشحن.\n\n"
        f"Service: {plan_title}\n"
        f"Manual ID: {mid}\n"
        f"Reason: {reason}\n\n"
        f"💰 تم إرجاع الرصيد: +{price:.3f} {CURRENCY}\n"
        f"Balance before refund: {bal_before_refund:.3f} {CURRENCY}\n"
        f"Balance after refund:  {bal_after_refund:.3f} {CURRENCY}\n\n"
        "إذا تحتاج مساعدة تواصل مع الدعم.",
    )

    await context.bot.send_message(
        ADMIN_ID,
        f"❌ Rejected Manual #{mid}\nUser: {user_id}\nRefund: +{price:.3f} {CURRENCY}\nReason: {reason}\n"
        f"User balance before refund: {bal_before_refund:.3f} {CURRENCY}\nUser balance after refund:  {bal_after_refund:.3f} {CURRENCY}"
    )


# =========================
# Callback handler
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

    # Admin home panel
    if data == "admin:panel":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("👑 Admin Panel", reply_markup=kb_admin_panel())

    # Admin professional sections
    if data == "ap:cats":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("📁 Categories", reply_markup=kb_ap_categories())

    if data == "ap:prods":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("🧩 Products", reply_markup=kb_ap_products())

    if data == "ap:codes":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("🔑 Codes / Stock", reply_markup=kb_ap_codes())

    if data == "ap:orders":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("📦 Orders", reply_markup=kb_ap_orders())

    if data == "ap:deps":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("💰 Deposits", reply_markup=kb_ap_deposits())

    if data == "ap:users":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("👤 Users", reply_markup=kb_ap_users())

    if data == "ap:manual":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("⚡ Manual Orders", reply_markup=kb_ap_manual())

    if data == "ap:manual_items":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("🛠 Manual Prices", reply_markup=kb_ap_manual_items())

    if data == "ap:stats":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute("SELECT COUNT(*) FROM users")
        ucnt = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM orders")
        ocnt = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM manual_orders")
        mcnt = int(cur.fetchone()[0])
        cur.execute("SELECT IFNULL(SUM(amount),0) FROM deposits WHERE status='APPROVED'")
        dep_sum = float(cur.fetchone()[0] or 0)
        text = (
            "📊 Stats\n\n"
            f"👤 Users: {ucnt}\n"
            f"📦 Orders: {ocnt}\n"
            f"⚡ Manual Orders: {mcnt}\n"
            f"💰 Approved Deposits Sum: {dep_sum:.3f} {CURRENCY}\n"
        )
        return await q.edit_message_text(text, reply_markup=kb_admin_panel())

    # Manual approve/reject callbacks
    if data.startswith("man:approve:"):
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        mid = int(data.split(":")[2])
        await q.edit_message_text(f"✅ Approving Manual #{mid}...")
        await process_manual_approve(update, context, mid)
        return

    if data.startswith("man:reject_custom:"):
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        mid = int(data.split(":")[2])
        context.user_data[UD_PENDING_MANUAL_REJECT_ID] = mid
        await q.edit_message_text(f"✍️ Send custom reject reason for Manual #{mid}:\n\n/cancel to stop")
        return ST_ADMIN_MANUAL_REASON

    if data.startswith("man:reject:"):
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        _p, _r, mid_s, code = data.split(":")
        mid = int(mid_s)
        reason = reason_text(code)
        await q.edit_message_text(f"❌ Rejecting Manual #{mid}...")
        await process_manual_reject(update, context, mid, reason)
        return

    if data.startswith("man:list:pending"):
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute(
            "SELECT id,user_id,service,plan_title,price,created_at FROM manual_orders WHERE status='PENDING' ORDER BY id DESC LIMIT 10"
        )
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("✅ No pending manual orders.", reply_markup=kb_ap_manual())
        lines = ["⚡ Pending Manual Orders (last 10)\n"]
        for mid, uid, svc, title, price, created in rows:
            lines.append(f"#{mid} | {svc} | user={uid} | {float(price):.3f}{CURRENCY} | {created}\n{title}")
            lines.append("")
        text = "\n".join(lines)[:3800]
        return await q.edit_message_text(text, reply_markup=kb_ap_manual())

    # Manual items admin actions
    if data == "mi:list":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        cur.execute("SELECT id,service,sku,title,price,active FROM manual_items ORDER BY service, price ASC")
        rows = cur.fetchall()
        if not rows:
            return await q.edit_message_text("No manual items.")
        lines = ["🛠 Manual Items\n"]
        for mid, svc, sku, title, price, active in rows:
            lines.append(f"ID {mid} | {svc} | {sku} | {title} | {float(price):.3f}{CURRENCY} | {'ON' if active else 'OFF'}")
        text = "\n".join(lines)
        if len(text) > 3800:
            text = text[:3800] + "\n..."
        return await q.edit_message_text(text, reply_markup=kb_ap_manual_items())

    if data == "mi:setprice":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "mi_setprice"
        await q.edit_message_text("Send: item_id | new_price\nExample: 3 | 19.5\n/cancel to stop")
        return ST_ADMIN_INPUT

    if data == "mi:toggle":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        context.user_data[UD_ADMIN_MODE] = "mi_toggle"
        await q.edit_message_text("Send: item_id (toggle ON/OFF)\nExample: 3\n/cancel to stop")
        return ST_ADMIN_INPUT

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
        sku = data.split(":")[2]
        cur.execute("SELECT title,price,active FROM manual_items WHERE service='SHAHID' AND sku=?", (sku,))
        row = cur.fetchone()
        if not row or int(row[2]) != 1:
            return await q.edit_message_text("❌ This plan is unavailable now.")
        plan_title, price = row[0], float(row[1])

        uid = update.effective_user.id
        bal = get_balance(uid)
        if bal + 1e-9 < price:
            missing = price - bal
            return await q.edit_message_text(
                f"❌ Insufficient balance.\n\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {price:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}\n\nClick below to top up 👇",
                reply_markup=kb_topup_now(),
            )

        context.user_data[UD_MANUAL_SERVICE] = "SHAHID"
        context.user_data[UD_MANUAL_PLAN] = sku
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
        if not _ff_pack_lookup(sku):
            return await q.edit_message_text("❌ This pack is unavailable.", reply_markup=kb_ff_menu(context))
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

    # Navigation shop
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

    # Confirm purchase (Shop) + show balance before/after
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
        bal_before = get_balance(uid)
        if not charge_balance(uid, total):
            bal = get_balance(uid)
            missing = total - bal
            return await q.edit_message_text(
                f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total:.3f} {CURRENCY}\nMissing: {missing:.3f} {CURRENCY}",
                reply_markup=kb_topup_now(),
            )
        bal_after = get_balance(uid)

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

        await q.edit_message_text(
            "✅ Order created!\n"
            f"Order ID: {oid}\n"
            f"Total: {total:.3f} {CURRENCY}\n"
            f"💳 Balance before: {bal_before:.3f} {CURRENCY}\n"
            f"✅ Balance after:  {bal_after:.3f} {CURRENCY}\n\n"
            "Delivering codes..."
        )
        await send_codes_delivery(chat_id=uid, context=context, order_id=oid, codes=codes_list)

        await context.bot.send_message(
            ADMIN_ID,
            f"✅ NEW COMPLETED ORDER\nOrder ID: {oid}\nUser: {uid}\nProduct: {title}\nQty: {qty}\nTotal: {total:.3f} {CURRENCY}\n"
            f"Balance before: {bal_before:.3f} {CURRENCY}\nBalance after:  {bal_after:.3f} {CURRENCY}"
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

    # Payment create deposit
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

    # Admin actions entry
    if data.startswith("admin:"):
        if not is_admin(update.effective_user.id):
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
            lines = [f"PID {pid} | {cat} | {title} | {float(price):.3f}{CURRENCY} | {'ON' if act else 'OFF'}" for pid, cat, title, price, act in rows]
            text = "\n".join(lines)
            if len(text) > 3800:
                text = text[:3800] + "\n..."
            return await q.edit_message_text(text)

        prompts = {
            "addcat": 'Send category title:\nExample: 🪂 PUBG MOBILE UC VOUCHERS\n/cancel to stop',
            "searchcat": 'Send part of category title to search\n/cancel to stop',
            "searchpid": 'Send PID to view product\n/cancel to stop',
            "addprod": 'Send product:\nFormat: "Category Title" | "Product Title" | price\nExample:\n"🍎 ITUNES GIFTCARD (USA)" | "10$ iTunes US" | 9.2\n/cancel to stop',
            "addcodes": 'Add Codes (Text)\nFormat:\npid | CODE1\\nCODE2\\n...\nExample:\n12 | AAAA-1111\\nBBBB-2222\n/cancel to stop',
            "addcodesfile": '📥 Send a .txt file now.\nFile format:\nFirst line: pid | (optional)\nThen codes each line.\nExample file:\n12 |\nAAAA-1111\nBBBB-2222\n/cancel to stop',
            "setprice": 'Send: pid | new_price\nExample: 12 | 9.5\n/cancel to stop',
            "toggle": 'Send: pid (toggle ON/OFF)\nExample: 12\n/cancel to stop',
            "cancelorder": 'Send: order_id (refund)\nExample: 55\n/cancel to stop',
            "approvedep": 'Send: deposit_id\nExample: 10\n/cancel to stop',
            "rejectdep": 'Send: deposit_id\nExample: 10\n/cancel to stop',
            "addbal": 'Send: user_id | amount\nExample: 1997968014 | 5\n/cancel to stop',
            "takebal": 'Send: user_id | amount\nExample: 1997968014 | 5\n/cancel to stop',
        }

        await q.edit_message_text(prompts.get(mode, "Send input now...\n/cancel to stop"))
        if mode == "addcodesfile":
            return ST_ADMIN_CODES_FILE
        return ST_ADMIN_INPUT


# =========================
# Admin input (text)
# =========================
async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    mode = context.user_data.get(UD_ADMIN_MODE)
    text = (update.message.text or "").strip()

    if text.lower() in ("/cancel", "cancel"):
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    try:
        if mode == "addcat":
            cur.execute("INSERT OR IGNORE INTO categories(title) VALUES(?)", (text,))
            con.commit()
            await update.message.reply_text("✅ Category added.")
            return ConversationHandler.END

        if mode == "searchcat":
            cur.execute("SELECT cid,title FROM categories WHERE title LIKE ? ORDER BY title LIMIT 20", (f"%{text}%",))
            rows = cur.fetchall()
            if not rows:
                await update.message.reply_text("No results.")
                return ConversationHandler.END
            out = "\n".join([f"{cid} | {title}" for cid, title in rows])
            await update.message.reply_text(out[:3800])
            return ConversationHandler.END

        if mode == "searchpid":
            pid = int(text)
            cur.execute("SELECT pid,title,price,active FROM products WHERE pid=?", (pid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Not found.")
                return ConversationHandler.END
            pid, title, price, active = row
            st = product_stock(pid)
            await update.message.reply_text(
                f"PID: {pid}\nTitle: {title}\nPrice: {float(price):.3f}{CURRENCY}\nStock: {st}\nStatus: {'ON' if active else 'OFF'}"
            )
            return ConversationHandler.END

        if mode == "addprod":
            m = re.match(r'^"(.+?)"\s*\|\s*"(.+?)"\s*\|\s*([\d.]+)\s*$', text)
            if not m:
                await update.message.reply_text("❌ Format invalid.\nExample:\n\"CAT\" | \"TITLE\" | 9.2")
                return ConversationHandler.END
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
                return ConversationHandler.END
            pid_s, codes_blob = [x.strip() for x in text.split("|", 1)]
            pid = int(pid_s)
            codes = [c.strip() for c in codes_blob.splitlines() if c.strip()]
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
                await update.message.reply_text("❌ Format: pid | price\nExample: 12 | 9.5")
                return ConversationHandler.END
            pid, price = int(m.group(1)), float(m.group(2))
            cur.execute("UPDATE products SET price=? WHERE pid=?", (price, pid))
            con.commit()
            await update.message.reply_text("✅ Price updated.")
            return ConversationHandler.END

        if mode == "toggle":
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
            await update.message.reply_text(f"✅ Product {'enabled' if newv else 'disabled'}.")
            return ConversationHandler.END

        if mode == "cancelorder":
            oid = int(text)
            cur.execute("SELECT user_id,total,status FROM orders WHERE id=?", (oid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Order not found.")
                return ConversationHandler.END
            user_id, total, status = int(row[0]), float(row[1]), row[2]
            if status == "COMPLETED":
                await update.message.reply_text("❌ Cannot cancel completed order.")
                return ConversationHandler.END
            if status == "CANCELLED":
                await update.message.reply_text("❌ Already cancelled.")
                return ConversationHandler.END
            add_balance(user_id, total)
            cur.execute("UPDATE orders SET status='CANCELLED' WHERE id=?", (oid,))
            con.commit()
            await update.message.reply_text(f"✅ Order #{oid} cancelled + refunded.")
            await context.bot.send_message(user_id, f"❌ Order #{oid} cancelled.\nRefunded: +{money(total)}")
            return ConversationHandler.END

        if mode == "approvedep":
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
            cur.execute("UPDATE deposits SET status='APPROVED' WHERE id=?", (dep_id,))
            con.commit()
            add_balance(user_id, float(amount))
            await update.message.reply_text(f"✅ Deposit #{dep_id} approved. +{money(float(amount))}")
            await context.bot.send_message(user_id, f"✅ Top up approved: +{money(float(amount))}")
            return ConversationHandler.END

        if mode == "rejectdep":
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
                return ConversationHandler.END
            user_id, amount = int(m.group(1)), float(m.group(2))
            add_balance(user_id, amount)
            await update.message.reply_text(f"✅ Added +{money(amount)} to {user_id}")
            await context.bot.send_message(user_id, f"✅ Admin added balance: +{money(amount)}")
            return ConversationHandler.END

        if mode == "takebal":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: user_id | amount\nExample: 1997968014 | 5")
                return ConversationHandler.END
            user_id, amount = int(m.group(1)), float(m.group(2))
            if not charge_balance(user_id, amount):
                bal = get_balance(user_id)
                await update.message.reply_text(f"❌ User has insufficient balance. User balance: {bal:.3f} {CURRENCY}")
                return ConversationHandler.END
            add_balance(ADMIN_ID, amount)
            await update.message.reply_text(f"✅ Took {money(amount)} from {user_id} → added to Admin.")
            await context.bot.send_message(user_id, f"➖ Admin deducted: -{money(amount)}")
            return ConversationHandler.END

        if mode == "mi_setprice":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: item_id | price\nExample: 3 | 19.5")
                return ConversationHandler.END
            item_id, price = int(m.group(1)), float(m.group(2))
            cur.execute("UPDATE manual_items SET price=? WHERE id=?", (price, item_id))
            con.commit()
            await update.message.reply_text("✅ Manual item price updated.")
            return ConversationHandler.END

        if mode == "mi_toggle":
            item_id = int(text)
            cur.execute("SELECT active FROM manual_items WHERE id=?", (item_id,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Item not found.")
                return ConversationHandler.END
            active = int(row[0])
            newv = 0 if active else 1
            cur.execute("UPDATE manual_items SET active=? WHERE id=?", (newv, item_id))
            con.commit()
            await update.message.reply_text(f"✅ Manual item {'enabled' if newv else 'disabled'}.")
            return ConversationHandler.END

        await update.message.reply_text("✅ Done.")
        return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END


# =========================
# Admin: Add codes by FILE
# =========================
async def admin_codes_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    if update.message.text and update.message.text.strip().lower() in ("/cancel", "cancel"):
        await update.message.reply_text("✅ Cancelled.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Please send a .txt file.\n/cancel to stop")
        return ST_ADMIN_CODES_FILE

    if not (doc.file_name or "").lower().endswith(".txt"):
        await update.message.reply_text("❌ Only .txt file allowed.\n/cancel to stop")
        return ST_ADMIN_CODES_FILE

    file = await context.bot.get_file(doc.file_id)
    data = await file.download_as_bytearray()
    try:
        content = data.decode("utf-8", errors="ignore")
    except Exception:
        await update.message.reply_text("❌ Could not read file as UTF-8.")
        return ConversationHandler.END

    lines = [x.strip() for x in content.splitlines() if x.strip()]
    if not lines:
        await update.message.reply_text("❌ File is empty.")
        return ConversationHandler.END

    # allow first line "pid |" or "pid" or "pid | CODE"
    first = lines[0]
    pid = None
    codes = []
    if "|" in first:
        left, right = [x.strip() for x in first.split("|", 1)]
        if left.isdigit():
            pid = int(left)
            if right:
                codes.append(right)
            codes += lines[1:]
    else:
        if first.isdigit():
            pid = int(first)
            codes += lines[1:]
        else:
            await update.message.reply_text("❌ First line must contain PID.\nExample:\n12 |")
            return ConversationHandler.END

    codes = [c.strip() for c in codes if c.strip()]
    if not pid or not codes:
        await update.message.reply_text("❌ No codes found in file.")
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


# =========================
# Admin commands
# =========================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed.")
    await update.message.reply_text("👑 Admin Panel", reply_markup=kb_admin_panel())


async def approvedep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /approvedep <deposit_id>")
    context.user_data[UD_ADMIN_MODE] = "approvedep"
    update.message.text = context.args[0]
    return await admin_input(update, context)


async def rejectdep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_callback)],
        states={
            ST_QTY: [MessageHandler(filters.TEXT, qty_input)],
            ST_TOPUP_DETAILS: [MessageHandler(filters.TEXT, topup_details_input)],
            ST_ADMIN_INPUT: [MessageHandler(filters.TEXT, admin_input)],
            ST_ADMIN_CODES_FILE: [MessageHandler(filters.Document.ALL | filters.TEXT, admin_codes_file)],
            ST_ADMIN_MANUAL_REASON: [MessageHandler(filters.TEXT, admin_manual_reason_input)],
            ST_MANUAL_EMAIL: [MessageHandler(filters.TEXT, manual_email_input)],
            ST_MANUAL_PASS: [MessageHandler(filters.TEXT, manual_pass_input)],
            ST_FF_PLAYERID: [MessageHandler(filters.TEXT, ff_playerid_input)],
        },
        fallbacks=[CommandHandler("start", start_cmd)],
        allow_reentry=True,
    )

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("approvedep", approvedep_cmd))
    app.add_handler(CommandHandler("rejectdep", rejectdep_cmd))

    # Conversation
    app.add_handler(conv)

    # Menu
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    return app


def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

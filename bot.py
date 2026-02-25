import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "shop.db")

CURRENCY = os.getenv("CURRENCY", "$")

BINANCE_ID = os.getenv("BINANCE_ID", "YOUR_BINANCE_ID")
BYBIT_ID = os.getenv("BYBIT_ID", "YOUR_BYBIT_ID")
USDT_TRC20 = os.getenv("USDT_TRC20", "YOUR_USDT_TRC20")
USDT_BEP20 = os.getenv("USDT_BEP20", "YOUR_USDT_BEP20")

SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+213xxxxxxxxx")
SUPPORT_GROUP = os.getenv("SUPPORT_GROUP", "@yourgroup")
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "@yourchannel")

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

CREATE TABLE IF NOT EXISTS categories(
  cid INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL UNIQUE
);

-- product_type: 'CODE' => تسليم كود تلقائي من المخزون
-- product_type: 'MANUAL' => طلب يحتاج تدخل أدمن (مثل شحن UC)
-- need_player_id: 1/0
CREATE TABLE IF NOT EXISTS products(
  pid INTEGER PRIMARY KEY AUTOINCREMENT,
  cid INTEGER NOT NULL,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  product_type TEXT NOT NULL DEFAULT 'CODE',
  need_player_id INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(cid) REFERENCES categories(cid)
);

-- مخزون الأكواد (لكل منتج CODE)
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
  status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/COMPLETED/CANCELLED
  player_id TEXT,
  delivered_text TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- شحن الرصيد (يرسل Ref/TXID ثم الأدمن يوافق)
CREATE TABLE IF NOT EXISTS deposits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  method TEXT NOT NULL,    -- BINANCE/BYBIT/TRC20/BEP20
  ref TEXT NOT NULL,
  amount REAL,
  status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
)
con.commit()

# =========================
# SEED: أقسام كاملة مثل اللي طلبتها
# (تقدر تعدل/تزيد من الأدمن بعدين)
# =========================
DEFAULT_CATEGORIES = [
    "🪂 PUBG MOBILE UC VOUCHERS",
    "💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)",
    "🎲 YALLA LUDO",
    "🍏 ITUNES GIFTCARD (USA)",
    "🎮 PLAYSTATION USA GIFTCARDS",
    "🕹 ROBLOX (USA)",
    "🟦 STEAM (USA)",
]

DEFAULT_PRODUCTS = [
    # category, title, price, type, need_player_id
   ("🪂 PUBG MOBILE UC VOUCHERS", "60 UC", 0.875, "CODE", 0),
("🪂 PUBG MOBILE UC VOUCHERS", "325 UC", 4.375, "CODE", 0),
("🪂 PUBG MOBILE UC VOUCHERS", "660 UC", 0.875, "CODE", 0),
    ("🪂 PUBG MOBILE UC VOUCHERS", "1800 UC", 0.875, "CODE", 0),
    ("🪂 PUBG MOBILE UC VOUCHERS", "3850 UC", 0.875, "CODE", 0),
    ("🪂 PUBG MOBILE UC VOUCHERS", "8100 UC", 0.875, "CODE", 0),

    
("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "1 USD - 100+10", 0.920, "CODE", 0),
("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "2 USD - 210+21", 1.840, "CODE", 0),
("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "5 USD - 530+53", 0.920, "CODE", 0),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "10 USD - 1080+108", 0.920, "CODE", 0),
    ("💎 GARENA FREE FIRE VOUCHERS (OFFICIAL)", "20 USD - 2200+220", 0.920, "CODE", 0),
    
("🎲 YALLA LUDO", "3.7K Hearts + 10 RP", 9.000, "CODE", 0),

    ("🍏 ITUNES GIFTCARD (USA)", "10$ iTunes US", 9.200, "CODE", 0),
    ("🍏 ITUNES GIFTCARD (USA)", "25$ iTunes US", 23.000, "CODE", 0),

    ("🎮 PLAYSTATION USA GIFTCARDS", "25$ PSN USA", 22.000, "CODE", 0),

    ("🕹 ROBLOX (USA)", "Roblox 10$", 9.000, "CODE", 0),

    ("🟦 STEAM (USA)", "Steam 10$", 9.500, "CODE", 0),
]

def seed_defaults():
    # categories
    for cat in DEFAULT_CATEGORIES:
        cur.execute("INSERT OR IGNORE INTO categories(title) VALUES(?)", (cat,))
    con.commit()

    # products (if not exists by title+category)
    for cat, title, price, ptype, need_pid in DEFAULT_PRODUCTS:
        cur.execute("SELECT cid FROM categories WHERE title=?", (cat,))
        row = cur.fetchone()
        if not row:
            continue
        cid = int(row[0])
        cur.execute("SELECT pid FROM products WHERE cid=? AND title=?", (cid, title))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO products(cid,title,price,product_type,need_player_id,active) VALUES(?,?,?,?,?,1)",
            (cid, title, float(price), ptype, int(need_pid)),
        )
    con.commit()

seed_defaults()

# =========================
# UI: Reply Menu مثل الصور
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
# Helpers
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

def to_tme(x: str) -> str:
    x = (x or "").strip()
    if x.startswith("http://") or x.startswith("https://"):
        return x
    if x.startswith("@"):
        return f"https://t.me/{x[1:]}"
    return f"https://t.me/{x}"

# =========================
# Keyboards (Inline)
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
        rows.append([InlineKeyboardButton(f"{title} | {cnt}", callback_data=f"cat:{cid}")])
    return InlineKeyboardMarkup(rows)

def kb_products(cid: int) -> InlineKeyboardMarkup:
    cur.execute(
        "SELECT pid,title,price,product_type FROM products WHERE cid=? AND active=1 ORDER BY title",
        (cid,),
    )
    rows = []
    items = cur.fetchall()
    if not items:
        rows.append([InlineKeyboardButton("⚠️ No products", callback_data="noop")])
    for pid, title, price, ptype in items:
        # stock for CODE products = count unused codes
        stock_label = "-"
        if ptype == "CODE":
            cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
            stock_label = str(cur.fetchone()[0])
        rows.append([InlineKeyboardButton(f"{title} | {money(price)} | {stock_label}", callback_data=f"prod:{pid}")])
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
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✉️ Contact Support (Chat)", url=to_tme(SUPPORT_GROUP))],
            [InlineKeyboardButton("📣 Support Channel", url=to_tme(SUPPORT_CHANNEL))],
        ]
    )

def kb_admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Category", callback_data="admin:addcat")],
            [InlineKeyboardButton("➕ Add Product", callback_data="admin:addprod")],
            [InlineKeyboardButton("➕ Add Codes (stock)", callback_data="admin:addcodes")],
            [InlineKeyboardButton("💲 Set Price", callback_data="admin:setprice")],
            [InlineKeyboardButton("⛔ Disable/Enable Product", callback_data="admin:toggle")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data="admin:cancelorder")],
            [InlineKeyboardButton("✅ Deliver Order", callback_data="admin:deliver")],
            [InlineKeyboardButton("💰 Approve Deposit", callback_data="admin:approvedep")],
        ]
    )

# =========================
# States
# =========================
ST_TOPUP_REF = 10
ST_PLAYER_ID = 20
ST_ADMIN_INPUT = 99

# user_data keys
UD_CID = "cid"
UD_PID = "pid"
UD_QTY = "qty"
UD_NEED_PLAYER = "need_player"
UD_PLAYER = "player"
UD_TOPUP_METHOD = "topup_method"
UD_ORD_RNG = "orders_rng"
UD_ADMIN_MODE = "admin_mode"

# =========================
# Pages
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
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
        f"📞 Phone: {SUPPORT_PHONE}\n"
        f"👥 Support Group: {SUPPORT_GROUP}\n\n"
        "Choose an option below:"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=kb_support())
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb_support())

# =========================
# Smart support (لمسات ذكاء بسيطة بدون API)
# =========================
def smart_reply(msg: str) -> Optional[str]:
    m = msg.lower()
    if any(x in m for x in ["price", "سعر", "كم", "ثمن"]):
        return "💡 الأسعار تظهر داخل زر المنتجات. ادخل Our Products واختر القسم."
    if any(x in m for x in ["balance", "رصيد", "wallet", "محفظة"]):
        return "💡 اضغط My Balance لمشاهدة الرصيد وطرق الشحن."
    if any(x in m for x in ["order", "طلب", "orders", "طلباتي"]):
        return "💡 اضغط My Orders لمشاهدة الطلبات."
    if any(x in m for x in ["usdt", "trc20", "bep20", "txid"]):
        return "💡 من My Balance اختر طريقة الشحن ثم أرسل TXID/Reference داخل البوت."
    return None

# =========================
# Router (Reply Menu)
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
        await update.message.reply_text(
            "⚡ Manual Order:\nاكتب تفاصيل طلبك هنا (المنتج + الكمية + Player ID إن وجد). سيتم إرسالها للأدمن."
        )
        # forward to admin in next normal message
        context.user_data["manual_mode"] = True
        return

    # إذا المستخدم داخل manual_mode
    if context.user_data.get("manual_mode"):
        context.user_data["manual_mode"] = False
        text = update.message.text or ""
        uid = update.effective_user.id
        await update.message.reply_text("✅ تم إرسال طلبك للأدمن. سيتم الرد قريباً.", reply_markup=REPLY_MENU)
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚡ MANUAL ORDER\nUser: {uid}\n\n{text}\n\n(رد على المستخدم من عندك مباشرة)",
        )
        return

    # smart reply
    hint = smart_reply(t)
    if hint:
        await update.message.reply_text(hint, reply_markup=REPLY_MENU)
    else:
        await update.message.reply_text("Use the menu 👇", reply_markup=REPLY_MENU)

# =========================
# Callback (Shop + Orders + Topup + Admin panel)
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "noop":
        return

    # Admin panel open
    if data == "admin:panel":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        return await q.edit_message_text("👑 Admin Panel", reply_markup=kb_admin_panel())

    # Admin choose an action -> ask for input
    if data.startswith("admin:") and data != "admin:panel":
        if not is_admin(update.effective_user.id):
            return await q.edit_message_text("❌ Not allowed.")
        mode = data.split(":", 1)[1]
        context.user_data[UD_ADMIN_MODE] = mode

        prompts = {
            "addcat": 'Send category title:\nExample: 🪂 PUBG MOBILE UC VOUCHERS',
            "addprod": 'Send product:\nFormat: "Category Title" | "Product Title" | price | TYPE(CODE/MANUAL) | need_player(0/1)\nExample:\n"🍏 ITUNES GIFTCARD (USA)" | "10$ iTunes US" | 9.2 | CODE | 0',
            "addcodes": 'Send codes:\nFormat: pid | code1\\ncode2\\n...\nExample:\n12 | ABCD-1234\nEFGH-5678',
            "setprice": 'Send: pid | new_price\nExample: 12 | 9.5',
            "toggle": 'Send: pid (will toggle active)\nExample: 12',
            "cancelorder": 'Send: order_id\nExample: 55',
            "deliver": 'Send: order_id | message/codes\nExample: 55 | CODE: XXXX-YYYY',
            "approvedep": 'Send: deposit_id | amount\nExample: 10 | 5.0',
        }
        await q.edit_message_text(prompts.get(mode, "Send input now..."))
        return ST_ADMIN_INPUT

    # Categories
    if data == "back:cats":
        return await show_categories(update, context)

    if data.startswith("cat:"):
        cid = int(data.split(":", 1)[1])
        context.user_data[UD_CID] = cid
        context.user_data[UD_QTY] = 1
        return await q.edit_message_text("Choose a product:", reply_markup=kb_products(cid))

    if data.startswith("back:prods:"):
        cid = int(data.split(":", 2)[2])
        return await q.edit_message_text("Choose a product:", reply_markup=kb_products(cid))

    if data.startswith("cancel:"):
        cid = int(data.split(":", 1)[1])
        context.user_data[UD_QTY] = 1
        context.user_data[UD_PID] = None
        return await q.edit_message_text("✅ Cancelled.", reply_markup=kb_products(cid))

    # Product
    if data.startswith("prod:"):
        pid = int(data.split(":", 1)[1])
        cur.execute("SELECT pid,title,price,cid,product_type,need_player_id FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await q.edit_message_text("❌ Product not found.")
        _, title, price, cid, ptype, need_player = row

        # For CODE, check stock count
        stock_text = "-"
        if ptype == "CODE":
            cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
            stock = int(cur.fetchone()[0])
            stock_text = str(stock)
            if stock <= 0:
                return await q.edit_message_text("❌ Out of stock.", reply_markup=kb_products(cid))

        context.user_data[UD_PID] = pid
        context.user_data[UD_CID] = cid
        context.user_data[UD_QTY] = 1
        context.user_data[UD_NEED_PLAYER] = int(need_player)

        msg = (
            "🛒 Your Order\n\n"
            f"🔹 Product: {title}\n"
            f"💎 Price: {money(float(price))}\n"
            f"📦 Stock: {stock_text}\n\n"
            "Choose quantity then Confirm."
        )
        return await q.edit_message_text(msg, reply_markup=kb_qty(pid, 1, cid))

    # Qty
    if data.startswith("qty:"):
        _, op, pid_s = data.split(":", 2)
        pid = int(pid_s)
        qty = int(context.user_data.get(UD_QTY, 1))
        if op == "+":
            qty += 1
        else:
            qty = max(1, qty - 1)
        context.user_data[UD_QTY] = qty

        cur.execute("SELECT title,price,cid,product_type FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return
        title, price, cid, ptype = row
        stock_text = "-"
        if ptype == "CODE":
            cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
            stock_text = str(int(cur.fetchone()[0]))

        msg = (
            "🛒 Your Order\n\n"
            f"🔹 Product: {title}\n"
            f"💎 Price: {money(float(price))}\n"
            f"📦 Stock: {stock_text}\n\n"
            "Choose quantity then Confirm."
        )
        return await q.edit_message_text(msg, reply_markup=kb_qty(pid, qty, cid))

    # Confirm -> if need_player_id ask first
    if data.startswith("confirm:"):
        pid = int(data.split(":", 1)[1])
        qty = int(context.user_data.get(UD_QTY, 1))
        need_player = int(context.user_data.get(UD_NEED_PLAYER, 0))
        context.user_data[UD_PID] = pid
        context.user_data[UD_QTY] = qty

        if need_player:
            await q.edit_message_text("🆔 Please enter Player ID:")
            return ST_PLAYER_ID

        # proceed without player id
        return await _place_order(update, context, player_id=None)

    # Orders
    if data.startswith("orders:range:"):
        _, _, rng, page = data.split(":")
        return await show_orders(update, context, rng=rng, page=int(page))

    if data.startswith("orders:next:"):
        _, _, page = data.split(":")
        rng = context.user_data.get(UD_ORD_RNG) or "all"
        return await show_orders(update, context, rng=rng, page=int(page))

    # Topup select method
    if data.startswith("topup:"):
        method = data.split(":", 1)[1]
        context.user_data[UD_TOPUP_METHOD] = method

        if method == "BINANCE":
            msg = f"🌕 Binance ID top up\n\nSend transfer to Binance Pay ID:\n{BINANCE_ID}\n\nThen send your reference/TXID here."
        elif method == "BYBIT":
            msg = f"🌕 Bybit ID top up\n\nSend transfer to Bybit ID:\n{BYBIT_ID}\n\nThen send your reference here."
        elif method == "TRC20":
            msg = f"💎 USDT (TRC20)\n\nSend USDT to:\n{USDT_TRC20}\n\nThen send TXID here."
        else:
            msg = f"💎 USDT (BEP20)\n\nSend USDT to:\n{USDT_BEP20}\n\nThen send TXID here."

        await q.edit_message_text(msg)
        return ST_TOPUP_REF

# =========================
# Place order logic (CODE auto-delivery or MANUAL admin delivery)
# =========================
async def _place_order(update: Update, context: ContextTypes.DEFAULT_TYPE, player_id: Optional[str]):
    # Called either from callback or after player_id input
    uid = update.effective_user.id
    pid = int(context.user_data.get(UD_PID))
    qty = int(context.user_data.get(UD_QTY, 1))

    cur.execute("SELECT title,price,product_type,cid FROM products WHERE pid=? AND active=1", (pid,))
    row = cur.fetchone()
    if not row:
        txt = "❌ Product not found."
        if update.callback_query:
            return await update.callback_query.edit_message_text(txt)
        return await update.message.reply_text(txt, reply_markup=REPLY_MENU)

    title, price, ptype, cid = row
    total = float(price) * qty

    # for CODE check stock
    if ptype == "CODE":
        cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
        stock = int(cur.fetchone()[0])
        if stock < qty:
            txt = "❌ Out of stock."
            if update.callback_query:
                return await update.callback_query.edit_message_text(txt, reply_markup=kb_products(cid))
            return await update.message.reply_text(txt, reply_markup=kb_products(cid))

    # charge
    if not charge_balance(uid, total):
        bal = get_balance(uid)
        txt = f"❌ Insufficient balance.\nYour balance: {bal:.3f} {CURRENCY}\nRequired: {total:.3f} {CURRENCY}"
        if update.callback_query:
            return await update.callback_query.edit_message_text(txt)
        return await update.message.reply_text(txt, reply_markup=REPLY_MENU)

    # create order
    cur.execute(
        "INSERT INTO orders(user_id,pid,product_title,qty,total,status,player_id) VALUES(?,?,?,?,?,?,?)",
        (uid, pid, title, qty, total, "PENDING", player_id),
    )
    oid = cur.lastrowid
    con.commit()

    # deliver
    if ptype == "CODE":
        # fetch qty codes and mark used
        cur.execute("SELECT code_id, code_text FROM codes WHERE pid=? AND used=0 LIMIT ?", (pid, qty))
        picked = cur.fetchall()
        if len(picked) < qty:
            # rollback (very rare)
            add_balance(uid, total)
            cur.execute("UPDATE orders SET status='CANCELLED' WHERE id=?", (oid,))
            con.commit()
            txt = "❌ Stock error. Try again."
            if update.callback_query:
                return await update.callback_query.edit_message_text(txt)
            return await update.message.reply_text(txt, reply_markup=REPLY_MENU)

        codes_text = "\n".join([c for _, c in picked])
        for code_id, _ in picked:
            cur.execute(
                "UPDATE codes SET used=1, used_at=datetime('now'), order_id=? WHERE code_id=?",
                (oid, code_id),
            )
        cur.execute("UPDATE orders SET status='COMPLETED', delivered_text=? WHERE id=?", (codes_text, oid))
        con.commit()

        msg = (
            f"✅ Order COMPLETED\n\n"
            f"📦 Order ID: {oid}\n"
            f"Product: {title}\n"
            f"Qty: {qty}\n"
            f"Total: {total:.3f} {CURRENCY}\n\n"
            f"🎁 Your Codes:\n{codes_text}"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg, reply_markup=REPLY_MENU)
        await context.bot.send_message(ADMIN_ID, f"✅ AUTO-DELIVERED\nOrder {oid}\nUser {uid}\n{title} x{qty}")
        return ConversationHandler.END

    # MANUAL: notify admin
    msg_user = (
        f"✅ Order Created\n\n"
        f"📦 Order ID: {oid}\n"
        f"Product: {title}\n"
        f"Qty: {qty}\n"
        f"Total: {total:.3f} {CURRENCY}\n"
        f"Player ID: {player_id or '-'}\n\n"
        "⏳ Waiting for admin delivery."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(msg_user)
    else:
        await update.message.reply_text(msg_user, reply_markup=REPLY_MENU)

    await context.bot.send_message(
        ADMIN_ID,
        (
            f"🛒 NEW MANUAL ORDER\n"
            f"Order ID: {oid}\nUser: {uid}\n"
            f"Product: {title}\nQty: {qty}\nTotal: {total:.3f} {CURRENCY}\n"
            f"Player ID: {player_id or '-'}\n\n"
            f"Deliver with:\n/deliver {oid} <message/codes>\n"
            f"Or cancel:\n/cancelorder {oid}"
        ),
    )
    return ConversationHandler.END

# =========================
# Player ID input
# =========================
async def player_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid_txt = (update.message.text or "").strip()
    return await _place_order(update, context, player_id=pid_txt)

# =========================
# Topup ref input
# =========================
async def topup_ref_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ref = (update.message.text or "").strip()
    method = context.user_data.get(UD_TOPUP_METHOD)
    if not method:
        await update.message.reply_text("❌ Choose method from My Balance.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    uid = update.effective_user.id
    cur.execute("INSERT INTO deposits(user_id,method,ref,status) VALUES(?,?,?,?)", (uid, method, ref[:1500], "PENDING"))
    dep_id = cur.lastrowid
    con.commit()

    await update.message.reply_text(
        f"✅ Top up request received.\nDeposit ID: {dep_id}\nStatus: PENDING\n\nWe will review it soon.",
        reply_markup=REPLY_MENU,
    )

    await context.bot.send_message(
        ADMIN_ID,
        f"💰 NEW TOPUP\nDeposit ID: {dep_id}\nUser: {uid}\nMethod: {method}\nRef:\n{ref}\n\nApprove: /approvedep {dep_id} <amount>\nReject: /rejectdep {dep_id}",
    )
    return ConversationHandler.END

# =========================
# Admin commands (full control)
# =========================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("👑 Admin Panel", reply_markup=kb_admin_panel())

async def deliver_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /deliver <order_id> <message/codes>")
    oid = int(context.args[0])
    delivered = " ".join(context.args[1:]).strip()

    cur.execute("SELECT user_id,status FROM orders WHERE id=?", (oid,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Order not found.")
    user_id, status = int(row[0]), row[1]
    if status == "COMPLETED":
        return await update.message.reply_text("❌ Already completed.")

    cur.execute("UPDATE orders SET status='COMPLETED', delivered_text=? WHERE id=?", (delivered, oid))
    con.commit()

    await update.message.reply_text(f"✅ Order #{oid} delivered.")
    await context.bot.send_message(user_id, f"✅ Order #{oid} COMPLETED:\n\n{delivered}")

async def cancelorder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 1:
        return await update.message.reply_text("Usage: /cancelorder <order_id>")
    oid = int(context.args[0])

    cur.execute("SELECT user_id,total,status FROM orders WHERE id=?", (oid,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Order not found.")
    user_id, total, status = int(row[0]), float(row[1]), row[2]
    if status in ("CANCELLED",):
        return await update.message.reply_text("❌ Already cancelled.")
    if status == "COMPLETED":
        return await update.message.reply_text("❌ Cannot cancel completed order.")

    # refund
    add_balance(user_id, total)
    cur.execute("UPDATE orders SET status='CANCELLED' WHERE id=?", (oid,))
    con.commit()

    await update.message.reply_text(f"✅ Order #{oid} cancelled and refunded.")
    await context.bot.send_message(user_id, f"❌ Order #{oid} cancelled. Refunded: +{money(total)}")

async def approvedep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /approvedep <deposit_id> <amount>")
    dep_id = int(context.args[0])
    amount = float(context.args[1])

    cur.execute("SELECT user_id,status FROM deposits WHERE id=?", (dep_id,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Deposit not found.")
    user_id, status = int(row[0]), row[1]
    if status != "PENDING":
        return await update.message.reply_text("❌ Already processed.")

    cur.execute("UPDATE deposits SET status='APPROVED', amount=? WHERE id=?", (amount, dep_id))
    con.commit()
    add_balance(user_id, amount)

    await update.message.reply_text(f"✅ Deposit #{dep_id} approved. +{money(amount)} to {user_id}")
    await context.bot.send_message(user_id, f"✅ Top up approved: +{money(amount)}")

async def rejectdep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 1:
        return await update.message.reply_text("Usage: /rejectdep <deposit_id>")
    dep_id = int(context.args[0])

    cur.execute("SELECT user_id,status FROM deposits WHERE id=?", (dep_id,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Deposit not found.")
    user_id, status = int(row[0]), row[1]
    if status != "PENDING":
        return await update.message.reply_text("❌ Already processed.")

    cur.execute("UPDATE deposits SET status='REJECTED' WHERE id=?", (dep_id,))
    con.commit()

    await update.message.reply_text(f"✅ Deposit #{dep_id} rejected.")
    await context.bot.send_message(user_id, f"❌ Top up #{dep_id} rejected. Contact support.")

# =========================
# Admin input via panel (single message parser)
# =========================
async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    mode = context.user_data.get(UD_ADMIN_MODE)
    text = (update.message.text or "").strip()

    try:
        if mode == "addcat":
            cur.execute("INSERT OR IGNORE INTO categories(title) VALUES(?)", (text,))
            con.commit()
            await update.message.reply_text("✅ Category added.")
            return ConversationHandler.END

        if mode == "addprod":
            # "Category" | "Product" | price | TYPE | need_player
            m = re.match(r'^"(.+?)"\s*\|\s*"(.+?)"\s*\|\s*([\d.]+)\s*\|\s*(CODE|MANUAL)\s*\|\s*([01])$', text)
            if not m:
                await update.message.reply_text("❌ Format invalid. Check example.")
                return ConversationHandler.END
            cat_title, prod_title, price_s, ptype, need_p = m.groups()
            cur.execute("SELECT cid FROM categories WHERE title=?", (cat_title,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Category not found.")
                return ConversationHandler.END
            cid = int(row[0])
            cur.execute(
                "INSERT INTO products(cid,title,price,product_type,need_player_id,active) VALUES(?,?,?,?,?,1)",
                (cid, prod_title, float(price_s), ptype, int(need_p)),
            )
            con.commit()
            await update.message.reply_text("✅ Product added.")
            return ConversationHandler.END

        if mode == "addcodes":
            # pid | code1\ncode2...
            if "|" not in text:
                await update.message.reply_text("❌ Missing '|'.")
                return ConversationHandler.END
            pid_s, codes_blob = [x.strip() for x in text.split("|", 1)]
            pid = int(pid_s)
            codes = [c.strip() for c in codes_blob.splitlines() if c.strip()]
            if not codes:
                await update.message.reply_text("❌ No codes.")
                return ConversationHandler.END
            for ctext in codes:
                cur.execute("INSERT INTO codes(pid,code_text,used) VALUES(?,?,0)", (pid, ctext))
            con.commit()
            await update.message.reply_text(f"✅ Added {len(codes)} codes to pid {pid}.")
            return ConversationHandler.END

        if mode == "setprice":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: pid | price")
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
            # reuse command logic quickly
            context.args = [str(oid)]
            return await cancelorder_cmd(update, context)

        if mode == "deliver":
            if "|" not in text:
                await update.message.reply_text("❌ Format: order_id | message")
                return ConversationHandler.END
            oid_s, msg = [x.strip() for x in text.split("|", 1)]
            context.args = [oid_s] + msg.split()
            return await deliver_cmd(update, context)

        if mode == "approvedep":
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("❌ Format: deposit_id | amount")
                return ConversationHandler.END
            dep_id, amount = int(m.group(1)), float(m.group(2))
            context.args = [str(dep_id), str(amount)]
            return await approvedep_cmd(update, context)

        await update.message.reply_text("✅ Done.")
        return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END

# =========================
# Entry callbacks for menu inline
# =========================
async def entry_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler exists only to route callback states via ConversationHandler.
    return await on_callback(update, context)

# =========================
# Main
# =========================
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    # Conversation: topup ref + player_id + admin input
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(entry_callback_router),
        ],
        states={
            ST_TOPUP_REF: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_ref_input)],
            ST_PLAYER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, player_id_input)],
            ST_ADMIN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_input)],
        },
        fallbacks=[CommandHandler("start", start_cmd)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("deliver", deliver_cmd))
    app.add_handler(CommandHandler("cancelorder", cancelorder_cmd))
    app.add_handler(CommandHandler("approvedep", approvedep_cmd))
    app.add_handler(CommandHandler("rejectdep", rejectdep_cmd))

    # Reply menu router
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    # Conversation handler for callbacks + staged inputs
    app.add_handler(conv)

    return app

def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

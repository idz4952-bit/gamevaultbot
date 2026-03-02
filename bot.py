# bot.py - SINGLE FILE (Instant + Manual) - PTB 21.x
import os
import re
import json
import html
import io
import sqlite3
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any

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
    ConversationHandler,
    ContextTypes,
    filters,
)

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("gamevault_onefile")

# =========================
# ENV
# =========================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "shop.db")
MANUAL_STOCK_FILE = os.getenv("MANUAL_STOCK_FILE", "manual_stock.json")
CURRENCY = os.getenv("CURRENCY", "$")

if not TOKEN:
    raise RuntimeError("TOKEN env var is missing")
if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID env var is missing or 0")

# =========================
# SAFE HTML SEND
# =========================
def H(s: str) -> str:
    return html.escape(s or "")

async def safe_edit(q, text: str, reply_markup=None):
    # ALWAYS HTML to avoid markdown parsing errors
    await q.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

async def safe_send(chat_id: int, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

# =========================
# ROLES
# =========================
ROLE_OWNER = "OWNER"
ROLE_HELPER = "HELPER"

def money(x: float) -> str:
    return f"{x:.3f} {CURRENCY}"

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
# MANUAL STOCK FILE (JSON)
# =========================
def load_manual_stock() -> Dict[str, int]:
    try:
        if not os.path.exists(MANUAL_STOCK_FILE):
            return {}
        with open(MANUAL_STOCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = {}
        for k, v in (data or {}).items():
            try:
                out[str(k)] = int(v)
            except Exception:
                out[str(k)] = 0
        return out
    except Exception:
        return {}

def save_manual_stock(d: Dict[str, int]) -> None:
    try:
        with open(MANUAL_STOCK_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception("save_manual_stock failed: %s", e)

def manual_stock_get(plan_id: int) -> int:
    d = load_manual_stock()
    return int(d.get(str(plan_id), 0))

def manual_stock_set(plan_id: int, value: int) -> None:
    d = load_manual_stock()
    d[str(plan_id)] = max(0, int(value))
    save_manual_stock(d)

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

-- INSTANT (codes)
CREATE TABLE IF NOT EXISTS categories(
  cid INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS products(
  pid INTEGER PRIMARY KEY AUTOINCREMENT,
  cid INTEGER NOT NULL,
  title TEXT NOT NULL,
  price REAL NOT NULL,
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_codes_unique ON codes(pid, code_text);
CREATE INDEX IF NOT EXISTS idx_codes_pid_used ON codes(pid, used);

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

CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_ref_unique ON orders(client_ref);
CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at);

-- MANUAL
CREATE TABLE IF NOT EXISTS manual_services(
  sid INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL UNIQUE,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS manual_plans(
  plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sid INTEGER NOT NULL,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(sid) REFERENCES manual_services(sid)
);

CREATE TABLE IF NOT EXISTS manual_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  plan_id INTEGER NOT NULL,
  service_title TEXT NOT NULL,
  plan_title TEXT NOT NULL,
  price REAL NOT NULL,
  player_id TEXT,
  email TEXT,
  password TEXT,
  note TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING',
  delivered_text TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
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

def is_owner(uid: int) -> bool:
    return admin_role(uid) == ROLE_OWNER

def is_admin_any(uid: int) -> bool:
    return admin_role(uid) in (ROLE_OWNER, ROLE_HELPER)

def ensure_user(u):
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
    cur.execute("SELECT suspended FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return bool(int(r[0])) if r else False

def get_balance(uid: int) -> float:
    cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return float(r[0] or 0.0) if r else 0.0

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

def product_stock(pid: int) -> int:
    cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
    return int(cur.fetchone()[0] or 0)

# =========================
# DEFAULT DATA
# =========================
DEFAULT_CATEGORIES = ["🪂 PUBG MOBILE UC"]
DEFAULT_PRODUCTS = [
    ("🪂 PUBG MOBILE UC", "60 UC", 0.875),
    ("🪂 PUBG MOBILE UC", "325 UC", 4.375),
    ("🪂 PUBG MOBILE UC", "660 UC", 8.750),
    ("🪂 PUBG MOBILE UC", "1800 UC", 22.000),
    ("🪂 PUBG MOBILE UC", "3850 UC", 44.000),
    ("🪂 PUBG MOBILE UC", "8100 UC", 88.000),
]

DEFAULT_MANUAL = [
    # service, plans(title, price)
    ("📺 Shahid", [("VIP 1 Month", 6.0), ("VIP 3 Months", 15.0)]),
    ("🔥 Free Fire", [("100 Diamonds", 1.2), ("500 Diamonds", 5.5)]),
]

def seed_defaults():
    for c in DEFAULT_CATEGORIES:
        cur.execute("INSERT OR IGNORE INTO categories(title) VALUES(?)", (c,))
    con.commit()

    for cat, title, price in DEFAULT_PRODUCTS:
        cur.execute("SELECT cid FROM categories WHERE title=?", (cat,))
        r = cur.fetchone()
        if not r:
            continue
        cid = int(r[0])
        cur.execute("SELECT pid FROM products WHERE cid=? AND title=?", (cid, title))
        if cur.fetchone():
            continue
        cur.execute("INSERT INTO products(cid,title,price,active) VALUES(?,?,?,1)", (cid, title, float(price)))
    con.commit()

    for svc, plans in DEFAULT_MANUAL:
        cur.execute("INSERT OR IGNORE INTO manual_services(title, active) VALUES(?,1)", (svc,))
        con.commit()
        cur.execute("SELECT sid FROM manual_services WHERE title=?", (svc,))
        sid = int(cur.fetchone()[0])
        for ptitle, pprice in plans:
            cur.execute("SELECT plan_id FROM manual_plans WHERE sid=? AND title=?", (sid, ptitle))
            if cur.fetchone():
                continue
            cur.execute("INSERT INTO manual_plans(sid,title,price,active) VALUES(?,?,?,1)", (sid, ptitle, float(pprice)))
    con.commit()

seed_defaults()

# =========================
# UI (NO EXTERNAL URL BUTTONS)
# =========================
REPLY_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🛒 Products"), KeyboardButton("💰 Balance")],
        [KeyboardButton("📦 Orders"), KeyboardButton("👑 Admin")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

MENU_BUTTONS = {"🛒 Products", "💰 Balance", "📦 Orders", "👑 Admin"}

def kb_products_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚡ منتجات فورية", callback_data="root:instant")],
            [InlineKeyboardButton("🛠 منتجات يدوية", callback_data="root:manual")],
        ]
    )

def kb_back_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="root:back")]])

def kb_categories() -> InlineKeyboardMarkup:
    cur.execute(
        """
        SELECT c.cid, c.title
        FROM categories c
        ORDER BY c.title
        """
    )
    rows = []
    for cid, title in cur.fetchall():
        rows.append([InlineKeyboardButton(f"{title}", callback_data=f"cat:{cid}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="root:back")])
    return InlineKeyboardMarkup(rows)

def kb_products(cid: int) -> InlineKeyboardMarkup:
    cur.execute("SELECT pid,title,price FROM products WHERE cid=? AND active=1", (cid,))
    items = cur.fetchall()
    items.sort(key=lambda r: extract_sort_value(r[1]))
    rows = []
    for pid, title, price in items:
        stock = product_stock(pid)
        rows.append([InlineKeyboardButton(f"{title} | {money(float(price))} | 📦{stock}"[:64], callback_data=f"view:{pid}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="instant:cats")])
    return InlineKeyboardMarkup(rows)

def kb_view(pid: int, cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 شراء", callback_data=f"buy:{pid}")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data=f"instant:prods:{cid}")],
        ]
    )

def kb_confirm(pid: int, client_ref: str, cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تأكيد", callback_data=f"confirm:{pid}:{client_ref}")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data=f"instant:prods:{cid}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="root:back")],
        ]
    )

def kb_manual_services() -> InlineKeyboardMarkup:
    cur.execute("SELECT sid,title FROM manual_services WHERE active=1 ORDER BY title")
    rows = []
    for sid, title in cur.fetchall():
        rows.append([InlineKeyboardButton(f"{title}", callback_data=f"msvc:{sid}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="root:back")])
    return InlineKeyboardMarkup(rows)

def kb_manual_plans(sid: int) -> InlineKeyboardMarkup:
    cur.execute("SELECT plan_id,title,price FROM manual_plans WHERE sid=? AND active=1 ORDER BY title", (sid,))
    rows = []
    for plan_id, title, price in cur.fetchall():
        st = manual_stock_get(int(plan_id))
        rows.append([InlineKeyboardButton(f"{title} | {money(float(price))} | 📦{st}"[:64], callback_data=f"mplan:{plan_id}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="manual:services")])
    return InlineKeyboardMarkup(rows)

def kb_manual_confirm(plan_id: int, client_ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تأكيد الطلب", callback_data=f"mconfirm:{plan_id}:{client_ref}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="root:back")],
        ]
    )

def kb_admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 عرض المنتجات", callback_data="admin:list")],
            [InlineKeyboardButton("➕ إضافة أكواد", callback_data="admin:addcodes"),
             InlineKeyboardButton("💲 تغيير سعر", callback_data="admin:setprice")],
            [InlineKeyboardButton("⛔ تعطيل/تفعيل منتج", callback_data="admin:toggle")],
            [InlineKeyboardButton("🛠 يدوي: سعر", callback_data="admin:mprice"),
             InlineKeyboardButton("🛠 يدوي: ستوك (ملف)", callback_data="admin:mstock")],
            [InlineKeyboardButton("💰 إضافة رصيد", callback_data="admin:addbal")],
        ]
    )

# =========================
# STATES
# =========================
ST_QTY = 10
ST_ADMIN_INPUT = 20
ST_MANUAL_FORM = 30

UD_PID = "pid"
UD_CID = "cid"
UD_QTY_MAX = "qty_max"
UD_LAST_QTY = "last_qty"
UD_LAST_PID = "last_pid"
UD_ORDER_REF = "order_ref"

UD_ADMIN_MODE = "admin_mode"

UD_MPLAN = "mplan"
UD_MREF = "mref"
UD_MFORM_STAGE = "mform_stage"
UD_MFORM_DATA = "mform_data"  # dict

# =========================
# DELIVERY (Instant)
# =========================
MAX_CODES_IN_MESSAGE = 200
TELEGRAM_TEXT_LIMIT = 3800

async def send_codes_delivery(chat_id: int, context: ContextTypes.DEFAULT_TYPE, order_id: int, codes: List[str]):
    codes = [c.strip() for c in codes if c and c.strip()]
    count = len(codes)
    header = f"🎁 <b>تم التسليم بنجاح!</b>\n🧾 رقم الطلب <b>#{order_id}</b>\n📦 عدد الأكواد: <b>{count}</b>\n\n"
    if count == 0:
        await safe_send(chat_id, context, f"✅ Order #{order_id} COMPLETED\n(No codes)")
        return

    if count > MAX_CODES_IN_MESSAGE:
        content = "\n".join(codes)
        bio = io.BytesIO(content.encode("utf-8"))
        bio.name = f"order_{order_id}_codes.txt"
        await safe_send(chat_id, context, header + "📎 تم إرسال الأكواد في ملف:")
        await context.bot.send_document(chat_id=chat_id, document=bio)
        return

    body = "\n".join(codes)
    # HTML safe
    text = header + f"<pre>{H(body)}</pre>"
    if len(text) <= TELEGRAM_TEXT_LIMIT:
        await safe_send(chat_id, context, text)
        return

    await safe_send(chat_id, context, header + "🎁 الأكواد (جزء 1):")
    chunk = ""
    for c in codes:
        line = c + "\n"
        if len(chunk) + len(line) > 3500:
            await safe_send(chat_id, context, f"<pre>{H(chunk.rstrip())}</pre>")
            chunk = line
        else:
            chunk += line
    if chunk.strip():
        await safe_send(chat_id, context, f"<pre>{H(chunk.rstrip())}</pre>")

# =========================
# PAGES
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text("✅ البوت شغال", reply_markup=REPLY_MENU)

async def show_root_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_suspended(update.effective_user.id) and not is_admin_any(update.effective_user.id):
        return await update.message.reply_text("⛔ حسابك موقوف.", reply_markup=REPLY_MENU)
    await update.message.reply_text("اختر نوع المنتجات:", reply_markup=None)
    await update.message.reply_text("👇", reply_markup=None)
    await update.message.reply_text("🔽", reply_markup=None)
    await update.message.reply_text("🛒 Products:", reply_markup=None)
    await update.message.reply_text("اختر:", reply_markup=None)
    await update.message.reply_text(" ", reply_markup=None)
    await update.message.reply_text(" ", reply_markup=None)
    await update.message.reply_text(" ", reply_markup=None)
    await update.message.reply_text(" ", reply_markup=None)
    await update.message.reply_text(" ", reply_markup=None)
    await update.message.reply_text(" ", reply_markup=None)
    await update.message.reply_text("🧩", reply_markup=None)
    await update.message.reply_text("اختر:", reply_markup=kb_products_root())

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bal = get_balance(uid)
    await update.message.reply_text(
        f"💰 Balance: {bal:.3f} {CURRENCY}",
        reply_markup=REPLY_MENU
    )

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cur.execute("SELECT id,product_title,qty,total,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,))
    rows = cur.fetchall()
    if not rows:
        return await update.message.reply_text("📦 لا توجد طلبات فورية.", reply_markup=REPLY_MENU)
    lines = ["📦 <b>آخر 10 طلبات فورية</b>\n"]
    for oid, title, qty, total, status, created_at in rows:
        lines.append(f"🧾 <b>#{oid}</b> | {H(title)} | x{qty} | {money(float(total))} | {H(status)} | {H(created_at)}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=REPLY_MENU)

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("❌ غير مسموح", reply_markup=REPLY_MENU)
    await update.message.reply_text("👑 Admin Panel", reply_markup=kb_admin_panel())

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    t = (update.message.text or "").strip()
    uid = update.effective_user.id

    if is_suspended(uid) and not is_admin_any(uid):
        return await update.message.reply_text("⛔ حسابك موقوف.", reply_markup=REPLY_MENU)

    if t == "🛒 Products":
        return await show_root_products(update, context)
    if t == "💰 Balance":
        return await show_balance(update, context)
    if t == "📦 Orders":
        return await show_orders(update, context)
    if t == "👑 Admin":
        return await admin_cmd(update, context)

    await update.message.reply_text("استخدم الأزرار 👇", reply_markup=REPLY_MENU)

# =========================
# MANUAL FORM FLOW
# =========================
MANUAL_FIELDS = [
    ("player_id", "🆔 أرسل Player ID (أو اكتب - لتخطي):"),
    ("email", "📧 أرسل Email (أو - لتخطي):"),
    ("password", "🔑 أرسل Password (أو - لتخطي):"),
    ("note", "📝 ملاحظة (اختياري) (أو - لتخطي):"),
]

async def manual_form_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # allow cancel
    if text.lower() in ("/cancel", "cancel"):
        context.user_data.pop(UD_MFORM_STAGE, None)
        context.user_data.pop(UD_MFORM_DATA, None)
        context.user_data.pop(UD_MPLAN, None)
        context.user_data.pop(UD_MREF, None)
        await update.message.reply_text("✅ تم الإلغاء", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    stage = int(context.user_data.get(UD_MFORM_STAGE, 0))
    data = context.user_data.get(UD_MFORM_DATA) or {}
    if stage < 0 or stage >= len(MANUAL_FIELDS):
        await update.message.reply_text("❌ Session expired. افتح يدوي من جديد.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    key, _prompt = MANUAL_FIELDS[stage]
    if text == "-":
        data[key] = ""
    else:
        data[key] = text[:500]

    stage += 1
    context.user_data[UD_MFORM_STAGE] = stage
    context.user_data[UD_MFORM_DATA] = data

    if stage >= len(MANUAL_FIELDS):
        # show confirm
        plan_id = int(context.user_data.get(UD_MPLAN, 0))
        if not plan_id:
            await update.message.reply_text("❌ Session expired.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        cur.execute(
            """
            SELECT s.title, p.title, p.price
            FROM manual_plans p JOIN manual_services s ON s.sid=p.sid
            WHERE p.plan_id=? AND p.active=1
            """,
            (plan_id,),
        )
        row = cur.fetchone()
        if not row:
            await update.message.reply_text("❌ Plan not found.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        svc_title, plan_title, price = row
        st = manual_stock_get(plan_id)
        if st <= 0 and not is_admin_any(update.effective_user.id):
            await update.message.reply_text("❌ هذا المنتج اليدوي غير متوفر حاليا (Stock=0).", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        client_ref = secrets.token_hex(10)
        context.user_data[UD_MREF] = client_ref

        summary = (
            f"🛠 <b>تأكيد طلب يدوي</b>\n\n"
            f"الخدمة: <b>{H(svc_title)}</b>\n"
            f"الباقة: <b>{H(plan_title)}</b>\n"
            f"السعر: <b>{H(money(float(price)))}</b>\n"
            f"📦 Stock: <b>{st}</b>\n\n"
            f"PlayerID: <code>{H(data.get('player_id',''))}</code>\n"
            f"Email: <code>{H(data.get('email',''))}</code>\n"
            f"Password: <code>{H(data.get('password',''))}</code>\n"
            f"Note: <code>{H(data.get('note',''))}</code>\n\n"
            "اضغط ✅ للتأكيد"
        )
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML, reply_markup=kb_manual_confirm(plan_id, client_ref))
        return ConversationHandler.END

    # ask next field
    _, prompt = MANUAL_FIELDS[stage]
    await update.message.reply_text(prompt + "\n/cancel للإلغاء", reply_markup=REPLY_MENU)
    return ST_MANUAL_FORM

# =========================
# ADMIN INPUT
# =========================
async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ غير مسموح", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    mode = context.user_data.get(UD_ADMIN_MODE)
    text = (update.message.text or "").strip()

    if text.lower() in ("/cancel", "cancel"):
        context.user_data.pop(UD_ADMIN_MODE, None)
        await update.message.reply_text("✅ تم الإلغاء", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    try:
        if mode == "addcodes":
            # pid | CODE1\nCODE2
            if "|" not in text:
                await update.message.reply_text("صيغة:\nPID | CODE1\\nCODE2\n/cancel", reply_markup=REPLY_MENU)
                return ST_ADMIN_INPUT
            pid_s, codes_blob = [x.strip() for x in text.split("|", 1)]
            if not pid_s.isdigit():
                await update.message.reply_text("PID لازم رقم", reply_markup=REPLY_MENU)
                return ST_ADMIN_INPUT
            pid = int(pid_s)
            codes = [c.strip().replace(" ", "") for c in codes_blob.splitlines() if c.strip()]
            added = 0
            skipped = 0
            for ctext in codes:
                try:
                    cur.execute("INSERT INTO codes(pid,code_text,used) VALUES(?,?,0)", (pid, ctext))
                    added += 1
                except sqlite3.IntegrityError:
                    skipped += 1
            con.commit()
            await update.message.reply_text(f"✅ Added {added} codes. Skipped {skipped}.", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        if mode == "setprice":
            # pid | price
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("صيغة: PID | PRICE\nمثال: 12 | 9.5", reply_markup=REPLY_MENU)
                return ST_ADMIN_INPUT
            pid, price = int(m.group(1)), float(m.group(2))
            cur.execute("UPDATE products SET price=? WHERE pid=?", (price, pid))
            con.commit()
            await update.message.reply_text("✅ تم تغيير السعر", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        if mode == "toggle":
            # pid
            if not text.isdigit():
                await update.message.reply_text("أرسل PID فقط", reply_markup=REPLY_MENU)
                return ST_ADMIN_INPUT
            pid = int(text)
            cur.execute("SELECT active FROM products WHERE pid=?", (pid,))
            r = cur.fetchone()
            if not r:
                await update.message.reply_text("❌ غير موجود", reply_markup=REPLY_MENU)
                return ConversationHandler.END
            newv = 0 if int(r[0]) == 1 else 1
            cur.execute("UPDATE products SET active=? WHERE pid=?", (newv, pid))
            con.commit()
            await update.message.reply_text(f"✅ {'تم تفعيل' if newv else 'تم تعطيل'} المنتج", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        if mode == "addbal":
            # user_id | amount
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("صيغة: USER_ID | AMOUNT\nمثال: 8335... | 5", reply_markup=REPLY_MENU)
                return ST_ADMIN_INPUT
            user_id, amount = int(m.group(1)), float(m.group(2))
            add_balance(user_id, amount)
            await update.message.reply_text("✅ تم إضافة الرصيد", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        if mode == "mprice":
            # plan_id | price
            m = re.match(r"^(\d+)\s*\|\s*([\d.]+)$", text)
            if not m:
                await update.message.reply_text("صيغة: PLAN_ID | PRICE", reply_markup=REPLY_MENU)
                return ST_ADMIN_INPUT
            plan_id, price = int(m.group(1)), float(m.group(2))
            cur.execute("UPDATE manual_plans SET price=? WHERE plan_id=?", (price, plan_id))
            con.commit()
            await update.message.reply_text("✅ تم تغيير سعر اليدوي", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        if mode == "mstock":
            # plan_id | stock  (stored in JSON file)
            m = re.match(r"^(\d+)\s*\|\s*(\d+)$", text)
            if not m:
                await update.message.reply_text("صيغة: PLAN_ID | STOCK\nمثال: 3 | 50", reply_markup=REPLY_MENU)
                return ST_ADMIN_INPUT
            plan_id, stock = int(m.group(1)), int(m.group(2))
            manual_stock_set(plan_id, stock)
            await update.message.reply_text("✅ تم تغيير ستوك اليدوي (ملف)", reply_markup=REPLY_MENU)
            return ConversationHandler.END

        await update.message.reply_text("❌ أمر غير معروف", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    except Exception as e:
        logger.exception("admin_input error: %s", e)
        await update.message.reply_text(f"❌ Error: {e}", reply_markup=REPLY_MENU)
        return ConversationHandler.END

# =========================
# CALLBACKS
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    uid = update.effective_user.id

    if is_suspended(uid) and not is_admin_any(uid):
        return await safe_edit(q, "⛔ حسابك موقوف.")

    # ROOT
    if data == "root:back":
        return await safe_edit(q, "رجعت للقائمة ✅", reply_markup=None)

    if data == "root:instant":
        return await safe_edit(q, "⚡ اختر قسم المنتجات الفورية:", reply_markup=kb_categories())

    if data == "root:manual":
        return await safe_edit(q, "🛠 اختر خدمة المنتجات اليدوية:", reply_markup=kb_manual_services())

    # INSTANT NAV
    if data == "instant:cats":
        return await safe_edit(q, "⚡ اختر قسم المنتجات الفورية:", reply_markup=kb_categories())

    if data.startswith("cat:"):
        cid = int(data.split(":", 1)[1])
        context.user_data[UD_CID] = cid
        return await safe_edit(q, "🛒 اختر منتج:", reply_markup=kb_products(cid))

    if data.startswith("instant:prods:"):
        cid = int(data.split(":", 2)[2])
        context.user_data[UD_CID] = cid
        return await safe_edit(q, "🛒 اختر منتج:", reply_markup=kb_products(cid))

    if data.startswith("view:"):
        pid = int(data.split(":", 1)[1])
        cur.execute("SELECT title, price, cid FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await safe_edit(q, "❌ المنتج غير موجود.")
        title, price, cid = row
        stock = product_stock(pid)
        text = (
            f"🎁 <b>{H(title)}</b>\n\n"
            f"🆔 ID: <code>{pid}</code>\n"
            f"💵 Price: <b>{H(money(float(price)))}</b>\n"
            f"📦 Stock: <b>{stock}</b>"
        )
        return await safe_edit(q, text, reply_markup=kb_view(pid, cid))

    if data.startswith("buy:"):
        pid = int(data.split(":", 1)[1])
        cur.execute("SELECT title, cid FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await safe_edit(q, "❌ المنتج غير موجود.")
        title, cid = row
        stock = product_stock(pid)
        if stock <= 0:
            return await safe_edit(q, "❌ Out of stock.", reply_markup=kb_products(cid))

        context.user_data[UD_PID] = pid
        context.user_data[UD_CID] = cid
        context.user_data[UD_QTY_MAX] = stock

        return await safe_edit(
            q,
            f"🛒 المنتج: <b>{H(title)}</b>\nأرسل الكمية (1 → {stock})\n\n/cancel للإلغاء",
            reply_markup=None
        )

    if data.startswith("confirm:"):
        parts = data.split(":")
        pid = int(parts[1])
        ref = parts[2]
        qty = int(context.user_data.get(UD_LAST_QTY, 0))
        cid = int(context.user_data.get(UD_CID, 0))
        if qty <= 0:
            return await safe_edit(q, "❌ انتهت الجلسة. أعد المحاولة.", reply_markup=kb_products(cid))

        # idempotent
        cur.execute("SELECT id, delivered_text, status FROM orders WHERE client_ref=?", (ref,))
        already = cur.fetchone()
        if already:
            oid, delivered_text, status = int(already[0]), already[1] or "", already[2]
            await safe_edit(q, f"✅ تم تنفيذ الطلب سابقاً. Order #{oid} ({H(status)})\nإعادة إرسال التسليم...")
            if delivered_text.strip():
                await send_codes_delivery(uid, context, oid, delivered_text.splitlines())
            return ConversationHandler.END

        cur.execute("SELECT title, price FROM products WHERE pid=? AND active=1", (pid,))
        row = cur.fetchone()
        if not row:
            return await safe_edit(q, "❌ المنتج غير موجود.")
        title, price = row
        total = float(price) * qty

        if not charge_balance(uid, total):
            bal = get_balance(uid)
            missing = total - bal
            return await safe_edit(
                q,
                f"❌ رصيد غير كافي.\nرصيدك: <b>{bal:.3f} {CURRENCY}</b>\nالمطلوب: <b>{total:.3f} {CURRENCY}</b>\nالناقص: <b>{missing:.3f} {CURRENCY}</b>"
            )

        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT code_id, code_text FROM codes WHERE pid=? AND used=0 ORDER BY code_id ASC LIMIT ?", (pid, qty))
            picked = cur.fetchall()
            if len(picked) < qty:
                cur.execute("ROLLBACK")
                add_balance(uid, total)
                return await safe_edit(q, "❌ نقص في الستوك. تم إرجاع المبلغ.")

            cur.execute(
                "INSERT INTO orders(user_id,pid,product_title,qty,total,status,client_ref) VALUES(?,?,?,?,?,'PENDING',?)",
                (uid, pid, title, qty, total, ref),
            )
            oid = int(cur.lastrowid)

            for code_id, _ in picked:
                cur.execute(
                    "UPDATE codes SET used=1, used_at=datetime('now'), order_id=? WHERE code_id=? AND used=0",
                    (oid, int(code_id)),
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
            logger.exception("instant purchase failed: %s", e)
            return await safe_edit(q, "❌ خطأ أثناء التنفيذ. تم إرجاع المبلغ.")

        await safe_edit(
            q,
            f"✅ <b>تم الشراء</b>\n"
            f"🧾 Order: <b>#{oid}</b>\n"
            f"🎁 Product: {H(title)}\n"
            f"🔢 Qty: <b>{qty}</b>\n"
            f"💵 Total: <b>{H(money(total))}</b>\n\n"
            f"🚚 جاري إرسال الأكواد..."
        )
        await send_codes_delivery(uid, context, oid, codes_list)

        # notify admin (safe)
        try:
            await safe_send(
                ADMIN_ID, context,
                f"✅ <b>NEW INSTANT ORDER</b>\nOrder <b>#{oid}</b>\nUser: <code>{uid}</code>\n{H(title)} x{qty}\nTotal: <b>{H(money(total))}</b>"
            )
        except Exception:
            pass

        return ConversationHandler.END

    # MANUAL NAV
    if data == "manual:services":
        return await safe_edit(q, "🛠 اختر خدمة المنتجات اليدوية:", reply_markup=kb_manual_services())

    if data.startswith("msvc:"):
        sid = int(data.split(":", 1)[1])
        return await safe_edit(q, "🛠 اختر باقة:", reply_markup=kb_manual_plans(sid))

    if data.startswith("mplan:"):
        plan_id = int(data.split(":", 1)[1])

        cur.execute(
            """
            SELECT s.title, p.title, p.price
            FROM manual_plans p JOIN manual_services s ON s.sid=p.sid
            WHERE p.plan_id=? AND p.active=1
            """,
            (plan_id,),
        )
        row = cur.fetchone()
        if not row:
            return await safe_edit(q, "❌ الباقة غير موجودة.")

        st = manual_stock_get(plan_id)
        if st <= 0 and not is_admin_any(uid):
            return await safe_edit(q, "❌ هذه الباقة اليدوية غير متوفرة حاليا (Stock=0).")

        # start form
        context.user_data[UD_MPLAN] = plan_id
        context.user_data[UD_MFORM_STAGE] = 0
        context.user_data[UD_MFORM_DATA] = {}

        key, prompt = MANUAL_FIELDS[0]
        await safe_edit(q, "✅ تمام. أكمل البيانات في الرسائل.\n" + H(prompt) + "\n/cancel للإلغاء", reply_markup=None)
        return ST_MANUAL_FORM

    if data.startswith("mconfirm:"):
        # create manual order + notify admin
        parts = data.split(":")
        plan_id = int(parts[1])
        ref = parts[2]

        data_form = context.user_data.get(UD_MFORM_DATA) or {}
        cur.execute(
            """
            SELECT s.title, p.title, p.price
            FROM manual_plans p JOIN manual_services s ON s.sid=p.sid
            WHERE p.plan_id=? AND p.active=1
            """,
            (plan_id,),
        )
        row = cur.fetchone()
        if not row:
            return await safe_edit(q, "❌ الباقة غير موجودة.")

        svc_title, plan_title, price = row
        price = float(price)

        # optional: deduct balance now (you can change to admin-deduct)
        if not charge_balance(uid, price):
            bal = get_balance(uid)
            return await safe_edit(q, f"❌ رصيد غير كافي للطلب اليدوي.\nرصيدك: <b>{bal:.3f} {CURRENCY}</b>\nالمطلوب: <b>{price:.3f} {CURRENCY}</b>")

        # decrement manual stock in file
        if not is_admin_any(uid):
            st = manual_stock_get(plan_id)
            if st <= 0:
                add_balance(uid, price)
                return await safe_edit(q, "❌ نفد الستوك. تم إرجاع المبلغ.")
            manual_stock_set(plan_id, st - 1)

        cur.execute(
            """
            INSERT INTO manual_orders(user_id, plan_id, service_title, plan_title, price, player_id, email, password, note, status)
            VALUES(?,?,?,?,?,?,?,?,?,'PENDING')
            """,
            (
                uid, plan_id, svc_title, plan_title, price,
                data_form.get("player_id",""),
                data_form.get("email",""),
                data_form.get("password",""),
                data_form.get("note",""),
            ),
        )
        con.commit()
        mid = int(cur.lastrowid)

        await safe_edit(q, f"✅ <b>تم إنشاء طلب يدوي</b>\n🧾 رقم الطلب: <b>#{mid}</b>\n⏳ الحالة: <b>PENDING</b>\n\nسيتم التنفيذ من الإدارة.")
        try:
            await safe_send(
                ADMIN_ID, context,
                "🛠 <b>NEW MANUAL ORDER</b>\n"
                f"🧾 <b>#{mid}</b>\n"
                f"User: <code>{uid}</code>\n"
                f"Service: <b>{H(svc_title)}</b>\n"
                f"Plan: <b>{H(plan_title)}</b>\n"
                f"Price: <b>{H(money(price))}</b>\n\n"
                f"PlayerID: <code>{H(data_form.get('player_id',''))}</code>\n"
                f"Email: <code>{H(data_form.get('email',''))}</code>\n"
                f"Pass: <code>{H(data_form.get('password',''))}</code>\n"
                f"Note: <code>{H(data_form.get('note',''))}</code>\n\n"
                f"✅ للتسليم: /deliver_manual {mid} نص_التسليم"
            )
        except Exception:
            pass

        # clear session
        context.user_data.pop(UD_MFORM_STAGE, None)
        context.user_data.pop(UD_MFORM_DATA, None)
        context.user_data.pop(UD_MPLAN, None)
        context.user_data.pop(UD_MREF, None)

        return ConversationHandler.END

    # ADMIN CALLBACKS
    if data.startswith("admin:"):
        if not is_owner(uid):
            return await safe_edit(q, "❌ غير مسموح")

        mode = data.split(":", 1)[1]
        context.user_data[UD_ADMIN_MODE] = mode

        if mode == "list":
            cur.execute(
                """
                SELECT p.pid, c.title, p.title, p.price, p.active
                FROM products p JOIN categories c ON c.cid=p.cid
                ORDER BY c.title, p.title
                """
            )
            rows = cur.fetchall()
            if not rows:
                return await safe_edit(q, "لا توجد منتجات")
            lines = ["<b>Products</b>\n"]
            for pid, cat, title, price, active in rows:
                lines.append(f"PID <code>{pid}</code> | {H(cat)} | {H(title)} | {H(money(float(price)))} | {'ON' if active else 'OFF'}")
            text = "\n".join(lines)
            return await safe_edit(q, text[:3800])

        prompts = {
            "addcodes": "أرسل:\nPID | CODE1\\nCODE2\n/cancel",
            "setprice": "أرسل:\nPID | PRICE\n/cancel",
            "toggle": "أرسل:\nPID\n/cancel",
            "addbal": "أرسل:\nUSER_ID | AMOUNT\n/cancel",
            "mprice": "أرسل:\nPLAN_ID | PRICE\n/cancel",
            "mstock": "أرسل:\nPLAN_ID | STOCK\n(يُحفظ في ملف JSON)\n/cancel",
        }
        return await safe_edit(q, H(prompts.get(mode, "أرسل البيانات...")))

    return ConversationHandler.END

# =========================
# QTY INPUT (Instant)
# =========================
async def qty_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    if txt.lower() in ("/cancel", "cancel"):
        context.user_data.clear()
        await update.message.reply_text("✅ تم الإلغاء", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    # if user pressed menu while in qty
    if txt in MENU_BUTTONS:
        context.user_data.clear()
        return await menu_router(update, context)

    try:
        qty = int(txt)
    except ValueError:
        return await update.message.reply_text("❌ أرسل رقم فقط")

    pid = int(context.user_data.get(UD_PID, 0))
    cid = int(context.user_data.get(UD_CID, 0))
    stock = int(context.user_data.get(UD_QTY_MAX, 0))

    if not pid or stock <= 0:
        await update.message.reply_text("❌ انتهت الجلسة. افتح المنتج من جديد.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    if qty < 1 or qty > stock:
        return await update.message.reply_text(f"❌ الكمية من 1 إلى {stock}")

    cur.execute("SELECT title, price FROM products WHERE pid=? AND active=1", (pid,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ المنتج غير موجود.", reply_markup=REPLY_MENU)
        return ConversationHandler.END

    title, price = row
    total = float(price) * qty

    ref = secrets.token_hex(10)
    context.user_data[UD_LAST_QTY] = qty
    context.user_data[UD_ORDER_REF] = ref

    await update.message.reply_text(
        f"🧾 <b>تأكيد الطلب</b>\n\n"
        f"Product: <b>{H(title)}</b>\n"
        f"Qty: <b>{qty}</b>\n"
        f"Total: <b>{H(money(total))}</b>\n\n"
        "اضغط ✅ للتأكيد",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_confirm(pid, ref, cid),
    )
    return ConversationHandler.END

# =========================
# ADMIN COMMAND: deliver manual
# =========================
async def deliver_manual_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /deliver_manual <order_id> <delivery_text>")

    mid = int(context.args[0])
    delivery_text = " ".join(context.args[1:]).strip()

    cur.execute("SELECT user_id,status FROM manual_orders WHERE id=?", (mid,))
    row = cur.fetchone()
    if not row:
        return await update.message.reply_text("❌ Manual order not found.")
    user_id, status = int(row[0]), row[1]
    if status == "COMPLETED":
        await update.message.reply_text("✅ Already completed. Sending again...")
    cur.execute("UPDATE manual_orders SET status='COMPLETED', delivered_text=? WHERE id=?", (delivery_text[:2000], mid))
    con.commit()

    await update.message.reply_text("✅ Marked completed.")

    try:
        await safe_send(
            user_id, context,
            f"✅ <b>تم تسليم طلبك اليدوي</b>\n🧾 رقم الطلب: <b>#{mid}</b>\n\n"
            f"<pre>{H(delivery_text)}</pre>"
        )
    except Exception as e:
        logger.exception("notify manual delivery failed: %s", e)

# =========================
# BUILD APP
# =========================
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    CB_PATTERN = r"^(root:|cat:|view:|buy:|confirm:|instant:|msvc:|mplan:|mconfirm:|manual:|admin:)"

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_callback, pattern=CB_PATTERN)],
        states={
            ST_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, qty_input),
                CallbackQueryHandler(on_callback, pattern=CB_PATTERN),
            ],
            ST_ADMIN_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_input),
                CallbackQueryHandler(on_callback, pattern=CB_PATTERN),
            ],
            ST_MANUAL_FORM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_form_input),
                CallbackQueryHandler(on_callback, pattern=CB_PATTERN),
            ],
        },
        fallbacks=[CommandHandler("start", start_cmd)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("deliver_manual", deliver_manual_cmd))

    # callbacks + conversations
    app.add_handler(conv)

    # menu text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    return app

def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

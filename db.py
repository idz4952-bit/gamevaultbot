# db.py
import io
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict

import config

# =========================
# DB connect + schema
# =========================
con = sqlite3.connect(config.DB_PATH, check_same_thread=False)
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


ensure_schema()


def seed_owner_admin():
    # Ensure owner exists as OWNER in admins table
    try:
        cur.execute("INSERT OR REPLACE INTO admins(user_id, role) VALUES(?,?)", (config.ADMIN_ID, config.ROLE_OWNER))
        con.commit()
    except Exception:
        pass


seed_owner_admin()


def admin_role(uid: int) -> Optional[str]:
    cur.execute("SELECT role FROM admins WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r[0] if r else None


def is_admin_any(uid: int) -> bool:
    return admin_role(uid) in (config.ROLE_OWNER, config.ROLE_HELPER)


def is_manual_admin(uid: int) -> bool:
    # helper can only manage manual orders; owner can do everything
    return admin_role(uid) in (config.ROLE_OWNER, config.ROLE_HELPER)


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


# =========================
# Products helpers
# =========================
def product_stock(pid: int) -> int:
    cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
    return int(cur.fetchone()[0])


# =========================
# Orders helpers
# =========================
def orders_query(uid: int, rng: str) -> List[Tuple]:
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


def users_page(page: int, page_size: int = 10) -> Tuple[List[Tuple], int]:
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
        out.append(
            (
                uid,
                username or "",
                first_name or "",
                float(bal or 0),
                int(oc or 0),
                float(osp or 0),
                int(mc or 0),
                float(msp or 0),
                float(dep or 0),
                int(suspended or 0),
            )
        )
    return out, total_pages


def user_report_text(uid: int, limit_each: int = 10) -> str:
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
    lines.append(f"💰 Balance: {bal:.3f}{config.CURRENCY}")
    lines.append("")
    lines.append(f"🧾 Orders Completed: {int(oc or 0)} | Spent: {float(osp or 0):.3f}{config.CURRENCY}")
    lines.append(f"⚡ Manual Completed: {int(mc or 0)} | Spent: {float(msp or 0):.3f}{config.CURRENCY}")
    lines.append(f"💳 Deposits Approved: {float(dep or 0):.3f}{config.CURRENCY}")
    lines.append("\n--- LAST ORDERS ---")
    cur.execute(
        "SELECT id, product_title, total, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit_each),
    )
    for oid, title, total, status, created_at in cur.fetchall():
        lines.append(f"#{oid} | {status} | {float(total):.3f}{config.CURRENCY} | {created_at} | {title}")

    lines.append("\n--- LAST MANUAL ---")
    cur.execute(
        "SELECT id, service, plan_title, price, status, created_at, COALESCE(approved_by,'') FROM manual_orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit_each),
    )
    for mid, service, plan_title, price, status, created_at, approved_by in cur.fetchall():
        ab = f" | approved_by={approved_by}" if approved_by else ""
        lines.append(f"M#{mid} | {status} | {float(price):.3f}{config.CURRENCY} | {created_at} | {service} | {plan_title}{ab}")

    lines.append("\n--- LAST DEPOSITS ---")
    cur.execute(
        "SELECT id, method, amount, status, created_at, txid FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit_each),
    )
    for did, method, amount, status, created_at, txid in cur.fetchall():
        a = "None" if amount is None else f"{float(amount):.3f}{config.CURRENCY}"
        t = (txid or "")[:18] + ("..." if (txid and len(txid) > 18) else "")
        lines.append(f"D#{did} | {status} | {a} | {created_at} | {method} | {t}")

    return "\n".join(lines)


def dashboard_text() -> str:
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
    lines.append(f"🧾 Completed Orders: *{int(oc or 0)}*  | 💰 Revenue: *{float(osp or 0):.3f}{config.CURRENCY}*")
    lines.append(f"⚡ Completed Manual: *{int(mc or 0)}*  | 💰 Revenue: *{float(msp or 0):.3f}{config.CURRENCY}*")
    lines.append(f"💳 Approved Deposits: *{int(dc or 0)}* | 💵 Total: *{float(dep_sum or 0):.3f}{config.CURRENCY}*")
    lines.append("")
    lines.append(f"📦 Total Stock Codes (unused): *{stock_all}*")
    lines.append("")
    lines.append("🏆 *Top Products (Revenue)*")
    if not top:
        lines.append("— No data yet.")
    else:
        for title, rev in top:
            lines.append(f"• {title[:40]} — *{float(rev):.3f}{config.CURRENCY}*")
    lines.append("")
    lines.append("✅ Everything running smooth 🚀")
    return "\n".join(lines)[:3800]


# =========================
# Code validation rules for Admin adding codes
# =========================
FF_CODE_RE = re.compile(r"^\d{16}$")
PUBG_CODE_RE = re.compile(r"^[A-Za-z0-9]{18}$")


def pid_code_rule(pid: int) -> Optional[str]:
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
    rule = pid_code_rule(pid)
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

    sample = bad[0]
    if rule == "FF16":
        msg = f"❌ Free Fire code must be 16 digits فقط.\nمثال: 1234567890123456\nBad sample: {sample}"
    else:
        msg = f"❌ PUBG code must be 18 characters (A-Z a-z 0-9).\nBad sample: {sample}"
    return False, msg

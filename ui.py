# ui.py
import re
from typing import List, Tuple, Dict, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.helpers import escape_markdown

import db
from config import (
    CURRENCY,
    HIDDEN_CATEGORIES,
    to_tme,
    SUPPORT_PHONE,
    SUPPORT_CHANNEL,
    extract_sort_value,
    manual_hours_text,
)
from db import get_manual_price, MANUAL_PRICE_DEFAULTS

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

def md(x: str) -> str:
    return escape_markdown(x or "", version=1)

def money3(x: float) -> str:
    return f"{x:.3f} {CURRENCY}"

# =========================
# Delivery limits
# =========================
MAX_CODES_IN_MESSAGE = 200
TELEGRAM_TEXT_LIMIT = 3800

# =========================
# Keyboards
# =========================
def kb_categories(is_admin_user: bool) -> InlineKeyboardMarkup:
    db.cur.execute(
        """
        SELECT c.cid, c.title, COUNT(p.pid)
        FROM categories c
        LEFT JOIN products p ON p.cid=c.cid AND p.active=1
        GROUP BY c.cid
        ORDER BY c.title
        """
    )
    rows = []
    for cid, title, cnt in db.cur.fetchall():
        if title in HIDDEN_CATEGORIES:
            continue
        rows.append([InlineKeyboardButton(f"{title} | {cnt}", callback_data=f"cat:{cid}")])

    if is_admin_user:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin:panel")])

    return InlineKeyboardMarkup(rows)

def product_stock(pid: int) -> int:
    db.cur.execute("SELECT COUNT(*) FROM codes WHERE pid=? AND used=0", (pid,))
    return int(db.cur.fetchone()[0])

def kb_products(cid: int) -> InlineKeyboardMarkup:
    db.cur.execute("SELECT pid,title,price FROM products WHERE cid=? AND active=1", (cid,))
    items = db.cur.fetchall()
    items.sort(key=lambda r: extract_sort_value(r[1]))

    rows = []
    for pid, title, price in items:
        stock = product_stock(pid)
        label = f"{title} | {money3(float(price))} | 📦{stock}"
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
        [InlineKeyboardButton("💬 Support", url=to_tme(SUPPORT_PHONE))],
        [InlineKeyboardButton("📣 Support Channel", url=to_tme(SUPPORT_CHANNEL))],
    ]
    return InlineKeyboardMarkup(rows)

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

def _ff_cart_get(context):
    cart = context.user_data.get("ff_cart")
    if not isinstance(cart, dict):
        cart = {}
        context.user_data["ff_cart"] = cart
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
        price = get_manual_price(sku, MANUAL_PRICE_DEFAULTS.get(sku, 0.0))
        total_price += float(price) * qty
        total_diamonds += diamonds * qty
        lines.append((title, qty, float(price), diamonds))

    order_map = {t: i for i, (_, t, _) in enumerate(FF_PACKS)}
    lines.sort(key=lambda x: order_map.get(x[0], 999))
    return total_price, total_diamonds, lines

def kb_ff_menu(context) -> InlineKeyboardMarkup:
    cart = _ff_cart_get(context)
    rows = []
    for sku, title, _ in FF_PACKS:
        qty = int(cart.get(sku, 0))
        suffix = f"  🧺[{qty}]" if qty > 0 else ""
        price = get_manual_price(sku, MANUAL_PRICE_DEFAULTS.get(sku, 0.0))
        rows.append([InlineKeyboardButton(f"{title} 💎 | {float(price):.3f}{CURRENCY}{suffix}", callback_data=f"manual:ff:add:{sku}")])

    rows.append([InlineKeyboardButton("🗑 Clear Cart", callback_data="manual:ff:clear")])
    rows.append([InlineKeyboardButton("✅ Proceed to Checkout", callback_data="manual:ff:checkout")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="manual:services")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")])
    return InlineKeyboardMarkup(rows)

def ff_checkout_text(context) -> str:
    cart = _ff_cart_get(context)
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

# تصدير دوال FF للحاجة في bot.py
ff_cart_get = _ff_cart_get
ff_calc_totals = _ff_calc_totals
ff_pack = _ff_pack

# ui.py
import io
from typing import List, Tuple, Dict, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

import config
import db

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
# Delivery
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
        await context.bot.send_message(
            chat_id=chat_id,
            text=header + "📎 *Your codes are attached in a file:*",
            parse_mode=ParseMode.MARKDOWN,
        )
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
# Helpers
# =========================
def md(x: str) -> str:
    return escape_markdown(x or "", version=1)


def smart_reply(msg: str) -> Optional[str]:
    m = (msg or "").lower()
    if any(x in m for x in ["price", "سعر", "كم", "ثمن"]):
        return "💡 الأسعار تظهر داخل 🛒 Our Products → اختر القسم."
    if any(x in m for x in ["balance", "رصيد", "wallet", "محفظة"]):
        return "💡 اضغط 💰 My Balance لمشاهدة الرصيد وطرق الشحن."
    if any(x in m for x in ["order", "طلب", "orders", "طلباتي"]):
        return "💡 اضغط 📦 My Orders لمشاهدة الطلبات."
    if any(x in m for x in ["usdt", "trc20", "bep20", "txid"]):
        return "💡 من 💰 My Balance اختر طريقة الشحن ثم اضغط ✅ I Have Paid وأرسل Amount | TXID."
    return None


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
        if title in config.HIDDEN_CATEGORIES:
            continue
        rows.append([InlineKeyboardButton(f"{title} | {cnt}", callback_data=f"cat:{cid}")])

    if is_admin_user:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin:panel")])

    return InlineKeyboardMarkup(rows)


def kb_products(cid: int) -> InlineKeyboardMarkup:
    db.cur.execute("SELECT pid,title,price FROM products WHERE cid=? AND active=1", (cid,))
    items = db.cur.fetchall()
    items.sort(key=lambda r: config.extract_sort_value(r[1]))

    rows = []
    for pid, title, price in items:
        stock = db.product_stock(pid)
        label = f"{title} | {config.money(float(price))} | 📦{stock}"
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
        [InlineKeyboardButton("💬 Support Chat", url=config.to_tme(config.SUPPORT_PHONE))],
        [InlineKeyboardButton("📣 Support Channel", url=config.to_tme(config.SUPPORT_CHANNEL))],
    ]
    return InlineKeyboardMarkup(rows)


def kb_qty_cancel(cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:prods:{cid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="goto:cats")],
        ]
    )


def kb_admin_panel(uid: int) -> InlineKeyboardMarkup:
    if db.admin_role(uid) == config.ROLE_HELPER:
        return InlineKeyboardMarkup([[InlineKeyboardButton("📥 Manual Orders", callback_data="admin:manuallist:0")]])

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Dashboard", callback_data="admin:dash"),
                InlineKeyboardButton("👥 Customers", callback_data="admin:users:0"),
            ],
            [
                InlineKeyboardButton("📥 Manual Orders", callback_data="admin:manuallist:0"),
                InlineKeyboardButton("💰 Deposits", callback_data="admin:deps:0"),
            ],
            [
                InlineKeyboardButton("📋 Products (PID)", callback_data="admin:listprod"),
                InlineKeyboardButton("⛔ Toggle Product", callback_data="admin:toggle"),
            ],
            [
                InlineKeyboardButton("🗑 Delete Product", callback_data="admin:delprod"),
                InlineKeyboardButton("🗑 Delete Category (FULL)", callback_data="admin:delcatfull"),
            ],
            [
                InlineKeyboardButton("➕ Add Category", callback_data="admin:addcat"),
                InlineKeyboardButton("➕ Add Product", callback_data="admin:addprod"),
            ],
            [
                InlineKeyboardButton("➕ Add Codes (text)", callback_data="admin:addcodes"),
                InlineKeyboardButton("📄 Add Codes (file)", callback_data="admin:addcodesfile"),
            ],
            [
                InlineKeyboardButton("💲 Set Price", callback_data="admin:setprice"),
                InlineKeyboardButton("🛠 Manual Prices", callback_data="admin:manualprices"),
            ],
            [
                InlineKeyboardButton("➕ Add Balance", callback_data="admin:addbal"),
                InlineKeyboardButton("➖ Take Balance", callback_data="admin:takebal"),
            ],
            [
                InlineKeyboardButton("👑 Admins", callback_data="admin:admins"),
            ],
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


def kb_admin_users_page(page: int, total_pages: int, rows: List[Tuple]) -> InlineKeyboardMarkup:
    buttons = []
    for uid, username, first_name, bal, oc, osp, mc, msp, dep, suspended in rows:
        uname = f"@{username}" if username else ""
        name = first_name or ""
        sflag = " ⛔" if int(suspended) == 1 else ""
        label = f"👤 {uid}{sflag} {uname} {name}".strip()
        sub = f" | 💰{bal:.3f}{config.CURRENCY} | 🧾{oc} | 🔥{osp:.3f}{config.CURRENCY}"
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
    can_suspend = (not db.is_admin_any(uid)) and (uid != config.ADMIN_ID)

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

# config.py
import os
import re
import secrets
import logging
from datetime import datetime, timedelta

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

# ✅ خلي SUPPORT_PHONE واحد فقط (يوزر أو رقم)
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "@your_support")  # direct chat (not group)
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "@yourchannel")

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


def new_client_ref() -> str:
    return secrets.token_hex(10)

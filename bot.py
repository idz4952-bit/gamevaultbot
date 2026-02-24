import os, sqlite3
from telegram import *
from telegram.ext import *

TOKEN=os.getenv("TOKEN")
ADMIN_ID=int(os.getenv("ADMIN_ID","0"))
USDT_ADDRESS=os.getenv("USDT_ADDRESS","PUT_ADDRESS")

if not TOKEN:
    raise RuntimeError("TOKEN missing")

# ===== DATABASE =====
db=sqlite3.connect("shop.db",check_same_thread=False)
cur=db.cursor()

cur.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,balance REAL DEFAULT 0)")
cur.execute("""CREATE TABLE IF NOT EXISTS orders(
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,product TEXT,qty INTEGER,total REAL,status TEXT DEFAULT 'pending')""")
db.commit()

# ===== PRODUCTS =====
CATALOG={
"PUBG":[["60 UC",0.875,500],["325 UC",4.375,100]],
"iTunes":[["10$",9.2,50],["25$",23,20]],
"Free Fire":[["100 Diamonds",1.2,1000]]
}

# ===== MENUS =====
menu=ReplyKeyboardMarkup(
[["🛍 الأقسام","💳 محفظتي"],
["➕ شحن","📦 طلباتي"]],
resize_keyboard=True)

def cats_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton(c,callback_data=f"cat:{c}")]for c in CATALOG])

def prod_kb(cat):
    rows=[]
    for name,price,stock in CATALOG[cat]:
        rows.append([InlineKeyboardButton(f"{name} | {price}$ | {stock}",callback_data=f"prod:{cat}:{name}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع",callback_data="home")])
    return InlineKeyboardMarkup(rows)

def qty_kb(q):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➖",callback_data="minus"),
         InlineKeyboardButton(str(q),callback_data="noop"),
         InlineKeyboardButton("➕",callback_data="plus")],
        [InlineKeyboardButton("✅ تأكيد",callback_data="confirm")]
    ])

# ===== START =====
async def start(u,c):
    cur.execute("INSERT OR IGNORE INTO users(id) VALUES(?)",(u.effective_user.id,))
    db.commit()
    await u.message.reply_text("🛒 GameVault PRO",reply_markup=menu)

# ===== MENU =====
async def text(u,c):
    t=u.message.text

    if t=="🛍 الأقسام":
        await u.message.reply_text("اختر قسم:",reply_markup=cats_kb())

    elif t=="💳 محفظتي":
        bal=cur.execute("SELECT balance FROM users WHERE id=?",(u.effective_user.id,)).fetchone()[0]
        await u.message.reply_text(f"رصيدك: {bal}$")

    elif t=="➕ شحن":
        await u.message.reply_text(f"ارسل USDT الى:\n{USDT_ADDRESS}\nثم ارسل TXID")

    elif t=="📦 طلباتي":
        rows=cur.execute("SELECT product,total,status FROM orders WHERE user_id=?",(u.effective_user.id,)).fetchall()
        if not rows: return await u.message.reply_text("لا توجد طلبات")
        await u.message.reply_text("\n".join([f"{p} | {t}$ | {s}"for p,t,s in rows]))

# ===== CALLBACK =====
async def cb(u,c):
    q=u.callback_query
    await q.answer()
    d=q.data

    if d.startswith("cat:"):
        cat=d.split(":")[1]
        await q.edit_message_text("اختر منتج:",reply_markup=prod_kb(cat))

    elif d.startswith("prod:"):
        _,cat,name=d.split(":")
        for n,p,s in CATALOG[cat]:
            if n==name:
                c.user_data["order"]=[cat,n,p,s]
        c.user_data["qty"]=1
        await q.edit_message_text(f"{name}\nاختر الكمية:",reply_markup=qty_kb(1))

    elif d=="plus":
        c.user_data["qty"]+=1
        await q.edit_reply_markup(qty_kb(c.user_data["qty"]))

    elif d=="minus":
        c.user_data["qty"]=max(1,c.user_data["qty"]-1)
        await q.edit_reply_markup(qty_kb(c.user_data["qty"]))

    elif d=="confirm":
        cat,name,price,stock=c.user_data["order"]
        qty=c.user_data["qty"]
        total=qty*price

        bal=cur.execute("SELECT balance FROM users WHERE id=?",(u.effective_user.id,)).fetchone()[0]
        if bal<total:
            return await q.edit_message_text("❌ رصيد غير كافي")

        cur.execute("UPDATE users SET balance=balance-? WHERE id=?",(total,u.effective_user.id))
        cur.execute("INSERT INTO orders(user_id,product,qty,total) VALUES(?,?,?,?)",(u.effective_user.id,name,qty,total))
        db.commit()

        await q.edit_message_text(f"✅ تم الطلب\n{name} x{qty}\n{total}$")

        if ADMIN_ID:
            await c.bot.send_message(ADMIN_ID,f"طلب جديد\n{name} x{qty}\n{total}$\nUser:{u.effective_user.id}")

# ===== ADMIN =====
async def approve(u,c):
    if u.effective_user.id!=ADMIN_ID: return
    user=int(c.args[0]);amount=float(c.args[1])
    cur.execute("UPDATE users SET balance=balance+? WHERE id=?",(amount,user))
    db.commit()
    await u.message.reply_text("تم الشحن")

async def deliver(u,c):
    if u.effective_user.id!=ADMIN_ID: return
    order=int(c.args[0])
    cur.execute("UPDATE orders SET status='done' WHERE id=?",(order,))
    db.commit()
    await u.message.reply_text("تم التسليم")

# ===== RUN =====
def main():
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("approve",approve))
    app.add_handler(CommandHandler("deliver",deliver))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text))
    app.run_polling()

main()

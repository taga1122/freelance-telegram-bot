import asyncio
import sqlite3
import time
import aiohttp
import feedparser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# === НАСТРОЙКИ ===
BOT_TOKEN = "8835569941:AAEDNHyBOoFUnVVQsHcr1DibH9QHGHAWa4w"  # Токен от @BotFather
CRYPTO_TOKEN = "645870:AAXeBZRubXUrVvazuiAvE0Ad3btDiAgvaef"  # Токен от @CryptoBot

RSS_FEEDS = [
    "https://freelance.habr.com/tasks/rss",
    "https://www.fl.ru/rss/all.xml"
]

PRICE_USDT = 5.0  # Стоимость подписки в USDT
TRIAL_LIMIT = 3   # Количество бесплатных заказов для новичка

seen_tasks = set()

# === БАЗА ДАННЫХ (SQLite) ===
def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            free_count INTEGER DEFAULT 0,
            sub_until INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT free_count, sub_until FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (user_id, free_count, sub_until) VALUES (?, 0, 0)", (user_id,))
        conn.commit()
        row = (0, 0)
    conn.close()
    return {"free_count": row[0], "sub_until": row[1]}

def increment_free_count(user_id: int):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET free_count = free_count + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_subscription(user_id: int, days=30):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    now = int(time.time())
    user = get_user(user_id)
    base_time = max(now, user["sub_until"])
    new_until = base_time + (days * 86400)
    cursor.execute("UPDATE users SET sub_until = ? WHERE user_id = ?", (new_until, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

# === CRYPTOPAY API ===
async def create_invoice(user_id: int, amount: float):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
    payload = {
        "asset": "USDT",
        "amount": str(amount),
        "description": f"Подписка Freelance Radar (30 дней) для ID {user_id}",
        "payload": str(user_id)
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if data.get("ok"):
                return data["result"]
            return None

async def check_invoice(invoice_id: int):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
    payload = {"invoice_ids": [invoice_id]}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if data.get("ok") and data["result"]["items"]:
                return data["result"]["items"][0]["status"] == "paid"
            return False

# === ХЭНДЛЕРЫ БОТА ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    now = int(time.time())
    
    is_subbed = user["sub_until"] > now
    sub_status = "✅ Активна" if is_subbed else "❌ Неактивна"
    
    msg = (
        f"👋 **Привет! Я мониторю фриланс-биржи 24/7.**\n\n"
        f"📊 **Твой статус:**\n"
        f"• Использовано бесплатной демо-выгрузки: {user['free_count']}/{TRIAL_LIMIT}\n"
        f"• Безлимитная подписка: {sub_status}\n\n"
        f"Получай заказы первее всех и откликайся в первые минуты!"
    )
    
    keyboard = []
    if not is_subbed:
        keyboard.append([InlineKeyboardButton("💳 Купить подписку ($5 USDT)", callback_data="buy_sub")])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    invoice = await create_invoice(user_id, PRICE_USDT)
    if invoice:
        pay_url = invoice["pay_url"]
        invoice_id = invoice["invoice_id"]
        
        keyboard = [
            [InlineKeyboardButton("🔗 Оплатить в CryptoBot", url=pay_url)],
            [InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_{invoice_id}")]
        ]
        await query.edit_message_text(
            f"💰 **Счет на оплату подписки (30 дней)**\n\n"
            f"Сумма: **{PRICE_USDT} USDT**\n"
            f"После оплаты нажми кнопку «Проверить оплату».",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await query.edit_message_text("❌ Ошибка при создании счета. Попробуй позже.")

async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    invoice_id = int(query.data.split("_")[1])
    
    if await check_invoice(invoice_id):
        add_subscription(user_id, days=30)
        await query.edit_message_text(
            "🎉 **Оплата подтверждена!**\n\n"
            "Тебе активирован безлимитный доступ на 30 дней. Теперь все свежие заказы будут приходить мгновенно!",
            parse_mode="Markdown"
        )
    else:
        await query.answer("❌ Оплата еще не поступила! Проверь статус в CryptoBot.", show_alert=True)

# === ФОНОВЫЙ ПАРСЕР ===
async def fetch_orders(app):
    print("🚀 Автономный парсер с пейволлом запущен...")
    now = int(time.time())
    
    while True:
        try:
            for url in RSS_FEEDS:
                feed = feedparser.parse(url)
                for entry in reversed(feed.entries[:5]):
                    if entry.id not in seen_tasks:
                        seen_tasks.add(entry.id)
                        
                        title = entry.title
                        link = entry.link
                        
                        msg = (
                            f"⚡️ **НОВЫЙ ЗАКАЗ!**\n\n"
                            f"📌 **{title}**\n\n"
                            f"🔗 [Открыть и откликнуться]({link})"
                        )
                        
                        paywall_msg = (
                            f"🔒 **Новый заказ скрыт!**\n\n"
                            f"Ты исчерпал лимит из {TRIAL_LIMIT} бесплатных заказов.\n"
                            f"Оформи подписку, чтобы получать ссылки на заказы первее конкурентов!"
                        )
                        paywall_kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Купить подписку ($5 USDT)", callback_data="buy_sub")]])
                        
                        current_time = int(time.time())
                        all_users = get_all_users()
                        
                        for uid in all_users:
                            u_data = get_user(uid)
                            # Проверка наличия активной подписки
                            if u_data["sub_until"] > current_time:
                                try:
                                    await app.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
                                except Exception:
                                    pass
                            # Проверка бесплатного лимита
                            elif u_data["free_count"] < TRIAL_LIMIT:
                                try:
                                    await app.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
                                    increment_free_count(uid)
                                except Exception:
                                    pass
                            # Триал закончен — отправляем пейволл
                            elif u_data["free_count"] == TRIAL_LIMIT:
                                try:
                                    await app.bot.send_message(chat_id=uid, text=paywall_msg, parse_mode="Markdown", reply_markup=paywall_kb)
                                    increment_free_count(uid) # Чтобы не спамить пейволлом на каждый заказ
                                except Exception:
                                    pass
        except Exception as e:
            print(f"Ошибка при парсинге: {e}")
            
        await asyncio.sleep(45)

async def post_init(application):
    asyncio.create_task(fetch_orders(application))

if __name__ == "__main__":
    init_db()
    
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_sub$"))
    app.add_handler(CallbackQueryHandler(handle_check, pattern="^check_"))

    print("Запуск бота...")
    app.run_polling()
import asyncio
import sqlite3
from datetime import datetime
import random
import logging
from contextlib import contextmanager
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramRetryAfter
from instagrapi import Client
from cryptography.fernet import Fernet
import os
import sys

# -----------------------
# CONFIG - Update as needed
# -----------------------
TELEGRAM_TOKEN = '8236696657:AAEAAMIz2peAuLvXzgMvzcJT2GEt1SRyDOA'  # Bot token
ADMIN_USERNAME = 'serinaqu'
INSTAGRAM_USERNAME = ''  # Instagram login (fill if needed)
INSTAGRAM_PASSWORD = ''  # Instagram password (fill if needed)
PROXY = {}  # Example: {'proxy': {'https': 'http://proxy_user:proxy_pass@proxy_host:proxy_port'}}
ENCRYPTION_KEY_FILE = 'encryption_key.key'
DB_FILE = 'users.db'

# -----------------------
# Logging setup
# -----------------------
logging.basicConfig(
    filename='bot.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def log_action(action, user_id):
    logging.info(f"{action} by user {user_id} at {datetime.now()}")

# -----------------------
# Encryption key management
# -----------------------
def generate_encryption_key():
    try:
        if not os.path.exists(ENCRYPTION_KEY_FILE):
            key = Fernet.generate_key()
            with open(ENCRYPTION_KEY_FILE, 'wb') as f:
                f.write(key)
            logging.info("New encryption key generated")
        with open(ENCRYPTION_KEY_FILE, 'rb') as f:
            return f.read()
    except Exception as e:
        logging.error(f"Encryption key generation error: {str(e)}")
        print(f"Error: Failed to generate encryption key: {str(e)}")
        return None

ENCRYPTION_KEY = generate_encryption_key()
if ENCRYPTION_KEY is None:
    print("Critical error: Encryption key not generated. Bot will exit.")
    sys.exit(1)
cipher = Fernet(ENCRYPTION_KEY)

# -----------------------
# Database context manager
# -----------------------
@contextmanager
def db_connection():
    conn = sqlite3.connect(DB_FILE)
    try:
        yield conn
    except Exception as e:
        conn.rollback()
        logging.error(f"Database operation error: {str(e)}")
        raise
    finally:
        conn.close()

# -----------------------
# Database initialization and helpers
# -----------------------
def init_db():
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS users
                         (user_id INTEGER PRIMARY KEY, username TEXT, phone TEXT, join_date TEXT, 
                          country TEXT, language TEXT, activity_level INTEGER, referrals INTEGER, 
                          balance INTEGER DEFAULT 0, referrer_id INTEGER)''')
            c.execute('''CREATE TABLE IF NOT EXISTS mandatory_channels
                         (channel TEXT PRIMARY KEY)''')
            c.execute('''CREATE TABLE IF NOT EXISTS reklama_groups
                         (group_id TEXT PRIMARY KEY)''')
            c.execute('''CREATE TABLE IF NOT EXISTS user_ads
                         (ad_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ad_text TEXT, 
                          status TEXT DEFAULT 'pending', created_at TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS payments
                         (payment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, 
                          method TEXT, status TEXT DEFAULT 'pending', created_at TEXT)''')
            conn.commit()
        logging.info("Database initialized successfully")
    except Exception as e:
        logging.error(f"Database initialization error: {str(e)}")
        print(f"Error: Database initialization failed: {str(e)}")

def encrypt_data(data):
    try:
        if data is None:
            return None
        return cipher.encrypt(str(data).encode()).decode()
    except Exception as e:
        logging.error(f"Encryption error: {str(e)}")
        return None

def decrypt_data(data):
    try:
        if data is None:
            return None
        return cipher.decrypt(data.encode()).decode()
    except Exception as e:
        logging.error(f"Decryption error: {str(e)}")
        return None

def add_user(user_id, username, phone=None, country="UZ", language="uz", activity_level=1, referrer_id=None):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            phone_encrypted = encrypt_data(phone) if phone else None
            username_encrypted = encrypt_data(username) if username else None
            c.execute("""INSERT OR IGNORE INTO users
                         (user_id, username, phone, join_date, country, language, activity_level, referrals, balance, referrer_id)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (user_id, username_encrypted, phone_encrypted, datetime.now().isoformat(), country, language, activity_level, 0, 0, referrer_id))
            conn.commit()
        logging.info(f"User {user_id} added")
    except Exception as e:
        logging.error(f"User addition error: {str(e)}")

def get_stats():
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as total, SUM(CASE WHEN activity_level > 0 THEN 1 ELSE 0 END) as active FROM users")
            result = c.fetchone()
            c.execute("SELECT join_date FROM users ORDER BY join_date")
            dates = [row[0].split('T')[0] for row in c.fetchall()]
            growth = {}
            for date in dates:
                growth[date] = growth.get(date, 0) + 1
            total = result[0] if result else 0
            active = result[1] if result and result[1] is not None else total
            return {"total_users": total, "active_users": active, "growth": growth}
    except Exception as e:
        logging.error(f"Stats retrieval error: {str(e)}")
        return {"total_users": 0, "active_users": 0, "growth": {}}

def filter_users(country=None, language=None, activity_level=None):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            query = "SELECT * FROM users WHERE 1=1"
            params = []
            if country:
                query += " AND country = ?"
                params.append(country)
            if language:
                query += " AND language = ?"
                params.append(language)
            if activity_level is not None:
                query += " AND activity_level = ?"
                params.append(activity_level)
            c.execute(query, params)
            users = c.fetchall()
            decrypted_users = []
            for user in users:
                decrypted_user = list(user)
                decrypted_user[1] = decrypt_data(user[1]) if user[1] else None
                decrypted_user[2] = decrypt_data(user[2]) if user[2] else None
                decrypted_users.append(decrypted_user)
            return decrypted_users
    except Exception as e:
        logging.error(f"User filtering error: {str(e)}")
        return []

# -----------------------
# Small utilities
# -----------------------
async def verify_user(user_id):
    try:
        num1, num2 = random.randint(1, 10), random.randint(1, 10)
        correct_answer = num1 + num2
        return {"question": f"{num1} + {num2} = ?", "answer": correct_answer}
    except Exception as e:
        logging.error(f"CAPTCHA generation error: {str(e)}")
        return {"question": "Error occurred", "answer": 0}

async def generate_referral_link(user_id):
    try:
        me = await bot.get_me()
        return f"https://t.me/{me.username}?start={user_id}"
    except Exception as e:
        logging.error(f"Referral link generation error: {str(e)}")
        return "Error generating link"

async def process_referral(referred_user_id, referrer_id):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET referrals = referrals + 1, balance = balance + 5 WHERE user_id = ?", (referrer_id,))
            c.execute("SELECT referrer_id FROM users WHERE user_id = ?", (referrer_id,))
            second_level = c.fetchone()
            if second_level and second_level[0]:
                c.execute("UPDATE users SET balance = balance + 2 WHERE user_id = ?", (second_level[0],))
            conn.commit()
        log_action(f"Referral processed for {referrer_id}", referred_user_id)
    except Exception as e:
        logging.error(f"Referral processing error: {str(e)}")

async def add_instagram_follower(username, password, target_account):
    if not username or not password:
        return {"status": "error", "message": "Instagram login ma'lumotlari kiritilmagan"}
    try:
        cl = Client(**PROXY)
        cl.login(username, password)
        user_id = cl.user_id_from_username(target_account)
        cl.user_follow(user_id)
        return {"status": "success"}
    except Exception as e:
        logging.error(f"Instagram follow error: {str(e)}")
        return {"status": "error", "message": str(e)}

# -----------------------
# Payments & ads & channels
# -----------------------
async def process_payment(user_id, amount, method):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO payments (user_id, amount, method, status, created_at) VALUES (?, ?, ?, ?, ?)",
                      (user_id, amount, method, 'pending', datetime.now().isoformat()))
            conn.commit()
        return {"status": "pending", "message": f"{method} orqali {amount} so'm to'lov so'raldi. Admin tasdiqlashini kuting."}
    except Exception as e:
        logging.error(f"Payment processing error: {str(e)}")
        return {"status": "error", "message": str(e)}

def add_balance(user_id, amount):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            current_balance = c.fetchone()
            if current_balance:
                new_balance = current_balance[0] + amount
                if new_balance < 0:
                    raise ValueError("Balans salbiy bo'lib qolishi mumkin emas")
                c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
                conn.commit()
                logging.info(f"Balance updated: {amount} units for user {user_id}, new balance: {new_balance}")
            else:
                raise ValueError(f"User {user_id} not found")
    except Exception as e:
        logging.error(f"Balance update error: {str(e)}")
        raise

def get_balance(user_id):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            result = c.fetchone()
            return result[0] if result else 0
    except Exception as e:
        logging.error(f"Balance retrieval error: {str(e)}")
        return 0

async def post_ad(ad_text, group, bot, user_id):
    try:
        balance = get_balance(user_id)
        if balance < 50:
            return False, "Balans yetarli emas (kamida 50 birlik kerak)"
        await bot.send_message(chat_id=group, text=ad_text)
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO user_ads (user_id, ad_text, status, created_at) VALUES (?, ?, ?, ?)",
                      (user_id, ad_text, 'posted', datetime.now().isoformat()))
            conn.commit()
        add_balance(user_id, -50)  # Deduct 50 units
        logging.info(f"Ad posted by user {user_id}")
        return True, "Success"
    except Exception as e:
        logging.error(f"Ad posting error: {str(e)}")
        return False, str(e)

def get_mandatory_channels():
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT channel FROM mandatory_channels")
            channels = [row[0] for row in c.fetchall()]
            return channels
    except Exception as e:
        logging.error(f"Mandatory channels retrieval error: {str(e)}")
        return []

def add_mandatory_channel(channel):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO mandatory_channels (channel) VALUES (?)", (channel,))
            conn.commit()
        logging.info(f"Mandatory channel added: {channel}")
    except Exception as e:
        logging.error(f"Channel addition error: {str(e)}")

def remove_mandatory_channel(channel):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM mandatory_channels WHERE channel = ?", (channel,))
            conn.commit()
        logging.info(f"Mandatory channel removed: {channel}")
    except Exception as e:
        logging.error(f"Channel removal error: {str(e)}")

def get_reklama_groups():
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT group_id FROM reklama_groups")
            groups = [row[0] for row in c.fetchall()]
            return groups
    except Exception as e:
        logging.error(f"Ad groups retrieval error: {str(e)}")
        return []

def add_reklama_group(group):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO reklama_groups (group_id) VALUES (?)", (group,))
            conn.commit()
        logging.info(f"Ad group added: {group}")
    except Exception as e:
        logging.error(f"Ad group addition error: {str(e)}")

def remove_reklama_group(group):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM reklama_groups WHERE group_id = ?", (group,))
            conn.commit()
        logging.info(f"Ad group removed: {group}")
    except Exception as e:
        logging.error(f"Ad group removal error: {str(e)}")

def get_user_ads():
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM user_ads")
            ads = c.fetchall()
            return ads
    except Exception as e:
        logging.error(f"Ads retrieval error: {str(e)}")
        return []

def get_pending_payments():
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM payments WHERE status = 'pending'")
            payments = c.fetchall()
            return payments
    except Exception as e:
        logging.error(f"Pending payments retrieval error: {str(e)}")
        return []

def is_admin(username):
    return username == ADMIN_USERNAME

def approve_payment(user_id, amount):
    try:
        with db_connection() as conn:
            c = conn.cursor()
            add_balance(user_id, amount)
            c.execute("UPDATE payments SET status = 'approved' WHERE user_id = ? AND amount = ?", (user_id, amount))
            conn.commit()
        logging.info(f"Payment approved: {amount} units for user {user_id}")
    except Exception as e:
        logging.error(f"Payment approval error: {str(e)}")
        raise

# -----------------------
# FSM States
# -----------------------
class UserStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_captcha = State()
    waiting_for_payment_amount = State()
    waiting_for_ad_text = State()
    waiting_for_channel_to_add = State()
    waiting_for_channel_to_remove = State()
    waiting_for_group_to_add = State()
    waiting_for_group_to_remove = State()

# -----------------------
# Rate limit middleware
# -----------------------
class RateLimitMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except TelegramRetryAfter as e:
            logging.warning(f"Rate limit hit, retrying after {e.retry_after} seconds")
            await asyncio.sleep(e.retry_after)
            return await handler(event, data)
        except Exception as e:
            logging.error(f"Middleware error: {str(e)}")
            return

# -----------------------
# Keyboards / Menus
# -----------------------
def main_menu(is_admin_flag=False):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Vazifalar", callback_data="tasks"),
         InlineKeyboardButton(text="💰 Balans", callback_data="balance")],
        [InlineKeyboardButton(text="👥 Referral", callback_data="referral"),
         InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
        [InlineKeyboardButton(text="📢 Reklama joylash", callback_data="post_ad"),
         InlineKeyboardButton(text="➕ Obuna topshiriq", callback_data="subscribe")],
        [InlineKeyboardButton(text="💳 Toʻlov qilish", callback_data="pay"),
         InlineKeyboardButton(text="ℹ️ Yordam", callback_data="help")],
        [InlineKeyboardButton(text="📸 Instagram obuna", callback_data="add_instagram")]
    ])
    if is_admin_flag:
        kb.inline_keyboard.append([InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin_panel")])
    return kb

def menu_button():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="📋 Menyu")]
    ])

def admin_panel_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Kanal qoʻshish", callback_data="admin_add_channel")],
        [InlineKeyboardButton(text="➖ Kanal oʻchirish", callback_data="admin_remove_channel")],
        [InlineKeyboardButton(text="➕ Reklama guruhi qoʻshish", callback_data="admin_add_group")],
        [InlineKeyboardButton(text="➖ Reklama guruhi oʻchirish", callback_data="admin_remove_group")],
        [InlineKeyboardButton(text="📊 Toʻliq statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💳 Kutilayotgan toʻlovlar", callback_data="admin_payments")],
        [InlineKeyboardButton(text="↩️ Orqaga", callback_data="back_to_main")]
    ])

def pay_method_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Payme", callback_data="pay_method_Payme"),
         InlineKeyboardButton(text="Click", callback_data="pay_method_Click")],
        [InlineKeyboardButton(text="Bankomat", callback_data="pay_method_Bankomat"),
         InlineKeyboardButton(text="↩️ Orqaga", callback_data="back_to_main")]
    ])

# -----------------------
# Bot initialization
# -----------------------
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# -----------------------
# Handlers
# -----------------------
async def on_startup(_):
    logging.info("Bot started")
    init_db()

@dp.message(Command(commands=["start"]))
async def start_command(message: Message, state: FSMContext):
    try:
        logging.debug(f"Start: {message.from_user.id}")
        referrer_id = None
        text = message.text or ""
        parts = text.split()
        if len(parts) > 1:
            try:
                referrer_id = int(parts[1])
            except:
                referrer_id = None

        with db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT phone FROM users WHERE user_id = ?", (message.from_user.id,))
            row = c.fetchone()

        if row and row[0]:
            decrypted_phone = decrypt_data(row[0]) if row[0] else None
            add_user(message.from_user.id, message.from_user.username, decrypted_phone, referrer_id=referrer_id)
            if referrer_id:
                await process_referral(message.from_user.id, referrer_id)
            await message.answer("👋 Xush kelibsiz! Menyuni ochish uchun 📋 Menyu tugmasini bosing.", reply_markup=menu_button())
            await state.clear()
        else:
            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.add(KeyboardButton("📲 Telefon raqamini yuborish", request_contact=True))
            await message.answer("Iltimos, telefon raqamingizni yuboring.", reply_markup=kb)
            await state.set_state(UserStates.waiting_for_phone)
        log_action("User started bot", message.from_user.id)
    except Exception as e:
        logging.error(f"Start error: {e}", exc_info=True)
        await message.answer("Xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring.", reply_markup=menu_button())

@dp.message(lambda m: m.text == "📋 Menyu")
async def show_menu(message: Message, state: FSMContext):
    try:
        await message.answer("Asosiy menyu:", reply_markup=main_menu(is_admin_flag=is_admin(message.from_user.username)))
        log_action("Menu opened", message.from_user.id)
    except Exception as e:
        logging.error(f"Show menu error: {e}", exc_info=True)
        await message.answer("Menyuni ochishda xatolik yuz berdi.", reply_markup=menu_button())

@dp.message(lambda m: m.contact is not None, UserStates.waiting_for_phone)
async def process_phone_contact(message: Message, state: FSMContext):
    try:
        phone = message.contact.phone_number
        add_user(message.from_user.id, message.from_user.username, phone)
        await message.answer("✅ Telefon raqamingiz saqlandi.", reply_markup=menu_button())
        await message.answer("Asosiy menyu:", reply_markup=main_menu(is_admin_flag=is_admin(message.from_user.username)))
        await state.clear()
        log_action("Phone saved", message.from_user.id)
    except Exception as e:
        logging.error(f"Process phone error: {e}", exc_info=True)
        await message.answer("Telefonni saqlashda xatolik yuz berdi.", reply_markup=menu_button())

@dp.callback_query()
async def callbacks_router(query: CallbackQuery, state: FSMContext):
    data = query.data
    user = query.from_user
    try:
        await query.answer()

        if data == "back_to_main":
            await query.message.edit_text("Asosiy menyu:", reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
            return

        if data == "help":
            help_text = ("Bu bot orqali obuna qilib ball yig'ish, reklama joylash va to'lovlar bo'yicha ishlash mumkin.\n\n"
                         f"Har qanday muammo bo'lsa adminga yozing: @{ADMIN_USERNAME}")
            await query.message.edit_text(help_text, reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
            return

        if data == "tasks":
            tasks_text = ("📋 Vazifalar:\n\n"
                          "1) Kanal/guruhga obuna bo'ling — ball olasiz (➕ Obuna tugmasi orqali tekshirish)\n"
                          "2) Instagram obunasi — +10 ball (Instagram bo'limi orqali, CAPTCHA bilan)\n"
                          "3) Do'st taklif qilsangiz — +5 ball (Referral bo'limida havola)")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Obuna topshiriqni tekshirish", callback_data="subscribe"),
                 InlineKeyboardButton(text="📸 Instagram obuna", callback_data="add_instagram")],
                [InlineKeyboardButton(text="↩️ Orqaga", callback_data="back_to_main")]
            ])
            await query.message.edit_text(tasks_text, reply_markup=kb)
            return

        if data == "balance":
            bal = get_balance(user.id)
            await query.message.edit_text(f"💰 Sizning balansingiz: {bal} birlik", reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
            return

        if data == "referral":
            link = await generate_referral_link(user.id)
            await query.message.edit_text(f"👥 Sizning referral havolangiz:\n{link}", reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
            return

        if data == "stats":
            stats = get_stats()
            growth_text = "\n".join([f"{date}: {'█' * count}" for date, count in stats['growth'].items()]) or "Hech qanday o'sish yo'q"
            text = f"📊 Statistika:\nUmumiy foydalanuvchilar: {stats['total_users']}\nFaol foydalanuvchilar: {stats['active_users']}\n\nO'sish grafigi:\n{growth_text}"
            await query.message.edit_text(text, reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
            return

        if data == "subscribe":
            channels = get_mandatory_channels()
            if not channels:
                await query.message.answer("Majburiy kanal yoki guruhlar mavjud emas. Admin bilan bog'laning.", reply_markup=menu_button())
                return
            bonus_per_channel = 5
            total_bonus = 0
            not_subscribed = []
            for ch in channels:
                ok = await check_subscription(user.id, ch, bot)
                if ok:
                    total_bonus += bonus_per_channel
                else:
                    not_subscribed.append(ch)
            if total_bonus > 0:
                add_balance(user.id, total_bonus)
                text = f"🎉 Obuna tekshirildi! Sizga {total_bonus} birlik qo'shildi."
                if not_subscribed:
                    text += "\n\nQuyidagi kanallarga hali obuna bo'lmagansiz:\n" + "\n".join(not_subscribed)
            else:
                text = "❌ Siz hali majburiy kanallarga obuna bo'lmagansiz. Iltimos obuna bo'ling va qayta tekshiring."
            await query.message.edit_text(text, reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
            log_action("Subscribe task attempted", user.id)
            return

        if data == "post_ad":
            balance = get_balance(user.id)
            if balance < 50:
                await query.message.edit_text("❌ Reklama joylash uchun balansingiz yetarli emas (kamida 50 birlik kerak).", reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
                return
            await query.message.edit_text("✍️ Reklama matnini yuboring (matn yuborilgach admin tasdiqlaydi).", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Orqaga", callback_data="back_to_main")]]))
            await state.set_state(UserStates.waiting_for_ad_text)
            return

        if data == "pay":
            await query.message.edit_text("💳 To'lov usulini tanlang:", reply_markup=pay_method_kb())
            return

        if data.startswith("pay_method_"):
            method = data.split("_", 2)[2]
            await query.message.edit_text(f"💳 Tanlangan usul: {method}\nIltimos to'lov miqdorini so'mda kiriting:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Orqaga", callback_data="back_to_main")]]))
            await state.update_data(selected_payment_method=method)
            await state.set_state(UserStates.waiting_for_payment_amount)
            return

        if data == "add_instagram":
            if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
                await query.message.edit_text("❌ Instagram obunasi faol emas. Admin bilan bog'laning.", reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
                return
            captcha = await verify_user(user.id)
            await state.update_data(captcha_answer=captcha['answer'], instagram_target="target_account")  # Replace with actual target account
            await state.set_state(UserStates.waiting_for_captcha)
            await query.message.edit_text(f"🔒 CAPTCHA: {captcha['question']}\nIltimos javobni yozing.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Orqaga", callback_data="back_to_main")]]))
            log_action("CAPTCHA requested for Instagram", user.id)
            return

        if data == "admin_panel":
            if not is_admin(user.username):
                await query.message.edit_text("❌ Siz admin emassiz.", reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
                return
            await query.message.edit_text("⚙️ Admin panel:", reply_markup=admin_panel_menu())
            return

        if data == "admin_add_channel":
            if not is_admin(user.username):
                await query.message.answer("Siz admin emassiz.", reply_markup=menu_button())
                return
            await query.message.edit_text("Kanal username ni yuboring (masalan @kanal_nomi):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Orqaga", callback_data="admin_panel")]]))
            await state.set_state(UserStates.waiting_for_channel_to_add)
            return

        if data == "admin_remove_channel":
            if not is_admin(user.username):
                await query.message.answer("Siz admin emassiz.", reply_markup=menu_button())
                return
            await query.message.edit_text("O'chiriladigan kanal username ni yuboring (masalan @kanal_nomi):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Orqaga", callback_data="admin_panel")]]))
            await state.set_state(UserStates.waiting_for_channel_to_remove)
            return

        if data == "admin_add_group":
            if not is_admin(user.username):
                await query.message.answer("Siz admin emassiz.", reply_markup=menu_button())
                return
            await query.message.edit_text("Reklama guruhi ID yoki @username ni yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Orqaga", callback_data="admin_panel")]]))
            await state.set_state(UserStates.waiting_for_group_to_add)
            return

        if data == "admin_remove_group":
            if not is_admin(user.username):
                await query.message.answer("Siz admin emassiz.", reply_markup=menu_button())
                return
            await query.message.edit_text("O'chiriladigan reklama guruhi ID yoki @username ni yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Orqaga", callback_data="admin_panel")]]))
            await state.set_state(UserStates.waiting_for_group_to_remove)
            return

        if data == "admin_stats":
            if not is_admin(user.username):
                await query.message.answer("Siz admin emassiz.", reply_markup=menu_button())
                return
            stats = get_stats()
            channels = get_mandatory_channels()
            groups = get_reklama_groups()
            ads = get_user_ads()
            payments = get_pending_payments()
            users = filter_users()
            response = f"📊 To'liq Statistika:\nUmumiy foydalanuvchilar: {stats['total_users']}\nFaol: {stats['active_users']}\n"
            response += f"Majburiy kanallar: {len(channels)}\nReklama guruhlari: {len(groups)}\nReklamalar: {len(ads)}\nKutilayotgan to'lovlar: {len(payments)}\n\n"
            response += "Foydalanuvchilar (birinchi 30):\n"
            for u in users[:30]:
                response += f"ID: {u[0]}, Username: {u[1] or 'N/A'}, Phone: {u[2] or 'N/A'}, Balance: {u[8]}\n"
            await query.message.edit_text(response, reply_markup=admin_panel_menu())
            return

        if data == "admin_payments":
            if not is_admin(user.username):
                await query.message.answer("Siz admin emassiz.", reply_markup=menu_button())
                return
            payments = get_pending_payments()
            if not payments:
                await query.message.edit_text("Kutilayotgan to'lovlar yo'q.", reply_markup=admin_panel_menu())
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[])
            for p in payments:
                pid, uid, amount, method, status, created = p
                kb.inline_keyboard.append([InlineKeyboardButton(text=f"✅ Tasdiqlash: ID:{pid} User:{uid} {amount} so'm ({method})", callback_data=f"admin_approve_{pid}_{uid}_{amount}")])
            kb.inline_keyboard.append([InlineKeyboardButton(text="↩️ Orqaga", callback_data="admin_panel")])
            await query.message.edit_text("Kutilayotgan to'lovlar:", reply_markup=kb)
            return

        if data.startswith("admin_approve_"):
            if not is_admin(user.username):
                await query.message.answer("Siz admin emassiz.", reply_markup=menu_button())
                return
            parts = data.split("_")
            try:
                pid = int(parts[2])
                uid = int(parts[3])
                amount = int(parts[4])
                approve_payment(uid, amount)
                await query.message.edit_text(f"✅ To'lov tasdiqlandi: User {uid} ga {amount} birlik qo'shildi.", reply_markup=admin_panel_menu())
                return
            except Exception as e:
                logging.error(f"Admin approve parse error: {e}", exc_info=True)
                await query.message.answer("To'lovni tasdiqlashda xato.", reply_markup=menu_button())
                return

        await query.message.edit_text("Asosiy menyu:", reply_markup=main_menu(is_admin_flag=is_admin(user.username)))
    except Exception as e:
        logging.error(f"Callback error: {e}", exc_info=True)
        await query.message.answer("Xatolik yuz berdi. Menyuni ochish uchun 📋 Menyu tugmasini bosing.", reply_markup=menu_button())

@dp.message(UserStates.waiting_for_ad_text)
async def receive_ad_text(message: Message, state: FSMContext):
    try:
        ad_text = message.text
        groups = get_reklama_groups()
        if not groups:
            await message.answer("Reklama guruhlari mavjud emas. Admin bilan bog'laning.", reply_markup=menu_button())
            await state.clear()
            return
        success, msg = await post_ad(ad_text, groups[0], bot, message.from_user.id)
        if success:
            await message.answer("✅ Reklama joylandi va 50 birlik yechildi.", reply_markup=menu_button())
            await message.answer("Asosiy menyu:", reply_markup=main_menu(is_admin_flag=is_admin(message.from_user.username)))
        else:
            await message.answer(f"❌ Reklama joylashda xatolik: {msg}", reply_markup=menu_button())
        await state.clear()
    except Exception as e:
        logging.error(f"Receive ad text error: {e}", exc_info=True)
        await message.answer("Reklama yuborishda xatolik yuz berdi.", reply_markup=menu_button())
        await state.clear()

@dp.message(UserStates.waiting_for_payment_amount)
async def receive_payment_amount(message: Message, state: FSMContext):
    try:
        text = message.text.strip()
        if not text.isdigit():
            await message.answer("Iltimos, faqat raqam kiriting (to'lov miqdori).", reply_markup=menu_button())
            return
        amount = int(text)
        if amount <= 0:
            await message.answer("To'lov miqdori 0 dan katta bo'lishi kerak.", reply_markup=menu_button())
            return
        data = await state.get_data()
        method = data.get("selected_payment_method", "Unknown")
        result = await process_payment(message.from_user.id, amount, method)
        await message.answer(result['message'], reply_markup=menu_button())
        await message.answer("Asosiy menyu:", reply_markup=main_menu(is_admin_flag=is_admin(message.from_user.username)))
        await state.clear()
        log_action("Payment requested", message.from_user.id)
    except Exception as e:
        logging.error(f"Payment amount error: {e}", exc_info=True)
        await message.answer("To'lov miqdorini qabul qilishda xato yuz berdi.", reply_markup=menu_button())
        await state.clear()

@dp.message(UserStates.waiting_for_captcha)
async def process_captcha(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        correct = data.get("captcha_answer")
        target_account = data.get("instagram_target", "target_account")
        try:
            user_ans = int(message.text.strip())
        except:
            await message.answer("Iltimos raqam kiriting.", reply_markup=menu_button())
            return
        if user_ans == correct:
            res = await add_instagram_follower(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, target_account)
            if res.get("status") == "success":
                add_balance(message.from_user.id, 10)
                await message.answer("✅ Instagram obunasi muvaffaqiyatli! +10 birlik", reply_markup=menu_button())
                await message.answer("Asosiy menyu:", reply_markup=main_menu(is_admin_flag=is_admin(message.from_user.username)))
            else:
                await message.answer(f"❌ Instagram xatosi: {res.get('message')}", reply_markup=menu_button())
        else:
            await message.answer("❌ Noto'g'ri javob. CAPTCHA xatosi.", reply_markup=menu_button())
        await state.clear()
    except Exception as e:
        logging.error(f"Captcha processing error: {e}", exc_info=True)
        await message.answer("CAPTCHAni qayta ishlashda xato yuz berdi.", reply_markup=menu_button())
        await state.clear()

@dp.message(UserStates.waiting_for_channel_to_add)
async def admin_channel_add(message: Message, state: FSMContext):
    try:
        if not is_admin(message.from_user.username):
            await message.answer("Siz admin emassiz.", reply_markup=menu_button())
            await state.clear()
            return
        ch = message.text.strip()
        if not ch.startswith("@"):
            await message.answer("Iltimos, kanal username'ini @ bilan kiriting (masalan, @kanal_nomi).", reply_markup=menu_button())
            return
        add_mandatory_channel(ch)
        await message.answer(f"✅ {ch} majburiy kanal sifatida qo'shildi.", reply_markup=admin_panel_menu())
        await state.clear()
        log_action("Admin add channel", message.from_user.id)
    except Exception as e:
        logging.error(f"Admin add channel error: {e}", exc_info=True)
        await message.answer("Kanal qo'shishda xatolik yuz berdi.", reply_markup=menu_button())
        await state.clear()

@dp.message(UserStates.waiting_for_channel_to_remove)
async def admin_channel_remove(message: Message, state: FSMContext):
    try:
        if not is_admin(message.from_user.username):
            await message.answer("Siz admin emassiz.", reply_markup=menu_button())
            await state.clear()
            return
        ch = message.text.strip()
        if not ch.startswith("@"):
            await message.answer("Iltimos, kanal username'ini @ bilan kiriting (masalan, @kanal_nomi).", reply_markup=menu_button())
            return
        remove_mandatory_channel(ch)
        await message.answer(f"✅ {ch} majburiy kanallardan olib tashlandi.", reply_markup=admin_panel_menu())
        await state.clear()
        log_action("Admin remove channel", message.from_user.id)
    except Exception as e:
        logging.error(f"Admin remove channel error: {e}", exc_info=True)
        await message.answer("Kanal o'chirishda xatolik yuz berdi.", reply_markup=menu_button())
        await state.clear()

@dp.message(UserStates.waiting_for_group_to_add)
async def admin_group_add(message: Message, state: FSMContext):
    try:
        if not is_admin(message.from_user.username):
            await message.answer("Siz admin emassiz.", reply_markup=menu_button())
            await state.clear()
            return
        g = message.text.strip()
        if not (g.startswith("@") or g.startswith("-")):
            await message.answer("Iltimos, guruh ID'sini yoki @username'ni kiriting (masalan, @guruh_nomi yoki -123456789).", reply_markup=menu_button())
            return
        add_reklama_group(g)
        await message.answer(f"✅ {g} reklama guruhi sifatida qo'shildi.", reply_markup=admin_panel_menu())
        await state.clear()
        log_action("Admin add group", message.from_user.id)
    except Exception as e:
        logging.error(f"Admin add group error: {e}", exc_info=True)
        await message.answer("Guruh qo'shishda xatolik yuz berdi.", reply_markup=menu_button())
        await state.clear()

@dp.message(UserStates.waiting_for_group_to_remove)
async def admin_group_remove(message: Message, state: FSMContext):
    try:
        if not is_admin(message.from_user.username):
            await message.answer("Siz admin emassiz.", reply_markup=menu_button())
            await state.clear()
            return
        g = message.text.strip()
        if not (g.startswith("@") or g.startswith("-")):
            await message.answer("Iltimos, guruh ID'sini yoki @username'ni kiriting (masalan, @guruh_nomi yoki -123456789).", reply_markup=menu_button())
            return
        remove_reklama_group(g)
        await message.answer(f"✅ {g} reklama guruhidan olib tashlandi.", reply_markup=admin_panel_menu())
        await state.clear()
        log_action("Admin remove group", message.from_user.id)
    except Exception as e:
        logging.error(f"Admin remove group error: {e}", exc_info=True)
        await message.answer("Guruh o'chirishda xatolik yuz berdi.", reply_markup=menu_button())
        await state.clear()

@dp.message()
async def catch_all(message: Message, state: FSMContext):
    try:
        text = message.text or ""
        if text.startswith("/start"):
            await message.answer("👋 Xush kelibsiz! Menyuni ochish uchun 📋 Menyu tugmasini bosing.", reply_markup=menu_button())
            return
        await message.answer("Iltimos, 📋 Menyu tugmasini bosing:", reply_markup=menu_button())
    except Exception as e:
        logging.error(f"Catch all error: {e}", exc_info=True)
        await message.answer("Xatolik yuz berdi. Menyuni ochish uchun 📋 Menyu tugmasini bosing.", reply_markup=menu_button())

# -----------------------
# Utility: check_subscription
# -----------------------
async def check_subscription(user_id: int, channel: str, bot_obj: Bot) -> bool:
    try:
        member = await bot_obj.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.debug(f"check_subscription error for {channel}: {e}")
        return False

# -----------------------
# Run
# -----------------------
async def main():
    try:
        dp.message.middleware(RateLimitMiddleware())
        logging.info("Bot polling starting...")
        await dp.start_polling(bot, on_startup=on_startup)
    except Exception as e:
        logging.error(f"Bot failed to start: {str(e)}", exc_info=True)
        print(f"Error: Bot failed to start: {str(e)}. Please check TELEGRAM_TOKEN.")

if __name__ == "__main__":
    asyncio.run(main())
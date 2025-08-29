# bot.py
import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# -----------------------
# CONFIG - O'ZGARTIRING
# -----------------------
TELEGRAM_TOKEN = "8236696657:AAEAAMIz2peAuLvXzgMvzcJT2GEt1SRyDOA"  # siz bergan token
ADMIN_USERNAME = "serinaqu"  # adminning Telegram username (without @) — log/ma'lumotlar shu usernamega yuboriladi
# Agar adminning numeric chat_id ma'lum bo'lsa qo'shing (ustunlik bilan ishlaydi)
ADMIN_CHAT_ID: Optional[int] = None  # misol: 123456789  (yozmasangiz username ishlatiladi)

DB_FILE = "security_bot.db"
LOG_FILE = "bot_security.log"

# Bad words va blacklist domenlar — o'z ehtiyojingizga ko'ra to'ldiring
BAD_WORDS = {"nojoya1", "nojoya2", "nojoya3"}
BLACKLISTED_DOMAINS = {"badsite.com", "spam.example"}

# Verification timeout (sekundlarda) — foydalanuvchi shu muddat ichida verify qilmasa, kick qilinadi
VERIFICATION_TIMEOUT = 10 * 60  # 10 daqiqa

# -----------------------
# Logging
# -----------------------
logging.basicConfig(
    level=logging.INFO,
    filename=LOG_FILE,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# -----------------------
# DB helpers (sync sqlite)
# -----------------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        telefon TEXT,
        ism TEXT,
        familiya TEXT,
        yosh INTEGER,
        join_date TEXT,
        verified INTEGER DEFAULT 0,
        verified_at TEXT
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        user_id INTEGER,
        username TEXT,
        chat_id INTEGER,
        message_text TEXT,
        deleted INTEGER DEFAULT 0,
        reason TEXT
    )
    """
    )
    conn.commit()
    conn.close()
    logger.info("Initialized DB")


def save_contact(user_id: int, username: Optional[str], phone: Optional[str]):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
    INSERT INTO users (user_id, username, telefon, join_date)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, telefon=excluded.telefon
    """,
        (user_id, username, phone, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    logger.info(f"Saved contact for {user_id}")


def update_user_field(user_id: int, field: str, value):
    if field not in {"ism", "familiya", "manzil", "username", "telefon", "yosh", "verified", "verified_at"}:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if field == "yosh":
        try:
            value = int(value)
        except Exception:
            value = None
    c.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()
    logger.info(f"Updated {field} for {user_id}: {value}")


def mark_verified(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET verified = 1, verified_at = ? WHERE user_id = ?",
        (datetime.utcnow().isoformat(), user_id),
    )
    conn.commit()
    conn.close()
    logger.info(f"Marked verified: {user_id}")


def log_message(user_id: int, username: Optional[str], chat_id: int, text: str, deleted: int = 0, reason: Optional[str] = None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO logs (ts, user_id, username, chat_id, message_text, deleted, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), user_id, username, chat_id, text, deleted, reason),
    )
    conn.commit()
    conn.close()
    logger.debug(f"Logged msg from {user_id} in chat {chat_id} deleted={deleted} reason={reason}")


def get_stats_text() -> str:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    c.execute("SELECT user_id, username, ism, familiya, telefon, yosh, verified FROM users ORDER BY join_date DESC LIMIT 200")
    rows = c.fetchall()
    conn.close()
    text = f"📊 Foydalanuvchilar soni: {total}\n\nRo'yxat (so'ngilar 200):\n"
    for r in rows:
        uid, uname, ism, fam, tel, yosh, ver = r
        text += f"ID:{uid} | @{uname or '-'} | {ism or '-'} {fam or '-'} | tel:{tel or '-'} | yosh:{yosh or '-'} | ver:{'✅' if ver else '❌'}\n"
    return text


# -----------------------
# Utilities
# -----------------------
URL_REGEX = re.compile(r"(https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)


def contains_bad_word(text: str) -> Optional[str]:
    low = text.lower()
    for w in BAD_WORDS:
        if w in low:
            return w
    return None


def contains_blacklisted_domain(text: str) -> Optional[str]:
    for m in URL_REGEX.finditer(text):
        url = m.group(0).lower()
        for bad in BLACKLISTED_DOMAINS:
            if bad in url:
                return bad
    return None


# -----------------------
# In-memory structures
# -----------------------
# pending_verification: user_id -> {chat_id, group_id, expires_at, task (asyncio.Task)}
pending_verification: dict[int, dict] = {}

# pending_field for interactive collection: user_id -> field name (ism/familiya/yosh)
pending_field: dict[int, str] = {}

# -----------------------
# Bot setup
# -----------------------
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Helper to send admin log (via numeric id if set, otherwise via @username)
async def send_admin_log(text: str):
    try:
        if ADMIN_CHAT_ID:
            await bot.send_message(ADMIN_CHAT_ID, text)
        else:
            # use username
            await bot.send_message(f"@{ADMIN_USERNAME}", text)
    except Exception as e:
        logger.warning(f"Couldn't send admin log: {e}")


# -----------------------
# Handlers
# -----------------------

# /start - user starts bot (or when DM from bot)
@dp.message.register(Command("start"))
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton("📲 Telefon raqamini yuborish", request_contact=True))
    await message.answer(
        "Salom! Guruhga qo'shilish uchun telefon raqamingizni yuboring (telefonni yuboring).",
        reply_markup=kb,
    )
    logger.info(f"/start by {message.from_user.id}")


# Contact handler: user shares phone
@dp.message.register(lambda m: m.contact is not None)
async def contact_handler(message: types.Message):
    contact = message.contact
    user = message.from_user
    phone = contact.phone_number
    save_contact(user.id, user.username, phone)

    # Inline keyboard to gather ism/familiya/yosh
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("Ismni kiritish", callback_data="fill_ism")],
        [InlineKeyboardButton("Familiyani kiritish", callback_data="fill_familiya")],
        [InlineKeyboardButton("Yoshni kiritish", callback_data="fill_yosh")],
        [InlineKeyboardButton("✅ Tasdiqlash va tugatish", callback_data="finish_verify")]
    ])
    await message.answer("✅ Telefon saqlandi. Endi boshqa ma'lumotlarni kiriting (inline tugmalar yordamida):", reply_markup=kb)

    # if user was pending verification (keldi va yubordi) mark partially saved
    if user.id in pending_verification:
        pending_verification[user.id]["has_phone"] = True
        await message.answer("Siz telefon yubordingiz. Iltimos boshqa maydonlarni to'ldiring yoki tasdiqlang.")


# Inline callback to set which field user will fill next or finish verification
@dp.callback_query.register(lambda c: c.data and (c.data.startswith("fill_") or c.data == "finish_verify"))
async def callback_fill(cq: types.CallbackQuery):
    user = cq.from_user
    data = cq.data
    if data.startswith("fill_"):
        field = data.split("_", 1)[1]  # ism/familiya/yosh
        pending_field[user.id] = field
        await cq.message.answer(f"Iltimos { 'ism' if field=='ism' else ('familiya' if field=='familiya' else 'yosh') } yozing:")
        await cq.answer()
        return
    elif data == "finish_verify":
        # Mark verified if minimal data present (phone at least)
        # Check DB if phone exists
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT telefon FROM users WHERE user_id = ?", (user.id,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            mark_verified(user.id)
            # cancel timeout task
            if user.id in pending_verification:
                task = pending_verification[user.id].get("task")
                if task:
                    task.cancel()
                pending_verification.pop(user.id, None)
            await cq.message.answer("✅ Siz tasdiqlandingiz. Guruhga kirishingiz normal davom etadi.")
            await send_admin_log(f"User verified: ID:{user.id} @{user.username or '-'}")
            await cq.answer("Tashakkur.")
        else:
            await cq.answer("Iltimos avval telefon yuboring.", show_alert=True)


# Catch plain text messages (either filling fields or normal group messages)
@dp.message.register()
async def handle_message(message: types.Message):
    user = message.from_user
    text = (message.text or "").strip()
    chat_id = message.chat.id
    username = user.username

    # If user is filling a pending field:
    if user.id in pending_field and text:
        field = pending_field.pop(user.id)
        if field == "ism":
            update_user_field(user.id, "ism", text)
            await message.answer("✅ Ism saqlandi.")
        elif field == "familiya":
            update_user_field(user.id, "familiya", text)
            await message.answer("✅ Familiya saqlandi.")
        elif field == "yosh":
            # validate yosh numeric
            try:
                y = int(text)
                update_user_field(user.id, "yosh", y)
                await message.answer("✅ Yosh saqlandi.")
            except Exception:
                await message.answer("Iltimos yoshni raqam bilan kiriting.")
                return
        # log and reward small trust (ball system can be implemented)
        log_message(user.id, username, chat_id, f"Filled field {field}: {text}", deleted=0)
        return

    # Otherwise, normal message — if in group, log and check
    # Log message
    log_message(user.id, username, chat_id, text, deleted=0)

    # Only process textual content
    if not text:
        return

    # Check for bad words
    bad = contains_bad_word(text)
    if bad:
        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"Couldn't delete message: {e}")
        log_message(user.id, username, chat_id, text, deleted=1, reason=f"bad_word:{bad}")
        await message.reply(f"{message.from_user.first_name}, nojo'ya so'z ishlatdingiz: `{bad}`. Xabar o'chirildi.", parse_mode="Markdown")
        # notify admin
        await send_admin_log(f"Bad word detected: user={user.id}@{username or '-'} word={bad} chat={chat_id} text={text[:200]}")
        return

    # Check for URLs and blacklisted domains
    if URL_REGEX.search(text):
        bad_dom = contains_blacklisted_domain(text)
        if bad_dom:
            try:
                await message.delete()
            except Exception as e:
                logger.warning(f"Couldn't delete message w/ bad dom: {e}")
            log_message(user.id, username, chat_id, text, deleted=1, reason=f"bad_domain:{bad_dom}")
            await message.reply(f"{message.from_user.first_name}, xavfli yoki qora ro‘yxatdagi domen: `{bad_dom}`. Xabar o'chirildi.", parse_mode="Markdown")
            await send_admin_log(f"Blacklisted domain posted: user={user.id}@{username or '-'} domain={bad_dom} chat={chat_id} text={text[:200]}")
            return
        else:
            # not blacklisted, but is link — warn (and log)
            await message.reply("E'tibor: havola joylatdingiz. Iltimos reklama va zararli havolalardan saqlaning.")
            log_message(user.id, username, chat_id, text, deleted=0, reason="link_warn")
            await send_admin_log(f"User posted link (not blacklisted): user={user.id}@{username or '-'} chat={chat_id} text={text[:200]}")
            return

    # otherwise normal message — just log
    return


# Chat member updates — when someone joins group or promoted/left
@dp.chat_member.register()
async def chat_member_update(update: types.ChatMemberUpdated):
    try:
        old = update.old_chat_member
        new = update.new_chat_member
        chat = update.chat
        # We care about new members who became "member"
        if new.status == "member":
            user = new.user
            # Send DM: ask to /start (or send contact)
            try:
                # First create pending entry to enforce verification
                # We'll store group chat id so we know which group they joined
                if user.id in pending_verification:
                    # already pending; reset timer
                    task = pending_verification[user.id].get("task")
                    if task:
                        task.cancel()
                pending_verification[user.id] = {
                    "group_id": chat.id,
                    "chat_title": chat.title or str(chat.id),
                    "joined_at": datetime.utcnow(),
                    "has_phone": False,
                    "task": None,
                }

                # schedule timeout to kick if not verified
                async def verification_timeout(uid: int):
                    try:
                        await asyncio.sleep(VERIFICATION_TIMEOUT)
                        # if still pending and not verified -> kick
                        conn = sqlite3.connect(DB_FILE)
                        c = conn.cursor()
                        c.execute("SELECT verified FROM users WHERE user_id = ?", (uid,))
                        row = c.fetchone()
                        conn.close()
                        verified = bool(row and row[0])
                        if not verified and uid in pending_verification:
                            group_id = pending_verification[uid]["group_id"]
                            # Attempt kick
                            try:
                                await bot.ban_chat_member(group_id, uid)
                                # unban to allow permanent removal? Keep banned state
                            except Exception as e:
                                logger.warning(f"Failed to ban user {uid} after timeout: {e}")
                            await send_admin_log(f"User {uid} was not verified in time and was banned from group {group_id}.")
                            pending_verification.pop(uid, None)
                    except asyncio.CancelledError:
                        # canceled because user verified earlier
                        return
                    except Exception as e:
                        logger.exception("verification timeout error")

                task = asyncio.create_task(verification_timeout(user.id))
                pending_verification[user.id]["task"] = task

                # Try send private message with /start flow
                kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                kb.add(KeyboardButton("📲 Telefon raqamini yuborish", request_contact=True))
                # Also mention group and reason
                dm_text = (
                    f"Salom {user.full_name}!\n"
                    f"Siz *{chat.title or 'guruh'}* ga qo‘shildingiz.\n\n"
                    "Guruh xavfsizligini ta'minlash uchun, iltimos quyidagilarni yuboring:\n"
                    "1) Telefon raqam (Kontakt yuboring)\n"
                    "2) Ismingiz va familiyangiz (inline tugmalar orqali)\n"
                    "3) Yosh (inline tugma orqali)\n\n"
                    f"Iltimos {VERIFICATION_TIMEOUT//60} daqiqa ichida bajarishingiz kerak. Aks holda adminlar avtomatik chetlatishi mumkin."
                )
                await bot.send_message(user.id, dm_text, reply_markup=kb, parse_mode="Markdown")
                await send_admin_log(f"New member {user.id}@{user.username or '-'} joined group {chat.title or chat.id}. DM sent for verification.")
            except Exception as e:
                # if bot can't DM - log and notify admin (user privacy settings)
                logger.warning(f"Couldn't DM new member {user.id}: {e}")
                await send_admin_log(f"Couldn't DM new member {user.id}@{user.username or '-'} who joined {chat.title or chat.id}. They might not receive verification dm.")
        else:
            # other status changes not handled specifically
            return
    except Exception as e:
        logger.exception("chat_member update error")


# Admin commands: /stats, /ban, /logs
@dp.message.register(Command("stats"))
async def cmd_stats(message: types.Message):
    # Only allow admins: either username matches ADMIN_USERNAME or they are group admin in chat
    if not (message.from_user.username and message.from_user.username.lower() == ADMIN_USERNAME.lower()):
        # also allow if user is chat admin
        try:
            member = await bot.get_chat_member(message.chat.id, message.from_user.id)
            if member.is_chat_admin():
                pass
            else:
                await message.reply("❌ Siz admin emassiz.")
                return
        except Exception:
            await message.reply("❌ Siz admin emassiz.")
            return
    text = get_stats_text()
    MAX = 4000
    for i in range(0, len(text), MAX):
        await message.answer(text[i : i + MAX])


@dp.message.register(Command("ban"))
async def cmd_ban(message: types.Message):
    # usage: /ban <user_id>
    if not (message.from_user.username and message.from_user.username.lower() == ADMIN_USERNAME.lower()):
        # also allow group admins
        try:
            member = await bot.get_chat_member(message.chat.id, message.from_user.id)
            if not member.is_chat_admin():
                await message.reply("❌ Siz admin emassiz.")
                return
        except Exception:
            await message.reply("❌ Siz admin emassiz.")
            return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.reply("Foydalanish: /ban <user_id>")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.reply("Iltimos to'g'ri user_id kiriting.")
        return
    try:
        await bot.ban_chat_member(message.chat.id, uid)
        await message.reply(f"✅ Foydalanuvchi {uid} guruhdan bloklandi.")
        await send_admin_log(f"Admin {message.from_user.id} banned {uid} in chat {message.chat.id}")
    except Exception as e:
        await message.reply(f"Xatolik: {e}")


@dp.message.register(Command("logs"))
async def cmd_logs(message: types.Message):
    # Only admin can request logs
    if not (message.from_user.username and message.from_user.username.lower() == ADMIN_USERNAME.lower()):
        await message.reply("❌ Siz admin emassiz.")
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT ts, user_id, username, chat_id, message_text, reason FROM logs ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    text = "🔍 Oxirgi 100 log:\n"
    for r in rows:
        ts, uid, uname, chatid, msg, reason = r
        text += f"{ts} | {uid}@{uname or '-'} | chat:{chatid} | reason:{reason or '-'}\n{(msg or '')[:200]}\n\n"
    MAX = 4000
    for i in range(0, len(text), MAX):
        await message.answer(text[i : i + MAX])


# -----------------------
# Startup / shutdown
# -----------------------
async def on_startup():
    init_db()
    logger.info("Bot started")
    # optional: notify admin bot started
    await send_admin_log("Security bot started.")


async def on_shutdown():
    await bot.close()
    logger.info("Bot stopped")


async def main():
    await on_startup()
    try:
        # start long polling
        await dp.start_polling(bot)
    finally:
        await on_shutdown()


if __name__ == "__main__":
    asyncio.run(main())

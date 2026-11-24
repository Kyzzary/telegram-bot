"""
Telegram image index bot — consent-based starter project.

Install:
    pip install -r requirements.txt

Create a Telethon session (run once on a trusted computer/terminal):
    python main.py make-session
Then save the printed session string as TELEGRAM_SESSION in your host's Secrets.

Required environment secrets:
    BOT_TOKEN          BotFather token
    API_ID             Telegram API ID (my.telegram.org)
    API_HASH           Telegram API hash (my.telegram.org)
    TELEGRAM_SESSION   Telethon StringSession created with "make-session"
    OWNER_ID           Your numeric Telegram user ID
    ALLOWED_CHAT_IDS   Comma-separated chat IDs the account owner is allowed to index

Important:
- Only index chats you own or have explicit permission to process.
- A person must privately opt in with /consent before their images are indexed.
- Only the bot owner can search or delete indexed entries.
- Never publish BOT_TOKEN, API_HASH, TELEGRAM_SESSION, or the database.
"""

import asyncio
import logging
import os
import sys
from io import BytesIO

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession
import aiosqlite

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("image-index-bot")

DB_PATH = os.getenv("DB_PATH", "image_index.db")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


def parse_chat_ids() -> list[int]:
    raw = os.getenv("ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return []
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS consent (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                consented_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                saved_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, message_id)
            )
        """)
        await db.commit()


async def is_consented(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM consent WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def consent_user(user_id: int, username: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO consent(user_id, username) VALUES(?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username""",
            (user_id, username),
        )
        await db.commit()


async def withdraw_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM consent WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM images WHERE user_id = ?", (user_id,))
        await db.commit()


async def save_image(user_id: int, username: str | None,
                     chat_id: int, message_id: int, file_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO images
               (user_id, username, chat_id, message_id, file_id)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, username, chat_id, message_id, file_id),
        )
        await db.commit()


async def consent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "Открой личный чат с ботом и отправь /consent."
        )
        return
    await consent_user(user.id, user.username)
    await update.effective_message.reply_text(
        "Согласие сохранено. Изображения из разрешённых чатов будут "
        "индексироваться только пока действует это согласие. "
        "Чтобы удалить свои записи, отправь /withdraw."
    )


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    await withdraw_user(user.id)
    await update.effective_message.reply_text(
        "Согласие отозвано, твои индексированные записи удалены."
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Бот индексирует изображения только из заранее разрешённых чатов "
        "и только от пользователей, которые дали согласие в личном чате.\n\n"
        "/consent — дать согласие\n"
        "/withdraw — отозвать согласие и удалить свои записи\n"
        "/help — справка"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


def owner_only(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == int(OWNER_ID))


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_only(update):
        await update.effective_message.reply_text("Команда доступна только владельцу бота.")
        return
    if not context.args:
        await update.effective_message.reply_text("Использование: /search Telegram_ID_или_username")
        return

    term = context.args[0].lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        if term.isdigit():
            query = "SELECT id, user_id, username, file_id FROM images WHERE user_id=? ORDER BY id DESC LIMIT 10"
            params = (int(term),)
        else:
            query = "SELECT id, user_id, username, file_id FROM images WHERE lower(username)=lower(?) ORDER BY id DESC LIMIT 10"
            params = (term,)
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.effective_message.reply_text("Совпадений среди пользователей, давших согласие, нет.")
        return

    for image_id, user_id, username, file_path in rows:
        try:
            with open(file_path, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=photo,
                    caption=f"Запись #{image_id} · ID {user_id} · @{username or 'нет username'}",
                )
        except Exception:
            await update.effective_message.reply_text(
                f"Запись #{image_id}: файл изображения недоступен на сервере."
            )


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_only(update):
        await update.effective_message.reply_text("Команда доступна только владельцу бота.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Использование: /delete ID_записи")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM images WHERE id = ?", (int(context.args[0]),))
        await db.commit()
    await update.effective_message.reply_text("Если такая запись была, она удалена.")


async def scan_loop(app: Application):
    """Scan only explicitly allowlisted chats; index only opted-in senders."""
    client = app.bot_data["user_client"]
    chat_ids = app.bot_data["allowed_chat_ids"]

    while True:
        for chat_id in chat_ids:
            try:
                async for message in client.iter_messages(chat_id, limit=100):
                    if not message or not message.photo or not message.sender_id:
                        continue
                    sender = await client.get_entity(message.sender_id)
                    username = getattr(sender, "username", None)
                    if not await is_consented(message.sender_id):
                        continue
                    # Save the image to the bot's private data directory.
                    os.makedirs("indexed_images", exist_ok=True)
                    file_path = os.path.join(
                        "indexed_images", f"{chat_id}_{message.id}.jpg"
                    )
                    if not os.path.exists(file_path):
                        downloaded = await client.download_media(message, file=file_path)
                        if not downloaded:
                            continue
                    await save_image(
                        message.sender_id, username, chat_id, message.id, file_path
                    )
            except Exception:
                log.exception("Не удалось просканировать разрешённый чат %s", chat_id)
        await asyncio.sleep(SCAN_INTERVAL)


async def post_init(app: Application):
    await init_db()
    api_id = int(required_env("API_ID"))
    api_hash = required_env("API_HASH")
    session = required_env("TELEGRAM_SESSION")
    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TELEGRAM_SESSION не авторизована. Создай её командой make-session.")
    app.bot_data["user_client"] = client
    app.bot_data["allowed_chat_ids"] = parse_chat_ids()
    app.create_task(scan_loop(app))


async def post_shutdown(app: Application):
    client = app.bot_data.get("user_client")
    if client:
        await client.disconnect()


def make_session():
    """Interactive one-time helper to create a Telethon StringSession."""
    from telethon.sync import TelegramClient as SyncTelegramClient
    api_id = int(required_env("API_ID"))
    api_hash = required_env("API_HASH")
    with SyncTelegramClient(StringSession(), api_id, api_hash) as client:
        print("\nСкопируй строку ниже в секрет TELEGRAM_SESSION на своём хостинге.")
        print("Не отправляй её никому и не публикуй в GitHub.\n")
        print(client.session.save())


def main():
    global OWNER_ID
    if len(sys.argv) > 1 and sys.argv[1] == "make-session":
        make_session()
        return

    OWNER_ID = int(required_env("OWNER_ID"))
    token = required_env("BOT_TOKEN")
    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("consent", consent_command))
    app.add_handler(CommandHandler("withdraw", withdraw_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("delete", delete_command))
    log.info("Бот запускается")
    app.run_polling()


if __name__ == "__main__":
    main()

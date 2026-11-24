import os
import re
import logging
from typing import List, Dict

from ddgs import DDGS
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_RESULTS_PER_QUERY = 5
MAX_MESSAGE_LENGTH = 3900

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# This bot searches only publicly indexed web pages.
# It does not log in to Telegram accounts, access private chats,
# query leaked databases, or collect phone numbers/home addresses.

def clean_text(value: str) -> str:
    value = re.sub(r"https?://\S+", "", value or "")
    value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email скрыт]", value)
    # Redact likely phone-number sequences in snippets.
    value = re.sub(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)", "[номер скрыт]", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:450]


def search_public_web(query: str) -> List[Dict[str, str]]:
    # Keep the search focused on public profiles, publications and mentions.
    queries = [
        f'"{query}" (публичный профиль OR публикация OR интервью OR упоминание)',
        f'site:t.me "{query}"',
    ]
    found = []
    seen = set()

    with DDGS() as ddgs:
        for search_query in queries:
            try:
                results = ddgs.text(search_query, max_results=MAX_RESULTS_PER_QUERY)
                for item in results or []:
                    url = (item.get("href") or "").strip()
                    title = clean_text(item.get("title") or "Без названия")
                    body = clean_text(item.get("body") or "")
                    if not url or url in seen:
                        continue
                    if not url.startswith(("https://", "http://")):
                        continue
                    seen.add(url)
                    found.append({"title": title, "body": body, "url": url})
            except Exception:
                logger.exception("Search provider error")
    return found


def format_results(query: str, results: List[Dict[str, str]]) -> str:
    if not results:
        return (
            f'По запросу «{query}» ничего не найдено в доступных публичных '
            "результатах. Попробуй другое написание или добавь username."
        )

    lines = [
        f'Публичные результаты по запросу: {query}',
        "",
        "Это совпадения из открыто индексируемых страниц, а не подтверждение личности.",
        "Проверяй каждый источник самостоятельно.",
        "",
    ]
    for i, item in enumerate(results[:10], 1):
        lines.append(f'{i}. {item["title"]}')
        if item["body"]:
            lines.append(item["body"])
        lines.append(item["url"])
        lines.append("")

    text = "\n".join(lines)
    return text[:MAX_MESSAGE_LENGTH]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я ищу совпадения только в открыто индексируемых источниках.\n\n"
        "Отправь имя и фамилию, username или оба вместе.\n"
        "Пример: Иван Иванов @ivan\n\n"
        "Я показываю ссылки на публичные публикации и упоминания. "
        "Я не ищу частные номера, адреса, закрытые переписки или данные из утечек."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Использование:\n"
        "/search Иван Иванов @ivan\n\n"
        "Или просто отправь имя/username сообщением."
    )


async def run_search(update: Update, query: str) -> None:
    query = query.strip()
    if len(query) < 2:
        await update.message.reply_text("Напиши имя с фамилией или username.")
        return
    if len(query) > 100:
        await update.message.reply_text("Запрос слишком длинный. Ограничение — 100 символов.")
        return

    status = await update.message.reply_text("Ищу совпадения в открытых источниках…")
    try:
        results = search_public_web(query)
        response = format_results(query, results)
        await status.edit_text(response, disable_web_page_preview=True)
    except Exception:
        logger.exception("Search failed")
        await status.edit_text(
            "Не удалось выполнить поиск. Попробуй позже. "
            "Поисковый сервис может временно ограничивать запросы."
        )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args)
    await run_search(update, query)


async def text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_search(update, update.message.text)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_search))
    app.run_polling()


if __name__ == "__main__":
    main()

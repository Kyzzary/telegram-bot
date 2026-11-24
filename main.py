import os
import re
import logging
from typing import List, Dict
from urllib.parse import urlparse

from ddgs import DDGS
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_RESULTS_PER_QUERY = 8
MAX_MESSAGE_LENGTH = 3900

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def clean_text(value: str) -> str:
    value = re.sub(r"https?://\S+", "", value or "")
    value = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "[email скрыт]",
        value,
    )
    value = re.sub(
        r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)",
        "[номер скрыт]",
        value,
    )
    value = re.sub(r"\s+", " ", value).strip()
    return value[:400]


def is_telegram_public_url(url: str) -> bool:
    """Разрешаем только публичные страницы Telegram."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme in ("http", "https")
            and host in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
        )
    except Exception:
        return False


def build_telegram_queries(query: str) -> List[str]:
    query = query.strip()
    queries = [f'site:t.me "{query}"', f'site:telegram.me "{query}"']

    # Если пользователь прислал @username, дополнительно ищем без @.
    username = query.lstrip("@").strip()
    if query.startswith("@") and username:
        queries.append(f"site:t.me {username}")

    # Убираем повторы, сохраняя порядок.
    return list(dict.fromkeys(queries))


def search_telegram_only(query: str) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    seen = set()

    with DDGS() as ddgs:
        for search_query in build_telegram_queries(query):
            try:
                results = ddgs.text(
                    search_query,
                    max_results=MAX_RESULTS_PER_QUERY,
                )
                for item in results or []:
                    url = (item.get("href") or "").strip()

                    # Даже если поисковик вернул посторонний сайт,
                    # такой результат не попадёт пользователю.
                    if not is_telegram_public_url(url) or url in seen:
                        continue

                    seen.add(url)
                    found.append(
                        {
                            "title": clean_text(item.get("title") or "Публичная страница Telegram"),
                            "body": clean_text(item.get("body") or ""),
                            "url": url,
                        }
                    )
            except Exception:
                logger.exception("Telegram-only search provider error")

    return found


def format_results(query: str, results: List[Dict[str, str]]) -> str:
    if not results:
        return (
            f'В открыто индексируемых страницах Telegram по запросу «{query}» '
            "ничего не найдено.\n\n"
            "Попробуй другое написание имени или точный @username. "
            "Это не означает, что у человека нет Telegram."
        )

    lines = [
        f'Результаты только из публичных страниц Telegram по запросу: {query}',
        "",
        "Результаты могут быть неполными и не подтверждают личность человека.",
        "",
    ]

    for index, item in enumerate(results[:10], 1):
        lines.append(f'{index}. {item["title"]}')
        if item["body"]:
            lines.append(item["body"])
        lines.append(item["url"])
        lines.append("")

    return "\n".join(lines)[:MAX_MESSAGE_LENGTH]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я ищу только публичные страницы Telegram, доступные поисковикам.\n\n"
        "Отправь @username или имя и фамилию.\n"
        "Пример: @username\n"
        "Или: Иван Иванов\n\n"
        "Я не имею доступа к закрытым каналам, личным перепискам "
        "и скрытым данным аккаунтов."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Использование:\n"
        "/search @username\n"
        "/search Иван Иванов\n\n"
        "Поиск выполняется только по публичным страницам Telegram."
    )


async def run_search(update: Update, query: str) -> None:
    query = (query or "").strip()

    if len(query) < 2:
        await update.message.reply_text("Напиши @username или имя и фамилию.")
        return

    if len(query) > 100:
        await update.message.reply_text(
            "Запрос слишком длинный. Ограничение — 100 символов."
        )
        return

    status = await update.message.reply_text(
        "Ищу только публичные страницы Telegram…"
    )

    try:
        results = search_telegram_only(query)
        response = format_results(query, results)
        await status.edit_text(
            response,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Search failed")
        await status.edit_text(
            "Не удалось выполнить поиск в Telegram. "
            "Поисковый сервис может временно ограничивать запросы. "
            "Попробуй позже."
        )


async def search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await run_search(update, " ".join(context.args))


async def text_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await run_search(update, update.message.text)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_search)
    )
    app.run_polling()


if __name__ == "__main__":
    main()

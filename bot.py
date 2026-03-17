import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

from fetcher import fetch_news
from wp_client import create_draft

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
SEARCH_PROMPT = os.environ.get("SEARCH_PROMPT", "flight simulator training aviation technology 2026")


def allowed(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_CHAT_ID


def format_pub_date(value: str) -> str:
    if not value:
        return ""

    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%d.%b.%y")
        except ValueError:
            continue

    return value


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "✈ *NewsBot — авиационное тренажёростроение*\n\n"
        "/scan — найти свежие новости\n"
        "/prompt — показать текущий поисковый запрос\n"
        "/help — справка",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "*Команды бота:*\n\n"
        "/scan — запустить поиск новостей\n"
        "/prompt — показать поисковый запрос\n"
        "/start — главное меню\n\n"
        "После /scan выберите нужные новости кнопками и нажмите «Создать черновик в WordPress».",
        parse_mode="Markdown",
    )


async def cmd_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        f"🔍 Текущий поисковый запрос:\n`{SEARCH_PROMPT}`",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return

    msg = await update.message.reply_text("🔍 Ищем новости, подождите...")

    try:
        articles = fetch_news(SEARCH_PROMPT)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка поиска: {e}")
        return

    if not articles:
        await msg.edit_text("Новостей не найдено. Попробуйте позже.")
        return

    ctx.bot_data["articles"] = articles
    ctx.bot_data["selected"] = set()

    await msg.edit_text(
        f"✅ Найдено материалов: *{len(articles)}*\n\nВыберите нужные новости:",
        parse_mode="Markdown",
    )

    for i, art in enumerate(articles):
        pub_date = format_pub_date(art.get("date", ""))
        caption = (
            f"`{pub_date}`\n"
            f"*{art['title_ru']}*\n"
            f"{art['summary']}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Источник", url=art["url"])],
            [InlineKeyboardButton("☐ Выбрать", callback_data=f"select:{i}")],
        ])
        if art.get("image_url"):
            try:
                await update.message.reply_photo(
                    photo=art["image_url"],
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
                continue
            except Exception:
                pass

        await update.message.reply_text(caption, parse_mode="Markdown", reply_markup=keyboard)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Создать черновик в WordPress", callback_data="draft")]]
    )
    await update.message.reply_text("Выберите новости выше и нажмите кнопку:", reply_markup=keyboard)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    articles = ctx.bot_data.get("articles", [])
    selected: set = ctx.bot_data.get("selected", set())

    if data.startswith("select:"):
        idx = int(data.split(":")[1])
        if idx in selected:
            selected.discard(idx)
            label = "☐ Выбрать"
        else:
            selected.add(idx)
            label = "✓ Выбрано"

        ctx.bot_data["selected"] = selected
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Источник", url=articles[idx]["url"])],
            [InlineKeyboardButton(label, callback_data=f"select:{idx}")],
        ])
        await query.edit_message_reply_markup(reply_markup=keyboard)

    elif data == "draft":
        if not selected:
            await query.edit_message_text("⚠️ Сначала выберите хотя бы одну новость.")
            return

        chosen = [articles[i] for i in sorted(selected)]
        await query.edit_message_text(f"⏳ Создаём черновик из {len(chosen)} материалов...")

        try:
            result = create_draft(chosen)
            await query.edit_message_text(
                f"✅ *Черновик создан в WordPress*\n\n"
                f"📝 *{result['title']}*\n\n"
                f"🔗 [Открыть в WordPress]({result['edit_url']})",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    if isinstance(ctx.error, Conflict):
        log.warning("Telegram 409 Conflict: убедитесь, что в Railway запущен один инстанс бота")
        return
    log.exception("Unhandled bot error", exc_info=ctx.error)


def main():
    token = os.environ["TELEGRAM_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("prompt", cmd_prompt))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)

    log.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

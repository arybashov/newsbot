import asyncio
import logging
import os
from datetime import datetime
from io import BytesIO

import requests
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import Conflict
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

from fetcher import fetch_news_result, load_published, save_published
from wp_client import create_draft

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))
SEARCH_PROMPT = os.environ.get("SEARCH_PROMPT", "flight simulator training aviation technology 2026")
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}


def allowed(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat) and chat.id == ALLOWED_CHAT_ID


def format_pub_date(value: str) -> str:
    if not value:
        return ""

    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%d.%b.%y")
        except ValueError:
            continue

    return value


def download_image(url: str) -> BytesIO | None:
    if not url:
        return None

    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    content_type = resp.headers.get("Content-Type", "").lower()
    if not content_type.startswith("image/"):
        return None

    image = BytesIO(resp.content)
    extension = content_type.split("/")[-1].split(";")[0] or "jpg"
    if extension == "jpeg":
        extension = "jpg"
    image.name = f"article.{extension}"
    image.seek(0)
    return image


MAIN_MENU_TEXT = "✈ *NewsBot — авиационное тренажёростроение*\n\nВыберите действие:"
MAIN_MENU_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔍 Новые новости", callback_data="menu:scan")],
    [InlineKeyboardButton("🔄 Пересканировать", callback_data="menu:rescan")],
    [InlineKeyboardButton("🔎 Поисковый запрос", callback_data="menu:prompt")],
])


async def send_main_menu(update: Update):
    await update.message.reply_text(
        MAIN_MENU_TEXT,
        parse_mode="Markdown",
        reply_markup=MAIN_MENU_KEYBOARD,
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await send_main_menu(update)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await send_main_menu(update)


async def cmd_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        f"Текущий поисковый запрос:\n`{SEARCH_PROMPT}`",
        parse_mode="Markdown",
    )


async def run_scan(message, ctx: ContextTypes.DEFAULT_TYPE, force: bool):
    status_text = "Пересканируем новости, подождите..." if force else "Ищем новости, подождите..."
    msg = await message.reply_text(status_text)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(fetch_news_result, SEARCH_PROMPT, force),
            timeout=180,
        )
        articles = result["articles"]
    except asyncio.TimeoutError:
        await msg.edit_text("Поиск занял слишком долго. Попробуйте позже.")
        return
    except Exception as e:
        await msg.edit_text(f"Ошибка поиска: {e}")
        return

    if not articles:
        if result.get("status") == "seen" and not force:
            await msg.edit_text(
                "Новых новостей сейчас нет.\n\n"
                "Найденные материалы уже были показаны раньше. Попробуйте `/rescan`.",
                parse_mode="Markdown",
            )
            return

        await msg.edit_text("Новостей не найдено. Попробуйте позже.")
        return

    ctx.bot_data["articles"] = articles
    ctx.bot_data["selected"] = set()

    await msg.edit_text(
        f"Найдено материалов: *{len(articles)}*\n\nВыберите нужные новости:",
        parse_mode="Markdown",
    )

    published = load_published()
    for i, art in enumerate(articles):
        pub_date = format_pub_date(art.get("date", ""))
        title = art.get("title_ru") or art.get("title") or "Без заголовка"
        summary = art.get("summary") or art.get("description") or ""
        caption = f"`{pub_date}`\n*{title}*\n{summary}".strip()
        already = art["url"] in published
        channel_btn_label = "✅ Опубликовано" if already else "📢 В канал"
        channel_btn_data = f"noop:{i}" if already else f"publish:{i}"
        buttons = [
            [InlineKeyboardButton("Источник", url=art["url"])],
            [InlineKeyboardButton("☐ Выбрать", callback_data=f"select:{i}")],
        ]
        if CHANNEL_ID:
            buttons.append([InlineKeyboardButton(channel_btn_label, callback_data=channel_btn_data)])
        keyboard = InlineKeyboardMarkup(buttons)

        if art.get("image_url"):
            try:
                image_file = download_image(art["image_url"])
                if image_file is None:
                    raise ValueError("Image download failed")
                await message.reply_photo(
                    photo=InputFile(image_file),
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
                continue
            except Exception:
                pass

        await message.reply_text(caption, parse_mode="Markdown", reply_markup=keyboard)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Создать черновик в WordPress", callback_data="draft")]]
    )
    await message.reply_text("Выберите новости выше и нажмите кнопку:", reply_markup=keyboard)


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await run_scan(update.message, ctx, force=False)


async def cmd_rescan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await run_scan(update.message, ctx, force=True)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    articles = ctx.bot_data.get("articles", [])
    selected: set[int] = ctx.bot_data.get("selected", set())

    if data.startswith("menu:"):
        action = data.split(":")[1]
        await query.answer()
        if action == "scan":
            await query.edit_message_reply_markup(reply_markup=None)
            await run_scan(query.message, ctx, force=False)
        elif action == "rescan":
            await query.edit_message_reply_markup(reply_markup=None)
            await run_scan(query.message, ctx, force=True)
        elif action == "prompt":
            await query.edit_message_text(
                f"Текущий поисковый запрос:\n`{SEARCH_PROMPT}`\n\n{MAIN_MENU_TEXT}",
                parse_mode="Markdown",
                reply_markup=MAIN_MENU_KEYBOARD,
            )
        return

    if data.startswith("noop:"):
        await query.answer("Эта новость уже опубликована в канале.")
        return

    if data.startswith("select:"):
        idx = int(data.split(":")[1])
        if idx in selected:
            selected.discard(idx)
            label = "☐ Выбрать"
        else:
            selected.add(idx)
            label = "✓ Выбрано"

        ctx.bot_data["selected"] = selected
        published = load_published()
        already = articles[idx]["url"] in published
        buttons = [
            [InlineKeyboardButton("Источник", url=articles[idx]["url"])],
            [InlineKeyboardButton(label, callback_data=f"select:{idx}")],
        ]
        if CHANNEL_ID:
            ch_label = "✅ Опубликовано" if already else "📢 В канал"
            ch_data = f"noop:{idx}" if already else f"publish:{idx}"
            buttons.append([InlineKeyboardButton(ch_label, callback_data=ch_data)])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("publish:"):
        idx = int(data.split(":")[1])
        art = articles[idx]
        published = load_published()

        if art["url"] in published:
            await query.answer("Уже опубликовано в канале.", show_alert=True)
            return

        title = art.get("title_ru") or art.get("title") or "Без заголовка"
        post_caption = f"*{title}*\n\n{art['url']}"

        try:
            if art.get("image_url"):
                image_file = download_image(art["image_url"])
                if image_file:
                    await ctx.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=InputFile(image_file),
                        caption=post_caption,
                        parse_mode="Markdown",
                    )
                else:
                    await ctx.bot.send_message(
                        chat_id=CHANNEL_ID, text=post_caption, parse_mode="Markdown"
                    )
            else:
                await ctx.bot.send_message(
                    chat_id=CHANNEL_ID, text=post_caption, parse_mode="Markdown"
                )

            published.add(art["url"])
            save_published(published)
            await query.answer("✅ Опубликовано!")

            # Обновляем кнопку на "✅ Опубликовано"
            sel_label = "✓ Выбрано" if idx in selected else "☐ Выбрать"
            buttons = [
                [InlineKeyboardButton("Источник", url=art["url"])],
                [InlineKeyboardButton(sel_label, callback_data=f"select:{idx}")],
                [InlineKeyboardButton("✅ Опубликовано", callback_data=f"noop:{idx}")],
            ]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            await query.answer(f"Ошибка публикации: {e}", show_alert=True)
        return

    if data == "draft":
        if not selected:
            await query.edit_message_text("Сначала выберите хотя бы одну новость.")
            return

        chosen = [articles[i] for i in sorted(selected)]
        await query.edit_message_text(f"Создаём черновик из {len(chosen)} материалов...")

        try:
            result = create_draft(chosen)
            await query.edit_message_text(
                f"*Черновик создан в WordPress*\n\n"
                f"*{result['title']}*\n\n"
                f"[Открыть в WordPress]({result['edit_url']})",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            await query.edit_message_text(f"Ошибка: {e}")


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
    app.add_handler(CommandHandler("rescan", cmd_rescan))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)

    log.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes
)
from fetcher import fetch_news
from wp_client import create_draft

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
SEARCH_PROMPT   = os.environ.get("SEARCH_PROMPT", "flight simulator training aviation technology 2026")


def allowed(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_CHAT_ID


# /start
async def cmd_start(update: Update, ctx: ContextTypes):
    if not allowed(update):
        return
    await update.message.reply_text(
        "✈ *NewsBot — авиационное тренажоростроение*\n\n"
        "/scan — найти свежие новости\n"
        "/prompt — показать текущий поисковый запрос\n"
        "/help — справка",
        parse_mode="Markdown"
    )


# /help
async def cmd_help(update: Update, ctx: ContextTypes):
    if not allowed(update):
        return
    await update.message.reply_text(
        "*Команды бота:*\n\n"
        "/scan — запустить поиск новостей\n"
        "/prompt — показать поисковый запрос\n"
        "/start — главное меню\n\n"
        "После /scan выберите нужные новости кнопками "
        "и нажмите «→ В WordPress» для создания черновика.",
        parse_mode="Markdown"
    )


# /prompt
async def cmd_prompt(update: Update, ctx: ContextTypes):
    if not allowed(update):
        return
    await update.message.reply_text(
        f"🔍 Текущий поисковый запрос:\n`{SEARCH_PROMPT}`",
        parse_mode="Markdown"
    )


# /scan
async def cmd_scan(update: Update, ctx: ContextTypes):
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

    # Сохраняем статьи в контексте бота
    ctx.bot_data["articles"] = articles
    ctx.bot_data["selected"] = set()

    await msg.edit_text(
        f"✅ Найдено материалов: *{len(articles)}*\n\n"
        "Выберите нужные новости:",
        parse_mode="Markdown"
    )

    # Показываем каждую статью отдельным сообщением с кнопкой
    for i, art in enumerate(articles):
        text = (
            f"*{art['title_ru']}*\n"
            f"_{art['title']}_\n\n"
            f"{art['summary']}\n\n"
            f"📰 {art['source']}  ·  {art.get('date', '')}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("☐ Выбрать", callback_data=f"select:{i}")
        ]])
        await update.message.reply_text(text, parse_mode="Markdown",
                                        reply_markup=keyboard,
                                        disable_web_page_preview=True)

    # Кнопка отправки в конце
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("→ Создать черновик в WordPress", callback_data="draft")
    ]])
    await update.message.reply_text(
        "Выберите новости выше и нажмите кнопку:",
        reply_markup=keyboard
    )


# Обработка кнопок выбора
async def on_button(update: Update, ctx: ContextTypes):
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

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(label, callback_data=f"select:{idx}")
        ]])
        await query.edit_message_reply_markup(reply_markup=keyboard)

    elif data == "draft":
        selected = ctx.bot_data.get("selected", set())
        if not selected:
            await query.edit_message_text("⚠️ Сначала выберите хотя бы одну новость.")
            return

        chosen = [articles[i] for i in sorted(selected)]
        await query.edit_message_text(
            f"⏳ Создаём черновик из {len(chosen)} материалов..."
        )

        try:
            result = create_draft(chosen)
            await query.edit_message_text(
                f"✅ *Черновик создан в WordPress*\n\n"
                f"📝 *{result['title']}*\n\n"
                f"🔗 [Открыть в WordPress]({result['edit_url']})",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")


def main():
    token = os.environ["TELEGRAM_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("prompt", cmd_prompt))
    app.add_handler(CommandHandler("scan",   cmd_scan))
    app.add_handler(CallbackQueryHandler(on_button))

    log.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()

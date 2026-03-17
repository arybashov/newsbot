# NewsBot — Редакционный монитор

Telegram-бот для поиска новостей по авиационному тренажоростроению
и автоматического создания черновиков в WordPress.

## Быстрый старт

### 1. Получить токен бота
Написать `@BotFather` в Telegram → `/newbot` → скопировать токен.

### 2. Узнать свой chat_id
Написать боту `/start`, затем открыть:
`https://api.telegram.org/bot<TOKEN>/getUpdates`
Найти поле `"chat": {"id": ...}` — это и есть `ALLOWED_CHAT_ID`.

### 3. Настроить переменные окружения
```bash
cp .env.example .env
# Заполнить .env своими значениями
```

### 4. Запуск локально
```bash
pip install -r requirements.txt
python bot.py
```

## Деплой на Railway

1. Подключить GitHub-репозиторий в Railway.
2. Убедиться, что сервис запущен как worker с командой `python bot.py` (или через `Procfile`).
3. Добавить Variables:
   - `TELEGRAM_TOKEN`
   - `ALLOWED_CHAT_ID`
   - `GROQ_API_KEY`
   - (опционально) `GROQ_MODEL`, `SEARCH_PROMPT`, `WP_URL`, `WP_USER`, `WP_PASS`

Если видите `409 Conflict: terminated by other getUpdates request`, значит одновременно запущено больше одного инстанса бота.

## Команды

| Команда | Действие |
|---------|----------|
| `/start` | Главное меню |
| `/scan` | Найти свежие новости |
| `/prompt` | Показать поисковый запрос |
| `/help` | Справка |

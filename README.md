# NewsBot — Редакционный монитор

Telegram-бот для поиска новостей по авиационному тренажоростроению
и автоматического создания черновиков в WordPress.

## Быстрый старт

### 1. Получить токен бота
Написать `@BotFather` в Telegram → `/newbot` → скопировать токен.

### 2. Узнать свой chat_id
Написать боту `/start`, затем открыть:
`https://api.telegram.org/bot<TOKEN>/getUpdates`
Найти поле `"chat": {"id": ...}` — это и есть ALLOWED_CHAT_ID.

### 3. Настроить переменные окружения
```bash
cp .env.example .env
# Заполнить .env своими значениями
```

### 4. Установить зависимости и запустить
```bash
pip install -r requirements.txt
python bot.py
```

## Деплой на Render

1. Залить код на GitHub
2. Создать новый сервис: New → Background Worker → Python
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python bot.py`
5. В Environment Variables добавить все переменные из .env

## Подключение WordPress

Когда будет готов сайт:

1. В WordPress: Пользователи → Ваш профиль → Пароли приложений
2. Создать новый пароль, скопировать
3. Добавить в .env:
   - `WP_URL` — адрес сайта
   - `WP_USER` — логин WordPress
   - `WP_PASS` — пароль приложения (не основной пароль!)

До этого момента бот работает в stub-режиме — всё работает,
черновик генерируется, но в WP не отправляется.

## Команды бота

| Команда | Действие |
|---------|----------|
| `/start` | Главное меню |
| `/scan` | Найти свежие новости |
| `/prompt` | Показать поисковый запрос |
| `/help` | Справка |

## Структура проекта

```
newsbot/
├── bot.py          # Telegram-бот, команды и кнопки
├── fetcher.py      # Google News RSS + обогащение через Claude
├── wp_client.py    # Отправка черновика в WordPress
├── seen_urls.txt   # Дедупликация (создаётся автоматически)
├── requirements.txt
└── .env.example
```

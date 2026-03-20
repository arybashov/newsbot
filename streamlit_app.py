import os
from io import BytesIO

import requests as req
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from fetcher import fetch_news_result, load_published, save_published
from wp_client import create_draft

st.set_page_config(page_title="NewsBot", page_icon="✈", layout="wide")

# ── Auth ──────────────────────────────────────────────────────────────────────
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

if APP_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title("✈ NewsBot")
        pwd = st.text_input("Пароль", type="password")
        if st.button("Войти"):
            if pwd == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Неверный пароль")
        st.stop()

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_PROMPT = os.environ.get(
    "SEARCH_PROMPT", "flight simulator training aviation technology 2026"
)

if "search_prompt" not in st.session_state:
    st.session_state.search_prompt = DEFAULT_PROMPT
if "articles" not in st.session_state:
    st.session_state.articles = []
if "selected" not in st.session_state:
    st.session_state.selected = set()


# ── Helpers ───────────────────────────────────────────────────────────────────
def publish_to_channel(art: dict, published: set):
    CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
    TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    if not CHANNEL_ID or not TOKEN:
        st.error("CHANNEL_ID или TELEGRAM_TOKEN не заданы")
        return

    title = art.get("title_ru") or art.get("title") or "Без заголовка"
    caption = f"*{title}*\n\n{art['url']}"
    image_url = art.get("image_url", "")

    try:
        if image_url:
            img_resp = req.get(image_url, timeout=10)
            img_resp.raise_for_status()
            files = {"photo": BytesIO(img_resp.content)}
            req.post(
                f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "Markdown"},
                files=files,
                timeout=15,
            )
        else:
            req.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHANNEL_ID, "text": caption, "parse_mode": "Markdown"},
                timeout=15,
            )
        published.add(art["url"])
        save_published(published)
        st.success("Опубликовано в канал!")
    except Exception as e:
        st.error(f"Ошибка публикации: {e}")


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("✈ NewsBot")
    st.caption("Авиационное тренажёростроение")
    st.divider()

    st.session_state.search_prompt = st.text_area(
        "Поисковый запрос",
        value=st.session_state.search_prompt,
        height=100,
    )

    col1, col2 = st.columns(2)
    scan_clicked = col1.button("🔍 Сканировать", use_container_width=True)
    rescan_clicked = col2.button("🔄 Рескан", use_container_width=True)

    if st.session_state.articles:
        st.divider()
        if st.button("📝 Черновик WordPress", use_container_width=True):
            chosen = [st.session_state.articles[i] for i in sorted(st.session_state.selected)]
            if not chosen:
                st.warning("Выберите хотя бы одну статью")
            else:
                with st.spinner("Создаём черновик..."):
                    try:
                        result = create_draft(chosen)
                        st.success(f"Черновик создан: [{result['title']}]({result['edit_url']})")
                    except Exception as e:
                        st.error(f"Ошибка: {e}")

# ── Scan ──────────────────────────────────────────────────────────────────────
if scan_clicked or rescan_clicked:
    force = rescan_clicked
    with st.spinner("Ищем новости..."):
        result = fetch_news_result(st.session_state.search_prompt, force=force)

    articles = result.get("articles", [])
    status = result.get("status")

    if status == "seen" and not force:
        st.info("Новых новостей нет. Нажмите «Рескан» чтобы показать уже виденные.")
    elif not articles:
        st.warning(f"Новостей не найдено. Запрос: `{st.session_state.search_prompt}`")
    else:
        st.session_state.articles = articles
        st.session_state.selected = set()
        st.success(f"Найдено: {len(articles)} материалов")

# ── Articles ──────────────────────────────────────────────────────────────────
articles = st.session_state.articles
if articles:
    published = load_published()

    for i, art in enumerate(articles):
        title = art.get("title_ru") or art.get("title") or "Без заголовка"
        summary = art.get("summary") or art.get("description") or ""
        url = art.get("url", "")
        image_url = art.get("image_url", "")
        date = art.get("date", "")
        source = art.get("source", "")
        already_published = url in published
        is_selected = i in st.session_state.selected

        with st.container(border=True):
            cols = st.columns([1, 3])

            with cols[0]:
                if image_url:
                    st.image(image_url, use_container_width=True)
                else:
                    st.caption("Нет изображения")

            with cols[1]:
                meta = " · ".join(filter(None, [date, source]))
                if meta:
                    st.caption(meta)
                st.markdown(f"**{title}**")
                if summary:
                    st.write(summary)

                btn_cols = st.columns(3)

                with btn_cols[0]:
                    st.link_button("🔗 Источник", url, use_container_width=True)

                with btn_cols[1]:
                    label = "✓ Выбрано" if is_selected else "☐ Выбрать"
                    if st.button(label, key=f"sel_{i}", use_container_width=True):
                        if is_selected:
                            st.session_state.selected.discard(i)
                        else:
                            st.session_state.selected.add(i)
                        st.rerun()

                with btn_cols[2]:
                    if already_published:
                        st.button(
                            "✅ Опубликовано", key=f"pub_{i}",
                            disabled=True, use_container_width=True,
                        )
                    else:
                        if st.button("📢 В канал", key=f"pub_{i}", use_container_width=True):
                            publish_to_channel(art, published)
                            st.rerun()

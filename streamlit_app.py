import os
from io import BytesIO

import requests as req
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from fetcher import fetch_news_result, load_published, save_published
from wp_client import create_draft

st.set_page_config(page_title="Trenager News Scanner", page_icon="✈", layout="wide")

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@300;400&family=Lato:wght@400;700&display=swap');

html, body, [class*="css"] { font-family: 'Lato', sans-serif; }

/* ── Card ── */
.tn-card {
    background: #fff;
    border-bottom: 1px solid #DDDDDD;
    padding-bottom: 18px;
    margin-bottom: 18px;
}
.tn-card img {
    width: 100%;
    aspect-ratio: 16/9;
    object-fit: cover;
    border-radius: 3px;
    display: block;
}
.tn-card .no-img {
    width: 100%;
    aspect-ratio: 16/9;
    background: #f0f0f0;
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #B4B4BA;
    font-size: 13px;
}
.tn-badge {
    display: inline-block;
    background: #DD0D82;
    color: #fff;
    font-family: 'Oswald', sans-serif;
    font-size: 11px;
    font-weight: 300;
    letter-spacing: 2px;
    text-transform: uppercase;
    padding: 3px 8px 2px 8px;
    margin: 10px 0 6px 0;
}
.tn-title {
    font-family: 'Oswald', sans-serif;
    font-size: 18px;
    font-weight: 300;
    color: #29293A;
    line-height: 1.35;
    margin: 0 0 6px 0;
}
.tn-meta {
    font-family: 'Lato', sans-serif;
    font-size: 12px;
    color: #B4B4BA;
    margin-bottom: 8px;
}
.tn-excerpt {
    font-family: 'Lato', sans-serif;
    font-size: 13px;
    color: #5B5B60;
    line-height: 1.6;
    margin: 0;
}

/* ── Page header ── */
.tn-header {
    font-family: 'Oswald', sans-serif;
    font-size: 32px;
    font-weight: 300;
    color: #29293A;
    letter-spacing: 1px;
    border-bottom: 3px solid #DD0D82;
    padding-bottom: 8px;
    margin-bottom: 24px;
}

/* ── Article dialog ── */
.tn-art-img {
    width: 100%;
    border-radius: 3px;
    display: block;
    margin-bottom: 16px;
}
.tn-art-badge {
    display: inline-block;
    background: #DD0D82;
    color: #fff;
    font-family: 'Oswald', sans-serif;
    font-size: 12px;
    font-weight: 300;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    padding: 4px 10px 3px 10px;
    margin-bottom: 12px;
}
.tn-art-title {
    font-family: 'Oswald', sans-serif;
    font-size: 30px;
    font-weight: 300;
    color: #1c1c21;
    line-height: 1.25;
    margin: 0 0 10px 0;
}
.tn-art-meta {
    font-family: 'Lato', sans-serif;
    font-size: 13px;
    color: #B4B4BA;
    margin-bottom: 20px;
    padding-bottom: 16px;
    border-bottom: 1px solid #DDDDDD;
}
.tn-art-summary {
    font-family: 'Oswald', sans-serif;
    font-size: 14px;
    font-weight: 300;
    color: #5B5B60;
    line-height: 1.71;
    letter-spacing: 0.02em;
    margin-bottom: 16px;
}
.tn-art-body {
    font-family: 'Lato', sans-serif;
    font-size: 14px;
    color: #5B5B60;
    line-height: 1.71;
}
</style>
""", unsafe_allow_html=True)

# ── Auth ──────────────────────────────────────────────────────────────────────
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

if APP_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown('<div class="tn-header">✈ Trenager News Scanner</div>', unsafe_allow_html=True)
        pwd = st.text_input("Пароль", type="password")
        if st.button("Войти"):
            if pwd == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Неверный пароль")
        st.stop()

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_PROMPT = os.environ.get("SEARCH_PROMPT", "flight simulator training aviation technology 2026")

if "search_prompt" not in st.session_state:
    st.session_state.search_prompt = DEFAULT_PROMPT
if "articles" not in st.session_state:
    st.session_state.articles = []
if "selected" not in st.session_state:
    st.session_state.selected = set()


# ── Article dialog ────────────────────────────────────────────────────────────
@st.dialog("", width="large")
def show_article(art: dict):
    title = art.get("title_ru") or art.get("title") or "Без заголовка"
    source = art.get("source") or ""
    date = art.get("date") or ""
    meta = " · ".join(filter(None, [date, source]))
    summary = art.get("summary") or art.get("description") or ""
    content = art.get("content") or ""
    image_url = art.get("image_url") or ""
    url = art.get("url") or ""

    if image_url:
        st.markdown(f'<img class="tn-art-img" src="{image_url}" />', unsafe_allow_html=True)

    html = f"""
    <div class="tn-art-badge">{source or "News"}</div>
    <div class="tn-art-title">{title.replace("<", "&lt;")}</div>
    <div class="tn-art-meta">{meta}</div>
    """
    if summary:
        html += f'<div class="tn-art-summary">{summary.replace("<", "&lt;")}</div>'
    if content and content != summary:
        html += f'<div class="tn-art-body">{content.replace("<", "&lt;")}</div>'
    st.markdown(html, unsafe_allow_html=True)

    if url:
        st.markdown("---")
        st.link_button("🔗 Читать оригинал", url)


# ── Card HTML ─────────────────────────────────────────────────────────────────
def card_html(art: dict) -> str:
    title = (art.get("title_ru") or art.get("title") or "Без заголовка").replace("<", "&lt;")
    excerpt = (art.get("summary") or art.get("description") or "").replace("<", "&lt;")
    if len(excerpt) > 180:
        excerpt = excerpt[:180].rsplit(" ", 1)[0] + "…"
    image_url = art.get("image_url", "")
    source = (art.get("source") or "").replace("<", "&lt;")
    date = art.get("date", "")
    meta = " · ".join(filter(None, [date, source]))

    img_block = (
        f'<img src="{image_url}" />'
        if image_url
        else '<div class="no-img">Нет изображения</div>'
    )
    return f"""
    <div class="tn-card">
        {img_block}
        <div class="tn-badge">{source or "News"}</div>
        <div class="tn-title">{title}</div>
        <div class="tn-meta">{meta}</div>
        <div class="tn-excerpt">{excerpt}</div>
    </div>
    """


# ── Publish to Telegram channel ───────────────────────────────────────────────
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
            req.post(
                f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": BytesIO(img_resp.content)},
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
        st.success("Опубликовано!")
    except Exception as e:
        st.error(f"Ошибка: {e}")


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        '<div style="font-family:Oswald,sans-serif;font-size:22px;font-weight:300;color:#29293A;">✈ Trenager News</div>',
        unsafe_allow_html=True,
    )
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
        selected_count = len(st.session_state.selected)
        if st.button(
            f"📝 Черновик WordPress{f' ({selected_count})' if selected_count else ''}",
            use_container_width=True,
            disabled=selected_count == 0,
        ):
            chosen = [st.session_state.articles[i] for i in sorted(st.session_state.selected)]
            with st.spinner("Создаём черновик..."):
                try:
                    result = create_draft(chosen)
                    st.success(f"[{result['title']}]({result['edit_url']})")
                except Exception as e:
                    st.error(f"Ошибка: {e}")

# ── Scan ──────────────────────────────────────────────────────────────────────
st.markdown('<div class="tn-header">Лента новостей</div>', unsafe_allow_html=True)

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

# ── Grid ──────────────────────────────────────────────────────────────────────
articles = st.session_state.articles
if articles:
    published = load_published()
    cols = st.columns(3)

    for i, art in enumerate(articles):
        url = art.get("url", "")
        already_published = url in published
        is_selected = i in st.session_state.selected

        with cols[i % 3]:
            st.markdown(card_html(art), unsafe_allow_html=True)

            btn_cols = st.columns(3)
            with btn_cols[0]:
                if st.button("📖", key=f"open_{i}", use_container_width=True, help="Читать статью"):
                    show_article(art)
            with btn_cols[1]:
                label = "✓" if is_selected else "☐"
                if st.button(label, key=f"sel_{i}", use_container_width=True):
                    if is_selected:
                        st.session_state.selected.discard(i)
                    else:
                        st.session_state.selected.add(i)
                    st.rerun()
            with btn_cols[2]:
                if already_published:
                    st.button("✅", key=f"pub_{i}", disabled=True, use_container_width=True)
                else:
                    if st.button("📢", key=f"pub_{i}", use_container_width=True, help="Опубликовать в канал"):
                        publish_to_channel(art, published)
                        st.rerun()

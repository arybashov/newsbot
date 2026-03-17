import os
import json
import feedparser
from pathlib import Path
from openai import OpenAI

SEEN_FILE = Path(__file__).parent / "seen_urls.txt"
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(SEEN_FILE.read_text().splitlines())
    return set()


def save_seen(urls: set):
    SEEN_FILE.write_text("\n".join(sorted(urls)))


def fetch_rss(query: str) -> list[dict]:
    """Тянет Google News RSS по поисковому запросу."""
    encoded = query.replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    feed = feedparser.parse(url)

    articles = []
    for entry in feed.entries[:20]:
        articles.append({
            "title":  entry.get("title", ""),
            "url":    entry.get("link", ""),
            "source": entry.get("source", {}).get("title", "Unknown"),
            "date":   entry.get("published", ""),
        })
    return articles


def enrich_with_ai(articles: list[dict]) -> list[dict]:
    """Через GPT добавляет русский заголовок и саммари."""
    if not articles:
        return []

    items = [{"title": a["title"], "url": a["url"]} for a in articles]
    prompt = (
        "For each article, add a Russian editorial headline (title_ru) "
        "and a 1-sentence Russian summary (summary). "
        "Return ONLY a JSON array, no markdown.\n\n"
        f"Articles: {json.dumps(items, ensure_ascii=False)}\n\n"
        'Format: [{"title":"...","title_ru":"...","summary":"...","url":"..."}]'
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        response_format={"type": "json_object"},
    )

    text = resp.choices[0].message.content.strip()
    parsed = json.loads(text)
    # GPT может вернуть {"articles": [...]} или просто [...]
    enriched = parsed if isinstance(parsed, list) else next(iter(parsed.values()))

    url_map = {a["url"]: a for a in articles}
    result = []
    for item in enriched:
        base = url_map.get(item.get("url", ""), {})
        result.append({
            **base,
            "title_ru":  item.get("title_ru", item.get("title", "")),
            "summary":   item.get("summary", ""),
            "image_url": "",
        })
    return result


def fetch_news(prompt: str) -> list[dict]:
    """
    1. Тянет Google News RSS
    2. Фильтрует уже виденные URL
    3. Обогащает через GPT
    4. Сохраняет новые URL в seen_urls.txt
    """
    seen = load_seen()
    raw = fetch_rss(prompt)
    new_articles = [a for a in raw if a["url"] not in seen]

    if not new_articles:
        return []

    enriched = enrich_with_ai(new_articles)

    seen.update(a["url"] for a in new_articles)
    save_seen(seen)

    return enriched

import os
import json
import feedparser
from pathlib import Path
from groq import Groq

SEEN_FILE = Path(__file__).parent / "seen_urls.txt"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
client = Groq(api_key=os.environ["GROQ_API_KEY"])


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(SEEN_FILE.read_text().splitlines())
    return set()


def save_seen(urls: set):
    SEEN_FILE.write_text("\n".join(sorted(urls)))


def fetch_rss(query: str) -> list[dict]:
    encoded = query.replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    feed = feedparser.parse(url)

    articles = []
    for entry in feed.entries[:20]:
        articles.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "source": entry.get("source", {}).get("title", "Unknown"),
            "date": entry.get("published", ""),
        })
    return articles


def enrich_with_ai(articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    items = [{"title": a["title"], "url": a["url"]} for a in articles]
    prompt = (
        "For each article, add a Russian editorial headline (title_ru) "
        "and a 1-sentence Russian summary (summary). "
        "Return ONLY a JSON object with key 'articles', no markdown.\n\n"
        f"Articles: {json.dumps(items, ensure_ascii=False)}\n\n"
        'Format: {"articles":[{"title":"...","title_ru":"...","summary":"...","url":"..."}]}'
    )

    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    parsed = json.loads(resp.choices[0].message.content.strip())
    enriched = parsed.get("articles", []) if isinstance(parsed, dict) else []

    url_map = {a["url"]: a for a in articles}
    result = []
    for item in enriched:
        base = url_map.get(item.get("url", ""), {})
        result.append({
            **base,
            "title_ru": item.get("title_ru", item.get("title", "")),
            "summary": item.get("summary", ""),
            "image_url": "",
        })
    return result


def fetch_news(prompt: str) -> list[dict]:
    seen = load_seen()
    raw = fetch_rss(prompt)
    new_articles = [a for a in raw if a["url"] not in seen]

    if not new_articles:
        return []

    enriched = enrich_with_ai(new_articles)

    seen.update(a["url"] for a in new_articles)
    save_seen(seen)

    return enriched

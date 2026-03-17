import json
import os
import re
from pathlib import Path
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from groq import Groq

SEEN_FILE = Path(__file__).parent / "seen_urls.txt"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
client = Groq(api_key=os.environ["GROQ_API_KEY"])
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}
GENERIC_IMAGE_SOURCES = {
    "Yahoo",
    "Yahoo News",
    "Morningstar",
}
GENERIC_IMAGE_PATTERNS = (
    "s.yimg.com",
    "yahoo",
    "morningstar",
)
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "after", "over",
    "more", "than", "will", "first", "new", "its", "their", "about", "amid", "announces",
    "announce", "launches", "launch", "training", "pilot", "pilots", "aviation",
}


def _child_text(item: ET.Element, name: str) -> str:
    for child in item:
        if child.tag == name or child.tag.endswith(f"}}{name}"):
            return (child.text or "").strip()
    return ""


def _extract_article_url(link: str) -> str:
    if not link:
        return link

    parsed = urlparse(link)
    url = parse_qs(parsed.query).get("url", [""])[0]
    return unquote(url) or link


def _extract_meta_image(url: str) -> str:
    if not url:
        return ""

    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return ""

    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type:
        return ""

    html = resp.text
    patterns = [
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']image_src["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return urljoin(url, unescape(match.group(1).strip()))

    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    if img_match:
        return urljoin(url, unescape(img_match.group(1).strip()))

    return ""


def _is_generic_image(image_url: str, source: str) -> bool:
    if not image_url:
        return True

    if source in GENERIC_IMAGE_SOURCES:
        return True

    normalized = image_url.lower()
    return any(pattern in normalized for pattern in GENERIC_IMAGE_PATTERNS)


def _tokenize(text: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return {token for token in normalized.split() if len(token) > 2 and token not in STOPWORDS}


def _is_same_story(left: dict, right: dict) -> bool:
    if left.get("url") == right.get("url"):
        return True

    if left.get("date") != right.get("date"):
        return False

    left_title = _tokenize(left.get("title", ""))
    right_title = _tokenize(right.get("title", ""))
    title_overlap = len(left_title & right_title)
    title_base = max(1, min(len(left_title), len(right_title)))

    left_desc = _tokenize(left.get("description", ""))
    right_desc = _tokenize(right.get("description", ""))
    desc_overlap = len(left_desc & right_desc)
    desc_base = max(1, min(len(left_desc), len(right_desc)))

    same_image = bool(left.get("image_url")) and left.get("image_url") == right.get("image_url")
    if same_image and (title_overlap / title_base >= 0.4 or desc_overlap / desc_base >= 0.35):
        return True

    return title_overlap / title_base >= 0.75 or desc_overlap / desc_base >= 0.75


def _dedupe_articles(articles: list[dict]) -> list[dict]:
    unique: list[dict] = []
    for article in articles:
        if any(_is_same_story(article, existing) for existing in unique):
            continue
        unique.append(article)
    return unique


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(SEEN_FILE.read_text().splitlines())
    return set()


def save_seen(urls: set):
    SEEN_FILE.write_text("\n".join(sorted(urls)))


def fetch_rss(query: str) -> list[dict]:
    encoded = query.replace(" ", "+")
    url = f"https://www.bing.com/news/search?q={encoded}&format=rss"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    articles = []
    for item in root.findall(".//item")[:20]:
        article_url = _extract_article_url(_child_text(item, "link"))
        rss_image_url = _child_text(item, "Image")
        image_url = _extract_meta_image(article_url) or rss_image_url
        if _is_generic_image(image_url, _child_text(item, "Source") or "Unknown"):
            image_url = ""

        articles.append({
            "title": _child_text(item, "title"),
            "url": article_url,
            "source": _child_text(item, "Source") or "Unknown",
            "date": _child_text(item, "pubDate"),
            "description": _child_text(item, "description"),
            "image_url": image_url,
        })
    return _dedupe_articles(articles)


def enrich_with_ai(articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    items = [
        {
            "title": a["title"],
            "description": a.get("description", ""),
            "url": a["url"],
        }
        for a in articles
    ]
    prompt = (
        "For each article, write a Russian editorial headline (title_ru) "
        "and a concise but informative 3-5 sentence Russian body summary (summary). "
        "The summary must describe the article content, key facts, named entities, and why the news matters. "
        "Do not repeat the headline in the summary. "
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
            "image_url": base.get("image_url", ""),
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

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from xml.etree import ElementTree as ET

import feedparser
import requests
import trafilatura
from groq import Groq

log = logging.getLogger(__name__)

SEEN_FILE = Path(__file__).parent / "seen_urls.txt"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
client = Groq(api_key=os.environ["GROQ_API_KEY"])
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}
GENERIC_IMAGE_SOURCES = {
    "Yahoo",
    "Yahoo News",
    "Morningstar",
}
NO_IMAGE_DOMAINS = {
    "yahoo.com",
    "www.yahoo.com",
    "morningstar.com",
    "www.morningstar.com",
}
GENERIC_IMAGE_PATTERNS = (
    "yahoo_default_logo",
    "/cv/apiv2/social/images/",
    "/cv/apiv2/default/finance/",
    "/rz/p/yahoo_",
    "privacy-choice-control",
    "/newsletter/",
    "favicon",
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


def _child_attr(item: ET.Element, name: str, attr: str) -> str:
    """Return an attribute value from the first matching child element (handles namespaces)."""
    for child in item:
        if child.tag == name or child.tag.endswith(f"}}{name}"):
            return (child.get(attr) or "").strip()
    return ""


def _rss_item_image(item: ET.Element) -> str:
    """Extract image URL from Bing RSS item via media:content / media:thumbnail / enclosure."""
    # media:content url="..."
    url = _child_attr(item, "content", "url")
    if url:
        return url
    # media:thumbnail url="..."
    url = _child_attr(item, "thumbnail", "url")
    if url:
        return url
    # <enclosure url="..." type="image/...">
    for child in item:
        tag = child.tag if "}" not in child.tag else child.tag.split("}")[1]
        if tag == "enclosure":
            mime = (child.get("type") or "").lower()
            if mime.startswith("image/"):
                return (child.get("url") or "").strip()
    # Bing-specific <Image> text element
    return _child_text(item, "Image")


def _feedparser_image(entry: dict) -> str:
    """Extract image URL from a feedparser entry (media:content / media:thumbnail / enclosures)."""
    for mc in entry.get("media_content", []):
        url = mc.get("url", "")
        if not url:
            continue
        medium = mc.get("medium", "")
        mime = mc.get("type", "")
        if medium == "image" or mime.startswith("image/") or not medium:
            return url
    for mt in entry.get("media_thumbnail", []):
        url = mt.get("url", "")
        if url:
            return url
    for enc in entry.get("enclosures", []):
        url = enc.get("url", "")
        if url and enc.get("type", "").startswith("image/"):
            return url
    return ""


def _extract_article_url(link: str) -> str:
    if not link:
        return link

    parsed = urlparse(link)
    url = parse_qs(parsed.query).get("url", [""])[0]
    return unquote(url) or link


def _resolve_google_news_url(link: str) -> str:
    if not link:
        return link

    try:
        resp = requests.get(link, headers=REQUEST_HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        resolved = str(resp.url)
        domain = urlparse(resolved).netloc.lower()
        if domain in {"consent.google.com", "news.google.com"}:
            return ""
        return resolved
    except requests.RequestException:
        return ""


def _extract_meta_image(url: str) -> str:
    """Download page and extract best image. Used as last-resort fallback."""
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

    return _extract_meta_image_from_html(resp.text, url)


def _extract_meta_image_from_html(html: str, url: str) -> str:
    """Extract best image from already-downloaded HTML — no extra HTTP request."""
    if not html or not url:
        return ""

    candidates: list[tuple[int, str]] = []
    domain = urlparse(url).netloc.lower()

    meta_patterns = [
        (120, r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']'),
        (120, r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']'),
        (118, r'<meta[^>]+property=["\']og:video:image["\'][^>]+content=["\']([^"\']+)["\']'),
        (118, r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video:image["\']'),
        (110, r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']'),
        (110, r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']'),
        (108, r'<meta[^>]+name=["\']twitter:player:image["\'][^>]+content=["\']([^"\']+)["\']'),
        (108, r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:player:image["\']'),
        (100, r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']'),
        (100, r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']image_src["\']'),
    ]
    for base_score, pattern in meta_patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            candidates.append((base_score, urljoin(url, unescape(match.group(1).strip()))))

    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            payload = json.loads(unescape(match.group(1).strip()))
        except json.JSONDecodeError:
            continue
        for image_url in _extract_images_from_jsonld(payload):
            candidates.append((95, urljoin(url, image_url)))

    for match in re.finditer(r'"thumbnailUrl"\s*:\s*"([^"]+)"', html, flags=re.IGNORECASE):
        candidates.append((105, urljoin(url, unescape(match.group(1).strip()))))

    for match in re.finditer(r'<video[^>]+poster=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        candidates.append((100, urljoin(url, unescape(match.group(1).strip()))))

    for match in re.finditer(r'<source[^>]+poster=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        candidates.append((95, urljoin(url, unescape(match.group(1).strip()))))

    for match in re.finditer(
        r'<(?:img|source)[^>]+(?:srcset|data-srcset)=["\']([^"\']+)["\']([^>]*)>',
        html,
        flags=re.IGNORECASE,
    ):
        best_src = _pick_best_srcset(match.group(1))
        if best_src:
            attrs = match.group(2).lower()
            score = 70
            if any(word in attrs for word in ("article", "hero", "main", "lead", "gallery")):
                score += 20
            candidates.append((score, urljoin(url, best_src)))

    lazy_patterns = [
        r'<img[^>]+data-lazy-src=["\']([^"\']+)["\']([^>]*)>',
        r'<img[^>]+data-src=["\']([^"\']+)["\']([^>]*)>',
        r'<img[^>]+data-original=["\']([^"\']+)["\']([^>]*)>',
    ]
    for pattern in lazy_patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            attrs = match.group(2).lower()
            score = 75
            if any(word in attrs for word in ("article", "hero", "main", "lead", "gallery")):
                score += 20
            candidates.append((score, urljoin(url, unescape(match.group(1).strip()))))

    for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']([^>]*)>', html, flags=re.IGNORECASE):
        image_url = urljoin(url, unescape(match.group(1).strip()))
        attrs = match.group(2).lower()
        score = 50
        if "article" in attrs or "hero" in attrs or "main" in attrs:
            score += 20
        candidates.append((score, image_url))

    candidates.extend(_extract_domain_specific_candidates(domain, url, html))

    return _choose_best_image(candidates)


def _extract_images_from_jsonld(payload) -> list[str]:
    urls: list[str] = []

    def visit(node):
        if isinstance(node, dict):
            image = node.get("image")
            if isinstance(image, str):
                urls.append(image)
            elif isinstance(image, list):
                for item in image:
                    if isinstance(item, str):
                        urls.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("url"), str):
                        urls.append(item["url"])
            elif isinstance(image, dict) and isinstance(image.get("url"), str):
                urls.append(image["url"])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return urls


def _pick_best_srcset(srcset: str) -> str:
    candidates: list[tuple[int, str]] = []
    for part in srcset.split(","):
        item = part.strip()
        if not item:
            continue
        bits = item.split()
        image_url = bits[0]
        score = 0
        if len(bits) > 1:
            descriptor = bits[1].lower()
            number = re.sub(r"[^0-9]", "", descriptor)
            if number.isdigit():
                score = int(number)
        candidates.append((score, image_url))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _extract_domain_specific_candidates(domain: str, base_url: str, html: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []

    patterns_by_domain = {
        "usatoday.com": [
            (140, r'https://www\.gannett-cdn\.com/authoring/authoring-images/[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?'),
            (120, r'https://www\.gannett-cdn\.com/[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?'),
        ],
        "www.usatoday.com": [
            (140, r'https://www\.gannett-cdn\.com/authoring/authoring-images/[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?'),
            (120, r'https://www\.gannett-cdn\.com/[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?'),
        ],
        "aviationweek.com": [
            (130, r'https://aviationweek\.com/sites/default/files/[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?'),
        ],
        "www.ainonline.com": [
            (130, r'https://[^"\']*ainonline[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?'),
        ],
        "www.flightglobal.com": [
            (130, r'https://[^"\']*flightglobal[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?'),
        ],
    }

    for base_score, pattern in patterns_by_domain.get(domain, []):
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            candidates.append((base_score, urljoin(base_url, match.group(0))))

    return candidates


def _choose_best_image(candidates: list[tuple[int, str]]) -> str:
    best_url = ""
    best_score = -10**9
    seen: set[str] = set()

    for base_score, image_url in candidates:
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)

        normalized = image_url.lower()
        score = base_score
        if any(pattern in normalized for pattern in GENERIC_IMAGE_PATTERNS):
            score -= 120
        if any(word in normalized for word in ("logo", "icon", "sprite", "avatar", "placeholder", "brand")):
            score -= 80
        if any(word in normalized for word in ("hero", "lead", "article", "story", "cover", "photo", "image")):
            score += 20
        if any(ext in normalized for ext in (".jpg", ".jpeg", ".png", ".webp")):
            score += 10
        if "data:image" in normalized:
            score -= 200

        if score > best_score:
            best_score = score
            best_url = image_url

    return best_url if best_score >= 0 else ""


def _extract_article_payload(url: str) -> dict:
    if not url:
        return {}

    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            # trafilatura couldn't fetch the page (bot-blocked, JS wall, etc.)
            # Fall back to a plain requests.get for image extraction only.
            log.debug("trafilatura failed for %s, trying requests fallback", url)
            return {"image": _extract_meta_image(url)}

        extracted = trafilatura.extract(
            downloaded,
            url=url,
            output_format="json",
            with_metadata=True,
            include_images=True,
        )
        data = json.loads(extracted) if extracted else {}

        # Use already-downloaded HTML for image extraction — no second HTTP request.
        # Prefer our scoring-based extractor; fall back to trafilatura's own image field.
        html = downloaded if isinstance(downloaded, str) else downloaded.decode("utf-8", errors="replace")
        meta_image = _extract_meta_image_from_html(html, url)
        trafilatura_image = (data.get("image") or "").strip()
        image = meta_image or trafilatura_image

        return {
            "text": (data.get("text") or data.get("raw_text") or "").strip(),
            "excerpt": (data.get("excerpt") or "").strip(),
            "image": image,
            "date": (data.get("date") or "").strip(),
        }
    except Exception:
        return {}


def _is_generic_image(image_url: str, source: str) -> bool:
    if not image_url:
        return True

    normalized = image_url.lower()
    if source in GENERIC_IMAGE_SOURCES and any(pattern in normalized for pattern in GENERIC_IMAGE_PATTERNS):
        return True

    return any(pattern in normalized for pattern in GENERIC_IMAGE_PATTERNS)


def _should_skip_images(article_url: str) -> bool:
    domain = urlparse(article_url).netloc.lower()
    return domain in NO_IMAGE_DOMAINS


def _tokenize(text: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return {token for token in normalized.split() if len(token) > 2 and token not in STOPWORDS}


_DATE_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %Z",
    "%a, %d %b %Y %H:%M:%S %z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def _normalize_date(value: str) -> str:
    """Return YYYY-MM-DD from any supported date string, or '' on failure."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _is_same_story(left: dict, right: dict) -> bool:
    if left.get("url") == right.get("url"):
        return True

    left_date = _normalize_date(left.get("date", ""))
    right_date = _normalize_date(right.get("date", ""))
    # Only gate on date when both are successfully parsed (same calendar day).
    if left_date and right_date and left_date != right_date:
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
        match_index = next(
            (index for index, existing in enumerate(unique) if _is_same_story(article, existing)),
            None,
        )
        if match_index is None:
            unique.append(article)
            continue

        existing = unique[match_index]
        existing_score = _article_quality_score(existing)
        article_score = _article_quality_score(article)
        preferred = article if article_score > existing_score else existing
        fallback = existing if preferred is article else article
        unique[match_index] = _merge_article_versions(preferred, fallback)
    return unique


def _article_quality_score(article: dict) -> int:
    score = 0

    if article.get("image_url"):
        score += 100
    score += min(len(article.get("content", "")) // 20, 80)
    score += min(len(article.get("description", "")) // 20, 40)
    score += min(len(article.get("title", "")) // 10, 20)

    source = (article.get("source") or "").lower()
    if "google news" in source:
        score -= 20

    return score


def _merge_article_versions(preferred: dict, fallback: dict) -> dict:
    merged = dict(preferred)

    for key in ("title", "url", "source", "date", "description", "content", "image_url"):
        if not merged.get(key) and fallback.get(key):
            merged[key] = fallback[key]

    if not merged.get("image_url") and fallback.get("image_url"):
        merged["image_url"] = fallback["image_url"]
    if len(merged.get("content", "")) < len(fallback.get("content", "")):
        merged["content"] = fallback["content"]
    if len(merged.get("description", "")) < len(fallback.get("description", "")):
        merged["description"] = fallback["description"]

    return merged


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(SEEN_FILE.read_text().splitlines())
    return set()


def save_seen(urls: set):
    SEEN_FILE.write_text("\n".join(sorted(urls)))


def fetch_rss(query: str) -> list[dict]:
    bing = fetch_bing_rss(query)
    google = fetch_google_rss(query)
    log.info("RSS fetch: bing=%d google=%d", len(bing), len(google))
    articles = _dedupe_articles(bing + google)
    log.info("After dedup: %d articles", len(articles))
    return articles


def fetch_bing_rss(query: str) -> list[dict]:
    encoded = query.replace(" ", "+")
    url = f"https://www.bing.com/news/search?q={encoded}&format=rss"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError) as exc:
        log.warning("Bing RSS fetch failed: %s", exc)
        return []

    articles = []
    for item in root.findall(".//item")[:20]:
        article_url = _extract_article_url(_child_text(item, "link"))
        rss_image_url = _rss_item_image(item)
        article_payload = _extract_article_payload(article_url)
        image_url = ""
        source = _child_text(item, "Source") or "Unknown"
        if not _should_skip_images(article_url):
            # Priority: page scraping → RSS media tag → Bing <Image> fallback.
            image_url = article_payload.get("image") or rss_image_url
        if _is_generic_image(image_url, source):
            image_url = ""

        articles.append({
            "title": _child_text(item, "title"),
            "url": article_url,
            "source": source,
            "date": article_payload.get("date") or _child_text(item, "pubDate"),
            "description": article_payload.get("excerpt") or _child_text(item, "description"),
            "content": article_payload.get("text", ""),
            "image_url": image_url,
        })
    return articles


def fetch_google_rss(query: str) -> list[dict]:
    encoded = query.replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Google RSS fetch failed: %s", exc)
        return []

    feed = feedparser.parse(resp.content)

    articles = []
    for entry in feed.entries[:20]:
        article_url = _resolve_google_news_url(entry.get("link", ""))
        if not article_url:
            continue
        rss_image_url = _feedparser_image(entry)
        article_payload = _extract_article_payload(article_url)
        source = entry.get("source", {}).get("title", "Unknown")
        image_url = ""
        if not _should_skip_images(article_url):
            # Priority: page scraping → RSS media tag (media:content / thumbnail / enclosure).
            image_url = article_payload.get("image") or rss_image_url
        if _is_generic_image(image_url, source):
            image_url = ""

        articles.append({
            "title": entry.get("title", ""),
            "url": article_url,
            "source": source,
            "date": article_payload.get("date") or entry.get("published", ""),
            "description": article_payload.get("excerpt") or "",
            "content": article_payload.get("text", ""),
            "image_url": image_url,
        })
    return articles


def enrich_with_ai(articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    items = [
        {
            "title": a["title"],
            "description": a.get("description", ""),
            "content": a.get("content", "")[:3000],
            "url": a["url"],
        }
        for a in articles
    ]
    prompt = (
        "For each article, write a Russian editorial headline (title_ru) "
        "and a concise but informative 3-5 sentence Russian body summary (summary). "
        "Use the article description and content when available. "
        "The summary must describe the article content, key facts, named entities, and why the news matters. "
        "Do not repeat the headline in the summary. "
        "Return ONLY a JSON object with key 'articles', no markdown.\n\n"
        f"Articles: {json.dumps(items, ensure_ascii=False)}\n\n"
        'Format: {"articles":[{"title":"...","title_ru":"...","summary":"...","url":"..."}]}'
    )

    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content.strip())
        enriched = parsed.get("articles", []) if isinstance(parsed, dict) else []
    except Exception as exc:
        log.warning("Groq enrichment failed (%s) — returning raw articles", exc)
        return [
            {**a, "title_ru": a.get("title", ""), "summary": a.get("description", "")}
            for a in articles
        ]

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


def fetch_news_result(prompt: str, force: bool = False) -> dict:
    seen = load_seen()
    raw = fetch_rss(prompt)
    new_articles = raw if force else [a for a in raw if a["url"] not in seen]

    if not raw:
        return {"articles": [], "status": "empty", "raw_count": 0, "new_count": 0}
    if not new_articles:
        return {"articles": [], "status": "seen", "raw_count": len(raw), "new_count": 0}

    enriched = enrich_with_ai(new_articles)

    seen.update(a["url"] for a in new_articles)
    save_seen(seen)

    return {
        "articles": enriched,
        "status": "ok",
        "raw_count": len(raw),
        "new_count": len(new_articles),
    }

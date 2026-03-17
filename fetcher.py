import json
import os
import re
from pathlib import Path
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
import trafilatura
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
            return {}
        extracted = trafilatura.extract(
            downloaded,
            url=url,
            output_format="json",
            with_metadata=True,
            include_images=True,
        )
        if not extracted:
            return {}
        data = json.loads(extracted)
        return {
            "text": (data.get("text") or data.get("raw_text") or "").strip(),
            "excerpt": (data.get("excerpt") or "").strip(),
            "image": (data.get("image") or "").strip(),
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
        article_payload = _extract_article_payload(article_url)
        image_url = ""
        source = _child_text(item, "Source") or "Unknown"
        if not _should_skip_images(article_url):
            image_url = article_payload.get("image") or _extract_meta_image(article_url) or rss_image_url
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
    return _dedupe_articles(articles)


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


def fetch_news(prompt: str, force: bool = False) -> list[dict]:
    seen = load_seen()
    raw = fetch_rss(prompt)
    new_articles = raw if force else [a for a in raw if a["url"] not in seen]

    if not new_articles:
        return []

    enriched = enrich_with_ai(new_articles)

    seen.update(a["url"] for a in new_articles)
    save_seen(seen)

    return enriched

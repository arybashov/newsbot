import os
import json
import requests
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

WP_URL  = os.environ.get("WP_URL", "")
WP_USER = os.environ.get("WP_USER", "")
WP_PASS = os.environ.get("WP_PASS", "")

STUB_MODE = not all([WP_URL, WP_USER, WP_PASS])


def generate_draft_content(articles: list[dict]) -> dict:
    """Генерирует контент черновика через GPT."""
    items = [
        {"title": a.get("title_ru") or a["title"],
         "summary": a.get("summary", ""),
         "url": a["url"]}
        for a in articles
    ]

    prompt = (
        "На основе этих статей создай черновик для редактора. "
        "Верни ТОЛЬКО JSON без markdown.\n"
        f"Статьи: {json.dumps(items, ensure_ascii=False)}\n\n"
        'Формат: {"wp_title":"заголовок по-русски до 80 символов",'
        '"wp_excerpt":"вводная 1-2 предложения по-русски",'
        '"wp_content":"текст 3 абзаца по-русски без подзаголовков",'
        '"wp_tags":["тег1","тег2","тег3"],'
        '"sources":[{"title":"...","url":"..."}]}'
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        response_format={"type": "json_object"},
    )

    return json.loads(resp.choices[0].message.content)


def create_draft(articles: list[dict]) -> dict:
    """Создаёт черновик в WordPress (или stub если WP не настроен)."""
    draft = generate_draft_content(articles)

    sources_block = "\n\n<p><strong>Источники:</strong></p><ul>"
    for s in draft.get("sources", []):
        sources_block += f'<li><a href="{s["url"]}">{s["title"]}</a></li>'
    sources_block += "</ul>"

    content = draft["wp_content"] + sources_block

    if STUB_MODE:
        print("=== STUB MODE: черновик не отправлен в WP ===")
        print(f"Заголовок: {draft['wp_title']}")
        return {
            "title":    draft["wp_title"],
            "edit_url": "https://example.com (stub — WP не настроен)",
        }

    endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    payload = {
        "title":   draft["wp_title"],
        "excerpt": draft.get("wp_excerpt", ""),
        "content": content,
        "status":  "draft",
        "tags":    _get_or_create_tags(draft.get("wp_tags", [])),
    }

    resp = requests.post(endpoint, json=payload,
                         auth=(WP_USER, WP_PASS), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    return {
        "title":    data["title"]["rendered"],
        "edit_url": f"{WP_URL.rstrip('/')}/wp-admin/post.php?post={data['id']}&action=edit",
    }


def _get_or_create_tags(tag_names: list[str]) -> list[int]:
    if not tag_names:
        return []
    ids = []
    base = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/tags"
    auth = (WP_USER, WP_PASS)
    for name in tag_names:
        r = requests.get(base, params={"search": name}, auth=auth, timeout=10)
        found = [t for t in r.json() if t["name"].lower() == name.lower()]
        if found:
            ids.append(found[0]["id"])
        else:
            r = requests.post(base, json={"name": name}, auth=auth, timeout=10)
            if r.status_code in (200, 201):
                ids.append(r.json()["id"])
    return ids

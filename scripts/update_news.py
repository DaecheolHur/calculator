"""한국·미국 Google News RSS를 수집하고 한국어 헤드라인 JSON을 생성합니다."""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "headlines.json"
USER_AGENT = "Mozilla/5.0 (compatible; HourlyHeadlines/1.0)"
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_AGE = timedelta(hours=48)
TITLE_STOPWORDS = {"속보", "단독", "포토", "영상", "뉴스", "종합"}

TOPIC_CATEGORIES = {
    "정치": "NATION",
    "경제": "BUSINESS",
    "기술": "TECHNOLOGY",
    "문화": "ENTERTAINMENT",
    "스포츠": "SPORTS",
    "과학": "SCIENCE",
}

SEARCH_CATEGORIES = {
    "IT": {
        "한국": "소프트웨어 OR 사이버보안 OR 클라우드 when:1d",
        "미국": "software OR cybersecurity OR cloud when:1d",
    },
    "주식": {
        "한국": "코스피 OR 코스닥 OR 국내증시 when:1d",
        "미국": "US stocks OR Nasdaq OR S&P 500 when:1d",
    },
    "사회": {
        "한국": "한국 사회 주요뉴스 when:1d",
        "미국": "US society major news when:1d",
    },
    "AI": {
        "한국": "인공지능 OR AI when:1d",
        "미국": "artificial intelligence OR AI when:1d",
    },
}

COUNTRIES = (("한국", "ko", "KR"), ("미국", "en-US", "US"))
CATEGORY_NAMES = [*TOPIC_CATEGORIES, *SEARCH_CATEGORIES]


def build_feeds() -> list[tuple[str, str, str]]:
    feeds = []
    for country, language, region in COUNTRIES:
        locale = f"hl={language}&gl={region}&ceid={region}:{language.split('-')[0]}"
        for category, topic in TOPIC_CATEGORIES.items():
            feeds.append(
                (
                    country,
                    category,
                    f"https://news.google.com/rss/headlines/section/topic/{topic}?{locale}",
                )
            )
        for category, queries in SEARCH_CATEGORIES.items():
            query = urllib.parse.quote_plus(queries[country])
            feeds.append(
                (
                    country,
                    category,
                    f"https://news.google.com/rss/search?q={query}&{locale}",
                )
            )
    return feeds


FEEDS = build_feeds()


def fetch(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    request_headers = {"User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers)
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def clean_text(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def iso_date(value: str | None) -> str:
    try:
        date = parsedate_to_datetime(value or "")
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def collect_feed(country: str, category: str, url: str) -> list[dict[str, str]]:
    root = ET.fromstring(fetch(url))
    articles = []
    for item in root.findall("./channel/item"):
        source = clean_text(item.findtext("source"))
        title = clean_text(item.findtext("title"))
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        link = clean_text(item.findtext("link"))
        if not title or not link.startswith("http"):
            continue
        detail = clean_text(item.findtext("description"))
        for noise in (title, source):
            if noise:
                detail = detail.replace(noise, " ")
        detail = re.sub(r"\s+", " ", detail).strip(" -|")
        articles.append(
            {
                "title": title,
                "summary": "",
                "detail": detail[:500],
                "url": link,
                "source": source or "Google News",
                "country": country,
                "category": category,
                "publishedAt": iso_date(item.findtext("pubDate")),
                "storyKey": normalized_title(title),
            }
        )
    return articles


def normalized_title(title: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", title.lower())


def published_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_recent(article: dict[str, str]) -> bool:
    published = published_datetime(article["publishedAt"])
    return published is not None and datetime.now(timezone.utc) - published <= MAX_AGE


def content_tokens(title: str) -> set[str]:
    tokens = set(re.findall(r"[가-힣]{2,}|[a-z0-9]{3,}", title.lower()))
    return {token for token in tokens if token not in TITLE_STOPWORDS}


def same_story(left: str, right: str) -> bool:
    if normalized_title(left) == normalized_title(right):
        return True
    left_tokens = content_tokens(left)
    right_tokens = content_tokens(right)
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 and len(overlap) / min(len(left_tokens), len(right_tokens)) >= 0.6


def resolve_publisher_url(url: str) -> str:
    if "news.google." not in url or "/articles/" not in url:
        return url
    article_id = url.split("/articles/", 1)[1].split("?", 1)[0]
    try:
        page = fetch(url).decode("utf-8", "replace")
        signature = re.search(r'data-n-a-sg="([^"]+)"', page)
        timestamp = re.search(r'data-n-a-ts="(\d+)"', page)
        if not signature or not timestamp:
            return url
        inner = json.dumps(
            [
                "garturlreq",
                [
                    ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
                    "X",
                    "X",
                    1,
                    [1, 1, 1],
                    1,
                    1,
                    None,
                    0,
                    0,
                    None,
                    0,
                ],
                article_id,
                int(timestamp.group(1)),
                signature.group(1),
            ],
            separators=(",", ":"),
        )
        payload = urllib.parse.urlencode(
            {"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"))}
        ).encode()
        body = fetch(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            payload,
            {
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Referer": "https://news.google.com/",
            },
        ).decode("utf-8", "replace")
        payload_line = next(line for line in body.splitlines() if line.startswith("[["))
        for envelope in json.loads(payload_line):
            if isinstance(envelope, list) and len(envelope) > 2 and envelope[1] == "Fbv4je" and envelope[2]:
                decoded = json.loads(envelope[2])
                if decoded and decoded[0] == "garturlres" and str(decoded[1]).startswith("http"):
                    return str(decoded[1])
    except Exception as error:
        print(f"원문 주소 복원 실패: {error}", file=sys.stderr)
    return url


def choose_articles() -> list[dict[str, str]]:
    buckets: dict[tuple[str, str], list[dict[str, str]]] = {}
    selected: list[dict[str, str]] = []
    seen: set[str] = set()

    for country, category, url in FEEDS:
        try:
            buckets[(country, category)] = [
                article for article in collect_feed(country, category, url) if is_recent(article)
            ]
        except Exception as error:
            print(f"수집 실패: {country}/{category}: {error}", file=sys.stderr)
            buckets[(country, category)] = []

    # 분야별 3개씩, 전체적으로 한국 15개와 미국 15개가 되도록 교대로 배분합니다.
    for index, category in enumerate(CATEGORY_NAMES):
        quotas = {"한국": 2, "미국": 1} if index % 2 == 0 else {"한국": 1, "미국": 2}
        for country in ("한국", "미국"):
            accepted = 0
            for article in buckets[(country, category)]:
                key = article["storyKey"]
                if not key or key in seen or any(same_story(article["title"], item["title"]) for item in selected):
                    continue
                seen.add(key)
                selected.append(article)
                accepted += 1
                if accepted == quotas[country]:
                    break

    if len(selected) < 30:
        reserves = sorted(
            (article for articles in buckets.values() for article in articles),
            key=lambda item: item["publishedAt"],
            reverse=True,
        )
        for article in reserves:
            key = article["storyKey"]
            if not key or key in seen or any(same_story(article["title"], item["title"]) for item in selected):
                continue
            seen.add(key)
            selected.append(article)
            if len(selected) == 30:
                break

    selected.sort(key=lambda item: item["publishedAt"], reverse=True)
    return selected[:30]


def localize_with_gemini(articles: list[dict[str, str]]) -> dict[str, list]:
    analysis: dict[str, list] = {"briefing": [], "trends": []}
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY가 없어 원문 제목으로 저장합니다.", file=sys.stderr)
        return analysis

    compact = [
        {
            "id": index,
            "country": item["country"],
            "category": item["category"],
            "title": item["title"],
            "detail": item.get("detail", ""),
        }
        for index, item in enumerate(articles)
    ]
    prompt = (
        "다음 뉴스 제목을 자연스러운 한국어로 작성하세요. 이미 한국어인 제목은 그대로 다듬으세요. "
        "각 기사마다 detail에 있는 추가 사실을 우선 사용해 제목을 반복하지 않는 한 문장의 한국어 요약을 작성하세요. "
        "detail이 비어 있으면 제목의 사실만 사용하고, 입력에 없는 정보는 추가하지 마세요. "
        "기사들을 서로 비교해 지금 주목할 핵심 흐름 "
        "3개와 10개 분야별 동향을 각각 한 문장으로 작성하세요. 특정 기사의 사실을 일반적인 추세로 "
        "과장하지 마세요. 다음 구조의 JSON 객체만 반환하세요: "
        '{"articles":[{"id":0,"title":"...","summary":"..."}],'
        '"briefing":["핵심 흐름 1","핵심 흐름 2","핵심 흐름 3"],'
        '"trends":[{"category":"정치","summary":"..."}]}. '
        f"trends에는 다음 분야를 정확히 한 번씩 포함하세요: {', '.join(CATEGORY_NAMES)}.\n"
        + json.dumps(compact, ensure_ascii=False)
    )
    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
    ).encode("utf-8")
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?"
        + urllib.parse.urlencode({"key": api_key})
    )

    try:
        response = json.loads(fetch(endpoint, payload, {"Content-Type": "application/json"}))
        parts = response["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        localized = json.loads(text)
        localized_articles = localized.get("articles", [])
        by_id = {int(item["id"]): item for item in localized_articles}
        for index, article in enumerate(articles):
            result = by_id.get(index, {})
            if result.get("title"):
                article["title"] = clean_text(str(result["title"]))
            if result.get("summary"):
                article["summary"] = clean_text(str(result["summary"]))
        analysis["briefing"] = [
            clean_text(str(item)) for item in localized.get("briefing", [])[:3] if item
        ]
        allowed_categories = set(CATEGORY_NAMES)
        analysis["trends"] = [
            {
                "category": clean_text(str(item.get("category", ""))),
                "summary": clean_text(str(item.get("summary", ""))),
            }
            for item in localized.get("trends", [])
            if item.get("category") in allowed_categories and item.get("summary")
        ]
    except Exception as error:
        print(f"Gemini 처리 실패, 원문 제목으로 저장합니다: {error}", file=sys.stderr)
    return analysis


def previous_story_keys() -> set[str]:
    if not OUTPUT.exists():
        return set()
    try:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {item["storyKey"] for item in previous.get("items", []) if item.get("storyKey")}


def main() -> None:
    known_stories = previous_story_keys()
    articles = choose_articles()
    if not articles:
        raise RuntimeError("수집된 기사가 없습니다. 기존 데이터는 변경하지 않습니다.")
    with ThreadPoolExecutor(max_workers=6) as pool:
        resolved_urls = list(pool.map(resolve_publisher_url, [article["url"] for article in articles]))
    for article, url in zip(articles, resolved_urls):
        article["url"] = url
        article["isNew"] = bool(known_stories) and article["storyKey"] not in known_stories
    analysis = localize_with_gemini(articles)
    for article in articles:
        article.pop("detail", None)
    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": articles,
        **analysis,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(articles)}개 기사를 {OUTPUT}에 저장했습니다.")


if __name__ == "__main__":
    main()

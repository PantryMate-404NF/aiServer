"""재료 이미지 URL 수집 — 백엔드의 `ingredients.csv` 빈 `image_url` 을 채웁니다.

    PYTHONUTF8=1 uv run python scripts/crawl_ingredient_images.py
    (--src 와 --out 의 기본값은 아래 상수입니다. --limit 으로 앞 N 건만 돌립니다)

**왜 만개의레시피가 아닌가.** 레시피의 `thumbnail_url` 은 만개의레시피 CDN 이지만 그 사이트에는
**재료 페이지가 없습니다**(2026-09-29 확인 — 홈에서 재료 경로 0건). 그 CDN 이 가진 것은 요리
사진뿐이라, 검색 결과의 썸네일을 재료 이미지로 쓰면 "마늘" 칸에 마늘장아찌 사진이 들어갑니다.
에러 없이 틀린 데이터가 되므로 쓰지 않습니다.

**대신 위키미디어입니다.** 한국어 위키백과 문서의 대표 이미지를 받아 그 원본이 있는 위키미디어
공용(`upload.wikimedia.org`)의 주소를 씁니다. 자유 라이선스(CC · 퍼블릭 도메인)라 제품에 실을 수
있고, 주소가 안정적이며 키가 필요 없습니다. **다만 대부분 저작자 표시 의무가 있어** 라이선스와
저작자를 함께 받아 적습니다(`licenses.csv`). 표시 없이 쓰면 라이선스 위반입니다.

지키는 것 세 가지입니다.

1. **지어내지 않습니다.** 못 찾은 재료는 빈 칸으로 두고 보고서에 사유를 적습니다.
2. **응답을 그대로 믿지 않습니다.** 받은 주소마다 HEAD 로 실제 이미지인지 확인합니다(CLAUDE.md 5절).
3. **상대 서버에 예의를 지킵니다.** 배치 조회(한 번에 50개)와 호출 간격을 둡니다. robots.txt 확인함.

산출물은 셋입니다 — 채워진 CSV(백엔드가 그대로 적재), 라이선스 표, 사람이 읽는 보고서.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/Backend_Data_Dump/pantry_recipe_domain_dump/csv/ingredients.csv"
OUT = ROOT / "data/ingredient_images"

WIKI_API = "https://ko.wikipedia.org/w/api.php"
EN_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
#: 위키미디어는 연락처가 있는 설명적 User-Agent 를 요구합니다. 없으면 429 로 막힙니다.
UA = "PantryMate-ingredient-images/1.0 (team project; https://github.com/PantryMate-404NF/aiServer)"
#: 한 번에 물어볼 제목 수. API 상한이 50 입니다.
BATCH = 50
#: 호출 사이 간격(초). 표본 조회에서 간격 0.2 초로 429 를 받았습니다.
PAUSE = 1.0
#: 이미지 폭. 원본은 수 MB 짜리가 있어 썸네일 주소를 씁니다.
THUMB_WIDTH = 800

#: 재료명 → 위키백과 문서 제목. **재료명 그대로는 문서가 없거나 다른 것을 가리키는 것만** 적습니다.
#: 예: "통깨" 는 문서가 있어도 대표 이미지가 없고, "맛술" 은 문서 자체가 없습니다.
#: 상표명(다시다 · 스팸)은 그 상표의 문서가 맞으므로 그대로 둡니다.
SYNONYMS: dict[str, str] = {
    # 동음이의어 — 이름 그대로는 "김"=성씨, "가지"=나뭇가지의 동음이의 문서로 갑니다.
    "김": "김 (음식)",
    "가지": "가지 (식물)",
    "통깨": "참깨",
    "맛술": "미림",
    "전분": "녹말",
    "마늘쫑": "마늘종",
    "건고추": "고추",
    "샐러드채소": "양상추",
    "슈가파우더": "분당 (설탕)",
    "부침가루": "밀가루",
    "튀김가루": "밀가루",
    "찹쌀가루": "찹쌀",
    "밥": "쌀밥",
    "맛살": "게맛살",
    "진미채": "오징어채",
    "또띠아": "토르티야",
    "파마산치즈": "파르미지아노 레지아노",
    "모짜렐라치즈": "모차렐라",
    "요거트": "요구르트",
    "이스트": "효모",
    "소면": "국수",
    "코코아가루": "코코아 고형분",
    "돈가스소스": "우스터 소스",
    "허브솔트": "허브",
    "참치액": "액젓",
    "다시다": "조미료",
    "견과류": "견과",
    "북어": "명태",
    "고추기름": "고추기름",
    "들깨가루": "들깨",
    "계피": "계피",
    "김치": "김치",
    "떡": "떡",
    "빵가루": "빵가루",
}


def clean_url(url: str) -> str:
    """API 가 붙여 주는 추적 파라미터(`utm_*`)를 뗍니다.

    DB 에 들어갈 주소라 짧고 안정적이어야 합니다.
    """
    head, _, query = url.partition("?")
    kept = [
        pair for pair in query.split("&") if pair and not pair.split("=", 1)[0].startswith("utm_")
    ]
    return head + ("?" + "&".join(kept) if kept else "")


def file_key(name: str) -> str:
    """위키 파일 이름의 열쇠. 주소에는 밑줄, API 응답 제목에는 공백이 들어와 한쪽으로 맞춥니다."""
    return name.replace("_", " ").strip()


#: 재료명 → 위키미디어 공용의 파일 이름. **문서로는 맞는 사진을 못 얻는 것만** 손으로 적습니다.
#: 넣기 전에 파일의 설명과 분류를 확인했습니다(2026-09-29). 예: 영어 "Flour" 문서의 대표 이미지는
#: 콩가루(`Soy_powder.jpg`) 사진이고, "배" 는 한국어 쪽이 동음이의라 배나무(나무) 사진으로 갑니다.
COMMONS_FILES: dict[str, str] = {
    "밀가루": "All-Purpose Flour (4107895947).jpg",
    "부침가루": "All-Purpose Flour (4107895947).jpg",
    "튀김가루": "All-Purpose Flour (4107895947).jpg",
    "배": "Pears.jpg",
    # 문서 대표 이미지가 그 재료가 아니어서 바꾼 것들(2026-09-29 검수).
    # "일본 카레" 문서의 대표 이미지가 고료카쿠 타워(건물)였습니다.
    "카레": "Taj Mahal - Lamb Curry Madras.jpg",
    # 스파게티 접시 사진이었습니다.
    "토마토소스": "Fresh Tomato Sauce (Unsplash).jpg",
    # 1793년 식물 삽화였습니다. 냉장고 화면에는 사진이 맞습니다.
    "마늘": "Knoblauch frisch.jpg",
    # 마늘 삽화로 가 있었습니다. 마늘종은 줄기입니다.
    "마늘쫑": "Garlic scape.jpg",
    # "조미료" 의 소금·후추 사진이었습니다. 육수 조미료 쪽이 가깝습니다.
    "다시다": "Brühwürfel-1.jpg",
    # 계란프라이 사진이었습니다. 냉장고 재고 화면이라 껍질째가 맞습니다.
    "달걀": "6-Pack-Chicken-Eggs.jpg",
    # 효모의 현미경 사진이었습니다. 부엌에서 쓰는 모양은 건조 이스트입니다.
    "이스트": "Dry Yeast (50995087326).jpg",
    # "엿" 문서의 엿 막대(고체) 사진이었습니다. 물엿은 액체입니다.
    "물엿": "Maltose syrup.jpg",
    # 백과 문서가 화학이라 구조식뿐입니다. 같은 선반의 액상 감미료 사진으로 대신합니다.
    "올리고당": "Corn syrup.jpg",
    # 백과 문서가 없습니다. 허브를 섞은 양념 소금 사진으로 대신합니다.
    "허브솔트": "Seasoned salt mixture.jpg",
}

#: 맞는 사진이 없어 **비워 두는** 재료와 그 사유. 억지로 채우면 에러 없이 틀린 데이터가 됩니다.
#: 지금은 비어 있습니다 — 올리고당 · 허브솔트는 "그 재료를 알아볼 수 있는 참고 이미지" 기준으로
#: 같은 갈래의 사진을 `COMMONS_FILES` 에 손으로 지정했습니다(2026-09-29 유재현 결정).
NO_IMAGE: dict[str, str] = {}


@dataclass
class Found:
    """재료 하나의 결과. 못 찾으면 `image_url` 이 빈 문자열이고 `note` 에 사유가 있습니다."""

    ingredient_id: int
    name: str
    category: str
    title: str = ""
    image_url: str = ""
    file_name: str = ""
    license_name: str = ""
    artist: str = ""
    credit_url: str = ""
    note: str = ""
    verified: bool = False
    matched_by: str = ""


def https_request(url: str, method: str = "GET") -> urllib.request.Request:
    """https 주소만 엽니다. `verify()` 가 받는 주소는 외부 응답에서 오므로 스킴을 직접 봅니다."""
    if not url.startswith("https://"):
        raise ValueError(f"https 주소가 아닙니다: {url[:80]}")
    # 바로 위에서 스킴을 검사했습니다. 이 함수가 바깥 주소로 나가는 유일한 통로입니다.
    return urllib.request.Request(url, headers={"User-Agent": UA}, method=method)  # noqa: S310


def api_get(endpoint: str, params: dict[str, str]) -> dict:
    """위키미디어 API 호출. 429 를 만나면 간격을 늘려 다시 시도합니다."""
    query = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    request = https_request(f"{endpoint}?{query}")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 3:
                raise
            wait = PAUSE * (attempt + 2) * 3
            print(f"    429 — {wait:.0f}초 쉬고 다시 시도합니다")
            time.sleep(wait)
    raise RuntimeError("429 가 계속됩니다")


def asked_to_final(query: dict) -> dict[str, str]:
    """물어본 이름 → 최종 문서 제목. 정규화와 넘겨주기를 순서대로 따라갑니다."""
    alias: dict[str, str] = {}
    for step in ("normalized", "redirects"):
        for row in query.get(step, []):
            alias[row["from"]] = row["to"]

    def resolve(name: str) -> str:
        seen = name
        for _ in range(4):
            nxt = alias.get(seen)
            if nxt is None:
                break
            seen = nxt
        return seen

    # 넘겨주기가 없는 이름은 담지 않습니다. 부르는 쪽이 `.get(asked, asked)` 으로 씁니다.
    return {name: resolve(name) for name in alias}


def english_titles(titles: list[str], api: str = WIKI_API) -> dict[str, str]:
    """한국어 문서 → 대응하는 영어 문서 제목. 한국어 쪽에 대표 이미지가 없을 때의 길입니다.

    "달걀" 은 한국어 문서에 대표 이미지가 없지만 영어의 "Egg as food" 에는 있습니다. 같은 것을
    가리키는 문서이므로 다른 재료의 사진이 들어갈 일은 없습니다.
    """
    out: dict[str, str] = {}
    for start in range(0, len(titles), BATCH):
        chunk = titles[start : start + BATCH]
        data = api_get(
            api,
            {
                "action": "query",
                "prop": "langlinks",
                "lllang": "en",
                "lllimit": "500",
                "redirects": "1",
                "titles": "|".join(chunk),
            },
        )
        query = data.get("query", {})
        final = asked_to_final(query)
        by_title = {page["title"]: page for page in query.get("pages", [])}
        for asked in chunk:
            links = (by_title.get(final.get(asked, asked)) or {}).get("langlinks") or []
            if links:
                out[asked] = links[0]["title"]
        time.sleep(PAUSE)
    return out


def page_images(titles: list[str], api: str = WIKI_API) -> dict[str, tuple[str, str, str]]:
    """문서 제목 → (해석된 제목, 이미지 주소, 파일 이름). 이미지가 없는 문서는 빠집니다.

    `redirects=1` 로 넘겨주기를 따라갑니다 — "대파" 는 "파 (종)" 으로 갑니다.
    `normalized` · `redirects` 를 되짚어 **물어본 이름** 을 열쇠로 돌려줍니다.
    """
    out: dict[str, tuple[str, str, str]] = {}
    for start in range(0, len(titles), BATCH):
        chunk = titles[start : start + BATCH]
        data = api_get(
            api,
            {
                "action": "query",
                "prop": "pageimages",
                "piprop": "thumbnail|name",
                "pithumbsize": str(THUMB_WIDTH),
                "redirects": "1",
                "titles": "|".join(chunk),
            },
        )
        query = data.get("query", {})
        final = asked_to_final(query)
        by_title = {
            page["title"]: page for page in query.get("pages", []) if not page.get("missing")
        }
        for asked in chunk:
            page = by_title.get(final.get(asked, asked))
            thumb = (page or {}).get("thumbnail", {}).get("source")
            if page is not None and thumb:
                out[asked] = (page["title"], clean_url(thumb), page.get("pageimage", ""))
        time.sleep(PAUSE)
    return out


def file_licenses(file_names: list[str]) -> dict[str, tuple[str, str, str]]:
    """파일 이름 → (라이선스, 저작자, 출처 주소). 표시 의무를 지키려면 이 값이 있어야 합니다."""
    out: dict[str, tuple[str, str, str]] = {}
    unique = sorted({file_key(name) for name in file_names if name})
    for start in range(0, len(unique), BATCH):
        chunk = unique[start : start + BATCH]
        data = api_get(
            COMMONS_API,
            {
                "action": "query",
                "prop": "imageinfo",
                "iiprop": "extmetadata|url",
                "titles": "|".join(f"File:{name}" for name in chunk),
            },
        )
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})

            def value(key: str, meta: dict = meta) -> str:
                raw = str(meta.get(key, {}).get("value", ""))
                # 저작자 칸에 HTML 링크가 들어 있습니다. 표에 넣을 것은 글자뿐입니다.
                text = raw.replace("&amp;", "&")
                while "<" in text and ">" in text:
                    head, _, rest = text.partition("<")
                    _, _, tail = rest.partition(">")
                    text = head + tail
                return " ".join(text.split())

            out[file_key(page["title"].removeprefix("File:"))] = (
                value("LicenseShortName") or value("License"),
                value("Artist"),
                info.get("descriptionurl", ""),
            )
        time.sleep(PAUSE)
    return out


def commons_urls(file_names: list[str]) -> dict[str, tuple[str, str]]:
    """공용 파일 이름 → (썸네일 주소, 파일 이름). 문서를 거치지 않고 파일을 가리킬 때 씁니다."""
    out: dict[str, tuple[str, str]] = {}
    unique = sorted(set(file_names))
    for start in range(0, len(unique), BATCH):
        chunk = unique[start : start + BATCH]
        data = api_get(
            COMMONS_API,
            {
                "action": "query",
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": str(THUMB_WIDTH),
                "titles": "|".join(f"File:{name}" for name in chunk),
            },
        )
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            if url:
                name = page["title"].removeprefix("File:")
                out[file_key(name)] = (clean_url(url), name)
        time.sleep(PAUSE)
    return out


def verify(url: str) -> bool:
    """받은 주소가 실제로 이미지인지 확인합니다. 응답을 그대로 믿지 않습니다(CLAUDE.md 5절)."""
    try:
        request = https_request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return response.status == 200 and response.headers.get("Content-Type", "").startswith(
                "image/"
            )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return False


def search_title(name: str) -> str:
    """이름으로 문서를 찾지 못했을 때의 마지막 수단. 검색 1위 문서 제목을 돌려줍니다."""
    data = api_get(
        WIKI_API,
        {"action": "query", "list": "search", "srsearch": name, "srlimit": "1", "srnamespace": "0"},
    )
    hits = data.get("query", {}).get("search", [])
    time.sleep(PAUSE)
    return hits[0]["title"] if hits else ""


def collect(rows: list[dict[str, str]]) -> list[Found]:
    """이름 → 동의어 → 검색 순으로 세 번 시도합니다. 단계마다 어떻게 찾았는지 남깁니다."""
    results = [Found(int(row["ingredient_id"]), row["name"], row["category"]) for row in rows]
    by_name = {found.name: found for found in results}

    for found in results:
        if found.name in NO_IMAGE:
            # `image_url` 을 비운 채 사유를 적어 두면 아래 단계가 전부 건너뜁니다.
            found.note, found.matched_by = NO_IMAGE[found.name], "비움"

    picked = {
        f.name: COMMONS_FILES[f.name]
        for f in results
        if f.name in COMMONS_FILES and f.name not in NO_IMAGE
    }
    if picked:
        print(f"0) 손으로 고른 공용 파일 — {len(picked)}건")
        urls = commons_urls(sorted(set(picked.values())))
        for name, file_name in picked.items():
            hit = urls.get(file_key(file_name))
            if hit:
                found = by_name[name]
                found.image_url, found.file_name = hit
                found.title, found.matched_by = f"공용 파일 {hit[1]}", "손으로 고름"

    # 동의어는 **이름보다 먼저** 씁니다. 동의어를 적어 둔 것은 이름 그대로가 틀리기 때문입니다 —
    # 나중으로 미루면 이름이 엉뚱한 문서로 "성공" 해 동의어가 안 쓰입니다(마늘쫑 → 마늘).
    print(f"1) 재료명(동의어가 있으면 그쪽) 조회 — {len(results) - len(picked)}건")
    todo = {
        f.name: SYNONYMS.get(f.name, f.name)
        for f in results
        if not f.image_url and f.matched_by != "비움"
    }
    hits = page_images(sorted(set(todo.values())))
    for name, asked in todo.items():
        hit = hits.get(asked)
        if hit:
            found = by_name[name]
            found.title, found.image_url, found.file_name = hit
            found.matched_by = "이름" if asked == name else f"동의어({asked})"

    missing = [f for f in results if not f.image_url and f.matched_by != "비움"]
    if missing:
        print(f"2) 영어 문서로 다시 조회 — {len(missing)}건")
        # 한국어 문서에 대표 이미지가 없을 때입니다. 물어보는 이름은 동의어가 있으면 그쪽입니다.
        asked = {f.name: SYNONYMS.get(f.name, f.name) for f in missing}
        english = english_titles(sorted(set(asked.values())))
        pairs = {name: english[ko] for name, ko in asked.items() if ko in english}
        hits = page_images(sorted(set(pairs.values())), api=EN_API) if pairs else {}
        for found in missing:
            hit = hits.get(pairs.get(found.name, ""))
            if hit:
                found.title, found.image_url, found.file_name = hit
                found.matched_by = f"영어 문서({pairs[found.name]})"

    missing = [f for f in results if not f.image_url and f.matched_by != "비움"]
    if missing:
        print(f"3) 검색으로 다시 조회 — {len(missing)}건")
        titles: dict[str, str] = {}
        for found in missing:
            title = search_title(found.name)
            if title:
                titles[found.name] = title
        hits = page_images(sorted(set(titles.values())))
        for found in missing:
            hit = hits.get(titles.get(found.name, ""))
            if hit:
                found.title, found.image_url, found.file_name = hit
                found.matched_by = f"검색({titles[found.name]})"
            else:
                found.note = "위키백과에 대표 이미지가 있는 문서를 찾지 못했습니다"
    return results


def annotate(results: list[Found]) -> None:
    """라이선스를 붙이고 주소를 확인합니다. 확인에 실패한 주소는 버립니다."""
    print("5) 라이선스 조회")
    licenses = file_licenses([f.file_name for f in results])
    for found in results:
        key = file_key(found.file_name)
        if key in licenses:
            found.license_name, found.artist, found.credit_url = licenses[key]

    targets = [f for f in results if f.image_url]
    print(f"6) 주소 확인(HEAD) — {len(targets)}건")
    for n, found in enumerate(targets, 1):
        found.verified = verify(found.image_url)
        if not found.verified:
            found.note = "주소가 이미지로 응답하지 않아 버렸습니다"
            found.image_url = ""
        if n % 25 == 0:
            print(f"    {n}/{len(targets)}")
        time.sleep(0.1)


def write(results: list[Found], rows: list[dict[str, str]], fields: list[str], out: Path) -> None:
    """산출물 셋 — 채워진 CSV · 라이선스 표 · 사람이 읽는 보고서."""
    out.mkdir(parents=True, exist_ok=True)
    found_by_id = {f.ingredient_id: f for f in results}
    with (out / "ingredients.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            patched = dict(row)
            patched["image_url"] = found_by_id[int(row["ingredient_id"])].image_url
            writer.writerow(patched)

    with (out / "licenses.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "ingredient_id",
                "name",
                "image_url",
                "license",
                "artist",
                "credit_url",
                "source_title",
            ]
        )
        for found in results:
            if found.image_url:
                writer.writerow(
                    [
                        found.ingredient_id,
                        found.name,
                        found.image_url,
                        found.license_name,
                        found.artist,
                        found.credit_url,
                        found.title,
                    ]
                )

    filled = [f for f in results if f.image_url]
    missing = [f for f in results if not f.image_url]
    lines = [
        "# 재료 이미지 수집 결과",
        "",
        f"- 대상 {len(results)}건 · 채움 **{len(filled)}건** · 못 찾음 {len(missing)}건",
        "- 출처: 한국어 위키백과 문서의 대표 이미지(원본은 위키미디어 공용)",
        "- 주소는 전부 HEAD 로 이미지인지 확인했습니다. 확인에 실패한 것은 채우지 않았습니다.",
        "- **라이선스 표시 의무가 있습니다.** `licenses.csv` 의 저작자 · 라이선스를 적습니다.",
        "",
        "## 못 찾은 재료",
        "",
    ]
    lines += (
        [f"- {f.name}({f.category}) — {f.note}" for f in missing] if missing else ["- 없습니다."]
    )
    lines += ["", "## 라이선스 분포", ""]
    counts: dict[str, int] = {}
    for found in filled:
        counts[found.license_name or "(표기 없음)"] = (
            counts.get(found.license_name or "(표기 없음)", 0) + 1
        )
    lines += [
        f"- {name}: {count}건" for name, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    lines += ["", "## 찾은 방법", ""]
    ways: dict[str, int] = {}
    for found in filled:
        key = found.matched_by.split("(")[0]
        ways[key] = ways.get(key, 0) + 1
    lines += [f"- {name}: {count}건" for name, count in sorted(ways.items(), key=lambda kv: -kv[1])]

    # 재료명과 다른 문서에서 가져온 것은 사람이 한 번 봐야 합니다. 기계는 "같은 것" 인지 모릅니다.
    review = [f for f in filled if f.title != f.name]
    lines += [
        "",
        "## 사람이 볼 것 — 재료명과 다른 문서에서 가져온 이미지",
        "",
        "기계는 문서가 그 재료를 가리키는지 알지 못합니다. 아래는 이름이 그대로 맞지 않아 동의어 ·",
        "영어 문서 · 검색으로 찾은 것입니다. 사진이 그 재료가 맞는지 확인해 주십시오.",
        "",
        "| 재료 | 가져온 문서 | 방법 | 이미지 |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {f.name} | {f.title} | {f.matched_by} | {f.image_url} |"
        for f in sorted(review, key=lambda f: f.ingredient_id)
    ] or ["| (없습니다) | | | |"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--src", type=Path, default=SRC)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--limit", type=int, default=0, help="앞 N 건만 (시험용)")
    args = parser.parse_args()

    with args.src.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if "image_url" not in fields:
        print(f"{args.src} 에 image_url 칸이 없습니다", file=sys.stderr)
        return 2
    if args.limit:
        rows = rows[: args.limit]

    results = collect(rows)
    annotate(results)
    write(results, rows, fields, args.out)

    filled = sum(1 for f in results if f.image_url)
    print(f"\n채움 {filled}/{len(results)}건 → {args.out}")
    for found in results:
        if not found.image_url:
            print(f"  못 찾음: {found.name}({found.category}) — {found.note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
쇼핑몰 카테고리(목록) URL을 받아 블로그용 HTML 초안을 로컬 파일로 만듭니다.

- Cafe24: `product/list.html?...` 목록에서 `/product/{slug}/{id}/category/.../display/1/` 링크 수집
  (예: recto.co, stuoffice.com)
- 아임웹(imweb): `https://도메인/숫자` 형태 카테고리에서 `?idx=상품번호` 링크 수집
  (예: automaticforthepeople.kr)
- Shopify 컬렉션: URL에 `/collections/` 포함 시, 컬렉션 HTML만 사용(상품 상세는 Cloudflare 등으로
  막히는 경우가 많음). `var meta`의 옵션·가격·SKU 표 + 목록 썸네일 2장을 사용합니다.
- Shopify 단일 상품(PDP): URL에 `/…/products/핸들` 형태(컬렉션 그리드 URL이 아닌 경우)면 해당 페이지 한 번만
  요청합니다. 갤러리 이미지 2장 + `var meta.product` 옵션 표. The Row 등 Eastside Co 사이즈 앱은
  PDP HTML에 있는 CDN JS(`size-guides-prod.esc-apps-cdn.com/…js`)에 `cachedCharts`가 들 있으므로,
  상품 태그와 차트 `tag`가 맞으면 변환 표를 초안에 포함합니다.

각 상품: 이미지 2장 + 상품명 + 사이즈(표 또는 실측 문단) + 구분선. 요청 간 time.sleep 으로 부담을 줄입니다.

사용 전 해당 사이트 이용약관·robots.txt 를 확인하고, 허용된 범위에서만 사용하세요.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from urllib.parse import ParseResult, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 사이즈표 영문 헤더 → 블로그용 한글 (없으면 원문 유지)
HEADER_KO = {
    "size": "사이즈",
    "shoulder": "어깨",
    "chest": "가슴",
    "bust": "가슴",
    "sleeve": "소매",
    "sleeve length": "소매",
    "sleeve length from center back": "중심 소매",
    "sleeve length from centre back": "중심 소매",
    "center back sleeve length": "중심 소매",
    "sleeve opening": "소매통",
    "length": "총장",
    "total length": "총장",
    "waist": "허리",
    "hip": "엉덩이",
    "thigh": "허벅지",
    "rise": "밑위",
    "hem": "밑단",
    "hem width": "밑단",
    "leg opening": "밑통",
    "front rise": "앞밑위",
    "back rise": "뒷밑위",
    "sleeve width": "소매폭",
}

PRODUCT_PATH_RE = re.compile(
    # slug에 한글/괄호/퍼센트 인코딩(예: ...-예약발송)도 올 수 있어 ASCII만 제한하지 않는다.
    r"/product/(?P<slug>[^/\"'?#]+)/(?P<pid>\d+)/category/(?P<cate>\d+)/display/\d+/",
    re.I,
)

# Cafe24 다른 스킨: /product/detail.html?product_no=195&cate_no=53&display_group=1
CAFE24_DETAIL_HREF_RE = re.compile(r"/product/detail\.html\?[^\"'#]*\bproduct_no=(\d+)[^\"'#]*", re.I)

# 아임웹 카테고리 내 상품: /69/?idx=295 또는 /shop_view/?idx=295
IMWEB_PRODUCT_HREF_RE = re.compile(r'href="([^"]+\?idx=(\d+))"', re.I)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
    return s


def _abs_url(base: str, src: str | None) -> str | None:
    if not src:
        return None
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    return urljoin(base, src)


def collect_product_urls(list_html: str, list_url: str) -> list[str]:
    """
    Cafe24 목록에서 상품 상세 링크 수집.

    - /product/slug/id/category/.../display/1/ 형태
    - /product/detail.html?product_no=... 형태
    """
    seen: set[str] = set()
    out: list[str] = []

    # 1) SEO path 형태
    for m in PRODUCT_PATH_RE.finditer(list_html):
        path = m.group(0)
        if path in seen:
            continue
        seen.add(path)
        out.append(urljoin(list_url, path))

    # 2) detail.html?product_no= 형태
    for m in CAFE24_DETAIL_HREF_RE.finditer(list_html):
        href = m.group(0)
        if href in seen:
            continue
        seen.add(href)
        out.append(urljoin(list_url, href))

    return out


def _is_imweb_category_url(parsed: ParseResult) -> bool:
    p = parsed.path.strip("/")
    return bool(p) and p.isdigit()


def collect_imweb_product_urls(list_html: str, list_url: str) -> list[str]:
    seen_idx: set[str] = set()
    out: list[str] = []
    for m in IMWEB_PRODUCT_HREF_RE.finditer(list_html):
        href = m.group(1)
        if "javascript:" in href.lower():
            continue
        if not re.search(r"/(?:\d+)/\?idx=|/shop_view/\?idx=", href):
            continue
        idx = m.group(2)
        if idx in seen_idx:
            continue
        seen_idx.add(idx)
        out.append(urljoin(list_url, href.split("#")[0]))
    return out


def imweb_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1.view_tit")
    if h1:
        return h1.get_text(" ", strip=True)
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        c = og["content"].strip()
        if " :" in c:
            return c.split(" :", 1)[0].strip()
        return c
    t = soup.find("title")
    return (t.get_text(strip=True) if t else "").strip() or "UNTITLED"


def imweb_product_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    box = soup.select_one("#prod_image_list .prod-owl-list")
    urls: list[str] = []
    if box:
        for img in box.select("img[src]"):
            src = (img.get("src") or "").strip()
            if not src or "placeholder" in src.lower():
                continue
            u = _abs_url(page_url, src)
            if u and u not in urls:
                urls.append(u)
    if len(urls) >= 2:
        return urls[:2]
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        u = _abs_url(page_url, og["content"].strip())
        if u:
            if not urls:
                urls.append(u)
            elif urls[0] != u and len(urls) == 1:
                urls.append(u)
    return urls


def imweb_size_note(soup: BeautifulSoup) -> str | None:
    view = soup.select_one("div.goods_summary .fr-view")
    if not view:
        return None
    lines: list[str] = []
    capture = False
    for p in view.find_all("p"):
        t = p.get_text(" ", strip=True)
        if not t:
            continue
        if re.search(r"size\s*guide", t, re.I) or ("사이즈" in t and "가이드" in t):
            capture = True
            continue
        if capture:
            if t.startswith("*") and ("세탁" in t or "드라이클리닝" in t):
                break
            lines.append(t)
    if not lines:
        return None
    return _format_size_note_paragraph("\n".join(lines))


def _is_shopify_collection_url(parsed: ParseResult) -> bool:
    return "/collections/" in (parsed.path or "")


def _is_shopify_product_url(parsed: ParseResult) -> bool:
    """단일 상품 PDP(예: /ko-kr/products/handle). /collections/…/products/… 도 PDP로 처리."""
    path = parsed.path or ""
    return bool(re.search(r"/products/[^/]+/?", path))


def _is_cafe24_product_url(parsed: ParseResult) -> bool:
    """Cafe24 단일 상품 URL(SEO path 또는 detail.html)."""
    path = parsed.path or ""
    if "/product/detail.html" in path:
        return True
    return bool(re.search(r"/product/[^/]+/\d+/", path))


def parse_shopify_var_meta(html: str) -> dict | None:
    needle = "var meta = "
    i = html.find(needle)
    if i < 0:
        return None
    fragment = html[i + len(needle) :].lstrip()
    try:
        obj, _end = json.JSONDecoder().raw_decode(fragment)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("products"), list):
        return obj
    prod = obj.get("product")
    if isinstance(prod, dict):
        return {"products": [prod]}
    return None


def shopify_collection_images_by_handle(soup: BeautifulSoup, base_url: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for div in soup.select(".ProductItem"):
        link = div.select_one('a[href*="/products/"]')
        if not link or not link.get("href"):
            continue
        m = re.search(r"/products/([^/?#]+)", link["href"])
        if not m:
            continue
        handle = m.group(1)
        urls: list[str] = []
        for img in div.select("img.ProductItem__Image"):
            cls = img.get("class") or []
            if "ProductItem__Image--carousel" in cls:
                continue
            src = (img.get("src") or "").strip()
            if not src:
                continue
            u = _abs_url(base_url, src)
            if u and u not in urls:
                urls.append(u)
        if urls:
            out[handle] = urls
    return out


# Eastside Co Size Guides: 동일 shop CDN JS를 여러 PDP에서 재사용
_ESC_CHARTS_CACHE: dict[str, list[dict]] = {}

ESC_SIZE_GUIDES_JS_RE = re.compile(
    r"https://size-guides-prod\.esc-apps-cdn\.com/[a-zA-Z0-9._-]+\.js(?:\?[^\s\"'<>]+)?",
    re.I,
)


def extract_esc_size_guide_script_urls(page_html: str) -> list[str]:
    """PDP/테마 HTML에 포함된 Eastside Co size guides 스크립트 URL(중복 제거, 순서 유지)."""
    normalized = page_html.replace("\\/", "/")
    seen: set[str] = set()
    out: list[str] = []
    for m in ESC_SIZE_GUIDES_JS_RE.finditer(normalized):
        u = m.group(0).strip()
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _json_array_scan_from(s: str, start_bracket: int) -> str | None:
    """s[start_bracket] == '[' 부터 깊이로 닫는 ]까지 (문자열 안의 괄호 무시)."""
    depth = 0
    i = start_bracket
    in_str = False
    esc = False
    n = len(s)
    while i < n:
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return s[start_bracket : i + 1]
        i += 1
    return None


def parse_esc_cached_charts_js(js_text: str) -> list[dict] | None:
    key = "cachedCharts"
    idx = js_text.find(key)
    if idx < 0:
        return None
    i = js_text.find("[", idx)
    if i < 0:
        return None
    raw = _json_array_scan_from(js_text, i)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _shopify_product_tag_set(product: dict) -> set[str]:
    raw = product.get("tags")
    out: set[str] = set()
    if isinstance(raw, list):
        for t in raw:
            if isinstance(t, str) and t.strip():
                out.add(t.strip().lower())
    elif isinstance(raw, str) and raw.strip():
        for t in raw.split(","):
            if t.strip():
                out.add(t.strip().lower())
    return out


def _esc_chart_tag_tokens(chart: dict) -> set[str]:
    raw = (chart.get("tag") or "").lower()
    return {t.strip() for t in raw.split(",") if t.strip()}


def eastsideco_data_tags_from_pdp(pdp_html: str) -> list[str]:
    """`var meta.product`에 tags가 없는 테마용: Eastside 블록의 data-tags."""
    soup = BeautifulSoup(pdp_html, "html.parser")
    div = soup.select_one('div[data-app="eastsideco_sizeGuides"][data-tags]')
    if not div:
        return []
    raw = html.unescape((div.get("data-tags") or "").strip())
    return [t.strip() for t in raw.split(",") if t.strip()]


def _esc_match_tags(product: dict, pdp_html: str | None) -> set[str]:
    tags = _shopify_product_tag_set(product)
    if pdp_html:
        for t in eastsideco_data_tags_from_pdp(pdp_html):
            tags.add(t.lower())
    return tags


def pick_eastsideco_chart_for_product(
    charts: list[dict], product: dict, pdp_html: str | None = None
) -> dict | None:
    ptags = _esc_match_tags(product, pdp_html)
    if not ptags:
        return None
    for ch in charts:
        if not isinstance(ch, dict):
            continue
        if ptags & _esc_chart_tag_tokens(ch):
            return ch
    return None


def _strip_esc_html_cell(fragment: str) -> str:
    fragment = html.unescape(fragment or "")
    frag = fragment.strip()
    if not frag:
        return ""
    cell = BeautifulSoup(frag, "html.parser")
    return cell.get_text(" ", strip=True)


def eastsideco_chart_to_table(chart: dict) -> tuple[list[str], list[list[str]]] | None:
    data = chart.get("data")
    if not isinstance(data, list) or not data:
        return None
    grid: list[list[str]] = []
    for row in data:
        if not isinstance(row, list):
            continue
        grid.append([_strip_esc_html_cell(c) if isinstance(c, str) else "" for c in row])
    if not grid:
        return None
    headers = [h or " " for h in grid[0]]
    body = grid[1:] if len(grid) > 1 else []
    headers = [_normalize_header(h) if h.strip() else " " for h in headers]
    return headers, body


def _blog_data_table_html(headers: list[str], body: list[list[str]]) -> str:
    ths = "".join(
        '<th style="padding:10px 6px;font-weight:600;border-bottom:1px solid #9a9a9a;'
        "font-family:'Nanum Gothic','나눔고딕',system-ui,-apple-system,sans-serif;font-size:15pt;\">"
        f"{html.escape(h)}</th>"
        for h in headers
    )
    trs = []
    for row in body:
        padded = list(row)
        while len(padded) < len(headers):
            padded.append("")
        padded = padded[: len(headers)]
        tds = "".join(
            '<td style="padding:10px 6px;color:#444;'
            "font-family:'Nanum Gothic','나눔고딕',system-ui,-apple-system,sans-serif;font-size:15pt;\">"
            f"{html.escape(c)}</td>"
            for c in padded
        )
        trs.append(f"<tr>{tds}</tr>")
    return (
        '<table style="width:100%;max-width:520px;margin:0 auto;border-collapse:collapse;'
        'text-align:center;">'
        f"<thead><tr>{ths}</tr></thead><tbody>{''.join(trs)}</tbody></table>"
    )


def load_eastsideco_charts(session: requests.Session, pdp_html: str) -> list[dict] | None:
    urls = extract_esc_size_guide_script_urls(pdp_html)
    if not urls:
        return None
    script_url = urls[0]
    if script_url in _ESC_CHARTS_CACHE:
        return _ESC_CHARTS_CACHE[script_url]
    try:
        js_text = fetch(session, script_url)
    except requests.RequestException:
        return None
    charts = parse_esc_cached_charts_js(js_text)
    if charts is None:
        return None
    _ESC_CHARTS_CACHE[script_url] = charts
    return charts


def eastsideco_size_guide_mid_html(session: requests.Session, pdp_html: str, product: dict) -> str | None:
    """매칭되는 Eastside Co 차트가 있으면 제목 + 표 HTML, 없으면 None."""
    charts = load_eastsideco_charts(session, pdp_html)
    if not charts:
        return None
    chart = pick_eastsideco_chart_for_product(charts, product, pdp_html)
    if not chart:
        return None
    tbl = eastsideco_chart_to_table(chart)
    if not tbl:
        return None
    headers, body = tbl
    title = (chart.get("title") or "사이즈 가이드").strip()
    safe_title = html.escape(title)
    cap = (
        f'<p style="margin:20px 0 8px;text-align:center;font-size:12px;font-weight:600;color:#333;">'
        f"{safe_title}</p>"
    )
    table_inner = _blog_data_table_html(headers, body)
    return f'{cap}<div style="overflow-x:auto;">{table_inner}</div>'


def shopify_pdp_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    """PDP 메인 슬라이드 이미지(The Row 테마: Product__SlideItem)."""
    items = soup.select("div.Product__SlideItem.Product__SlideItem--image[data-media-position]")

    def _pos(el) -> int:
        try:
            return int((el.get("data-media-position") or "0").strip())
        except ValueError:
            return 0

    items = sorted(items, key=_pos)
    urls: list[str] = []
    for div in items:
        img = div.select_one("img[src]")
        if not img:
            continue
        src = (img.get("src") or "").strip()
        if not src:
            continue
        u = _abs_url(page_url, src)
        if u and u not in urls:
            urls.append(u)
    if len(urls) >= 2:
        return urls[:2]
    for im in soup.select("a.Product__SlideshowNavImage img[src]"):
        src = (im.get("src") or "").strip()
        if not src:
            continue
        u = _abs_url(page_url, src)
        if u and u not in urls:
            urls.append(u)
        if len(urls) >= 2:
            break
    return urls[:2]


def shopify_product_title_from_meta(product: dict) -> str:
    for v in product.get("variants") or []:
        if not isinstance(v, dict):
            continue
        name = (v.get("name") or "").strip()
        if not name:
            continue
        if " - " in name:
            return name.split(" - ", 1)[0].strip()
        return name
    h = product.get("handle")
    return str(h).replace("-", " ").title() if h else "UNTITLED"


def shopify_variants_table(product: dict) -> tuple[list[str], list[list[str]]] | None:
    variants = product.get("variants")
    if not isinstance(variants, list) or not variants:
        return None
    headers = ["옵션", "가격(USD)", "SKU"]
    body: list[list[str]] = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        opt = (v.get("public_title") or "").strip()
        price = v.get("price")
        if isinstance(price, (int, float)):
            bucks = f"${price / 100:,.0f}"
        else:
            bucks = ""
        sku = (v.get("sku") or "").strip()
        body.append([opt, bucks, sku])
    if not body:
        return None
    return headers, body


def _shopify_products_path_prefix(parsed: ParseResult) -> str:
    segs = [s for s in (parsed.path or "").split("/") if s]
    if not segs:
        return ""
    if len(segs) >= 2 and segs[1] == "collections":
        return "/" + segs[0]
    if len(segs) >= 2 and segs[1] == "products":
        return "/" + segs[0]
    return ""


def shopify_build_block(
    product: dict,
    handle_imgs: dict[str, list[str]],
    base_url: str,
    *,
    from_pdp: bool = False,
    session: requests.Session | None = None,
    pdp_html: str | None = None,
) -> str:
    title = shopify_product_title_from_meta(product)
    handle = product.get("handle") or ""
    imgs = list(handle_imgs.get(handle, []))[:2]
    table = shopify_variants_table(product)
    note: str | None = None
    if not table:
        pfx = _shopify_products_path_prefix(urlparse(base_url))
        rel = f"{pfx}/products/{handle}" if pfx else f"/products/{handle}"
        pdp = urljoin(base_url, rel)
        note = (
            '<p style="text-align:center;font-size:11px;color:#666;max-width:520px;margin:0 auto;">'
            "컬렉션 데이터에 옵션 목록이 없습니다. 실측은 "
            f'<a href="{html.escape(pdp, quote=True)}">상품 페이지</a>'
            "의 사이즈 가이드를 확인해 주세요.</p>"
        )
    mid: str | None = None
    if from_pdp and session is not None and pdp_html:
        mid = eastsideco_size_guide_mid_html(session, pdp_html, product)
    return render_product_block(title, imgs, table, note, mid_html=mid)


def shopify_next_page_href(soup: BeautifulSoup) -> str | None:
    tag = soup.find("link", rel="next")
    if tag and tag.get("href"):
        return tag["href"].strip()
    return None


def shopify_merge_collection_page(
    html: str,
    base_url: str,
    seen_handles: set[str],
    handle_imgs: dict[str, list[str]],
    products_out: list[dict],
) -> None:
    meta = parse_shopify_var_meta(html)
    soup = BeautifulSoup(html, "html.parser")
    for h, lst in shopify_collection_images_by_handle(soup, base_url).items():
        if lst:
            handle_imgs[h] = lst
    if not meta:
        return
    for p in meta.get("products") or []:
        if not isinstance(p, dict):
            continue
        h = p.get("handle")
        if not isinstance(h, str) or not h or h in seen_handles:
            continue
        seen_handles.add(h)
        products_out.append(p)


def build_block_from_detail(soup: BeautifulSoup, page_url: str, engine: str) -> str:
    if engine == "imweb":
        title = imweb_title(soup)
        images = imweb_product_images(soup, page_url)
        note = imweb_size_note(soup)
        return render_product_block(title, images, None, note)

    title = _title_from_page(soup)
    images = _parse_ld_product_images(soup, page_url)
    if not images:
        area = soup.select_one("#desktop-imgarea") or soup.select_one(".imgArea")
        if area:
            for im in area.select("img.ThumbImage"):
                u = _abs_url(page_url, im.get("src"))
                if u:
                    images.append(u)
    table = parse_size_table(soup)
    note = None if table else parse_size_fallback_note(soup)
    return render_product_block(title, images, table, note)


def _parse_ld_product(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = (tag.string or "").strip()
        if not raw or "Product" not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return None


def _parse_ld_product_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    data = _parse_ld_product(soup)
    if not data:
        return []
    images = data.get("image")
    urls: list[str] = []
    if isinstance(images, str):
        u = _abs_url(page_url, images)
        if u:
            urls.append(u)
    elif isinstance(images, list):
        for im in images:
            if isinstance(im, str):
                u = _abs_url(page_url, im)
                if u:
                    urls.append(u)
    return urls


def _title_from_page(soup: BeautifulSoup) -> str:
    data = _parse_ld_product(soup)
    if data and isinstance(data.get("name"), str) and data["name"].strip():
        return data["name"].strip()
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    t = soup.find("title")
    return (t.get_text(strip=True) if t else "").strip() or "UNTITLED"


def _normalize_header(text: str) -> str:
    key = re.sub(r"\s+", " ", text.strip()).lower()
    return HEADER_KO.get(key, text.strip())


def _format_cell(text: str) -> str:
    t = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return f"{t}cm"
    return t


def parse_size_table(soup: BeautifulSoup) -> tuple[list[str], list[list[str]]] | None:
    table = soup.find("table", class_=lambda c: bool(c) and "size-table" in c)
    if not table:
        return None
    rows = table.find_all("tr")
    if not rows:
        return None
    headers: list[str] = []
    body: list[list[str]] = []
    for i, tr in enumerate(rows):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if not cells:
            continue
        if i == 0:
            headers = [_normalize_header(c) for c in cells]
        else:
            body.append([_format_cell(c) for c in cells])
    if not headers:
        return None
    return headers, body


def _format_size_note_paragraph(text: str) -> str:
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return ""
    inner = "<br/>".join(html.escape(ln) for ln in lines)
    return (
        '<p style="text-align:center;font-size:11px;color:#444;line-height:1.65;max-width:520px;'
        f'margin:0 auto;">{inner}</p>'
    )


def _extract_img_urls_from_fragment(fragment: str) -> list[str]:
    frag = (fragment or "").strip()
    if not frag:
        return []
    soup = BeautifulSoup(frag, "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for im in soup.find_all("img", src=True):
        src = (im.get("src") or "").strip()
        if not src or src in seen:
            continue
        seen.add(src)
        out.append(src)
    return out


def _looks_like_size_note(text: str) -> bool:
    t = text.lower()
    return (
        "cm" in t
        or "measurements" in t
        or "size guide" in t
        or "실측" in text
        or "사이즈" in text
        or "화장" in text
        or "shoulders" in t
        or "chest" in t
        or "waist" in t
    )


def parse_size_fallback_note(soup: BeautifulSoup) -> str | None:
    """표가 없는 스킨: simple_desc_css 또는 JSON-LD description."""
    # Kangjungseok 등: 탭 구조에서 'Size guide' 뒤에 이미지(<ol class="tab_wrap"><img ...>)만 있는 경우
    page_base = ""
    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        page_base = canon["href"].strip()
    if not page_base:
        ogu = soup.find("meta", attrs={"property": "og:url"})
        if ogu and ogu.get("content"):
            page_base = ogu["content"].strip()

    for a in soup.find_all("a"):
        txt = a.get_text(" ", strip=True).lower()
        if "size guide" not in txt and "size info" not in txt:
            continue
        cand = a.find_next(["ol", "div", "p"])
        if not cand:
            continue
        imgs = cand.find_all("img", src=True)
        if not imgs:
            continue
        parts: list[str] = []
        for im in imgs:
            src = (im.get("src") or "").strip()
            abs_src = _abs_url(page_base, src) if src else None
            if abs_src:
                parts.append(f'<img src="{html.escape(abs_src, quote=True)}" alt="" />')
        if parts:
            return "".join(parts)

    cell = soup.find("td", class_=lambda c: bool(c) and "simple_desc_css" in c)
    if cell:
        text = cell.get_text("\n", strip=True)
        if text and _looks_like_size_note(text):
            slim = _extract_size_info_note_block(text) or text
            return _format_size_note_paragraph(slim)
    # Epicenter 등: 아코디언 'Size guide' 섹션에 실측이 들어감
    h = soup.find(lambda t: getattr(t, "name", None) in ("h3", "h4", "h5") and "size guide" in t.get_text(" ", strip=True).lower())
    if h:
        li = h.find_parent("li") or h.parent
        desc_div = None
        if li:
            desc_div = li.find("div", class_=lambda c: bool(c) and "accordion-desc" in c)
        if not desc_div:
            desc_div = h.find_next("div", class_=lambda c: bool(c) and "accordion-desc" in c)
        if desc_div:
            txt = desc_div.get_text("\n", strip=True)
            if txt and _looks_like_size_note(txt):
                return _format_size_note_paragraph("Size guide\n" + txt)
    # Epicenter 일부 페이지: 본문에 '#####  Size guide' 형태로 들어가기도 해서
    # 페이지 전체 텍스트에서 size guide 이후만 잘라서 전달
    full = soup.get_text("\n", strip=True)
    low = full.lower()
    if "size guide" in low:
        si = low.find("size guide")
        cut = full[si : si + 6000]
        for tok in ("return&exchange", "return & exchange", "return", "exchange", "delivery", "배송"):
            j = cut.lower().find(tok)
            if j > 0:
                cut = cut[:j]
                break
        if cut and _looks_like_size_note(cut):
            return _format_size_note_paragraph(cut)
    # 마지막 fallback: JSON-LD description
    data = _parse_ld_product(soup)
    if data and isinstance(data.get("description"), str):
        desc = data["description"].strip()
        if desc and _looks_like_size_note(desc):
            slim = _extract_size_info_note_block(desc) or desc
            return _format_size_note_paragraph(slim)

    return None


def _extract_size_info_note_block(text: str) -> str | None:
    """
    HTAE 같은 상세 설명에서 'Size Info (cm)' 구간만 잘라 반환.
    (INFORMATION/DELIVERY INFO/SHIPPING INFO 이전까지만)
    """
    if not text:
        return None
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    start = None
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "size info" in low or "size guide" == low:
            start = i
            break
    if start is None:
        return None

    stop_tokens = (
        "information",
        "delivery info",
        "shipping info",
        "exchange",
        "return",
        "구매방법",
        "상품 옵션",
    )
    picked: list[str] = []
    for ln in lines[start:]:
        low = ln.lower()
        if any(tok in low for tok in stop_tokens):
            break
        if ln:
            picked.append(ln)
    if not picked:
        return None
    return "\n".join(picked)


def _html_fragment_to_plain_text(fragment: str) -> str:
    """네이버 붙여넣기용: 간단히 텍스트만 추출."""
    frag = (fragment or "").strip()
    if not frag:
        return ""
    soup = BeautifulSoup(frag, "html.parser")
    return soup.get_text("\n", strip=True)


def _table_to_tsv(headers: list[str], body: list[list[str]]) -> str:
    def esc_cell(v: str) -> str:
        # 네이버 표 붙여넣기: 탭/개행은 최소화
        return (v or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()

    lines: list[str] = []
    lines.append("\t".join(esc_cell(h) for h in headers))
    for row in body:
        padded = list(row)
        while len(padded) < len(headers):
            padded.append("")
        padded = padded[: len(headers)]
        lines.append("\t".join(esc_cell(c) for c in padded))
    return "\n".join(lines).strip() + "\n"


def _format_number_cm(v: str) -> str:
    t = (v or "").strip()
    if not t:
        return ""
    # 38.5 / 38.5cm / 38,5 같은 값 대응
    t2 = t.replace(",", ".")
    if re.fullmatch(r"\d+(\.\d+)?", t2):
        return f"{t2}cm"
    return t


def _extract_measurements_tables(text: str) -> list[tuple[str, tuple[list[str], list[list[str]]]]]:
    """
    Cafe24 상세 설명에 자주 있는 Measurements 블록을 TSV 표로 변환.

    패턴 예)
      Measurements
      1 - Waist    38.5
      Rise      28.8
      ...
      2 - Waist    41
      ...
    """
    if not text:
        return []
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]

    out: list[tuple[str, tuple[list[str], list[list[str]]]]] = []
    i = 0
    n = len(lines)
    while i < n:
        if not lines[i].strip().lower().startswith("measurements"):
            i += 1
            continue
        i += 1
        # size -> key -> value
        size_map: dict[str, dict[str, str]] = {}
        keys: list[str] = []
        cur_size: str | None = None

        while i < n:
            raw = lines[i].strip()
            if not raw:
                i += 1
                continue
            low = raw.lower()
            if low in ("one size", "onesize", "one-size", "free", "os"):
                cur_size = "ONE SIZE"
                i += 1
                continue
            if low.startswith("(") or "deviation" in low or low.startswith("woman ") or low.startswith("man "):
                break
            if raw.startswith("-") and "fit" in low:
                # 다음 상품 설명으로 넘어가는 케이스 방지
                break

            # 불릿/대시로 시작하는 항목(- Head circumference 61.5 등)
            raw_item = raw
            if raw_item.startswith(("-", "•", "·")):
                raw_item = raw_item.lstrip("-•·").strip()
            low_item = raw_item.lower()

            # 가방/소품류: H30cm x W36cm x D5cm 형태
            m_dim = re.match(
                r"^(?:H|Height)\s*([0-9]+(?:\.[0-9]+)?)\s*cm?\s*[x×]\s*"
                r"(?:W|Width)\s*([0-9]+(?:\.[0-9]+)?)\s*cm?\s*[x×]\s*"
                r"(?:D|Depth)\s*([0-9]+(?:\.[0-9]+)?)\s*cm?\s*$",
                raw,
                re.I,
            )
            if not m_dim:
                # 라벨 없이 숫자만: 30cm x 36cm x 5cm
                m_dim = re.match(
                    r"^([0-9]+(?:\.[0-9]+)?)\s*cm?\s*[x×]\s*"
                    r"([0-9]+(?:\.[0-9]+)?)\s*cm?\s*[x×]\s*"
                    r"([0-9]+(?:\.[0-9]+)?)\s*cm?\s*$",
                    raw,
                    re.I,
                )
                if m_dim:
                    key_vals = [("Height", m_dim.group(1)), ("Width", m_dim.group(2)), ("Depth", m_dim.group(3))]
                else:
                    key_vals = None
            else:
                key_vals = [("Height", m_dim.group(1)), ("Width", m_dim.group(2)), ("Depth", m_dim.group(3))]

            if key_vals:
                if not cur_size:
                    cur_size = "ONE SIZE"
                if cur_size not in size_map:
                    size_map[cur_size] = {}
                for key, val in key_vals:
                    if key not in keys:
                        keys.append(key)
                    size_map[cur_size][key] = _format_number_cm(val)
                i += 1
                continue

            num = r"(\d+(?:\.\d+)?|\d+(?:,\d+)?)(?:\s*(?:cm|㎝))?"
            m = re.match(
                rf"^([A-Za-z0-9]+)\s*-\s*([A-Za-z][A-Za-z /_-]*)\s+({num})\s*$",
                raw_item,
            )
            if m:
                cur_size = m.group(1)
                key = m.group(2).strip()
                val = m.group(3).strip()
            else:
                m2 = re.match(rf"^([A-Za-z][A-Za-z /_-]*)\s+({num})\s*$", raw_item)
                if not m2:
                    # Measurements 밖의 문장(예: Fabric) 만나면 종료
                    break
                if not cur_size:
                    cur_size = "ONE SIZE"
                key = m2.group(1).strip()
                val = m2.group(2).strip()

            if cur_size not in size_map:
                size_map[cur_size] = {}
            if key not in keys:
                keys.append(key)
            size_map[cur_size][key] = _format_number_cm(val)
            i += 1

        if size_map and keys:
            # 헤더: 사이즈 + keys
            headers = ["사이즈"] + [_normalize_header(k) for k in keys]
            body: list[list[str]] = []
            order = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"]

            def _sz_key(sz: str) -> tuple[int, int, str]:
                szu = (sz or "").strip().upper()
                if szu in ("ONE SIZE", "ONESIZE", "ONE-SIZE", "FREE", "OS"):
                    return (3, 0, "")
                if szu.isdigit():
                    return (0, int(szu), "")
                if szu in order:
                    return (1, order.index(szu), "")
                return (2, 0, szu)

            for sz in sorted(size_map.keys(), key=_sz_key):
                row = [sz]
                for k in keys:
                    row.append(size_map[sz].get(k, ""))
                body.append(row)
            out.append(("Measurements", (headers, body)))
        continue

    return out


def _extract_size_guide_tables(text: str) -> list[tuple[str, tuple[list[str], list[list[str]]]]]:
    """
    Epicenter 같은 Cafe24 커스텀 상세에서 'Size guide' 섹션을 표로 변환.

    예) Size guide 이후:
      M
      허리 38
      허벅지 32
      ...
      L
      허리 40
      ...
    """
    if not text:
        return []
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    # 시작 지점: 'Size guide' 또는 'SIZE GUIDE'
    start = None
    for idx, ln in enumerate(lines):
        if "size guide" in (ln or "").lower():
            start = idx + 1
            break
    if start is None:
        return []

    # Aieul/Cafe24 일부: 토큰이 줄단위로 펼쳐진 matrix 포맷
    # 예)
    #   Size Guide
    #   Size
    #   Shoulders
    #   Chest
    #   Sleeve
    #   Length
    #   46
    #   42
    #   51
    #   62
    #   61
    #   ...
    block_lines: list[str] = []
    stop_tokens = ("details", "fabric", "colour", "color", "shipping", "information", "delivery", "exchange", "return")
    for ln in lines[start:]:
        if not ln:
            continue
        low = ln.lower()
        if any(tok in low for tok in stop_tokens):
            break
        block_lines.append(ln)

    def _is_size_marker_token(tok: str) -> bool:
        t = (tok or "").strip().upper()
        if not t:
            return False
        if t in {"XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "ONE SIZE", "ONESIZE", "FREE", "OS"}:
            return True
        return bool(re.match(r"^\d", tok.strip()))

    if block_lines and block_lines[0].strip().lower() == "size":
        first_val_idx = None
        for bi in range(1, len(block_lines)):
            if _is_size_marker_token(block_lines[bi]):
                first_val_idx = bi
                break
        if first_val_idx and first_val_idx >= 2:
            raw_headers = block_lines[:first_val_idx]
            vals = block_lines[first_val_idx:]
            ncol = len(raw_headers)
            nrow = len(vals) // ncol
            if nrow >= 1:
                vals = vals[: nrow * ncol]
                headers = [_normalize_header(h) if re.search(r"[A-Za-z]", h) else h for h in raw_headers]
                body = [vals[ri * ncol : (ri + 1) * ncol] for ri in range(nrow)]
                return [("Size guide", (headers, body))]

    size_map: dict[str, dict[str, str]] = {}
    keys: list[str] = []
    cur_size: str | None = None

    size_token_re = re.compile(r"^(XXS|XS|S|M|L|XL|XXL|XXXL|ONE\s*SIZE|ONESIZE|FREE|OS)$", re.I)
    # 한국어 키/값: 허리 38 / 총장 106 / 밑단 25.5
    kv_ko_re = re.compile(
        r"^(허리|가슴|어깨|소매|소매통|허벅지|앞밑위|뒷밑위|밑단|총장)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:cm|㎝)?\s*$"
    )
    kv_en_re = re.compile(
        r"^([A-Za-z][A-Za-z /_-]*)\s*([0-9]+(?:\.[0-9]+)?)\s*$"
    )

    for ln in lines[start:]:
        if not ln:
            continue
        low = ln.lower()
        if low.startswith("*") or "실측" in ln or "오차" in ln or "deviation" in low:
            break
        # 사이즈 라벨 단독 라인
        if size_token_re.match(ln):
            cur_size = ln.strip().upper().replace("  ", " ")
            if cur_size in ("ONESIZE", "ONE-SIZE", "ONE SIZE"):
                cur_size = "ONE SIZE"
            if cur_size not in size_map:
                size_map[cur_size] = {}
            continue

        # 불릿 제거
        raw_item = ln.lstrip("-•·").strip()

        # 스카프/소품류: 130 / 35 같은 형태 (가로/세로)
        m_slash = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)\s*$", raw_item)
        if m_slash:
            if not cur_size:
                cur_size = "ONE SIZE"
                size_map.setdefault(cur_size, {})
            if "가로" not in keys:
                keys.append("가로")
            if "세로" not in keys:
                keys.append("세로")
            size_map[cur_size]["가로"] = _format_number_cm(m_slash.group(1))
            size_map[cur_size]["세로"] = _format_number_cm(m_slash.group(2))
            continue

        m = kv_ko_re.match(raw_item)
        if m:
            if not cur_size:
                cur_size = "ONE SIZE"
                size_map.setdefault(cur_size, {})
            key = m.group(1).strip()
            val = _format_number_cm(m.group(2).strip())
            if key not in keys:
                keys.append(key)
            size_map[cur_size][key] = val
            continue

        m2 = kv_en_re.match(raw_item)
        if m2:
            if not cur_size:
                cur_size = "ONE SIZE"
                size_map.setdefault(cur_size, {})
            key = m2.group(1).strip()
            val = _format_number_cm(m2.group(2).strip())
            if key not in keys:
                keys.append(key)
            size_map[cur_size][key] = val
            continue

    if not size_map or not keys:
        return []

    headers = ["사이즈"] + [_normalize_header(k) if re.search(r"[A-Za-z]", k) else k for k in keys]
    order = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "ONE SIZE"]

    def _sz_key(sz: str) -> tuple[int, int, str]:
        szu = (sz or "").strip().upper()
        if szu in order:
            return (0, order.index(szu), "")
        return (1, 0, szu)

    body: list[list[str]] = []
    for sz in sorted(size_map.keys(), key=_sz_key):
        row = [sz]
        for k in keys:
            row.append(size_map[sz].get(k, ""))
        body.append(row)
    return [("Size guide", (headers, body))]


def _extract_size_info_tables(text: str) -> list[tuple[str, tuple[list[str], list[list[str]]]]]:
    """
    HTAE/Cafe24 일부 상세의 'Size Info (cm)' 포맷을 표로 변환.

    예)
      Size Info (cm)
      Waist(허리) / Front Rise(밑위) / Thigh(허벅지) / Length(기장) / Hem(밑단)
      1(28) 37.5 / 30 / 30 / 104 / 21
      2(30) 40 / 31 / 31 / 106 / 22
    """
    if not text:
        return []
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    start = None
    for i, ln in enumerate(lines):
        if "size info" in ln.lower():
            start = i
            break
    if start is None:
        return []

    # 헤더 라인 탐색(대개 '/' 구분)
    header_line = None
    j = start + 1
    while j < len(lines):
        ln = lines[j]
        low = ln.lower()
        if not ln:
            j += 1
            continue
        if any(tok in low for tok in ("information", "delivery info", "shipping info", "exchange", "return")):
            return []
        if "/" in ln:
            header_line = ln
            j += 1
            break
        j += 1
    if not header_line:
        return []

    raw_headers = [h.strip() for h in header_line.split("/") if h.strip()]
    if not raw_headers:
        return []
    headers = ["사이즈"] + [_normalize_header(h) for h in raw_headers]

    body: list[list[str]] = []
    row_re = re.compile(r"^([0-9A-Za-z]+(?:\([^)]*\))?)\s+(.+)$")
    stop_tokens = ("information", "delivery info", "shipping info", "exchange", "return", "구매방법", "상품 옵션")
    for ln in lines[j:]:
        low = ln.lower()
        if not ln:
            continue
        if any(tok in low for tok in stop_tokens):
            break
        if ln.startswith("*"):
            break
        m = row_re.match(ln)
        if not m:
            # 모델 스펙/설명 문장 시작 시 종료
            if re.search(r"[A-Za-z가-힣]", ln) and "/" not in ln:
                break
            continue
        size_lbl = m.group(1).strip()
        vals = [v.strip() for v in m.group(2).split("/") if v.strip()]
        if len(vals) < len(raw_headers):
            continue
        vals = vals[: len(raw_headers)]
        body.append([size_lbl] + [_format_number_cm(v) for v in vals])

    if not body:
        return []
    return [("Size Info", (headers, body))]


def render_product_block_naver_text(
    title: str,
    img_urls: list[str],
    table: tuple[list[str], list[list[str]]] | None,
    size_note_html: str | None,
    *,
    mid_html: str | None = None,
) -> str:
    imgs = (img_urls or [])[:2]
    if len(imgs) == 1:
        imgs = [imgs[0], imgs[0]]

    lines: list[str] = []
    lines.append(title.strip() or "UNTITLED")
    if imgs:
        lines.append("")  # 이미지 URL은 따로 붙여넣기 쉽게
        for i, u in enumerate(imgs, start=1):
            lines.append(f"이미지{i}\t{u}")

    lines.append("")  # 본문/표 구분
    if table:
        headers, body = table
        lines.append(_table_to_tsv(headers, body).rstrip())
    elif size_note_html:
        plain = _html_fragment_to_plain_text(size_note_html)
        # Measurements가 있으면 표로도 같이 뽑아주기(네이버용)
        ms = _extract_measurements_tables(plain)
        if not ms:
            ms = _extract_size_guide_tables(plain)
        if not ms:
            ms = _extract_size_info_tables(plain)
        if ms:
            # 옵션/제품 설명 텍스트는 제외하고, 실측 표만 남김
            for _title, (h, b) in ms:
                lines.append("실측(Measurements)")
                lines.append(_table_to_tsv(h, b).rstrip())
                lines.append("")
        else:
            # Measurements가 없으면(=사이즈 안내 문구만 있는 케이스)만 출력
            lines.append(plain)
    else:
        lines.append("사이즈 표·실측 문구를 찾지 못했습니다. 상품 페이지에서 확인해 주세요.")

    if mid_html:
        mid_text = _html_fragment_to_plain_text(mid_html)
        if mid_text:
            lines.append("")
            lines.append(mid_text)

    # 네이버 구분선 대체(에디터에서 수평선 삽입해도 됨)
    lines.append("")
    lines.append("-" * 30)
    return "\n".join(lines).strip() + "\n\n"


def _naver_span(text: str, *, font_pt: int, bold: bool = False) -> str:
    w = "700" if bold else "400"
    return (
        "<span "
        "class=\"se-fs se-ff-nanumgothic se-fs15 se-node\" "
        f"style=\"color: rgb(0, 0, 0); font-family: 'Nanum Gothic','나눔고딕',sans-serif; "
        f"font-size: {font_pt}pt; font-weight: {w};\">"
        f"{html.escape(text)}</span>"
    )


def _naver_hr(*, width_pct: int = 100) -> str:
    """
    네이버 붙여넣기 시 <hr> 스타일이 정리되는 경우가 있어,
    se-horizontalLine 구조 + border-top 1px div로 '한 줄'을 그립니다.
    """
    w = max(1, min(100, int(width_pct)))
    return (
        "<div class=\"se-component se-horizontalLine se-l-default\" data-a11y-title=\"구분선\">"
        "<div class=\"se-component-content\">"
        f"<div class=\"se-section se-section-horizontalLine se-l-default se-section-align-center\" style=\"width:{w}%;margin:0 auto;\">"
        "<div style=\"width:100%;border-top:1px solid #ddd;height:0;line-height:0;display:block;\"></div>"
        "</div></div></div>"
    )


def _naver_spacer(px: int = 30) -> str:
    return f"<div style=\"height:{int(px)}px;\"></div>"


def _naver_image_strip(img1: str, img2: str) -> str:
    # 네이버 이미지 스트립 느낌(50% + 50%)
    def one(src: str) -> str:
        return (
            "<div class=\"se-module se-module-image se-unit\" "
            "style=\"flex:0 0 50%;max-width:50%;width:50%;\">"
            "<div class=\"se-module-image-container\" style=\"width:100%;\">"
            f"<img src=\"{html.escape(src, quote=True)}\" alt=\"\" class=\"se-image-resource\" "
            "style=\"width:100%;height:auto;display:block;object-fit:cover;\"/>"
            "</div></div>"
        )

    return (
        "<div class=\"se-component se-imageStrip2 se-component-imageStrip se-l-default\" data-a11y-title=\"나란히 사진\">"
        "<div class=\"se-component-content se-component-content-extend\">"
        "<div class=\"se-section se-section-imageStrip2 se-l-default se-section-align-center\">"
        "<div class=\"se-imageStrip2-container\" style=\"display:flex;flex-wrap:nowrap;gap:0;width:100%;\">"
        f"{one(img1)}{one(img2)}"
        "</div></div></div></div>"
    )


def _naver_text_center(text_html: str, *, width_pct: int = 50) -> str:
    return (
        "<div class=\"se-component se-text se-l-default\" data-a11y-title=\"본문\">"
        "<div class=\"se-component-content\">"
        f"<div class=\"se-section se-section-text se-l-default\" "
        f"style=\"width:{width_pct}%;margin:0 auto;text-align:center;\">"
        "<p class=\"se-text-paragraph se-text-paragraph-align-center\" "
        "style=\"line-height:1.8;text-align:center;margin:0;\">"
        f"{text_html}"
        "</p></div></div></div>"
    )


def _naver_table_from_tsv(headers: list[str], body: list[list[str]], *, width_pct: int = 50) -> str:
    # 네이버 표 느낌(테두리 없음, 가운데 정렬)
    # 폭은 균등 분배
    cols = max(1, len(headers))
    col_w = max(5, int(100 / cols))

    def td(inner: str, *, is_th: bool = False) -> str:
        tag = "th" if is_th else "td"
        return (
            f"<{tag} class=\"se-cell se-cell-{ 'th' if is_th else 'td' }\" "
            f"style=\"width:{col_w}%;height:40px;box-sizing:border-box;padding:0 8px;"
            "text-align:center;vertical-align:middle;white-space:nowrap;"
            "border-width:medium;border-style:none;border-color:currentcolor;\">"
            "<div class=\"se-module se-module-text se-unit\">"
            "<div class=\"se-module-text-paragraph se-text-paragraph-align-center\" style=\"line-height: 1.6;\">"
            f"{inner}"
            "</div></div></"
            f"{tag}>"
        )

    header_row = "<tr class=\"se-tr\">" + "".join(td(_naver_span(h, font_pt=13, bold=True), is_th=True) for h in headers) + "</tr>"
    body_rows = []
    for row in body:
        padded = list(row)
        while len(padded) < len(headers):
            padded.append("")
        padded = padded[: len(headers)]
        body_rows.append(
            "<tr class=\"se-tr\">" + "".join(td(_naver_span(c, font_pt=13), is_th=False) for c in padded) + "</tr>"
        )

    colgroup = "<colgroup>" + "".join(f"<col style=\"width:{col_w}%;\"/>" for _ in range(cols)) + "</colgroup>"

    return (
        "<div class=\"se-component se-table se-l-default\" data-a11y-title=\"표\">"
        "<div class=\"se-component-content\">"
        f"<div class=\"se-section se-section-table se-l-default se-section-align-center\" style=\"width:{width_pct}%;margin:0 auto;\">"
        "<div class=\"se-table-container\">"
        "<table class=\"se-table-content\" "
        "style=\"border-width:medium;border-style:none;border-color:currentcolor;border-image:initial;"
        "width:100%;table-layout:fixed;border-collapse:collapse;\">"
        "<tbody>"
        f"{colgroup}{header_row}{''.join(body_rows)}"
        "</tbody></table></div></div></div></div>"
    )


def render_product_block_naver_html(
    title: str,
    img_urls: list[str],
    table: tuple[list[str], list[list[str]]] | None,
    size_note_html: str | None,
    *,
    mid_html: str | None = None,
    title_pt: int = 17,
    table_pt: int = 13,
    text_width_pct: int = 100,
    table_width_pct: int = 80,
    gap_px: int = 60,
) -> str:
    imgs = (img_urls or [])[:2]
    if len(imgs) == 1:
        imgs = [imgs[0], imgs[0]]
    if len(imgs) < 2:
        imgs = imgs + [""] * (2 - len(imgs))

    parts: list[str] = []
    note_imgs: list[str] = _extract_img_urls_from_fragment(size_note_html or "")
    if imgs[0] and imgs[1]:
        parts.append(_naver_image_strip(imgs[0], imgs[1]))
        parts.append(_naver_spacer(gap_px))

    parts.append(
        _naver_text_center(
            _naver_span(title.strip() or "UNTITLED", font_pt=title_pt, bold=False),
            width_pct=text_width_pct,
        )
    )
    parts.append(_naver_spacer(gap_px))

    # 표 우선: (1) 기존 table, (2) Measurements 파싱, (3) 문단 텍스트
    picked_table = table
    extra_text = None
    if not picked_table and size_note_html:
        plain = _html_fragment_to_plain_text(size_note_html)
        ms = _extract_measurements_tables(plain)
        if not ms:
            ms = _extract_size_guide_tables(plain)
        if not ms:
            ms = _extract_size_info_tables(plain)
        if ms:
            _t, (h, b) = ms[0]
            picked_table = (h, b)
        else:
            extra_text = plain.strip()

    if picked_table:
        h, b = picked_table
        # 폰트 크기 주입 위해 span 생성 함수를 table_pt로 교체
        # _naver_table_from_tsv는 내부에서 13pt로 기본 생성하므로 여기서 table_pt 반영
        table_html = _naver_table_from_tsv(
            [str(x) for x in h],
            [[str(x) for x in row] for row in b],
            width_pct=table_width_pct,
        )
        if table_pt != 13:
            # 간단 치환: font-size: 13pt -> font-size: {table_pt}pt
            table_html = table_html.replace("font-size: 13pt;", f"font-size: {table_pt}pt;")
        parts.append(table_html)
        parts.append(_naver_spacer(gap_px))
        # 옵션표는 있는데 상세의 Size guide가 이미지인 경우, 이미지를 추가로 붙인다.
        if note_imgs:
            ni1 = note_imgs[0]
            ni2 = note_imgs[1] if len(note_imgs) > 1 else note_imgs[0]
            parts.append(_naver_image_strip(ni1, ni2))
            parts.append(_naver_spacer(gap_px))
    elif extra_text:
        parts.append(_naver_text_center(_naver_span(extra_text, font_pt=13), width_pct=text_width_pct))
        parts.append(_naver_spacer(gap_px))
    elif note_imgs:
        # 텍스트가 없고 이미지형 Size guide만 있는 경우
        i1 = note_imgs[0]
        i2 = note_imgs[1] if len(note_imgs) > 1 else note_imgs[0]
        parts.append(_naver_image_strip(i1, i2))
        parts.append(_naver_spacer(gap_px))

    if mid_html:
        mid_text = _html_fragment_to_plain_text(mid_html).strip()
        if mid_text:
            parts.append(_naver_text_center(_naver_span(mid_text, font_pt=13), width_pct=text_width_pct))
            parts.append(_naver_spacer(gap_px))

    parts.append(_naver_hr(width_pct=85))
    parts.append(_naver_spacer(gap_px))
    return "".join(parts)


def render_product_block(
    title: str,
    img_urls: list[str],
    table: tuple[list[str], list[list[str]]] | None,
    size_note_html: str | None,
    *,
    mid_html: str | None = None,
    footer_html: str | None = None,
) -> str:
    imgs = img_urls[:2]
    while len(imgs) < 2 and img_urls:
        imgs.append(img_urls[0])
    if len(imgs) == 1:
        imgs = [imgs[0], imgs[0]]

    safe_title = html.escape(title)
    img_tags = []
    for u in imgs[:2]:
        img_tags.append(
            f'<img src="{html.escape(u, quote=True)}" alt="" '
            'style="max-width:48%;width:48%;height:auto;vertical-align:top;object-fit:contain;" />'
        )
    images_row = (
        f'<div style="display:flex;justify-content:center;gap:6px;flex-wrap:wrap;margin:0 auto;">'
        f'{"".join(img_tags)}</div>'
    )
    name_block = (
        f'<p style="margin:18px 0 14px;text-align:center;'
        "font-family:'Nanum Gothic','나눔고딕',system-ui,-apple-system,sans-serif;"
        "font-size:19pt;letter-spacing:0.04em;text-transform:uppercase;color:#111;\">"
        f"{safe_title}</p>"
    )

    if table:
        headers, body = table
        table_html = _blog_data_table_html(headers, body)
    elif size_note_html:
        table_html = size_note_html
    else:
        table_html = (
            '<p style="text-align:center;font-size:11px;color:#888;">'
            "사이즈 표·실측 문구를 찾지 못했습니다. 상품 페이지에서 확인해 주세요.</p>"
        )

    hr = '<hr style="border:0;border-top:1px solid #ddd;margin:28px auto 0;max-width:560px;" />'

    mid = mid_html or ""
    foot = footer_html or ""
    return (
        '<div style="max-width:560px;margin:0 auto 40px;font-family:system-ui,-apple-system,sans-serif;">'
        f"{images_row}{name_block}{table_html}{mid}{foot}{hr}</div>"
    )


def fetch(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=45)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def main() -> None:
    ap = argparse.ArgumentParser(description="쇼핑몰 목록 URL → 블로그용 HTML 파일 (로컬)")
    ap.add_argument(
        "list_url",
        help="Cafe24 목록 / 아임웹 / Shopify 컬렉션 / Shopify 단일 상품(.../products/핸들) URL",
    )
    ap.add_argument("-o", "--output", default="recto_blog_draft.html", help="출력 HTML 경로")
    ap.add_argument(
        "--format",
        choices=["html", "naver", "naver_html"],
        default="html",
        help="출력 포맷: html(기본) / naver(텍스트+TSV 표) / naver_html(네이버 se-* 구조 HTML)",
    )
    ap.add_argument("--limit", type=int, default=0, help="처리할 상품 수 상한 (0이면 전체)")
    ap.add_argument("--sleep", type=float, default=1.2, help="요청 사이 대기(초)")
    ap.add_argument(
        "--shopify-pages",
        type=int,
        default=3,
        metavar="N",
        help="Shopify 컬렉션만: 가져올 목록 페이지 수(기본 3, rel=next 따라감)",
    )
    args = ap.parse_args()

    list_url = args.list_url.strip()
    parsed = urlparse(list_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SystemExit("http(s) 목록 URL을 입력하세요.")
    cafe24_list = (
        "list.html" in parsed.path
        or "/product/list" in parsed.path
        or ("/category/" in parsed.path and not _is_shopify_collection_url(parsed))
    )
    cafe24_prod = _is_cafe24_product_url(parsed) and not _is_shopify_product_url(parsed)
    imweb_cat = _is_imweb_category_url(parsed)
    shopify_pdp = _is_shopify_product_url(parsed)
    shopify_col = _is_shopify_collection_url(parsed) and not shopify_pdp
    if not cafe24_list and not cafe24_prod and not imweb_cat and not shopify_col and not shopify_pdp:
        raise SystemExit(
            "지원 형식: Cafe24 목록(.../product/list.html?...) / 아임웹(.../숫자/) / "
            "Shopify 컬렉션(.../collections/...) / Shopify 단일 상품(.../products/핸들) / "
            "Cafe24 단일 상품(.../product/... 또는 .../product/detail.html?product_no=...)"
        )

    session = _session()
    print(f"[1/2] 목록 가져오기: {list_url}")
    list_html = fetch(session, list_url)
    time.sleep(args.sleep)

    product_urls = [] if cafe24_prod else collect_product_urls(list_html, list_url)
    engine = "cafe24"
    shopify_products: list[dict] | None = None
    shopify_handle_imgs: dict[str, list[str]] | None = None

    if cafe24_prod:
        engine = "cafe24"
        product_urls = [list_url]
    elif not product_urls and imweb_cat:
        product_urls = collect_imweb_product_urls(list_html, list_url)
        engine = "imweb"
    elif not product_urls and shopify_pdp:
        engine = "shopify"
        meta = parse_shopify_var_meta(list_html)
        soup_pdp = BeautifulSoup(list_html, "html.parser")
        sprods = [p for p in (meta.get("products") or []) if isinstance(p, dict)] if meta else []
        himgs: dict[str, list[str]] = {}
        pdp_gallery = shopify_pdp_images(soup_pdp, list_url)
        for p in sprods:
            h = p.get("handle")
            if isinstance(h, str) and h and h not in himgs and pdp_gallery:
                himgs[h] = list(pdp_gallery)
        shopify_products = sprods
        shopify_handle_imgs = himgs
        product_urls = []
    elif not product_urls and shopify_col:
        engine = "shopify"
        seen_h: set[str] = set()
        himgs = {}
        sprods = []
        shopify_merge_collection_page(list_html, list_url, seen_h, himgs, sprods)
        soup_pg = BeautifulSoup(list_html, "html.parser")
        next_h = shopify_next_page_href(soup_pg)
        pages_done = 1
        max_pg = max(1, args.shopify_pages)
        while next_h and pages_done < max_pg:
            next_u = urljoin(list_url, next_h)
            print(f"      Shopify 페이지 {pages_done + 1}: {next_u}")
            h2 = fetch(session, next_u)
            time.sleep(args.sleep)
            shopify_merge_collection_page(h2, list_url, seen_h, himgs, sprods)
            soup_pg = BeautifulSoup(h2, "html.parser")
            next_h = shopify_next_page_href(soup_pg)
            pages_done += 1
        shopify_products = sprods
        shopify_handle_imgs = himgs
        product_urls = []

    if not product_urls and engine != "shopify":
        raise SystemExit("목록에서 상품 링크를 찾지 못했습니다.")
    if engine == "shopify" and not (shopify_products or []):
        raise SystemExit(
            "Shopify에서 상품 메타(var meta의 products 또는 product)를 찾지 못했습니다. "
            "테마·차단 여부를 확인하세요."
        )

    if engine == "shopify":
        src = "Shopify PDP 1회" if shopify_pdp else "컬렉션 목록만"
        print(f"      엔진: shopify, 상품 {len(shopify_products or [])}개 ({src})")
    else:
        print(f"      엔진: {engine}, 상품 {len(product_urls)}개")

    blocks: list[str] = []
    if engine == "shopify":
        prods = list(shopify_products or [])
        if args.limit and args.limit > 0:
            prods = prods[: args.limit]
        him = shopify_handle_imgs or {}
        for i, prod in enumerate(prods, start=1):
            h = prod.get("handle", "")
            print(f"[2/2] 상품 {i}/{len(prods)}: {h}")
            mid = None
            imgs = list(him.get(h, []))[:2] if isinstance(h, str) else []
            table = shopify_variants_table(prod)
            if shopify_pdp:
                mid = eastsideco_size_guide_mid_html(session, list_html, prod)

            if args.format == "naver":
                blocks.append(
                    render_product_block_naver_text(
                        shopify_product_title_from_meta(prod),
                        imgs,
                        table,
                        None,
                        mid_html=mid,
                    )
                )
            elif args.format == "naver_html":
                blocks.append(
                    render_product_block_naver_html(
                        shopify_product_title_from_meta(prod),
                        imgs,
                        table,
                        None,
                        mid_html=mid,
                        title_pt=17,
                        table_pt=13,
                    )
                )
            else:
                blocks.append(
                    shopify_build_block(
                        prod,
                        him,
                        list_url,
                        from_pdp=shopify_pdp,
                        session=session if shopify_pdp else None,
                        pdp_html=list_html if shopify_pdp else None,
                    )
                )
            if shopify_pdp:
                time.sleep(args.sleep)
        total = len(blocks)
    else:
        if args.limit and args.limit > 0:
            product_urls = product_urls[: args.limit]
        total = len(product_urls)
        for i, purl in enumerate(product_urls, start=1):
            print(f"[2/2] 상품 {i}/{total}: {purl}")
            try:
                detail = fetch(session, purl)
            except requests.RequestException as e:
                blocks.append(
                    f'<p style="color:#c00;">로드 실패: {html.escape(purl)} — {html.escape(str(e))}</p>'
                )
                time.sleep(args.sleep)
                continue

            soup = BeautifulSoup(detail, "html.parser")
            if args.format == "naver":
                # 기존 렌더러를 한 번 타고(HTML/표 추출은 재사용), 최종은 텍스트로 출력
                title = imweb_title(soup) if engine == "imweb" else _title_from_page(soup)
                images = imweb_product_images(soup, purl) if engine == "imweb" else _parse_ld_product_images(soup, purl)
                table = parse_size_table(soup)
                note = imweb_size_note(soup) if engine == "imweb" else parse_size_fallback_note(soup)
                blocks.append(render_product_block_naver_text(title, images, table, note))
            elif args.format == "naver_html":
                title = imweb_title(soup) if engine == "imweb" else _title_from_page(soup)
                images = imweb_product_images(soup, purl) if engine == "imweb" else _parse_ld_product_images(soup, purl)
                table = parse_size_table(soup)
                note = imweb_size_note(soup) if engine == "imweb" else parse_size_fallback_note(soup)
                blocks.append(render_product_block_naver_html(title, images, table, note, title_pt=17, table_pt=13))
            else:
                blocks.append(build_block_from_detail(soup, purl, engine))
            time.sleep(args.sleep)

    site_label = parsed.netloc.replace("www.", "")
    if args.format == "naver":
        doc = "".join(blocks)
    elif args.format == "naver_html":
        doc = (
            "<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\"/>"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>"
            "</head>"
            "<body style=\"margin:0;padding:24px;background:#fff;\">"
            f"{''.join(blocks)}</body></html>"
        )
    else:
        doc = (
            "<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\"/>"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>"
            f"<title>{html.escape(site_label)} 초안</title></head><body style=\"margin:24px;background:#fff;\">"
            f"{''.join(blocks)}</body></html>"
        )

    out_path = args.output
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"완료: {out_path} ({total}개 상품 블록)")


if __name__ == "__main__":
    main()

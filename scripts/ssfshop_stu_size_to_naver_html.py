#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSF Shop 브랜드 목록에서 지정한 상품명에 해당하는 상품만 골라,
「사이즈&핏」과 동일 출처의 실측 표(/public/goods/detail/realSize)를 가져와
recto_list_to_blog_html.py 의 naver_html 과 같은 se-* 블록 HTML 로 출력합니다.

기본 동작:
- list_url 과 --list-html 을 모두 생략하면, STU SSF 기본 목록 URL 로 자동 요청합니다.

주의:
- 목록의 추가 페이지는 브라우저에서 AJAX 로만 갱신되는 경우가 많아,
  기본은 목록 URL 첫 응답에 포함된 상품 블록만 인덱싱합니다.
  더 넓은 목록이 필요하면 --list-html 로 저장한 HTML 을 여러 번 주거나,
  --map-file 로 god 번호를 직접 지정하세요.
- 사이트 구조 변경 시 동작이 깨질 수 있습니다. robots/이용약관을 준수하세요.
"""

from __future__ import annotations

import argparse
import html as html_module
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# SSF Shop STU 브랜드 샵 기본 목록 (list_url / --list-html 을 모두 생략할 때 사용)
DEFAULT_STU_SSFSHOP_LIST_URL = (
    "https://www.ssfshop.com/STU/main?brandShopNo=BDMA09Z32&brndShopId=BQXSU&dspCtgryNo="
)


def _naver_span(text: str, *, font_pt: int, bold: bool = False) -> str:
    w = "700" if bold else "400"
    return (
        "<span "
        'class="se-fs se-ff-nanumgothic se-fs15 se-node" '
        'style="color: rgb(0, 0, 0); font-family: \'Nanum Gothic\',\'나눔고딕\',sans-serif; '
        f"font-size: {font_pt}pt; font-weight: {w};\">"
        f"{html_module.escape(text)}</span>"
    )


def _naver_hr(*, width_pct: int = 100) -> str:
    w = max(1, min(100, int(width_pct)))
    return (
        '<div class="se-component se-horizontalLine se-l-default" data-a11y-title="구분선">'
        '<div class="se-component-content">'
        f'<div class="se-section se-section-horizontalLine se-l-default se-section-align-center" style="width:{w}%;margin:0 auto;">'
        '<div style="width:100%;border-top:1px solid #ddd;height:0;line-height:0;display:block;"></div>'
        "</div></div></div>"
    )


def _naver_spacer(px: int = 30) -> str:
    return f'<div style="height:{int(px)}px;"></div>'


def _naver_image_strip(img1: str, img2: str) -> str:
    def one(src: str) -> str:
        return (
            '<div class="se-module se-module-image se-unit" '
            'style="flex:0 0 50%;max-width:50%;width:50%;">'
            '<div class="se-module-image-container" style="width:100%;">'
            f'<img src="{html_module.escape(src, quote=True)}" alt="" class="se-image-resource" '
            'style="width:100%;height:auto;display:block;object-fit:cover;"/>'
            "</div></div>"
        )

    return (
        '<div class="se-component se-imageStrip2 se-component-imageStrip se-l-default" data-a11y-title="나란히 사진">'
        '<div class="se-component-content se-component-content-extend">'
        '<div class="se-section se-section-imageStrip2 se-l-default se-section-align-center">'
        '<div class="se-imageStrip2-container" style="display:flex;flex-wrap:nowrap;gap:0;width:100%;">'
        f"{one(img1)}{one(img2)}"
        "</div></div></div></div>"
    )


def _naver_text_center(text_html: str, *, width_pct: int = 50) -> str:
    return (
        '<div class="se-component se-text se-l-default" data-a11y-title="본문">'
        '<div class="se-component-content">'
        f'<div class="se-section se-section-text se-l-default" '
        f'style="width:{width_pct}%;margin:0 auto;text-align:center;">'
        '<p class="se-text-paragraph se-text-paragraph-align-center" '
        'style="line-height:1.8;text-align:center;margin:0;">'
        f"{text_html}"
        "</p></div></div></div>"
    )


def _naver_table_from_tsv(headers: list[str], body: list[list[str]], *, width_pct: int = 50) -> str:
    cols = max(1, len(headers))
    col_w = max(5, int(100 / cols))

    def td(inner: str, *, is_th: bool = False) -> str:
        tag = "th" if is_th else "td"
        cell = "th" if is_th else "td"
        return (
            f"<{tag} class=\"se-cell se-cell-{cell}\" "
            f"style=\"width:{col_w}%;height:40px;box-sizing:border-box;padding:0 8px;"
            "text-align:center;vertical-align:middle;white-space:nowrap;"
            "border-width:medium;border-style:none;border-color:currentcolor;\">"
            '<div class="se-module se-module-text se-unit">'
            '<div class="se-module-text-paragraph se-text-paragraph-align-center" style="line-height: 1.6;">'
            f"{inner}"
            "</div></div>"
            f"</{tag}>"
        )

    header_row = "<tr class=\"se-tr\">" + "".join(td(_naver_span(h, font_pt=13, bold=True), is_th=True) for h in headers) + "</tr>"
    body_rows: list[str] = []
    for row in body:
        padded = list(row)
        while len(padded) < len(headers):
            padded.append("")
        padded = padded[: len(headers)]
        body_rows.append(
            "<tr class=\"se-tr\">" + "".join(td(_naver_span(c, font_pt=13), is_th=False) for c in padded) + "</tr>"
        )

    colgroup = "<colgroup>" + "".join(f'<col style="width:{col_w}%;"/>' for _ in range(cols)) + "</colgroup>"

    return (
        '<div class="se-component se-table se-l-default" data-a11y-title="표">'
        '<div class="se-component-content">'
        f'<div class="se-section se-section-table se-l-default se-section-align-center" style="width:{width_pct}%;margin:0 auto;">'
        '<div class="se-table-container">'
        '<table class="se-table-content" '
        'style="border-width:medium;border-style:none;border-color:currentcolor;border-image:initial;'
        'width:100%;table-layout:fixed;border-collapse:collapse;">'
        "<tbody>"
        f"{colgroup}{header_row}{''.join(body_rows)}"
        "</tbody></table></div></div></div></div>"
    )


def render_product_block_naver_html(
    title: str,
    img_urls: list[str],
    headers: list[str],
    body: list[list[str]],
    footnote: str | None,
    *,
    title_pt: int = 17,
    gap_px: int = 60,
    text_width_pct: int = 100,
    table_width_pct: int = 80,
) -> str:
    imgs = (img_urls or [])[:2]
    if len(imgs) == 1:
        imgs = [imgs[0], imgs[0]]
    while len(imgs) < 2:
        imgs.append(imgs[0] if imgs else "")

    parts: list[str] = []
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

    if headers and body:
        parts.append(_naver_table_from_tsv([str(x) for x in headers], [[str(x) for x in row] for row in body], width_pct=table_width_pct))
        parts.append(_naver_spacer(gap_px))

    if footnote and footnote.strip():
        parts.append(_naver_text_center(_naver_span(footnote.strip(), font_pt=11), width_pct=text_width_pct))
        parts.append(_naver_spacer(gap_px))

    parts.append(_naver_hr(width_pct=85))
    parts.append(_naver_spacer(gap_px))
    return "".join(parts)


_WS_RE = re.compile(r"\s+")


def norm_label(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[\u00a0]", " ", s)
    s = re.sub(r"[,_/]+", " ", s)
    s = s.replace("-", " ")
    s = re.sub(r"\bstu\b", " ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def tokens(s: str) -> set[str]:
    return {t for t in norm_label(s).split() if len(t) >= 2}


def match_score(user: str, catalog: str) -> float:
    ut, ct = tokens(user), tokens(catalog)
    if not ut:
        return 0.0
    inter = ut & ct
    return len(inter) / len(ut)


def god_no_from_fragment(s: str) -> str | None:
    s = s.strip()
    m = re.search(r"\b(GM\d{10,})\b", s)
    return m.group(1) if m else None


def brand_slug_from_list_url(url: str) -> str:
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    if "main" in parts:
        i = parts.index("main")
        if i > 0:
            return parts[i - 1]
    if parts:
        return parts[0]
    return "STU"


def brand_shop_no_from_url(url: str) -> str | None:
    q = parse_qs(urlparse(url).query)
    v = q.get("brandShopNo", [None])[0]
    return v


@dataclass
class CatalogItem:
    god_no: str
    catalog_title: str
    img_main: str
    img_hover: str


def _upgrade_img_url(u: str) -> str:
    if "cmd/LB_500x660/" in u:
        return u.replace("cmd/LB_500x660/", "cmd/LB_750x1000/")
    return u


def parse_catalog_html(html: str) -> list[CatalogItem]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[CatalogItem] = []
    for li in soup.select("li.god-item[data-prdno]"):
        god = (li.get("data-prdno") or "").strip()
        if not god:
            continue
        imgs = li.select(".god-img img[src]")
        if not imgs:
            continue
        main = _upgrade_img_url(imgs[0].get("src") or "")
        hover = ""
        if len(imgs) > 1:
            h = imgs[1].get("src") or ""
            if h and h != imgs[0].get("src"):
                hover = _upgrade_img_url(h)
        if not hover:
            hover = main
        alt = (imgs[0].get("alt") or "").strip() or god
        out.append(CatalogItem(god_no=god, catalog_title=alt, img_main=main, img_hover=hover))
    return out


def fetch(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=45)
    r.raise_for_status()
    return r.text


def parse_detail_meta(html: str) -> tuple[str, str]:
    god_tp_m = re.search(r'id="godTpCd"\s+value="([^"]*)"', html)
    part_m = re.search(r"_PARTMAL_SECT_CD\s*=\s*'([^']+)'", html)
    god_tp = god_tp_m.group(1) if god_tp_m else "GNRL_GOD"
    part = part_m.group(1) if part_m else "MCOM"
    return god_tp, part


_IMG_PREFIX = "https://img.ssfshop.com/cmd/LB_750x1000/src/https://img.ssfshop.com"


def parse_detail_images(html: str) -> tuple[str, str]:
    """defaultImage + 썸네일 data 경로로 대표 이미지 2장."""
    soup = BeautifulSoup(html, "html.parser")
    path0 = ""
    inp = soup.find("input", id="defaultImage")
    if inp and inp.get("value"):
        path0 = inp["value"].strip()
    thumbs = [t.get("data") for t in soup.select(".thumb-item[data]") if t.get("data")]

    def full(p: str) -> str:
        p = (p or "").strip()
        if not p:
            return ""
        if p.startswith("http"):
            return _upgrade_img_url(p)
        if not p.startswith("/"):
            p = "/" + p
        return _upgrade_img_url(_IMG_PREFIX + p)

    img1 = full(path0)
    img2 = full(thumbs[1]) if len(thumbs) > 1 else img1
    if not img1 and thumbs:
        img1 = full(thumbs[0])
        img2 = full(thumbs[1]) if len(thumbs) > 1 else img1
    if img1 and not img2:
        img2 = img1
    return img1, img2


def parse_real_size_table(real_html: str) -> tuple[list[str], list[list[str]], str]:
    soup = BeautifulSoup(real_html, "html.parser")
    tbl = soup.select_one("table.tbl_info")
    note_el = soup.select_one("div.txt-guide")
    note = ""
    if note_el:
        note = " ".join(note_el.get_text(" ", strip=True).split())

    if not tbl:
        return [], [], note

    rows = tbl.find_all("tr")
    if not rows:
        return [], [], note

    headers: list[str] = []
    body: list[list[str]] = []
    for tr in rows:
        cells: list[str] = []
        for cell in tr.find_all(["th", "td"]):
            t = " ".join(cell.get_text(" ", strip=True).split())
            cells.append(t)
        if not cells:
            continue
        if not headers:
            headers = cells
        else:
            body.append(cells)
    return headers, body, note


def _require_file(path: Path, label: str) -> None:
    p = path.expanduser()
    if not p.is_file():
        try:
            shown = str(p.resolve())
        except OSError:
            shown = str(p)
        print(
            f"{label} 파일을 찾을 수 없습니다:\n  {shown}\n\n"
            "상대 경로는 터미널의 현재 작업 폴더(cwd) 기준입니다. blog 저장소 루트로 이동한 뒤:\n"
            "  python3 scripts/ssfshop_stu_size_to_naver_html.py --names-file names.txt -o out.html\n"
            "또는 UTF-8 텍스트 파일을 만든 뒤 절대 경로로 --names-file 을 지정하세요.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def load_names(path: Path | None) -> list[str]:
    if path is None:
        lines = sys.stdin.read().splitlines()
    else:
        lines = path.read_text(encoding="utf-8").splitlines()
    names: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        names.append(s)
    return names


def load_mapping(path: Path) -> dict[str, str]:
    """
    한 줄:  <키> <탭 또는 | 구분> <GM... 또는 상세 URL>
    키는 원문·norm 둘 다로 조회됩니다.
    """
    m: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "\t" in s:
            key, rest = s.split("\t", 1)
        elif "|" in s:
            key, rest = s.split("|", 1)
        else:
            parts = s.split(None, 1)
            if len(parts) < 2:
                continue
            key, rest = parts[0], parts[1]
        key = key.strip()
        rest = rest.strip()
        gid = god_no_from_fragment(rest) or ""
        if gid:
            m[key] = gid
            m[norm_label(key)] = gid
    return m


def pick_catalog(user_line: str, catalog: list[CatalogItem], mapping: dict[str, str]) -> CatalogItem | None:
    raw = user_line.strip()
    nl = norm_label(raw)
    gid = mapping.get(raw) or mapping.get(nl)
    if gid:
        for c in catalog:
            if c.god_no == gid:
                return c
        return CatalogItem(god_no=gid, catalog_title=raw, img_main="", img_hover="")

    best: tuple[float, CatalogItem] | None = None
    for c in catalog:
        sc = match_score(raw, c.catalog_title)
        if sc < 0.55:
            continue
        if best is None or sc > best[0] or (sc == best[0] and len(c.catalog_title) < len(best[1].catalog_title)):
            best = (sc, c)
    return best[1] if best else None


def detail_good_url(brand_slug: str, god_no: str) -> str:
    return f"https://www.ssfshop.com/{brand_slug}/{god_no}/good"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="SSF Shop 실측 사이즈 → 네이버용 HTML",
        epilog=(
            "예: python3 scripts/ssfshop_stu_size_to_naver_html.py --names-file names.txt -o out.html\n"
            "  (목록 URL 생략 시 STU 기본 목록으로 자동 요청)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "list_url",
        nargs="?",
        help=f"브랜드 목록 URL. 생략 시 --list-html 이 없을 때만 기본 STU 목록 사용 ({DEFAULT_STU_SSFSHOP_LIST_URL})",
    )
    ap.add_argument(
        "--names-file",
        type=Path,
        help="UTF-8 텍스트, 상품명 한 줄에 하나 (# 로 시작하는 줄은 무시). 없으면 stdin",
    )
    ap.add_argument("--list-html", type=Path, action="append", default=[], help="추가 목록 HTML (저장 페이지 등, 여러 번 가능)")
    ap.add_argument("--map-file", type=Path, help="이름(또는 키) → GM번호 또는 상세 URL")
    ap.add_argument("--format", choices=["naver_html"], default="naver_html")
    ap.add_argument("-o", "--output", type=Path, help="출력 HTML (없으면 stdout)")
    ap.add_argument("--sleep", type=float, default=0.35, help="요청 간 대기(초)")
    ap.add_argument("--brand-slug", help="상세 경로 슬러그 (기본: 목록 URL 에서 추론)")
    args = ap.parse_args()

    if args.names_file is not None:
        _require_file(args.names_file, "상품명 목록(--names-file)")
    for hp in args.list_html or []:
        _require_file(hp, "목록 HTML(--list-html)")
    if args.map_file is not None:
        _require_file(args.map_file, "매핑(--map-file)")

    names = load_names(args.names_file)
    if not names:
        print("상품명이 비었습니다. --names-file 또는 stdin 에 한 줄에 하나씩 입력하세요.", file=sys.stderr)
        return 2

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})

    list_url = args.list_url
    if not list_url and not (args.list_html or []):
        list_url = DEFAULT_STU_SSFSHOP_LIST_URL
        print(f"[info] 목록 URL 생략 → 기본 STU 목록 사용: {list_url}", file=sys.stderr)

    catalog: list[CatalogItem] = []
    if list_url:
        catalog.extend(parse_catalog_html(fetch(session, list_url)))
        time.sleep(args.sleep)
    for hp in args.list_html or []:
        catalog.extend(parse_catalog_html(hp.read_text(encoding="utf-8")))

    # god_no 중복 제거(첫 항목 유지)
    seen: set[str] = set()
    uniq: list[CatalogItem] = []
    for c in catalog:
        if c.god_no in seen:
            continue
        seen.add(c.god_no)
        uniq.append(c)
    catalog = uniq

    mapping: dict[str, str] = {}
    if args.map_file:
        mapping.update(load_mapping(args.map_file))

    if list_url:
        brand_slug = args.brand_slug or brand_slug_from_list_url(list_url)
    elif args.brand_slug:
        brand_slug = args.brand_slug
    else:
        brand_slug = "STU"

    if not catalog and not mapping:
        print(
            "목록에서 상품 li 를 찾지 못했고 --map-file 도 없습니다. "
            "저장한 HTML 을 --list-html 로 넘기거나 --map-file 에 GM 번호를 적어 주세요.",
            file=sys.stderr,
        )
        return 2
    if not catalog and mapping:
        print(
            "[info] 목록 HTML 에서 li.god-item 을 찾지 못했습니다. --map-file 만으로 진행합니다.",
            file=sys.stderr,
        )

    meta_cache: dict[str, tuple[str, str]] = {}
    detail_html_cache: dict[str, str] = {}

    blocks: list[str] = []
    doc_head = (
        '<!DOCTYPE html><html lang="ko"><head>'
        '<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>'
        "</head><body style=\"margin:0;padding:24px;background:#fff;\">"
    )
    doc_foot = "</body></html>"

    for user_line in names:
        picked = pick_catalog(user_line, catalog, mapping)
        if not picked:
            print(f"[skip] 목록에서 찾지 못함: {user_line!r}", file=sys.stderr)
            continue

        god = picked.god_no
        durl = detail_good_url(brand_slug, god)
        if god not in meta_cache:
            dhtml = fetch(session, durl)
            time.sleep(args.sleep)
            detail_html_cache[god] = dhtml
            meta_cache[god] = parse_detail_meta(dhtml)
        elif (not picked.img_main or not picked.img_hover) and god not in detail_html_cache:
            detail_html_cache[god] = fetch(session, durl)
            time.sleep(args.sleep)
        god_tp, partmal = meta_cache[god]

        rs_url = (
            "https://www.ssfshop.com/public/goods/detail/realSize"
            f"?godNo={god}&godTpCd={god_tp}&partmalSectCd={partmal}&recommendItmList="
        )
        rs_html = fetch(session, rs_url)
        time.sleep(args.sleep)
        headers, body, note = parse_real_size_table(rs_html)

        img1, img2 = picked.img_main, picked.img_hover
        if not img1 or not img2:
            dh = detail_html_cache.get(god)
            if not dh:
                dh = fetch(session, durl)
                time.sleep(args.sleep)
                detail_html_cache[god] = dh
            di1, di2 = parse_detail_images(dh)
            if not img1:
                img1 = di1
            if not img2:
                img2 = di2

        title = user_line.strip()
        if not headers or not body:
            print(f"[warn] 실측 표 없음: {title} ({god})", file=sys.stderr)
            foot = note or "실측 표를 가져오지 못했습니다. 상품 상세의 사이즈&핏 탭을 확인해 주세요."
            blocks.append(
                render_product_block_naver_html(title, [img1, img2], [], [], footnote=foot)
            )
            continue

        blocks.append(
            render_product_block_naver_html(
                title,
                [img1, img2],
                headers,
                body,
                footnote=note or None,
            )
        )

    if not blocks:
        print("출력할 블록이 없습니다.", file=sys.stderr)
        return 1

    out = doc_head + "".join(blocks) + doc_foot
    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

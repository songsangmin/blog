#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KREAM 상품 URL 목록(한 줄에 하나)을 받아 상세 HTML에서 실측 사이즈 블록을 찾아
ssfshop_stu_size_to_naver_html.py 와 동일한 naver_html(se-*) 블록으로 묶습니다.

- 최신 상품은 SSR 로 '사이즈 … ∙ 실측 단위…' 문구가 들어오는 경우가 많습니다.
- 일부 구형/캐시 페이지에는 동일 문구가 없을 수 있어, 표는 비우고 안내만 넣습니다.
- 크림 이용약관·과도한 요청은 피해 주세요.
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# 실측 행 라벨(등장 순서대로 스캔)
_DIM_KR = (
    "허리",
    "밑위",
    "허벅지",
    "밑단",
    "총장",
    "소매",
    "어깨",
    "가슴",
    "목너비",
    "어깨너비",
    "가슴너비",
    "소매길이",
    "암홀",
    "엉덩이",
    "무릎",
    "스트링",
)
DIM_SET = frozenset(_DIM_KR)


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
        parts.append(
            _naver_table_from_tsv(
                [str(x) for x in headers],
                [[str(x) for x in row] for row in body],
                width_pct=table_width_pct,
            )
        )
        parts.append(_naver_spacer(gap_px))

    if footnote and footnote.strip():
        parts.append(_naver_text_center(_naver_span(footnote.strip(), font_pt=11), width_pct=text_width_pct))
        parts.append(_naver_spacer(gap_px))

    parts.append(_naver_hr(width_pct=85))
    parts.append(_naver_spacer(gap_px))
    return "".join(parts)


def _normalize_page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    t = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t)


def parse_kream_size_table(html: str) -> tuple[list[str], list[list[str]], str] | None:
    text = _normalize_page_text(html)
    # 본문 앞쪽에도 "사이즈 상세" 등이 있어, '∙ 실측 단위' 직전 구간에서 마지막 '사이즈'만 사용
    bullet = None
    for b in ("∙ 실측", "• 실측"):
        if b in text:
            bullet = b
            break
    if not bullet:
        return None
    end = text.find(bullet)
    if end < 0:
        return None
    seg = text[:end]
    idx = seg.rfind("사이즈")
    if idx < 0:
        return None
    body = seg[idx + len("사이즈") :].strip()
    tokens = body.split()
    idx_dim = None
    for i, tok in enumerate(tokens):
        if tok in DIM_SET:
            idx_dim = i
            break
    if idx_dim is None or idx_dim == 0:
        return None
    columns = tokens[:idx_dim]
    dims: list[str] = []
    j = idx_dim
    while j < len(tokens) and tokens[j] in DIM_SET:
        dims.append(tokens[j])
        j += 1
    if not columns or not dims:
        return None
    nums: list[str] = []
    while j < len(tokens):
        if re.match(r"^-?\d+\.?\d*$", tokens[j]):
            nums.append(tokens[j])
        j += 1
    n_rows, n_cols = len(dims), len(columns)
    if n_rows * n_cols != len(nums):
        if len(nums) > n_rows * n_cols:
            nums = nums[: n_rows * n_cols]
        else:
            return None
    headers = ["측정항목"] + columns
    body_rows: list[list[str]] = []
    for ri in range(n_rows):
        row = [dims[ri]] + [nums[ci * n_rows + ri] for ci in range(n_cols)]
        body_rows.append(row)
    foot = ""
    if end < len(text):
        tail = text[end : end + 400]
        foot = tail.strip()
    return headers, body_rows, foot


def parse_kream_title_images(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    ld = soup.find("script", id="Product")
    if ld and ld.string:
        try:
            d = json.loads(ld.string)
            title = (d.get("name") or "").strip()
        except json.JSONDecodeError:
            pass
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = og["content"].strip()
    imgs: list[str] = []
    for im in soup.find_all("img", src=True):
        src = im["src"].split("?")[0]
        if "kream-phinf.pstatic.net" in src and "/a_" in src:
            if src not in imgs:
                imgs.append(src)
        if len(imgs) >= 2:
            break
    if len(imgs) < 2 and ld and ld.string:
        try:
            d = json.loads(ld.string)
            for u in d.get("image") or []:
                u = str(u).split("?")[0]
                if u and u not in imgs:
                    imgs.append(u)
                if len(imgs) >= 2:
                    break
        except json.JSONDecodeError:
            pass
    while len(imgs) < 2:
        imgs.append(imgs[0] if imgs else "")
    return title, imgs[:2]


def load_urls(path: Path | None) -> list[str]:
    if path is None:
        lines = sys.stdin.read().splitlines()
    else:
        lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def product_id_from_kream_url(url: str) -> str | None:
    m = re.search(r"/products/(\d+)/?", url.strip())
    return m.group(1) if m else None


def is_kream_product_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    if p.netloc not in ("kream.co.kr", "www.kream.co.kr"):
        return False
    return bool(re.match(r"^/products/\d+/?$", p.path or ""))


def _require_file(path: Path, label: str) -> None:
    p = path.expanduser()
    if not p.is_file():
        print(f"{label} 을(를) 찾을 수 없습니다: {p}", file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="KREAM 상품 URL → 네이버용 실측 HTML")
    ap.add_argument("--urls-file", type=Path, help="상품 URL 한 줄에 하나 (없으면 stdin)")
    ap.add_argument("-o", "--output", type=Path, help="출력 HTML")
    ap.add_argument("--sleep", type=float, default=0.35, help="요청 간 대기(초)")
    ap.add_argument(
        "--cache-dir",
        type=Path,
        help="이 디렉터리에 kream_<상품번호>.html 이 있으면 해당 파일을 읽고 네트워크 요청은 생략",
    )
    ap.add_argument(
        "--save-html-to",
        type=Path,
        help="네트워크로 받은 HTML 을 kream_<상품번호>.html 로 이 디렉터리에 저장(다음에 --cache-dir 로 재사용)",
    )
    ap.add_argument(
        "--cache-only",
        action="store_true",
        help="네트워크 요청 없이 --cache-dir 의 kream_<번호>.html 만 사용",
    )
    args = ap.parse_args()

    if args.urls_file is not None:
        _require_file(args.urls_file, "URL 목록(--urls-file)")

    if args.cache_only and not args.cache_dir:
        ap.error("--cache-only 은 --cache-dir 과 함께 사용해야 합니다.")

    urls = load_urls(args.urls_file)
    if not urls:
        print("URL 이 없습니다. --urls-file 또는 stdin 을 사용하세요.", file=sys.stderr)
        return 2

    bad = [u for u in urls if not is_kream_product_url(u)]
    if bad:
        print("KREAM 상품 URL 이 아닌 줄이 있습니다:\n" + "\n".join(bad[:5]), file=sys.stderr)
        return 2

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.5,en;q=0.3",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://kream.co.kr/",
            "Cache-Control": "no-cache",
        }
    )

    blocks: list[str] = []
    doc_head = (
        '<!DOCTYPE html><html lang="ko"><head>'
        '<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>'
        "</head><body style=\"margin:0;padding:24px;background:#fff;\">"
    )
    doc_foot = "</body></html>"

    def write_partial() -> None:
        if args.output and blocks:
            args.output.write_text(doc_head + "".join(blocks) + doc_foot, encoding="utf-8")

    for url in urls:
        html = ""
        pid = product_id_from_kream_url(url)
        cached: Path | None = None
        if args.cache_dir and pid:
            cand = args.cache_dir / f"kream_{pid}.html"
            if cand.is_file() and cand.stat().st_size > 500:
                cached = cand
        if cached is not None:
            html = cached.read_text(encoding="utf-8", errors="replace")
        elif args.cache_only:
            print(f"[skip] 캐시 없음(--cache-only): {url}", file=sys.stderr)
            continue
        else:
            try:
                r = session.get(url, timeout=12)
                if r.status_code >= 500:
                    time.sleep(0.6)
                    r = session.get(url, timeout=12)
                r.raise_for_status()
                html = r.text
            except requests.RequestException as e:
                print(f"[skip] 요청 실패 {url}: {e}", file=sys.stderr)
                continue
            time.sleep(args.sleep)
            if args.save_html_to and pid and len(html) > 500:
                args.save_html_to.mkdir(parents=True, exist_ok=True)
                (args.save_html_to / f"kream_{pid}.html").write_text(html, encoding="utf-8")
        title, imgs = parse_kream_title_images(html)
        if not title:
            title = url
        parsed = parse_kream_size_table(html)
        if parsed:
            h, b, foot = parsed
            blocks.append(render_product_block_naver_html(title, imgs, h, b, footnote=foot or None))
        else:
            note = (
                "이 상품 HTML 에서 실측 사이즈 문구(「사이즈 … ∙ 실측 단위」)를 찾지 못했습니다. "
                "크림 앱/웹에서 직접 확인하거나, 다른 시즌 상품 URL 을 사용해 보세요."
            )
            print(f"[warn] 사이즈 블록 없음: {url}", file=sys.stderr)
            blocks.append(render_product_block_naver_html(title, imgs, [], [], footnote=note))

        write_partial()

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

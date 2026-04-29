# 쇼핑몰 URL → 네이버 블로그 글감 만들기 (정리)

이 저장소의 `scripts/recto_list_to_blog_html.py`는 쇼핑몰 **목록(카테고리/컬렉션) URL** 또는 일부 **단일 상품 URL**을 받아,
네이버 블로그 글쓰기에서 활용할 수 있는 초안(HTML 또는 네이버용 텍스트/표)을 생성합니다.

> 주의: 자동화/크롤링은 각 사이트의 이용약관/robots.txt 범위 내에서만 사용하세요.

---

## 실행 방법(공통)

### 1) 네이버 붙여넣기용(추천)
네이버는 “HTML 원본 붙여넣기”가 막혀있는 경우가 많아서, **`--format naver`** 출력이 안정적입니다.

```bash
python3 scripts/recto_list_to_blog_html.py "<목록URL>" --format naver -o out.txt
```

- 출력물은 상품별로 다음 형태입니다.
  - 상품명
  - 이미지 URL 2개
  - **실측(Measurements) 표(가능할 때)** 또는 사이즈 표/안내문
  - 구분선

네이버 에디터에서 표가 표로 변환이 잘 안 되면, **표(TSV) 부분만 따로 드래그해서 붙여넣기**하면 대부분 해결됩니다.

### 2) HTML 초안(로컬 미리보기/참고용)

```bash
python3 scripts/recto_list_to_blog_html.py "https://recto.co/product/list.html?cate_no=219" --format html -o out.html
```

---

## 옵션

- `--limit N`: 앞에서 N개 상품만 처리
- `--sleep SEC`: 요청 사이 대기(사이트 부담/차단 완화 목적)
- `--shopify-pages N`: Shopify “컬렉션”일 때 다음 페이지(rel=next)를 최대 N페이지까지 따라가며 상품 수집

---

## 지금까지 적용/테스트한 브랜드(사이트)별 정리

아래는 대화 중 실제로 넣어본/수정한 사이트들입니다.

### 1) RECTO (Cafe24)
- **형태**: Cafe24 목록
  - 예: `.../product/list.html?...`
- **동작**:
  - 목록에서 상품 상세 링크 수집 → 상세 페이지 요청
  - 이미지 2장 + 사이즈표(`table.size-table`) 또는 대체 문구 추출
- **권장 커맨드**:

```bash
python3 scripts/recto_list_to_blog_html.py "<RECTO 목록URL>" --format naver -o recto.txt --sleep 1.2
```

### 2) stuoffice (Cafe24)
- **형태/동작**: RECTO와 동일(Cafe24)

```bash
python3 scripts/recto_list_to_blog_html.py "<stuoffice 목록URL>" --format naver -o stuoffice.txt --sleep 1.2
```

### 3) AUTOMATIC FOR THE PEOPLE (아임웹)
- **형태**: 아임웹 카테고리 URL
  - 예: `https://도메인/69/` (경로가 숫자)
- **동작**:
  - 카테고리에서 `?idx=` 상품 링크 수집 → 상세 요청
  - 이미지 + 사이즈 안내(문단) 추출

```bash
python3 scripts/recto_list_to_blog_html.py "<아임웹 카테고리URL>" --format naver -o imweb.txt --sleep 1.2
```

### 4) The Row (Shopify, PDP 단일 상품)
- **형태**: 단일 상품(PDP)
  - 예: `https://www.therow.com/ko-kr/products/niosa-top-ice-blue`
- **동작**:
  - PDP 1회 요청
  - 이미지 2장 + `var meta.product`(또는 `var meta.products`) 기반 옵션/가격/sku 표
  - Eastside Co 사이즈가이드 앱이 있을 경우:
    - PDP에 포함된 CDN JS(`size-guides-prod.esc-apps-cdn.com/...js`)의 `cachedCharts`를 파싱해
    - 상품 태그(`data-tags`)와 차트 태그가 매칭되면 변환표를 포함
  - **주의**: The Row는 “cm 실측”이 아니라 “사이즈 변환표”만 제공되는 경우가 많습니다(앱 차트 구성에 따름).

```bash
python3 scripts/recto_list_to_blog_html.py "https://www.therow.com/ko-kr/products/niosa-top-ice-blue" --format naver -o therow.txt --sleep 1.2
```

### 5) thecolorcolour (Cafe24, detail.html?product_no= 형태)
- **형태**: Cafe24 목록이지만 상품 링크가 다른 스킨 형태
  - 목록: `https://thecolorcolour.com/product/list.html?cate_no=53`
  - 상세: `/product/detail.html?product_no=195&cate_no=53&display_group=1`
- **동작**:
  - 목록에서 `detail.html?product_no=` 링크도 수집하도록 확장됨
  - 상세 설명의 `Measurements` 블록을 파싱해 **실측 표(TSV)** 로 변환(네이버용)
  - 현재 `--format naver`에서는 **옵션/설명 텍스트를 제외**하고 실측 표 중심으로 출력

```bash
python3 scripts/recto_list_to_blog_html.py "https://thecolorcolour.com/product/list.html?cate_no=53" --format naver -o colorcolour_53.txt --sleep 1.2
```

---

## 문제 해결 팁

- **“목록에서 상품 링크를 찾지 못했습니다”**
  - 목록 URL 형태가 지원 패턴과 다른 경우입니다.
  - Cafe24는 `/product/.../id/...` 뿐 아니라 `/product/detail.html?product_no=...` 형태도 있으니,
    필요하면 스크립트의 `collect_product_urls()`에 패턴을 추가합니다.

- **네이버에 붙여넣었는데 표가 표로 안 바뀜**
  - 표 부분(TSV)만 따로 복사해서 붙여넣기
  - 혹은 표 삽입 후 셀 단위로 붙여넣기(최후 수단)

- **이미지 URL이 자동으로 이미지로 안 들어감**
  - 네이버 에디터에서 “사진”으로 직접 업로드/첨부가 가장 확실합니다.


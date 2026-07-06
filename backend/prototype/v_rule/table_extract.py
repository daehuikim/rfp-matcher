"""표(grid) → 구조 인식 요구사항 추출. 도메인 키워드 하드코딩 없이 **구조 신호**만 사용.

문제(진단): 요구사항이 다열표 안에 있는데 셀을 통째로 join하면 계위/요구사항명이 소실되고
배점표·현황표·다이어그램 격자가 행으로 누출된다. 여기서 표를 3종으로 분류해 처리한다:
  - junk       : 도표/현황/배점(열 많음·숫자 비중 높음·라벨만) → 드롭
  - vertical   : 세로 라벨-값 카드(HWP: [고유번호|ECR-001][요구사항 명칭|..][세부 내용|❍..]) → 1표=1요구
  - horizontal : 가로 요구표([구분|내용|비고], [No|항목명|요구사항|상세요건]) → 데이터행마다 요구
                 내용열=상세, 구분/항목열=계위(rowspan forward-fill), 헤더행 제거

반환: list[dict{level,name,detail}]  (junk면 []).  텍스트는 셀 원문 그대로(충실 전사).
"""
from __future__ import annotations

import re

# 숫자/기호만(현황·배점 셀) — 단위·연도 등 포함
_NUM_ONLY = re.compile(r"^[\s\d.,%~/()\-–—:·+*°]+(?:[가-힣A-Za-z]{0,3})?$")
_ID_PAT = re.compile(r"[A-Z]{2,5}[-–]?\d{2,4}")            # ECR-001, SFR001 등 요구사항 ID
# 한 셀/문자열 안 복수 항목 구분자(불릿·개행-대시·<br>·공백으로 둘러싼 ㅇ 하위불릿)
_ITEM_SPLIT = re.compile(r"\s*(?:[❍○●◦▪▫◆◇▶▷▸‣⁃∙•⦁☞]|<br\s*/?>)\s*|\s+ㅇ\s+")
# 줄 시작 항목 마커 — 셀 안 여러 줄(_cell 이 \n 보존) 중 '새 항목'의 시작 판정.
# 마커 없는 줄은 직전 항목의 연속(PDF 줄바꿈 아티팩트)으로 이어붙인다.
# 숫자는 1-2자리만(연도 2026. 오탐 방지), 대시는 뒤 공백 필수(하이픈 단어 오탐 방지).
_LINE_ITEM = re.compile(
    r"^(?:[❍○●◦▪▫◆◇■□▣◈▶▷▸‣⁃∙•⦁☞※]|[-–—]\s|\d{1,2}\s*[.)]\s?|[가-힣]\s*[.)]\s|"
    r"\([0-9가-힣]+\)|[①-⑳]|ㅇ\s)")


def _norm(c: str) -> str:
    """줄 내 공백만 정리, 줄 경계(\\n)는 보존 — 셀 내 항목 분해(split_items)의 원천 신호."""
    c = (c or "").strip()
    if "\n" not in c:
        return re.sub(r"\s+", " ", c)
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in c.split("\n") if ln.strip())


def _split_inline(text: str) -> list[str]:
    parts = [p.strip(" ·-–—\t") for p in _ITEM_SPLIT.split(text or "")]
    return [p for p in parts if p and len(p) >= 3]


def split_items(text: str) -> list[str]:
    """한 문자열 안에 나열된 복수 항목을 개별로 분해. 하나면 그대로 1개.

    줄 경계(\\n)가 있으면 줄 단위 우선: 마커로 시작하는 줄=새 항목, 마커 없는 줄=직전
    항목에 연속(줄바꿈 아티팩트 vs 항목 경계 구분). 각 항목 안 인라인 불릿은 재분해.
    줄 경계 없으면 기존 인라인 분해 그대로(기존 호출처 동작 불변).
    """
    text = text or ""
    if "\n" in text:
        items: list[str] = []
        for ln in text.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            if _LINE_ITEM.match(ln) or not items:
                items.append(ln)
            else:
                items[-1] = items[-1] + " " + ln
        out: list[str] = []
        for it in items:
            out.extend(_split_inline(it) or ([it.strip(" ·-–—\t")] if len(it.strip()) >= 3 else []))
        return out or ([re.sub(r"\s+", " ", text).strip()] if text.strip() else [])
    parts = _split_inline(text)
    return parts or ([text.strip()] if text.strip() else [])


def _col_stats(grid: list[list[str]]) -> tuple[int, list[dict]]:
    ncol = max((len(r) for r in grid), default=0)
    stats = []
    for c in range(ncol):
        cells = [_norm(r[c]) for r in grid if c < len(r) and _norm(r[c])]
        if not cells:
            stats.append({"avglen": 0.0, "numratio": 1.0, "n": 0, "maxlen": 0})
            continue
        stats.append({
            "avglen": sum(len(x) for x in cells) / len(cells),
            "numratio": sum(1 for x in cells if _NUM_ONLY.match(x)) / len(cells),
            "n": len(cells),
            "maxlen": max(len(x) for x in cells),
        })
    return ncol, stats


def is_junk_table(grid: list[list[str]]) -> bool:
    """도표/현황/배점 격자 판정 — 열이 많거나 숫자 비중이 높거나 서술 셀이 거의 없음."""
    cells = [_norm(c) for r in grid for c in r if _norm(c)]
    if len(cells) < 2:
        return True
    ncol, stats = _col_stats(grid)
    numratio = sum(1 for x in cells if _NUM_ONLY.match(x)) / len(cells)
    if numratio > 0.45:                       # 배점표/현황 숫자 격자
        return True
    if ncol >= 6:                             # 다이어그램/광폭 격자
        return True
    # 서술 셀(15자↑)이 전혀 없으면 요구 본문 없음(라벨/도표)
    if max((s["maxlen"] for s in stats), default=0) < 15:
        return True
    # (집계/수량표 규칙 제거: 순번(No) 열 있는 진짜 요구표까지 드롭돼 recall 손실.
    #  총괄표/집계표는 gemma keep 이 제목 기반으로 처리)
    return False


def _is_vertical_card(grid: list[list[str]], ncol: int, stats: list[dict]) -> bool:
    """세로 라벨-값 카드(HWP 요구사항 정의표): 좌열이 짧은 라벨(고유번호/명칭/세부내용…),
    우측에 값. 일부 행이 3열(라벨|소라벨|값)이어도 허용.

    주의: [구분|내용|비고] 같은 평범한 가로표도 '좌열 짧고 우열에 긴 값'을 만족해 예전엔
    오분류됐다(성능요구사항표 다중 행 중 1행만 남고 나머지 행이 통째로 드롭됨). HWP 세로카드는
    반드시 고유번호(ECR-001 등) 값을 갖는다는 구조 신호로 실제 요구사항 카드만 골라낸다."""
    if ncol not in (2, 3):
        return False
    rows2 = [r for r in grid if len(r) >= 2 and _norm(r[0])]
    if len(rows2) < 3:
        return False
    left = [_norm(r[0]) for r in rows2]
    short = sum(1 for x in left if len(x) <= 18) / len(left)
    has_long = any(len(_norm(c)) >= 15 for r in grid for c in r[1:])   # 세부내용 같은 서술값
    has_id = any(_ID_PAT.search(_norm(c)) for r in grid[:3] for c in r)  # 고유번호(ECR-001 등)
    return short > 0.7 and has_long and has_id


def _split_vertical_cards(grid: list[list[str]]) -> list[list[list[str]]]:
    """한 <table> 에 요구사항 카드 여러 개가 이어붙은 HWP 패턴 분할 — 고유번호 값 행마다
    새 카드 시작. (법제처 실측: 카드 21개가 한 표에 연속 → 첫 카드만 인식되고 나머지가
    최장값 병합으로 소실돼 원문ID 7/21 만 나오던 원인)."""
    def _is_id_row(r) -> bool:
        return any(_ID_PAT.search(_norm(c)) and len(_norm(c)) <= 16 for c in r[1:] if _norm(c))
    bounds = [i for i, r in enumerate(grid) if _is_id_row(r)]
    if len(bounds) <= 1:
        return [grid]
    segs: list[list[list[str]]] = []
    for k, s in enumerate(bounds):
        e = bounds[k + 1] if k + 1 < len(bounds) else len(grid)
        seg = grid[(0 if k == 0 else s):e]     # 첫 카드는 앞머리(구분행 등) 포함
        if seg:
            segs.append(seg)
    return segs


def _vertical_to_reqs(grid: list[list[str]]) -> list[dict]:
    """세로 라벨-값 카드 → 1요구. name=제목값(요구사항 명칭), code=ID값(고유번호).

    **고유번호가 있는 카드는 문서가 요구사항 단위를 스스로 선언한 것 — 카드 = 1행.**
    (사람 정답과 동일: 법제처 SFR-001 = 1행, 셀 안 불릿은 줄바꿈으로 담김. 반대로 기아
    정답은 카드 선언이 없는 뚱뚱 셀이라 불릿 1개=1행 — 분해는 무선언 카드에만 적용.)"""
    pairs = [(_norm(r[0]), " ".join(_norm(c) for c in r[1:] if _norm(c))) for r in grid if len(r) > 0]
    vals = [(lab, val) for lab, val in pairs if val]
    if not vals:
        return []
    code = next((v for _, v in vals if _ID_PAT.search(v) and len(v) <= 16), "")
    detail_val = max((v for _, v in vals), key=len)                    # 세부내용 = 최장값
    # 요구사항명 = '명칭/항목명/제목' 라벨의 값을 최우선. 최단길이 휴리스틱만 쓰면 '정의'·
    # '구분'(짧은 소라벨)이 진짜 제목("IP-PHONE"·"SLA 체계 마련 및 운영")보다 먼저 뽑혀
    # 오염된다(강원랜드 31%·경기도 58% 행에서 실측). 라벨 힌트를 우선 사용.
    name_labeled = next((v for lab, v in vals
                        if any(k in lab for k in ("명칭", "항목명", "제목")) and v != code and v != detail_val), None)
    name_cand = [v for _, v in vals if v != code and v != detail_val and not _NUM_ONLY.match(v)]
    name = name_labeled or (min(name_cand, key=len) if name_cand else "")
    if code:
        # 정의/세부내용 등 서술 값들을 줄바꿈으로 이어 카드 1행에 담는다(고유번호/명 제외).
        body = "\n".join(v for _, v in vals
                         if v != code and v != name and len(v) >= 8) or detail_val
        return [{"level": name or code, "name": name, "detail": body, "code_hint": code}]
    out = []
    for piece in split_items(detail_val):
        out.append({"level": name or code, "name": name, "detail": piece, "code_hint": code})
    return out


def _looks_header(row: list[str]) -> bool:
    """헤더행 판정 — 셀이 모두 짧고(≤12) 서술문이 아님."""
    cells = [_norm(c) for c in row if _norm(c)]
    if len(cells) < 2:
        return False
    return all(len(c) <= 12 for c in cells)


def _horizontal_reqs(grid: list[list[str]], ncol: int, stats: list[dict]) -> list[dict]:
    """가로 요구표 → 데이터행마다 요구. 내용열=상세(최장), 구분열=계위(rowspan forward-fill)."""
    header = grid[0] if grid and _looks_header(grid[0]) else None
    data = grid[1:] if header else grid
    if not data:
        return []
    # 내용(상세) 열 = 평균 길이 최장
    content = max(range(ncol), key=lambda c: stats[c]["avglen"])
    if stats[content]["avglen"] < 8:          # 서술열이 없음 → 요구표 아님
        return []
    # 계위(구분) 열 = content 왼쪽에서 '숫자 index 아니고 라벨성(짧음)'인 최좌열
    cat_col = None
    for c in range(content):
        if stats[c]["numratio"] > 0.6:        # 순번/No 열 스킵
            continue
        if stats[c]["avglen"] <= 25:
            cat_col = c
            break
    # 요구사항명 열 = content 왼쪽, cat_col 아닌 중간 길이 열(있으면).
    # 버그: 이전엔 avglen(문자열 길이)을 content(열 인덱스, 예 1·2)와 비교해 name_col이
    # 사실상 선택 불가능했다(스캔 신한카드 등에서 name이 통째로 번호열 값으로 오염).
    # content 열 자체의 평균길이(문자수)와 비교해야 의미가 맞는다.
    name_col = None
    for c in range(content):
        if c == cat_col or stats[c]["numratio"] > 0.6:
            continue
        if 4 <= stats[c]["avglen"] <= stats[content]["avglen"]:
            name_col = c
            break

    def cell(row, c):
        return _norm(row[c]) if c is not None and c < len(row) else ""

    out: list[dict] = []
    cat_fill = name_fill = ""
    for row in data:
        det = cell(row, content)
        if not det or len(det) < 3:
            continue
        # 구분/명 열은 한 줄로(개행이 탭명/계위에 새면 안 됨) — 상세(det)만 줄 보존.
        cat = re.sub(r"\s+", " ", cell(row, cat_col)) or cat_fill
        cat_fill = cat or cat_fill
        nm = re.sub(r"\s+", " ", cell(row, name_col)) or name_fill
        name_fill = nm or name_fill
        # _tab = 구분(계위)열 값 → 이 요구표를 ambient 헤딩이 아닌 자기 카테고리로 그룹핑
        # (기아처럼 요구표가 '상담석 규모' 같은 junk 헤딩 아래 misfiled 되는 것 방지)
        # 숫자-구분기호 사이 공백 허용(변환기가 '1-1)'을 '1 - 1)'로 벌려놓는 경우 대비).
        tab_hint = re.sub(r"^\s*(?:\d+\s*[-.)]\s*)+", "", cat).strip() or cat
        # 'N-M)' 형제번호(예: '1-3)')는 카드유닛 단계 형제묶음(_merge_sibling_numbered_tabs)이
        # 참조할 수 있게 정규화된 마커로 별도 보존 — tab_hint 는 마커 뗀 깔끔한 이름으로 남긴다.
        sib_m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\)", cat)
        plain_m = None if sib_m else re.match(r"^\s*(\d+)\s*\)", cat)
        if sib_m:
            tab_marker = f"{sib_m.group(1)}-{sib_m.group(2)})"
        elif plain_m:
            # 단순 'N)' 번호(예: '20) CS Plaza') — 형제묶음 텍스트 공통접두사가 없어도
            # (각 항목명이 서로 다름) 번호 연속성으로 상위 계열(_merge_numbered_family)에
            # 편입시킬 수 있게 순번만 보존.
            tab_marker = f"{plain_m.group(1)})"
        else:
            tab_marker = ""
        for piece in split_items(det):
            out.append({"level": cat, "name": nm or cat, "detail": piece,
                        "_tab": tab_hint, "_tab_marker": tab_marker})
    return out


def table_to_reqs(grid: list[list[str]]) -> list[dict] | None:
    """표 → 요구 dict 리스트. junk면 [](드롭). 요구표 아님(불명확)이면 None(호출측 폴백)."""
    grid = [[_norm(c) for c in r] for r in grid if any(_norm(c) for c in r)]
    if not grid:
        return []
    if is_junk_table(grid):
        return []
    ncol, stats = _col_stats(grid)
    if _is_vertical_card(grid, ncol, stats):
        return [r for seg in _split_vertical_cards(grid) for r in _vertical_to_reqs(seg)]
    if ncol >= 2:
        rows = _horizontal_reqs(grid, ncol, stats)
        if rows and not _rows_look_like_data_dump(rows):
            return rows
        if rows:
            return []            # 대량 단문 = 현황/규모/집계 데이터표 → 드롭
    return None


def _rows_look_like_data_dump(rows: list[dict]) -> bool:
    """추출된 행들이 요구사항이 아니라 현황/규모/집계 데이터표인지 — 상세가 대부분 단문."""
    lens = sorted(len(r["detail"]) for r in rows)
    med = lens[len(lens) // 2] if lens else 0
    if med < 10:                              # 상세 중앙값이 매우 짧음 = 라벨/숫자 격자
        return True
    if len(rows) > 60 and med < 20:           # 대량 + 단문 = 상담석 규모/현황 덤프
        return True
    return False

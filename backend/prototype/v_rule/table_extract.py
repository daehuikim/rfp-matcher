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
# 셀 전체가 문서고유번호 하나인지(가로 요구표의 ID 열 판정) — 3단(SFR-LDA-001)도 지원
_ID_CELL = re.compile(r"^[A-Z]{2,4}(?:[-–][A-Z]{2,6})?(?:[-–]\d{1,4}|\d{2,4})$")
# 한 셀/문자열 안 복수 항목 구분자(불릿·개행-대시·<br>·공백으로 둘러싼 ㅇ 하위불릿)
_ITEM_SPLIT = re.compile(r"\s*(?:[❍○●◦▪▫◆◇▶▷▸‣⁃∙•⦁☞]|<br\s*/?>)\s*|\s+ㅇ\s+")
# 줄 시작 항목 마커 — 셀 안 여러 줄(_cell 이 \n 보존) 중 '새 항목'의 시작 판정.
# 마커 없는 줄은 직전 항목의 연속(PDF 줄바꿈 아티팩트)으로 이어붙인다.
# 숫자는 1-2자리만(연도 2026. 오탐 방지), 대시는 뒤 공백 필수(하이픈 단어 오탐 방지).
_LINE_ITEM = re.compile(
    r"^(?:[❍○●◦▪▫◆◇■□▣◈▶▷▸‣⁃∙•⦁☞※]|[-–—]\s|\d{1,2}\s*[.)]\s?|[가-힣]\s*[.)]\s|"
    r"\([0-9가-힣]+\)|[①-⑳]|ㅇ\s)")
# 고유번호 카드(_vertical_to_reqs "카드=1행") 1행 최대 길이 — 정답지 관측 최대(~1109자)
# 근사치. 이보다 크면(내장 스펙표 등 혼입) 코드 유지한 채 여러 행으로 분해.
_CARD_ROW_MAX = 1200


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


def _dedupe_colspan_row(row: list[str]) -> list[str]:
    """rowspan/colspan 확장(cards._table_grid)이 라벨 칸(colspan=2 등)을 여러 그리드
    칸에 그대로 복제해 [라벨, 라벨, 값] 이 되는 경우, 연속 중복을 하나로 줄인다 —
    안 그러면 '라벨=r[0], 값=r[1:] join' 가정이 깨져 값에 라벨 문자열이 섞여 들어가고
    (예: 짧은 ID 값이 길이 초과로 code 판정에서 탈락) 고유번호가 통째로 유실된다."""
    out: list[str] = []
    for c in row:
        if not out or out[-1] != c:
            out.append(c)
    return out


def _card_pairs(grid: list[list[str]]) -> list[tuple[str, str]]:
    """카드 grid → (라벨, 값). [부모라벨|서브라벨|값] 3열 행(HWP '요구사항 상세설명'
    블록: [상세설명|정의|…], [상세설명|세부내용|…])은 **서브라벨**을 라벨로 쓴다 —
    r[1:] join 가정으로 처리하면 '정의'/'세부내용' 라벨이 값 머리에 접착돼(양형 실측)
    본문이 라벨 오염된 뚱뚱 블롭이 된다."""
    pairs: list[tuple[str, str]] = []
    for r in grid:
        r = _dedupe_colspan_row(r)
        if not r:
            continue
        if len(r) >= 3 and _norm(r[-1]) and 0 < len(_norm(r[-2])) <= 12:
            pairs.append((re.sub(r"\s+", " ", _norm(r[-2])), _norm(r[-1])))
        else:
            pairs.append((re.sub(r"\s+", " ", _norm(r[0])),
                          " ".join(_norm(c) for c in r[1:] if _norm(c))))
    return pairs


def _lab_is_name(lab: str) -> bool:
    """'요구사항 명칭'뿐 아니라 '요구사항 명'(양형 실측, 공백 포함)도 제목 라벨."""
    z = re.sub(r"\s+", "", lab or "")
    return any(k in z for k in ("명칭", "항목명", "제목")) or z.endswith("명")


def _split_card_lines(body: str) -> list[str]:
    """초과분(>_CARD_ROW_MAX) 카드 본문을 줄(문단) 단위로 분할.

    hwp5html 은 불릿 글리프(◦)를 소실시켜 마커 기반 split_items 가 전 문단을 한
    덩이로 접착한다(양형 ECR-001 실측 3,003자 1덩이). 변환기 특성상 줄 = 원문 문단
    이므로 줄이 곧 항목이다. □/■ 소제목 줄은 다음 항목 머리에 얹고, 하위기호(-·※)
    줄과 짧은 꼬리 줄은 직전 항목에 이어붙인다(줄 경계는 \\n 으로 보존)."""
    pieces: list[str] = []
    pending = ""                 # 소제목(□ 공통사항 등) — 다음 항목에 부착
    for ln in body.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if re.match(r"^[□■◇◆▣]", ln) and len(ln) <= 30:
            pending = f"{pending}\n{ln}" if pending else ln
            continue
        if pieces and not pending and (re.match(r"^[-–—※·]", ln) or len(ln) < 12):
            pieces[-1] += "\n" + ln
            continue
        pieces.append(f"{pending}\n{ln}" if pending else ln)
        pending = ""
    if pending:
        if pieces:
            pieces[-1] += "\n" + pending
        else:
            pieces.append(pending)
    return pieces


def _vertical_to_reqs(grid: list[list[str]]) -> list[dict]:
    """세로 라벨-값 카드 → 1요구. name=제목값(요구사항 명칭), code=ID값(고유번호).

    **고유번호가 있는 카드는 문서가 요구사항 단위를 스스로 선언한 것 — 카드 = 1행.**
    (사람 정답과 동일: 법제처 SFR-001 = 1행, 셀 안 불릿은 줄바꿈으로 담김. 반대로 기아
    정답은 카드 선언이 없는 뚱뚱 셀이라 불릿 1개=1행 — 분해는 무선언 카드에만 적용.)"""
    pairs = _card_pairs(grid)
    vals = [(lab, val) for lab, val in pairs if val]
    if not vals:
        return []
    code = next((v for _, v in vals if _ID_PAT.search(v) and len(v) <= 16), "")
    detail_val = max((v for _, v in vals), key=len)                    # 세부내용 = 최장값
    # 요구사항명 = '명칭/항목명/제목/…명' 라벨의 값을 최우선. 최단길이 휴리스틱만 쓰면
    # '정의'·'구분'(짧은 소라벨)이 진짜 제목보다 먼저 뽑혀 오염된다(강원랜드 31%·경기도
    # 58% 행 실측). 양형은 라벨이 '요구사항 명'이라 '명칭' 포함검사에 안 걸려 분류값이
    # name 으로 새던 버그 — _lab_is_name 으로 확장.
    name_labeled = next((v for lab, v in vals
                        if _lab_is_name(lab) and v != code and v != detail_val), None)
    name_cand = [v for _, v in vals if v != code and v != detail_val and not _NUM_ONLY.match(v)]
    name = name_labeled or (min(name_cand, key=len) if name_cand else "")
    # 계위 = '분류' 라벨 값(문서 자신의 분류 선언) — 없으면 name 폴백
    level = next((v for lab, v in vals
                  if "분류" in re.sub(r"\s+", "", lab) and v != code), "") or name or code
    if code:
        # 정의/세부내용 등 서술 값들을 줄바꿈으로 이어 카드 1행에 담는다.
        # 고유번호/명/분류(각자 칼럼으로 감)와 8자 미만 짧은 값은 제외.
        body_lines = []
        for lab, v in vals:
            z = re.sub(r"\s+", "", lab)
            if v == code or v == name or len(v) < 8:
                continue
            if "번호" in z or "분류" in z or _lab_is_name(lab):
                continue
            # '세부내용/상세설명' 라벨은 본문 그 자체라 프리픽스가 노이즈('세부 내용
            # □ 공통사항' 잔행 실측) — 생략. 정의/산출정보 등 의미 라벨만 프리픽스.
            skip_pref = (not lab) or "세부내용" in z or "상세설명" in z or v.startswith(lab)
            body_lines.append(v if skip_pref else f"{lab} {v}")
        body = "\n".join(body_lines) or detail_val
        if len(body) <= _CARD_ROW_MAX:
            return [{"level": level, "name": name, "detail": body, "code_hint": code}]
        # 카드 본문이 정답지 관측 범위(~1109자)를 크게 초과 — '같은 code_hint 반복'
        # 원칙을 유지하며 문단(줄) 단위로 여러 행에 나눠 담는다("길면 동그라미 기준으로
        # 나누되 ID는 그대로" 피드백). 마커 소실 변환기(hwp5html)에서도 동작해야 하므로
        # split_items(마커 기반)가 아니라 줄 기반 _split_card_lines 를 쓴다.
        pieces = _split_card_lines(body)
        if len(pieces) <= 1:
            return [{"level": level, "name": name, "detail": body, "code_hint": code}]
        return [{"level": level, "name": name, "detail": p, "code_hint": code} for p in pieces]
    out = []
    for piece in split_items(detail_val):
        out.append({"level": level, "name": name, "detail": piece, "code_hint": code})
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
    # 문서고유번호(ID) 열 — 'SFR-001' 형 셀이 과반인 열. 가로 요구표에도 문서가 ID를
    # 선언하는 경우가 있어(총괄표 ID 전수 포함 요구) 행별 code_hint 로 방출한다.
    # 한 ID의 상세가 여러 행/조각으로 나뉘어도 전부 같은 code_hint(같은 ID 반복 원칙).
    id_col = None
    for c in range(ncol):
        if c == content:
            continue
        vals = [re.sub(r"\s+", "", _norm(row[c])) for row in data if c < len(row)]
        vals = [v for v in vals if v]
        if len(vals) >= 2 and sum(1 for v in vals if _ID_CELL.match(v)) / len(vals) >= 0.5:
            id_col = c
            break
    # 계위(구분) 열 = content 왼쪽에서 '숫자 index 아니고 라벨성(짧음)'인 최좌열
    cat_col = None
    for c in range(content):
        if c == id_col or stats[c]["numratio"] > 0.6:   # 순번/No·ID 열 스킵
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
        if c == cat_col or c == id_col or stats[c]["numratio"] > 0.6:
            continue
        if 4 <= stats[c]["avglen"] <= stats[content]["avglen"]:
            name_col = c
            break

    def cell(row, c):
        return _norm(row[c]) if c is not None and c < len(row) else ""

    out: list[dict] = []
    cat_fill = name_fill = id_fill = ""
    for row in data:
        det = cell(row, content)
        if not det or len(det) < 3:
            continue
        # ID 열 값(공백 제거) — 빈 셀은 직전 ID 이어받기(rowspan/연속행 = 같은 요구사항)
        rid = re.sub(r"\s+", "", cell(row, id_col)) if id_col is not None else ""
        if id_col is not None:
            rid = rid if _ID_CELL.match(rid) else ""
            rid = rid or id_fill
            id_fill = rid or id_fill
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
                        "code_hint": rid,
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

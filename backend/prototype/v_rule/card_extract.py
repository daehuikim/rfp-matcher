"""카드 유닛 → (gemma 요구사항 판단) → 고정칼럼 행(요구사항ID/명/계위/상세).

흐름(사용자 재설계): HTML → 문서순 블록 → 章(상위)=탭 / 카드(가·나 등)=요구사항명 유닛,
그 아래 본문·표를 상세내용(atomic)으로. gemma 는 **카드가 요구사항인지 판단(keep)** 만 —
내용 생성·키워드 룰 없음. 탭별 ID 접두사(같은 탭=같은 접두사).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from .cards import IMG_MARKER_RE, iter_blocks, marker_level, strip_marker
from .table_extract import split_items, table_to_reqs


# 헤딩 오인 방지: 마커가 붙어도 '긴 완결 문장'은 헤딩이 아니라 내용(상세)이다.
# 예: '다. 제출한 제안서와 발표내용은 동일해야 하며…' → 헤딩 아님(스캔 catch-all 방지)
_SENT_END = re.compile(
    r"(?:한다|하다|해야|하며|되며|바람|함|음|됨|된다|는다|간다|온다|않다|같다|없다|있다|것|임)\s*[.]?\s*$")


def _is_heading_line(text: str) -> bool:
    t = strip_marker(text).strip()
    if not t or len(t) > 50:          # 너무 길면 헤딩 아님(문장)
        return False
    if _SENT_END.search(t):           # 종결어미로 끝나면 문장(내용)
        return False
    return True


def _detail_text(d) -> str:
    """상세는 str 또는 dict{level,name,detail} 둘 다 허용 — 텍스트만 꺼낸다."""
    return (d.get("detail", "") if isinstance(d, dict) else d) or ""


def _norm_tab_key(t: str) -> str:
    """탭 병합 키 — 공백·전각/반각 괄호 차이로 같은 카테고리가 별도 탭으로 쪼개지는 것 방지
    (예: 'SIP (IP-PBX)' 안의 공백 유무, '옴니채널상담' vs '옴니채널 상담')."""
    t = re.sub(r"[（）]", lambda m: "(" if m.group() == "（" else ")", t or "")
    return re.sub(r"\s+", "", t).lower()


def _tab_base_key(t: str) -> str:
    """괄호 안 내용까지 지운 느슨한 병합 키 — 'SIP (IP-PBX)'와 'SIP (Session Initiation
    Protocol)'처럼 같은 대상을 다르게 부연설명한 탭들을 하나로 합친다(overview/glossary
    잔여 탭 중복 방지). 완전탭명 병합(_norm_tab_key)이 실패했을 때의 느슨한 폴백으로만 사용."""
    return re.sub(r"[\(（][^\)）]*[\)）]", "", t or "").strip().lower()


# 섹션 헤더로 흔히 쓰는 도형 기호(속찬 사각/마름모). 이런 기호로 시작하는 짧은 라벨은
# 섹션 헤딩으로 승격(예: '■기능요구사항(SFR, System Function Requirement) 목록').
_SECTION_SYM = re.compile(r"^\s*[■▣◈◆◇□▷▶◎]")


def _toc_piece(p) -> bool:
    """짧은 헤딩형 조각(마커+비문장, ≤25자) — 목차 항목의 형태. str/dict 상세 모두 허용."""
    t = _detail_text(p).strip()
    return (bool(t) and len(t) <= 25 and marker_level(t) is not None
            and not _SENT_END.search(strip_marker(t)))


def _drop_toc_unit_runs(units: list) -> list:
    """같은 제목(ambient) 아래 '한 줄짜리 헤딩형 상세' 유닛이 5개 이상 연속되면 목차
    나열로 판정해 제거. 변환기가 목차를 개별 <p> 블록으로 쪼개 들어오면(대한항공 실측)
    블록 단위 _looks_toc_run 으로는 못 잡는다 — 유닛 레벨 후처리로 보완. 목차 항목은
    본문에 같은 제목으로 재등장하므로 recall 손실 없음. 구조 신호만 사용."""
    drop: set[int] = set()
    n = len(units)
    i = 0
    while i < n:
        u = units[i]
        if not (len(u.details) == 1 and _toc_piece(u.details[0])):
            i += 1
            continue
        j = i
        while (j < n and units[j].title == u.title
               and len(units[j].details) == 1 and _toc_piece(units[j].details[0])):
            j += 1
        if j - i >= 5:
            drop.update(range(i, j))
        i = j
    return [u for k, u in enumerate(units) if k not in drop]


def _looks_toc_run(pieces: list[str]) -> bool:
    """분해 결과가 '목차 나열'인지 — 다수(≥5) 조각의 80%↑가 짧은 헤딩형(마커+비문장)이면
    본문이 아니라 목차/색인 덩어리다(대한항공 실측: 'Ⅰ 사업안내 1. 사업목적 2. 사업범위…'
    338자 한 덩어리가 28개 헤딩 조각으로 분해되어 junk 폭증). 목차 항목은 본문에서 같은
    제목으로 재등장하므로 상세로 올리지 않아도 recall 손실이 없다. 구조 신호만 사용."""
    if len(pieces) < 5:
        return False
    short_head = sum(1 for p in pieces
                     if len(p) <= 25 and marker_level(p) is not None
                     and not _SENT_END.search(strip_marker(p)))
    return short_head / len(pieces) >= 0.8


def _is_section_bullet(text: str) -> bool:
    # ■/▣/◈ 로 시작하는 섹션 헤더는 다소 길어도(예: '■ 기능 요구사항 (SFR, System
    # Function Requirement) – 13. 보이는 ARS') 헤딩으로 인정. 완결 문장만 제외.
    if not _SECTION_SYM.match(text or ""):
        return False
    stripped = strip_marker(text).strip()
    return 0 < len(stripped) <= 75 and not _SENT_END.search(stripped)


@dataclass
class Unit:
    tab: str                         # 章(상위 섹션) = Excel 탭
    marker: str
    title: str                       # 요구사항명 후보
    level_path: str                  # 계위(마커 경로)
    details: list = field(default_factory=list)   # 상세내용: str 또는 dict{level,name,detail}
    page: int | None = None          # 원본 페이지(원문뷰어 #page=N 점프용) — 유닛 생성 시점 블록에서
    table_index: int | None = None   # 변환 HTML 안 <table> 문서순 인덱스(원문뷰어 표 스크롤용)


def _table_details(grid: list[list[str]]) -> list[str]:
    """표 행 → 상세 문자열. 헤더로 보이는 첫 행은 라벨로 접두."""
    out = []
    for row in grid:
        cells = [c.strip() for c in row if c and c.strip()]
        if cells:
            out.append(" | ".join(cells))
    return out


_HEAD_MAX = 3   # 레벨 0~3(Ⅰ/1./1.1/가./ㄱ/1.1.1)=헤딩, 4~5(1)/❍/-)=내용(상세)


def build_units(html: str | None = None, blocks: list | None = None) -> list[Unit]:
    """블록 → 유닛 (헤딩 스택 트리). 헤딩(레벨≤3, 단 긴 문장 제외)마다 유닛 시작,
    내용(불릿/❍/표/평문)은 가장 가까운 헤딩에 붙인다.

    개선(진단 반영):
      - 표는 셀 join 대신 table_to_reqs 로 구조 추출(내용열=상세, 구분열=계위; 도표/현황=드롭).
      - '긴 완결 문장' 마커는 헤딩 강등(스캔 catch-all 방지).
      - '■시스템구성요구사항' 같은 sparse 라벨 + 다음이 표면 헤딩 승격(기아 ECR/SFR 카테고리 복원).
      - 텍스트 블록에 ⦁/❍/• 다항목이 뭉치면 분해해 개별 상세로(JB ⦁ 복원).
    """
    if blocks is None:
        blocks = iter_blocks(html or "")
    stack: list[tuple[int, str]] = []   # (level, title)
    li_stack: list[tuple[int, str]] = []   # (li_depth, 항목텍스트) — 리스트 계위 복원용
    units: list[Unit] = []
    cur: Unit | None = None
    cur_page: int | None = None       # 현재 순회 중인 블록의 페이지/표인덱스(원문뷰어용)
    cur_tbl_idx: int | None = None    # attach_isolated/open_heading 등 클로저가 읽기전용 참조

    def ensure_cur() -> Unit:
        nonlocal cur
        if cur is None:
            cur = Unit(tab="요구사항", marker="", title="", level_path="",
                       page=cur_page, table_index=cur_tbl_idx)
            units.append(cur)
        return cur

    def attach_isolated(items: list, use_item_tab: bool = True, level_suffix: str = "") -> None:
        """ambient(cur) 탭/계위를 공유하되 항목마다 독립 유닛(keep 판정 격리).
        표·다항목 셀은 본질적으로 여러 요구사항이라, 한 유닛에 몰아넣으면 그중
        하나가 junk 라 판정될 때 옆의 진짜 요구사항까지 통째로 drop된다(대한항공 recall 원인).

        use_item_tab: 표에서 뽑힌 항목이 자기 카테고리(_tab)를 갖고 있을 때 그걸 탭으로 쓸지.
        큰 표(행 많음, 각 행=서로 다른 컴포넌트 — SIP/CTI/챗봇 등)는 True 로 각자 탭을 줘야
        기아 43행이 '상담석 규모' 같은 무관 ambient 헤딩에 오분류되지 않는다. 반대로 작은
        표(≤8행, 평균응답시간/평균처리시간/동시처리 같은 한 화제의 하위항목)까지 항목마다
        탭을 쪼개면 사람이 안 만들 만큼 탭이 폭발한다(기아 161탭) — 이런 경우는 False 로
        ambient 탭 하나에 묶되, keep 판정 격리는 그대로 유지.

        level_suffix: 리스트 계위(조상 <li> 텍스트 경로) — 헤딩 계위 뒤에 붙여 JB/신한처럼
        리스트로 쓰인 요구사항의 부모 항목을 계위로 복원(구조 신호만, 키워드 없음)."""
        base_tab = cur.tab if cur else "요구사항"
        base_title = cur.title if cur else ""
        base_level = cur.level_path if cur else ""
        if level_suffix:
            base_level = f"{base_level} > {level_suffix}" if base_level else level_suffix
        for it in items:
            item_tab = it.get("_tab") if (use_item_tab and isinstance(it, dict)) else None
            tab = item_tab[:40] if item_tab else base_tab
            # 표 구분열이 'N-M)' 형제번호를 갖고 있으면(예: 기아 SFR표의 '1-1)~1-5) 통합
            # 상담 시스템 ...') 마커를 유닛에 실어 _merge_sibling_numbered_tabs 가 인식하게 한다.
            tab_marker = it.get("_tab_marker") if isinstance(it, dict) else None
            mk = tab_marker if (tab_marker and item_tab) else ""
            title = item_tab[:60] if (tab_marker and item_tab) else base_title
            units.append(Unit(tab=tab, marker=mk, title=title, level_path=base_level, details=[it],
                              page=cur_page, table_index=cur_tbl_idx))

    def open_heading(text: str, lvl: int) -> None:
        nonlocal cur, stack
        title = strip_marker(text)[:60]
        stack = [(l, t) for (l, t) in stack if l < lvl] + [(lvl, title)]
        tab = (title or (stack[0][1] if stack else ""))[:40] or "요구사항"
        # 'N-M)' 형제마커는 변환기가 'N - M)' 처럼 사이 공백을 넣기도 해 text.split()[0]로는
        # "N"만 잡혀 형제묶음(_merge_sibling_numbered_tabs)이 인식 못 한다 — 패턴 전체를 캡처.
        sib = re.match(r"^\s*\d+\s*-\s*\d+\)", text or "")
        mk = re.sub(r"\s+", "", sib.group()) if sib else (text.split()[0] if text.split() else "")
        cur = Unit(tab=tab, marker=mk, title=title, level_path=" > ".join(t for _, t in stack),
                  page=cur_page, table_index=cur_tbl_idx)
        units.append(cur)

    def _card_head_fragment(grid: list[list[str]]) -> bool:
        """페이지 경계에서 잘린 '카드 머리' 조각인지 — '고유번호' 라벨 + ID값 행 보유.

        법제처 실측: [요구사항 분류|데이터][요구사항 고유번호|DAR-013] 2행 조각이
        junk 표로 드롭되며 선언 ID 가 통째로 유실됐다(몸통은 다음 표 조각에 있음)."""
        from .table_extract import _ID_CELL as _IDC
        if len(grid) > 8:
            return False
        for row in grid:
            cells = [re.sub(r"\s+", "", c or "") for c in row]
            if any("고유번호" in c for c in cells) and any(_IDC.match(c) for c in cells if c):
                return True
        return False

    n = len(blocks)
    pending_head: list | None = None   # 잘린 카드 머리 조각(다음 표에 이어붙임)
    pending_ttl = 0
    for i, b in enumerate(blocks):
        cur_page = getattr(b, "page", None)
        cur_tbl_idx = getattr(b, "table_index", None) if b.kind == "table" else None
        if b.kind != "table" and pending_head is not None:
            # 조각 사이에 끼는 건 페이지 머리글/바닥글 정도 — 헤딩이 오거나 멀어지면 포기
            pending_ttl -= 1
            if pending_ttl <= 0 or getattr(b, "htag", None):
                pending_head = None
        if b.kind == "table":
            grid = list(b.grid)
            merged_head = None
            if pending_head is not None:
                merged_head = pending_head
                grid = pending_head + grid
                pending_head = None
            reqs = table_to_reqs(grid)
            if merged_head is not None and reqs == []:
                # 머리 조각을 붙였더니 통째 junk — 무관한 표에 잘못 붙인 것(법제처 실측:
                # 부록 법령목록 32행이 같이 증발). 원본 표만으로 재시도해 콜래터럴 방지.
                grid = list(b.grid)
                reqs = table_to_reqs(grid)
            if reqs == [] and _card_head_fragment(grid):
                pending_head = grid
                pending_ttl = 3
                continue
            if reqs is None:
                # 요구표 판정 불가(1열 요구표 등) → 행마다 독립 유닛(표는 본질적으로 다중
                # 요구사항 나열이라 한 유닛에 몰아넣으면 keep 판정이 콜래터럴 드롭을 낸다).
                pieces = [p for line in _table_details(grid) for p in split_items(line)]
                if not _looks_toc_run(pieces):
                    attach_isolated(pieces)
            elif reqs and any("_tab" in r for r in reqs):
                # 구분(카테고리)열 반복도 확인 — 여러 행이 같은 구분을 공유해야 '진짜 분류표'
                # (예: SFR 20행이 'SIP' 공유). 행마다 구분값이 거의 다 다르면 분류표가 아니라
                # 로드맵/체크리스트(항목당 1라벨)이므로 탭 쪼개지 말고 ambient 유닛에 붙인다
                # (기아 '고객 채널 최적화 1' 등 1행짜리 탭 폭발 방지 — 구조 신호, 키워드 아님).
                hints = [r.get("_tab") for r in reqs if r.get("_tab")]
                n_cat = len(set(hints))
                grouped = bool(hints) and len(hints) >= 3 and n_cat / len(hints) <= 0.7
                if not grouped:
                    # 진짜 분류표 아님(구분값이 행마다 다 다름). '고유 구분값 개수'로 갈라 처리
                    # (reqs 총 길이 아님 — 불릿 분해로 한 구분값에서 여러 줄이 나와 부풀려짐,
                    # 예: 성능표 6구분→11reqs였는데 reqs로 재면 잘못 '크다'고 오판했었음):
                    # 구분값 많음(>8, SIP/CTI/챗봇 등 실제로 다른 컴포넌트) → 항목별 탭.
                    # 구분값 적음(≤8, 평균응답시간/평균처리시간 등 한 화제의 하위항목) → ambient 탭 하나.
                    attach_isolated(reqs, use_item_tab=n_cat > 8)
                else:
                    # **문서순서 절대 보존** — 이 표 블록 안에서만 카테고리 유닛을 만들고,
                    # 멀리 떨어진 이전 블록의 동명 유닛에 절대 append 하지 않는다(예전 창(80유닛)
                    # 병합이 p.13 유닛에 p.92 행을 이어붙여 조견표 순서가 점프하던 실측 버그).
                    # 같은 카테고리가 뒤에 다시 나오면 같은 '탭 이름'의 새 유닛이 되고, Excel
                    # 시트/FE 카드 병합은 이름 기준으로 순서 그대로 이뤄진다.
                    block_units: dict[str, Unit] = {}
                    for r in reqs:
                        t = (r.get("_tab") or (cur.tab if cur else "요구사항"))[:40] or "요구사항"
                        key = _norm_tab_key(t)
                        tu = block_units.get(key)
                        if tu is None:
                            # 구분열이 'N-M)' 형제번호를 가지면(예: '1-1)~1-6) 통합 상담
                            # 시스템 ...') 마커를 실어 _merge_sibling_numbered_tabs 가 인식.
                            tu = Unit(tab=t, marker=(r.get("_tab_marker") or ""), title=t,
                                      level_path=(cur.level_path + " > " + t if cur and cur.level_path else t),
                                      page=cur_page, table_index=cur_tbl_idx)
                            units.append(tu)
                            block_units[key] = tu
                        tu.details.append(r)
            elif reqs:                              # 세로 카드 등 → ambient 탭 공유 + 항목별 독립 유닛
                attach_isolated(reqs)
            # reqs == [] (도표/현황/배점) → 드롭
            continue

        lvl = marker_level(b.text)
        # HTML 헤딩 태그(h1~h4) = DOCX/HWP 순수제목(마커 없음) 전용 폴백.
        # 주의: OpenDataLoader 는 PDF 단락에도 자체 판단으로 <h3> 등을 붙이는데, 그 레벨이
        # 텍스트 마커(로마자 Ⅰ/Ⅱ 등)의 실제 계위와 어긋날 수 있다(대한항공 'I./II./III.'가
        # 문서 최상위(0)인데 <h3>로 붙어 레벨2로 강등돼 상위 계위가 통째로 무너진 사례).
        # 그래서 마커가 있으면 마커를 우선하고, htag 는 마커가 없을 때만 쓴다.
        tag_lvl = (b.htag - 1) if getattr(b, "htag", 0) and b.htag <= 4 else None
        # sparse 라벨(짧고 다음 블록이 표) → 섹션 헤딩 승격(레벨2). 예: ■시스템구성요구사항(ECR)
        nxt_is_table = i + 1 < n and blocks[i + 1].kind == "table"
        sparse_label = (len(strip_marker(b.text).strip()) <= 40 and nxt_is_table
                        and not _SENT_END.search(strip_marker(b.text)))
        marker_head = lvl is not None and lvl <= _HEAD_MAX and _is_heading_line(b.text)
        # 리스트 계위: <li> 블록이면 조상 li 텍스트 경로가 계위 접미가 된다(JB/신한 —
        # 요구사항이 표가 아닌 중첩 리스트로 옴). 리스트가 끝나면(li 아닌 텍스트) 스택 클리어.
        li_depth = getattr(b, "li_depth", 0)
        if li_depth:
            li_anc = [(d, t) for (d, t) in li_stack if d < li_depth]
            li_stack[:] = li_anc   # 같은/더 깊은 깊이의 이전 형제 항목 제거(스택 최신화)
        else:
            li_anc = []
            li_stack.clear()       # 리스트 밖 텍스트 → 리스트 종료
        li_suffix = " > ".join(t for _, t in li_anc)
        if marker_head:
            open_heading(b.text, lvl)
            continue
        if tag_lvl is not None:
            open_heading(b.text, tag_lvl)
            continue
        is_heading = sparse_label or _is_section_bullet(b.text)
        if is_heading:
            open_heading(b.text, 2)
        elif b.text.strip():
            base = strip_marker(b.text) if lvl is not None else b.text
            pieces = split_items(base)              # ⦁/❍/• 다항목 분해
            if len(pieces) > 1:
                if not _looks_toc_run(pieces):       # 목차 나열 덩어리는 상세로 안 올림
                    attach_isolated(pieces, level_suffix=li_suffix)   # 항목별 독립 유닛(keep 격리)
            elif (lvl is not None and lvl >= 4) or li_depth:
                # 레벨4/5 마커(가)/나)/1)/2)/❍/- 등) 또는 <li> 항목은 나열형 리스트의
                # 개별 항목이다(li 는 텍스트 마커가 없어도 구조적으로 이미 항목).
                # 방치하면 여러 항목이 한 유닛에 쌓여, 그중 하나가 junk 판정될 때
                # 옆의 진짜 요구사항까지 통째로 drop된다(신한은행/경기도 실측).
                attach_isolated([base], level_suffix=li_suffix)
            else:
                ensure_cur().details.append(base)
            if li_depth:                             # 이 li 가 다음 자식 li 들의 부모가 될 수 있음
                li_stack[:] = li_anc + [(li_depth, re.sub(r"\s+", " ", base)[:40])]
    units = _drop_toc_unit_runs(units)
    # 헤딩 자체는 상세가 비어(바로 표가 이어지므로) 필터에서 빠지지만, 형제계열 인식엔
    # 헤딩이 필요하므로 필터 전에 돌린다 — 산하 표/본문 유닛(level_path 로 추적)의 탭도
    # 함께 정정되므로 필터 후에도 효과가 남는다.
    _merge_bullet_heading_families(units)
    out = [u for u in units if u.details]   # 내용 있는 유닛만(빈 章 헤딩 제외)
    _merge_sibling_numbered_tabs(out)
    return out


_FAMILY_BULLETS = set("■▣◈◆◇□▷▶◎")


def _merge_bullet_heading_families(units: list[Unit]) -> None:
    """'■ 기능 요구사항(SFR, System Function Requirement) – 20. CS Plaza' 처럼 섹션헤딩이
    번호/개별 서비스명만 다르고 나머지 라벨은 그대로 반복되는 '형제 계열'이면, 반복되는
    공통 접두사(예: '기능 요구사항 (SFR, System Function Requirement)')를 진짜 탭으로
    쓴다. 안 그러면 항목마다 제각각 탭이 돼(SFR 하나가 20~30개 서비스로) 탭이 100개
    넘게 폭발하고, 사람이 볼 땐 'SFR'이어야 할 탭이 개별 서비스명('CS Plaza')이 돼버린다.

    헤딩 유닛 자신은 보통 상세가 비어 있고(바로 표가 이어짐) 진짜 내용은 표에서 뽑힌
    별도 유닛에 있다. 게다가 같은 계열의 일부 항목(예: '20) CS Plaza')은 자기 헤딩조차
    없이 표 구분열의 단순 번호로만 존재해, 제목 유사성으로는 계열을 못 알아본다 —
    그래서 헤딩 산하 유닛뿐 아니라, 체인의 첫~끝 헤딩 '사이 구간'에 있는 모든 유닛을
    통째로 계열에 편입한다(그 구간은 정의상 이 계열 항목들의 본문/표이거나 그 빈틈).
    같은 계열 헤딩 사이에 본문/표 유닛이 껴도 건너뛰며 체인을 유지 — 인접 구간에서만
    합치므로 페이지 순서는 그대로 보존된다."""
    n = len(units)
    i = 0
    while i < n:
        if (units[i].marker or "") not in _FAMILY_BULLETS:
            i += 1
            continue
        chain = [units[i]]
        chain_idx = [i]
        j = i + 1
        while j < n:
            mk = units[j].marker or ""
            if mk in _FAMILY_BULLETS:
                cand = _common_prefix_words([chain[0].title, units[j].title])
                if len(cand) >= 6:
                    chain.append(units[j])
                    chain_idx.append(j)
                    j += 1
                    continue
                break   # 접두사 안 맞는 다른 계열의 ■헤딩 → 체인 종료
            j += 1      # 마커 없는 본문/표 유닛은 건너뛰며 체인 유지
        if len(chain) >= 2:
            common = _common_prefix_words([u.title for u in chain]).rstrip(" –-:,").strip()
            if len(common) >= 6:
                lo, hi = chain_idx[0], chain_idx[-1]
                for k in range(lo, hi + 1):
                    units[k].tab = common[:40]
        i = j if len(chain) >= 2 else i + 1


_SIBLING_MARKER = re.compile(r"^(\d+)-(\d+)\)$")


def _common_prefix_words(strs: list[str]) -> str:
    """문자열들의 공통 접두사(단어 단위) — 'X 일반'/'X 고객센터'/'X 긴급출동' → 'X'."""
    if not strs:
        return ""
    split = [s.split() for s in strs]
    common: list[str] = []
    for tokens in zip(*split):
        if len(set(tokens)) == 1:
            common.append(tokens[0])
        else:
            break
    return " ".join(common)


def _merge_sibling_numbered_tabs(units: list[Unit]) -> None:
    """'1-1)/1-2)/1-3)' 형제번호 헤딩들이 각자 탭이 되면 사람이 만드는 것보다 훨씬 잘게
    쪼개진다(기아: 원문에 '1.통합상담시스템' 같은 부모 章 헤딩 자체가 없어(목차에만
    있음) 산하 5개 하위섹션이 각자 탭으로 흩어짐 — 정답지는 이 5개를 시트 1개로 묶음).
    문서 순서상 연속으로 이어지는 같은 부모번호(N) 형제들을 찾아, 제목의 공통 접두사
    (형제들이 다 같은 말로 시작 — '통합 상담 시스템 일반/고객센터/긴급출동...')를
    탭으로 재배정한다. 마커 있는 다른 부모번호가 끼면 체인을 끊어 오병합을 막는다."""
    n = len(units)
    i = 0
    while i < n:
        m = _SIBLING_MARKER.match(units[i].marker or "")
        if not m:
            i += 1
            continue
        parent = m.group(1)
        chain = [units[i]]
        j = i + 1
        expect = int(m.group(2)) + 1
        while j < n:
            m2 = _SIBLING_MARKER.match(units[j].marker or "")
            if m2 and m2.group(1) == parent and int(m2.group(2)) == expect:
                chain.append(units[j])
                expect += 1
                j += 1
            elif m2:
                break   # 다른 부모/순번의 형제 마커 → 체인 종료(다른 그룹 시작)
            else:
                j += 1  # 마커 없는 본문 유닛(개별 요구사항 등) — 체인 유지하며 건너뜀
        if len(chain) >= 2:
            common = _common_prefix_words([u.title for u in chain])
            if len(common) >= 2:   # 의미있는 공통어(2자 이상)일 때만 병합
                for u in chain:
                    u.tab = common[:40]
        i = j if len(chain) >= 2 else i + 1


class _KeepItem(BaseModel):
    index: int
    keep: bool


class _KeepResult(BaseModel):
    items: list[_KeepItem]


def _judge_keep(units: list[Unit]) -> dict[int, bool]:
    """gemma 가 각 유닛이 '제안사 이행 요구사항'인지 keep 판정(과포함 우선)."""
    from app.core.config import Settings
    from app.llm.base import Message
    from app.llm.factory import build_llm_client
    from app.llm.fake_client import FakeLlmClient
    from prototype.v2.async_run import run_coro

    client = build_llm_client(Settings())
    if isinstance(client, FakeLlmClient):
        return {i: True for i in range(len(units))}
    out: dict[int, bool] = {}
    CH = 30
    for k in range(0, len(units), CH):
        chunk = units[k:k + CH]

        def _clean(s: str) -> str:  # 대괄호 사업명 등 노이즈 제거 후 gemma 에 보여줌
            return re.sub(r"[\[\(（【][^\])）】]*[\])）】]", "", s or "").strip()
        block = "\n".join(
            f"[{k+j}] 계위='{_clean(u.level_path)[:50]}' 제목='{_clean(u.title)[:40]}' "
            f"상세='{' / '.join(_detail_text(d)[:60] for d in u.details[:3])}'"
            for j, u in enumerate(chunk)
        )
        prompt = (
            "RFP 카드들이다. 조견표에 넣을지(keep) 판정하라. (계위=섹션 경로 참고)\n"
            "keep=false(명백한 비요구만): 표지·개정이력·문서 목차·사업 배경/추진목적·추진일정·입찰/계약 안내·"
            "제안 평가배점·제출/작성 양식(서식)·연락처·발주처 현황(AS-IS 보유목록)·조직/인력 현황(명단)·서약서·"
            "요구사항 총괄표(집계)·용어/약어 정의·도입품목/장비 목록·규모/수량 현황·다이어그램 라벨 파편.\n"
            "그 외 **제안사가 이행·구축·개발·제공·준수·수행할 내용(요구/제안/방안/기준/기능)은 전부 keep=true. "
            "'~방안/방법/체계/기준'도 요구사항이므로 keep=true. 애매하면 keep=true.**\n\n"
            f"[카드]\n{block}\n\n"
            'JSON: {"items":[{"index":<int>,"keep":<bool>}, ...]} — 모든 index.'
        )
        try:
            res = run_coro(client.structured_output(
                [Message(role="user", content=prompt)], _KeepResult,
                purpose="card_keep", max_tokens=4000))
            for it in res.items:
                out[it.index] = it.keep
        except Exception:
            # 조용히 전체keep 폴백하면 gemma 장애를 알아챌 수 없다 — 반드시 로그.
            import logging
            logging.getLogger(__name__).warning(
                "gemma keep 호출 실패(청크 %d~%d) — 이 청크는 전부 keep=True 폴백", k, k + len(chunk) - 1,
                exc_info=True)
            for j in range(len(chunk)):
                out[k + j] = True
    # 안전망: 전부 drop 되면(엄격 오판) 문서 통째 손실 방지 — 전체 유지로 폴백.
    if units and not any(out.get(i, True) for i in range(len(units))):
        return {i: True for i in range(len(units))}
    return out


_JUNK_DETAIL = re.compile(r"^[\s\d.\-–—()·:;%~]+$")   # 숫자·기호·점만(페이지번호·날짜)
_TOC_LINE = re.compile(r"(?:[.·‧]\s*){4,}\s*\d*\s*$|…{2,}\s*\d*\s*$")  # 목차 점선리더+페이지번호
# 요구사항 총괄표 에코 — 'SFR-OOO'(ID부여규칙 셀), 'System Function Requirement'(영문 라벨만).
# 총괄표는 집계표라 조견표 행이 아니다(정답지도 별도 시트). canonical 감지에는 유닛 단계
# 신호를 이미 썼으므로 행에서 지워도 감지 무손실.
_SUMMARY_ECHO = re.compile(r"^[A-Za-z][A-Za-z ,&/()\-]{2,60}[Rr]equirement s?$|"
                           r"^[A-Za-z][A-Za-z ,&/()\-]{2,60}[Rr]equirement$")


def _is_junk_detail(d: str) -> bool:
    """요구사항 아닌 셀 — 페이지번호·날짜·기호·너무짧음·**목차(점선+페이지)**. 올리지 않는다."""
    d = (d or "").strip()
    if len(d) < 3:
        return True
    if _JUNK_DETAIL.match(d):
        return True
    if re.match(r"^\d{4}\s*[.\-]\s*\d{1,2}", d):   # 날짜류
        return True
    if _TOC_LINE.search(d):                          # 목차 라인(제목 …… 12)
        return True
    if _SUMMARY_ECHO.match(d):                       # 총괄표 영문라벨 에코('System Function Requirement')
        return True
    if _ID_RULE.search(d) and len(d) <= 40:          # 총괄표 ID부여규칙 셀('SFR-OOO 21')
        return True
    return False


def _clean_toc(s: str) -> str:
    """제목/계위에서 목차 점선리더(…… 12) 꼬리 제거."""
    return re.sub(r"\s*(?:[.·‧]\s*){3,}.*$|\s*…+.*$", "", s or "").strip()


def _slug_tab(t: str) -> str:
    # 깔끔한 접두사: 대괄호 내용·번호·마커·목차점선 제거 → 한글/영문만, 짧게.
    t = re.sub(r"[\[\(（【][^\])）】]*[\])）】]", "", t or "")   # [..](..) 제거
    toks = re.findall(r"[A-Za-z가-힣]+", t)                     # 숫자 제외(제안서'2'→제안서)
    return ("".join(toks)[:8]) or "REQ"


def _consolidate_small_tabs(rows: list[dict], small_max: int = 2, large_min: int = 5) -> list[dict]:
    """작은 탭(≤small_max행)을 괄호제거 키로 일치하는 더 큰 탭(≥large_min행)에 병합.

    'SIP (Session Initiation Protocol)'(1행, 개요/글로서리 잔여) 같은 표기변형이
    'SIP (IP-PBX)'(52행, 진짜 요구사항 그룹)와 별도 탭으로 남는 걸 방지 — 최종 탭
    크기를 다 안 뒤에 하는 후처리라 어느게 '진짜' 큰 탭인지 안전하게 판단 가능
    (실시간 처리 중엔 아직 크기를 모름). 병합 후 탭별 ID 재부여.
    """
    from collections import defaultdict
    by_tab: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_tab[r["tab"]].append(r)
    large_by_base: dict[str, str] = {}
    for t in sorted(by_tab, key=lambda t: -len(by_tab[t])):
        if len(by_tab[t]) >= large_min:
            bk = _tab_base_key(t)
            if bk and bk not in large_by_base:
                large_by_base[bk] = t
    changed = False
    for t in list(by_tab):
        if len(by_tab[t]) <= small_max:
            target = large_by_base.get(_tab_base_key(t))
            if target and target != t:
                for r in by_tab[t]:
                    r["tab"] = target
                changed = True
    if not changed:
        return rows
    return _reassign_codes(rows)


def _reassign_codes(rows: list[dict]) -> list[dict]:
    """탭 구성 변경 후 요구사항ID(code) 재부여 — 탭 접두사-일련번호(공통 유틸).

    단, 문서가 부여한 고유번호(SFR-001 등, code_hint)가 있으면 그걸 그대로 쓴다 —
    조견표의 존재 이유가 원문 요구사항과의 맵핑이므로 원문 ID 가 항상 우선(충실전사)."""
    tab_counter: dict[str, int] = {}
    tab_prefix: dict[str, str] = {}
    # 관측된 문서 ID 접두사 — 자동번호가 진짜 문서 ID 네임스페이스와 충돌하면
    # ("SFR" 탭명 → SFR-001 자동번호 = 가짜 ID) 원문 맵핑이 무너지므로 접두사를 바꾼다
    doc_prefixes = set()
    for r in rows:
        mm = _DOC_ID.match(_norm_hint(r.get("code_hint")))
        if mm:
            doc_prefixes.add(mm.group(1))
    out = []
    for r in rows:
        hint = _norm_hint(r.get("code_hint"))
        if _DOC_ID.match(hint):
            out.append({**r, "code": hint})
            continue
        tab = r["tab"]
        if tab not in tab_prefix:
            slug = _slug_tab(tab)
            if slug in doc_prefixes:
                slug = f"{slug}일반"
            tab_prefix[tab] = slug
        pfx = tab_prefix[tab]
        tab_counter[pfx] = tab_counter.get(pfx, 0) + 1
        out.append({**r, "code": f"{pfx}-{tab_counter[pfx]:03d}"})
    return out


# 문서가 스스로 선언하는 요구사항 분류 신호(도메인 키워드가 아니라 문서 자신의 선언을 읽음)
_ID_RULE = re.compile(r"\b([A-Z]{2,4})\s*[-–]\s*[O0Ø]{2,3}\b")          # 총괄표 ID부여규칙 'SFR-OOO'
_R_HEADING = re.compile(r"([가-힣A-Za-z·/ ]{0,24}(?:요구\s*사항|제약\s*사항))\s*[\(（]\s*([A-Z]{2,4})\s*[,，)]")
# 약어 없는 R-헤딩: '성능 요구사항 (Performance Requirement)' / 탭이 '…요구사항'으로 끝남
_R_HEADING_NOPFX = re.compile(r"^([가-힣·/ ]{0,20}(?:요구\s*사항|제약\s*사항))\s*(?:[\(（].{0,40})?$")
# 본문 고유번호 — 'SFR-001'(2단) 또는 'SFR-LDA-001'처럼 대분류 안에 하위도메인 코드를
# 끼워넣는 3단 체계도 쓴다(양형 실측: SFR만 SFR-LDA-001/SFR-NSS-001/SFR-SCF-001 식,
# 다른 분류는 PMR-001 처럼 2단) — group(1)은 항상 맨 앞 대분류 접두사만 잡는다.
# 대시가 있으면 1자리 번호(SFR-1)도 허용, 대시 없는 축약형(SFR001)은 2자리 이상만.
_DOC_ID = re.compile(r"^([A-Z]{2,4})(?:[-–][A-Z]{2,6})?(?:[-–]\d{1,4}|\d{2,4})$")


def _norm_hint(h: str) -> str:
    """code_hint 매칭 전 정규화 — 변환기가 'SFR - 001'처럼 벌려놓은 내부 공백 제거.

    strip 만으로는 내부 공백이 남아 _DOC_ID 전체일치가 깨지고, 그 순간 문서 부여
    ID 가 버려진 채 자동번호로 대체된다(분할행 동일-ID 유지 원칙 위반). 매칭과
    최종 code 표기 모두 이 정규화본을 쓴다."""
    return re.sub(r"\s+", "", h or "")


def detect_canonical_categories(units: list, raw_text: str | None = None) -> list[dict]:
    """문서 자체 선언 분류 체계 감지 — 요구사항 총괄표(ID부여규칙 XXX-OOO 행)·
    R-헤딩('기능 요구사항(SFR, ...)' 및 약어 없는 '성능 요구사항(Performance…)')·
    세로카드 고유번호(SFR-001) 접두사 다수 등장.

    raw_text(태그 제거한 원문)를 함께 주면 총괄표를 원문에서 직접 스캔한다 —
    표 추출 단계가 ID부여규칙/건수 열을 떨어뜨려도(법제처 실측: 유닛에 'SIR-OOO'와
    '10'이 아예 없음) 선언 정보를 잃지 않는다.

    감지되면 이 분류가 곧 조견표 탭이 된다(사람이 만든 정답과 동일한 원칙 —
    법제처 정답 시트 = 총괄표의 10개 분류 그대로). 반환 [{name, prefix}] 문서 등장순.
    """
    from collections import OrderedDict
    canon: "OrderedDict[str, str]" = OrderedDict()    # prefix -> name
    nopfx: "OrderedDict[str, None]" = OrderedDict()   # 약어 없는 분류명(등장순)
    declared_counts: dict[str, int] = {}              # prefix -> 총괄표 선언 건수

    def _texts(u):
        yield u.tab or ""
        yield u.title or ""
        yield u.level_path or ""
        for d in u.details:
            # 총괄표가 한 detail 로 뭉쳐 오면 200자 절단 시 뒷 분류(선언 건수 포함)가
            # 통째로 안 보인다(법제처 실측: COR 14 건수 미파싱) — 넉넉히 본다.
            yield _detail_text(d)[:1500]

    id_hint_ids: dict[str, set[str]] = {}
    pfx_alias: dict[str, str] = {}                    # 헤딩 접두사 -> ID규칙 접두사

    def _scan(t: str) -> None:
        for m in _R_HEADING.finditer(t):
            # 원문(raw_text)은 태그 제거 자리의 공백 런이 남는다 — 탭명 오염 방지 정규화
            name, pfx = re.sub(r"\s+", " ", m.group(1)).strip(), m.group(2)
            # 헤딩 괄호 접두사와 ID부여규칙 접두사가 다른 문서가 있다(법제처 실측:
            # '인터페이스 요구사항(INR) … SIR-OOO'). 실제 카드 ID 가 쓰는 건
            # ID부여규칙 쪽이므로, 헤딩 직후 ~80자 안의 ID규칙 접두사를 정본으로
            # 삼고, 헤딩 접두사는 별칭으로 기억한다(본문 섹션 헤딩 '(INR' 매칭용).
            m2 = _ID_RULE.search(t, m.end(), min(len(t), m.end() + 80))
            if m2 and m2.group(1) != pfx:
                pfx_alias[pfx] = m2.group(1)
                pfx = m2.group(1)
            elif pfx in pfx_alias:
                pfx = pfx_alias[pfx]
            # 총괄표 _ID_RULE 이 먼저 등장해 빈 이름으로 등록됐어도 R-헤딩을 만나면
            # 채운다 — 이름이 영영 빈 채면 탭명이 맨접두사("SFR")가 되고, 그 탭의
            # 자동번호 슬러그가 진짜 문서 ID 와 충돌하는 가짜 ID 경로가 된다.
            if pfx not in canon or not canon[pfx]:
                canon[pfx] = f"{re.sub(r'요구 사항', '요구사항', name)}({pfx})"
        for m in _ID_RULE.finditer(t):
            canon.setdefault(m.group(1), "")
            # 총괄표의 선언 건수('SFR-OOO 21') — ID규칙 바로 뒤 숫자. 전-무ID 분류
            # 순번 부여의 신뢰성 게이트로 쓴다(선언 건수 초과 조작 방지).
            m3 = re.match(r"\s*(\d{1,3})\b", t[m.end():m.end() + 12])
            if m3:
                declared_counts.setdefault(m.group(1), int(m3.group(1)))

    # 원문 스캔을 먼저 — 총괄표(헤딩·ID규칙·건수가 한 자리에 모임)가 가장 권위 있는
    # 선언이라 별칭/건수를 유닛 스캔 전에 확보한다(유닛 스캔이 INR 를 따로 만들기 전에).
    if raw_text:
        _scan(raw_text)
    label_pages: dict[str, set[int]] = {}   # prefix -> 섹션 라벨이 찍힌 페이지들
    for u in units:
        for t in _texts(u):
            _scan(t)
            # 섹션 라벨 페이지 수집 — 기아처럼 '■기능요구사항(SFR…)'을 러닝헤더로
            # 매 페이지 반복하는 문서는 헤딩 트리(길이 제한)로 못 잡아도, "이 페이지는
            # 이 분류 섹션"이라는 구조 신호로 쓸 수 있다(_apply_canonical_tabs 최후 폴백).
            if u.page:
                for m in _R_HEADING.finditer(t):
                    p2 = m.group(2)
                    p2 = pfx_alias.get(p2, p2)
                    label_pages.setdefault(p2, set()).add(u.page)
        # 약어 없는 R-헤딩은 '헤딩 유래 탭'(tab==title 접두)에서만 — 본문 문장 오탐 방지
        tt = (u.title or "").strip()
        if tt and (u.tab or "").strip() == tt[:40].strip():
            m2 = _R_HEADING_NOPFX.match(re.sub(r"\s+", " ", tt))
            if m2:
                nm = re.sub(r"요구 사항", "요구사항", m2.group(1).strip())
                nopfx.setdefault(nm, None)
        for d in u.details:
            if isinstance(d, dict):
                h = _norm_hint(d.get("code_hint"))
                mm = _DOC_ID.match(h)
                if mm:
                    id_hint_ids.setdefault(mm.group(1), set()).add(h)
    # 실제 고유 ID(예: PMR-001, PMR-002) 개수로 판단 — 카드=1행 초과분해(위 body 분해)로
    # 같은 카드가 여러 detail-dict 로 나뉘어도 부풀려지지 않는다. 엄격한 ID 형식 매치 자체가
    # 이미 강한 신호라 문턱을 낮게(≥1) 둬도 안전 — 우연 오탐은 서로 다른 접두사 2개가 각각
    # 우연히 나타나야 하는데(전체 활성화 문턱 len(canon)>=2), 실측상 극히 드묾. 경기도 실측:
    # 이 문턱이 3이면 PSR/TER/SIR/QUR/PER/CUR(각 1~2장)이 전부 빠져 '일반사항'에 뭉침.
    for pfx, ids in id_hint_ids.items():
        if len(ids) >= 1:
            canon.setdefault(pfx, "")
    out = [{"name": (nm or pfx), "prefix": pfx,
            "ids": sorted(id_hint_ids.get(pfx, set())),
            "declared": declared_counts.get(pfx),
            "aliases": sorted(a for a, p in pfx_alias.items() if p == pfx),
            "label_pages": sorted(label_pages.get(pfx, set()))}
           for pfx, nm in canon.items()]
    known = {c["name"] for c in out}
    for nm in nopfx:
        # 접두사형과 같은 분류의 무접두 표기(예: '기능 요구사항' vs '기능 요구사항(SFR)') 중복 방지
        if not any(nm in k for k in known):
            out.append({"name": nm, "prefix": ""})
    # 접두사(ID체계) 신호가 하나도 없으면 '자체 분류 선언'으로 보지 않는다 —
    # 단순히 '…요구사항' 제목이 몇 개 있는 문서(금융 RFP 등)까지 canonical 강제하면
    # LLM 이 못 묶고 '일반사항'만 비대해짐. 총괄표/약어가 있는 공공형에서만 발동.
    return out if len(canon) >= 2 else []


def merge_pdf_label_pages(canon: list[dict], pdf_path) -> None:
    """원본 PDF 텍스트층에서 페이지별 섹션 라벨을 수집해 canon[*].label_pages 에 병합.

    opendataloader 는 매 페이지 반복되는 러닝헤더('■기능요구사항(SFR…)')를 페이지
    헤더로 판단해 변환 HTML 에서 제거한다(기아 실측: 본문 p17-66 라벨 전멸) — 그러면
    본문 요구표들이 섹션 우산을 잃고 일반사항에 뭉친다. 원본 PDF 에는 그대로 있으므로
    페이지 상단(선두 6줄)에서 분류명으로 시작하는 줄을 찾아 그 페이지를 라벨링한다."""
    try:
        import fitz
    except ImportError:
        return
    names: list[tuple[str, dict]] = []
    for c in canon:
        kor = re.sub(r"[\(（].*?[\)）]", "", c.get("name") or "")
        kor = re.sub(r"\s+", "", kor)
        if len(kor) >= 4:
            names.append((kor, c))
    if not names:
        return
    try:
        with fitz.open(str(pdf_path)) as doc:
            for i, pg in enumerate(doc, 1):
                # PPT 유래 PDF 는 라벨이 텍스트 스트림 후반부에 올 수 있어(기아 실측:
                # 표가 먼저, 슬라이드 타이틀이 나중) 전체 줄을 본다. 다만 본문 불릿
                # (•/-) 오탐을 막기 위해 박스형 글리프(■ 등)만 벗기고, 분류명으로
                # 시작하는 짧은 줄만 라벨로 인정한다.
                for ln in (pg.get_text() or "").split("\n"):
                    z = re.sub(r"\s+", "", ln).lstrip("■□◆◇▣")
                    if not z or len(z) > 120:
                        continue
                    for kor, c in names:
                        if z.startswith(kor):
                            lp = c.setdefault("label_pages", [])
                            if i not in lp:
                                lp.append(i)
                            break
    except Exception:  # noqa: BLE001 — 라벨 수집 실패는 배정 폴백만 잃을 뿐
        return
    for c in canon:
        if c.get("label_pages"):
            c["label_pages"] = sorted(c["label_pages"])


def _apply_canonical_tabs(rows: list[dict], canon: list[dict]) -> list[dict]:
    """룰 매핑(구조 신호): (1) 고유번호 code_hint 접두사(SFR-001), (2) 탭/계위/명의
    '(SFR' 헤딩 패턴, (3) 행 텍스트 안 고유번호 언급('PMR-003 …'), (4) 문서가 선언한
    분류명 별칭('성능 요구사항(PER)' → '성능요구사항') — 하나라도 canonical 분류와
    일치하면 그 분류명으로. 나머지는 LLM 배정에 맡긴다."""
    by_pfx = {c["prefix"]: c["name"] for c in canon if c["prefix"]}
    # 헤딩 접두사 별칭(INR→SIR): 본문 섹션 헤딩이 '(INR)'로 쓰여 있어도 같은 분류로
    for c in canon:
        for a in c.get("aliases") or []:
            by_pfx.setdefault(a, c["name"])
    # 분류명 별칭: 접두사 괄호를 뗀 한글명(공백 제거). 법제처 실측 — PER/SIR/COR 는
    # 개별 카드의 고유번호 셀이 텍스트층에 비어 있어(이미지/공란) hint 로 못 잡지만,
    # 섹션 헤딩('성능 요구사항')은 총괄표 선언명과 그대로 일치한다.
    alias: dict[str, str] = {}
    for c in canon:
        if not c["prefix"]:
            continue
        kor = re.sub(r"[\(（].*?[\)）]", "", c["name"] or "")
        kor = re.sub(r"\s+", "", kor)
        if len(kor) >= 4:
            alias[c["prefix"]] = kor
    canon_names = {c["name"] for c in canon}
    # 페이지 → 섹션 라벨 탭명 (무접두 분류 포함, 모호한 페이지 = 라벨 2개 이상 → 제외)
    page_name: dict[int, str] = {}
    _page_seen: dict[int, str] = {}
    for c in canon:
        for pg in c.get("label_pages") or []:
            if pg in _page_seen and _page_seen[pg] != c["name"]:
                page_name.pop(pg, None)
                continue
            _page_seen[pg] = c["name"]
            page_name[pg] = c["name"]
    for r in rows:
        hint = _norm_hint(r.get("code_hint"))
        mm = _DOC_ID.match(hint)
        if mm and mm.group(1) in by_pfx:
            r["tab"] = by_pfx[mm.group(1)]
            continue
        blob = " ".join([r.get("tab") or "", r.get("level") or "", r.get("name") or "",
                         r.get("_lp") or ""])
        hit = None
        for pfx in by_pfx:
            if re.search(rf"[\(（]\s*{pfx}\s*[,，)]|\b{pfx}\b\s*[,，)]", blob):
                hit = pfx
                break
        if hit is None:
            # 세로카드 감지가 놓친 카드도 텍스트에 고유번호가 남아있으면 그걸로 소속 결정
            # (법제처 실측: code_hint 잡힌 행 111 vs 정답 ID 151 — 갭은 텍스트 언급으로 복구)
            m3 = re.search(r"\b([A-Z]{2,4})(?:[-–][A-Z]{2,6})?[-–]\d{2,4}\b",
                           blob + " " + (r.get("detail") or "")[:120])
            if m3 and m3.group(1) in by_pfx:
                hit = m3.group(1)
        if hit is None:
            blob2 = re.sub(r"\s+", "", blob)
            for pfx, kor in alias.items():
                if kor and kor in blob2:
                    hit = pfx
                    break
        if hit is None:
            # 최후 폴백 — 섹션 라벨 페이지: 이 행의 페이지에 특정 분류의 섹션 라벨
            # ('■기능요구사항(SFR…)')이 찍혀 있으면 그 분류 소속(기아처럼 라벨을 매
            # 페이지 반복하는 러닝헤더 문서용, 무접두 분류 포함). 두 분류 라벨이
            # 겹치는 전환 페이지는 모호하므로 건드리지 않고, 이미 정본 탭에 배정된
            # 행(무접두 R-헤딩 유닛: '데이터 이관 요구사항' 등)도 덮어쓰지 않는다.
            pg = r.get("page")
            if (pg is not None and pg in page_name
                    and r.get("tab") not in canon_names):
                r["tab"] = page_name[pg]
                continue
        if hit:
            r["tab"] = by_pfx[hit]
    # 전-무ID 분류 순번 부여: 문서가 ID부여규칙(PER-OOO)과 건수를 총괄표에 선언했는데
    # 그 분류의 개별 카드 ID 가 텍스트층 어디에도 없으면(법제처 PER/SIR/COR 실측:
    # 고유번호 셀 공란), 선언 규칙대로 문서순 순번을 부여한다. 같은 카드(_unit)에서
    # 나뉜 행들은 같은 번호를 공유(분할행 동일-ID 원칙 — 재번호 금지).
    # 안전장치 2중: (1) 진짜 ID 가 하나라도 관측된 분류(SFR 등)는 절대 건드리지 않고,
    # (2) 유닛 수가 총괄표 선언 건수를 초과하면(과분절 신호) 번호를 아예 붙이지 않는다
    # — 선언에 없는 COR-015… 같은 가짜 ID 를 만드느니 탭 소속만 유지(충실전사 원칙).
    observed: set[str] = set()
    for r in rows:
        mm = _DOC_ID.match(_norm_hint(r.get("code_hint")))
        if mm:
            observed.add(mm.group(1))
    declared = {c["prefix"]: c.get("declared") for c in canon if c["prefix"]}
    for c in canon:                        # 별칭(INR) 제외, 정본 접두사만 순회
        pfx, name = c["prefix"], c["name"]
        if not pfx or pfx in observed:
            continue
        units_in_tab: list = []
        for r in rows:
            if r["tab"] == name and r.get("_unit") not in units_in_tab:
                units_in_tab.append(r.get("_unit"))
        want = declared.get(pfx)
        if not units_in_tab or (want and len(units_in_tab) > want):
            continue
        unit_no = {u: k + 1 for k, u in enumerate(units_in_tab)}
        for r in rows:
            if r["tab"] == name:
                r["code_hint"] = f"{pfx}-{unit_no[r.get('_unit')]:03d}"
    return _reassign_codes(rows)


class _SplitItem(BaseModel):
    index: int
    pieces: list[str]


class _SplitResult(BaseModel):
    items: list[_SplitItem]


def _llm_split_long_details(rows: list[dict], max_len: int = 300, cap: int = 120) -> list[dict]:
    """마커 없는 긴 상세(>max_len자)를 gemma 가 의미단위로 분해 — 충실전사 강제.

    줄/불릿 마커 분해(split_items)가 못 자르는 '개행 없는 인라인 뭉침'(하나 928자·JB
    703자 실측)의 마지막 폴백. **반환 조각이 (공백 무시) 원문의 연속 부분문자열이고
    조각 ≥2개, 원문 커버리지 ≥80% 일 때만 채택** — 하나라도 어기면 그 행은 원문 통째
    유지(LLM 재작성 원천 차단, recall 구조 보장). 배치 ≤5건/≤4000자, 문서당 상한 cap.
    VRULE_LLM_SPLIT=0 으로 비활성(결정적 측정용).
    """
    import os
    if os.environ.get("VRULE_LLM_SPLIT", "1") == "0":
        return rows
    # 고유번호 카드(문서 선언 단위)는 분해 대상에서 제외 — 카드 = 1행 원칙(정답지 동일).
    # 표이미지 마커가 있는 행도 제외 — gemma 의미분해가 마커를 "요구사항 아님"으로 보고
    # 조각에서 빠뜨려도 커버리지(80%) 체크를 통과할 수 있어(마커는 전체의 일부일 뿐)
    # 이미지 유실 위험이 있다. 이런 행은 split_items(줄 마커 기반)만으로 충분.
    targets = [i for i, r in enumerate(rows)
               if len(r["detail"]) > max_len
               and not _DOC_ID.match(_norm_hint(r.get("code_hint")))
               and not IMG_MARKER_RE.search(r["detail"])][:cap]
    if not targets:
        return rows

    from app.core.config import Settings
    from app.llm.base import Message
    from app.llm.factory import build_llm_client
    from app.llm.fake_client import FakeLlmClient
    from prototype.v2.async_run import run_coro

    client = build_llm_client(Settings())
    if isinstance(client, FakeLlmClient):
        return rows

    def _z(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    split_map: dict[int, list[str]] = {}
    batch: list[int] = []
    bchars = 0

    def flush() -> None:
        nonlocal batch, bchars
        if not batch:
            return
        listing = "\n\n".join(f"[{k}]\n{rows[k]['detail']}" for k in batch)
        prompt = (
            "다음은 RFP 조견표에서 한 행에 뭉쳐 들어간 긴 요구사항 텍스트들이다. 각각을 "
            "사람이 조견표 행으로 나눌 법한 **의미단위(개별 요구/항목)**로 분해하라.\n"
            "규칙(엄수):\n"
            "1) 각 조각은 원문에서 **연속된 구간을 글자 그대로 복사**한다 — 요약·수정·"
            "재배열·추가 금지(공백/줄바꿈 차이만 허용).\n"
            "2) 조각들을 이어붙이면 원문 대부분(80% 이상)이 복원되어야 한다(중요 내용 누락 금지).\n"
            "3) 의미 있게 나눌 수 없으면 그 index 는 조각 1개(원문 전체)로 반환.\n\n"
            f"[텍스트들]\n{listing}\n\n"
            'JSON: {"items":[{"index":<int>,"pieces":["<원문 그대로 조각1>","<조각2>",...]}]} — 모든 index 포함.'
        )
        try:
            res = run_coro(client.structured_output(
                [Message(role="user", content=prompt)], _SplitResult,
                purpose="detail_split", max_tokens=8000))
            for it in res.items:
                if it.index not in batch:
                    continue
                orig = rows[it.index]["detail"]
                zorig = _z(orig)
                pieces = [p.strip() for p in (it.pieces or []) if p and p.strip()]
                if len(pieces) < 2:
                    continue
                zp = [_z(p) for p in pieces]
                if not all(z and z in zorig for z in zp):
                    continue                          # 재작성/환각 조각 → 통째 유지
                if sum(len(z) for z in zp) < 0.8 * len(zorig):
                    continue                          # 내용 누락 → 통째 유지
                split_map[it.index] = pieces
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "gemma 상세 분해 호출 실패(배치 %s) — 해당 행들 원문 유지", batch, exc_info=True)
        batch = []
        bchars = 0

    for k in targets:
        if len(batch) >= 5 or bchars > 4000:
            flush()
        batch.append(k)
        bchars += len(rows[k]["detail"])
    flush()

    if not split_map:
        return rows
    out: list[dict] = []
    for i, r in enumerate(rows):
        if i in split_map:
            out.extend({**r, "detail": p} for p in split_map[i])
        else:
            out.append(r)
    return _reassign_codes(out)


    # 예전엔 여기서 룰로 못 정한 행을 gemma 가 "내용이 비슷해 보여서" 정본 탭(SFR 등)에
    # 배정했으나(_classify_rows_llm, 삭제됨), 이는 문서 총괄표에 없는 가짜 ID("SFR-048"
    # 등)를 만들어 정본 탭이 실제 ID 목록과 어긋나는 문제가 있었다 — 이제
    # rows_from_units 에서 룰 매칭 실패 시 바로 '일반사항'으로 보낸다(추측 배정 금지).


class _TabGroup(BaseModel):
    new_tab: str
    old_tabs: list[str]


class _TabGroupResult(BaseModel):
    groups: list[_TabGroup]


def _consolidate_tabs_llm(rows: list[dict], target_max: int = 20,
                          canonical: list[dict] | None = None) -> list[dict]:
    """구조 규칙만으론 못 잡는 과분할(예: SFR 하위 20+개 서비스가 각자 탭)을 gemma 가
    사람이 쓸 법한 상위 카테고리로 묶는다. **탭 이름과 그에 따른 code 재부여만 하고,
    detail/name/level(요구사항 본문)은 절대 건드리지 않는다** — 원문 충실 전사 원칙 유지.

    canonical 이 있으면(문서가 총괄표/R-헤딩으로 분류를 스스로 선언) 새 이름 발명을
    금지하고 **그 분류 + '일반사항'** 으로만 배정하게 강제한다 — 사람 정답(법제처 등)이
    총괄표 분류 그대로 시트를 만드는 원칙과 동일. 실패/미매핑 탭은 원래 이름 유지."""
    from collections import Counter, OrderedDict

    canon_names = [c["name"] for c in (canonical or [])]
    order = list(OrderedDict.fromkeys(r["tab"] for r in rows))
    # canonical 있으면 비-canonical 탭이 남아있는 한 항상 배정 시도, 없으면 기존 상한 기준.
    pending = [t for t in order if t not in canon_names]
    if (not canon_names and len(order) <= target_max) or (canon_names and not pending):
        return rows

    from app.core.config import Settings
    from app.llm.base import Message
    from app.llm.factory import build_llm_client
    from app.llm.fake_client import FakeLlmClient
    from prototype.v2.async_run import run_coro

    client = build_llm_client(Settings())
    if isinstance(client, FakeLlmClient):
        return rows

    counts = Counter(r["tab"] for r in rows)
    mapping: dict[str, str] = {}

    def _call(prompt: str) -> None:
        try:
            res = run_coro(client.structured_output(
                [Message(role="user", content=prompt)], _TabGroupResult,
                purpose="tab_consolidate", max_tokens=6000))
            for g in res.groups:
                for old in g.old_tabs:
                    mapping[old.strip()] = g.new_tab
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "gemma 탭 통합 호출 실패(일부 배치) — 해당 탭 원본 유지", exc_info=True)

    if canon_names:
        allowed = "\n".join(f"* {n}" for n in canon_names)
        # 탭이 많으면(문장형 탭 등) 한 번에 다 못 다룸 — 25개씩 배치.
        for s in range(0, len(pending), 25):
            chunk = pending[s:s + 25]
            listing = "\n".join(f"- '{t}' ({counts[t]}행)" for t in chunk)
            _call(
                "이 RFP 문서는 요구사항 분류 체계를 **문서 스스로 선언**했다(요구사항 총괄표/"
                "섹션 헤딩). 조견표 탭은 이 분류를 그대로 따라야 한다(사람이 만드는 방식).\n\n"
                f"[문서 선언 분류(이것만 사용)]\n{allowed}\n* 일반사항\n\n"
                "아래 각 탭을 위 분류 중 정확히 하나로 배정하라. 요구사항이 아닌 내용(사업개요·"
                "입찰/계약 안내·행정·평가기준 등)은 '일반사항'으로. **새 분류 이름을 만들지 말고 "
                "위 목록의 표기를 그대로 써라.**\n\n"
                f"[배정할 탭 목록]\n{listing}\n\n"
                'JSON: {"groups":[{"new_tab":"<위 분류 중 하나>","old_tabs":["<원본 탭1>",...]}]} — 모든 탭 포함.'
            )
    else:
        listing = "\n".join(f"- '{t}' ({counts[t]}행)" for t in order)
        _call(
            "아래는 한 RFP 문서에서 자동 추출된 탭(섹션) 목록이다(문서 등장 순서, 괄호는 행수).\n"
            "탭이 지나치게 잘게 쪼개져 있다 — 사람이 조견표를 만들 때 쓸 법한 상위 카테고리로 "
            "묶어라. 예를 들어 'SIP','CTI','챗봇','CS Plaza','옴니채널상담' 처럼 하나의 상위 "
            "기능요구사항 목록 아래 나열되는 개별 서비스/컴포넌트들은 그 상위 카테고리 이름으로 "
            "하나의 탭에 묶는다. 이미 명확히 독립적인 대분류(예: 보안요구사항, 데이터요구사항, "
            "품질/하자보수 요구사항 등)는 그대로 유지해도 된다.\n"
            "목표 탭 개수는 문서 규모에 맞게 대략 10~20개 내외(사람이 실제로 조견표 시트를 "
            "나눌 법한 개수). 모든 원본 탭을 정확히 하나의 새 그룹에 배정해야 한다(누락 금지).\n\n"
            f"[탭 목록]\n{listing}\n\n"
            'JSON: {"groups":[{"new_tab":"<새 탭 이름>","old_tabs":["<원본 탭1>","<원본 탭2>",...]}]}'
        )
    if not mapping:
        return rows

    changed = False
    if canon_names:
        # 표기 변형(공백/괄호) 흡수 — 정규화 키로 canonical 정본 표기에 스냅.
        canon_by_key = {_norm_tab_key(n): n for n in canon_names}
        canon_by_key[_norm_tab_key("일반사항")] = "일반사항"
        for r in rows:
            nt = (mapping.get(r["tab"]) or "").strip()
            snap = canon_by_key.get(_norm_tab_key(nt)) if nt else None
            if snap and snap != r["tab"]:
                r["tab"] = snap
                changed = True
    else:
        for r in rows:
            nt = (mapping.get(r["tab"]) or "").strip()
            if nt and nt != r["tab"]:
                r["tab"] = nt[:40]
                changed = True
    if not changed:
        return rows
    return _reassign_codes(rows)


def rows_from_units(units: list[Unit], keep: dict[int, bool],
                    canonical: list[dict] | None = None) -> list[dict]:
    """유닛 + keep 판정 → 고정칼럼 행. junk 셀(페이지번호·날짜·기호) 제외. (공개)"""
    rows: list[dict] = []
    tab_counter: dict[str, int] = {}
    tab_prefix: dict[str, str] = {}
    _slug = _slug_tab

    for i, u in enumerate(units):
        if not keep.get(i, True):
            continue
        # 상세는 str 또는 dict{level,name,detail}. dict 면 표에서 뽑은 행별 계위/명을 쓴다.
        items = u.details or [u.title]
        cleaned: list[dict] = []
        for d in items:
            # 줄바꿈 보존(고유번호 카드=1행에 불릿들이 줄로 담김 — 정답지 형식과 동일).
            # 줄 내 공백만 정리.
            raw = _detail_text(d)
            txt = "\n".join(re.sub(r"[ \t]+", " ", ln).strip()
                            for ln in raw.split("\n") if ln.strip()).strip()
            if not txt or _is_junk_detail(re.sub(r"\s+", " ", txt)):
                continue
            if isinstance(d, dict):
                nm = _clean_toc(re.sub(r"\s+", " ", d.get("name") or u.title or ""))
                lv = _clean_toc(re.sub(r"\s+", " ", d.get("level") or u.level_path or ""))
            else:
                nm = _clean_toc(u.title)
                lv = _clean_toc(u.level_path)
            # 요구사항명 품질 가드 — 명은 사람 정답 기준 짧은 라벨(수십자)이지 본문이 아니다.
            # 상세 전문이 명에 복제되거나(법제처 실측) 과도하게 길면, 짧은 카드 제목으로
            # 대체하고 그것도 길면 비워둔다(사람이 채움; 엉뚱한 긴 텍스트보다 낫다).
            def _z24(s):
                return re.sub(r"\W", "", s or "")[:24]
            if nm and (len(nm) > 60 or (_z24(nm) and _z24(nm) == _z24(txt))):
                alt = _clean_toc((u.title or "").strip())
                nm = alt if (0 < len(alt) <= 60 and _z24(alt) != _z24(txt)) else ""
            cleaned.append({"detail": txt, "name": nm, "level": lv,
                            "code_hint": (d.get("code_hint") or "").strip() if isinstance(d, dict) else ""})
        if not cleaned:               # 실내용 없는 카드(표지·페이지번호뿐)는 올리지 않음
            continue
        if u.tab not in tab_prefix:
            tab_prefix[u.tab] = _slug(u.tab)
        pfx = tab_prefix[u.tab]
        tab = _clean_toc(u.tab) or "요구사항"
        for it in cleaned:
            hint = _norm_hint(it["code_hint"])
            if _DOC_ID.match(hint):
                code = hint                # 문서 부여 고유번호 우선(원문 맵핑성)
            else:
                tab_counter[pfx] = tab_counter.get(pfx, 0) + 1
                code = f"{pfx}-{tab_counter[pfx]:03d}"
            rows.append({"tab": tab, "code": code,
                         "name": it["name"], "level": it["level"], "detail": it["detail"],
                         "code_hint": hint, "page": u.page, "table_index": u.table_index,
                         "_unit": i,    # 같은 카드에서 나뉜 행 식별(분할행 동일-ID 부여용)
                         # 섹션 경로 — canonical 탭 매칭용. 표에서 뽑힌 행은 level 이
                         # 구분열 값으로 대체돼 조상 섹션('기능 요구사항(SFR)')이 사라지고,
                         # 그러면 SFR 섹션 전체가 증거 없음으로 일반사항에 뭉친다(기아 실측).
                         "_lp": u.level_path or ""})
    rows = _consolidate_small_tabs(_llm_split_long_details(rows))
    canon = canonical if canonical is not None else []
    if canon:
        # 문서 자체 선언 분류 모드: SFR/DAR 같은 정본 탭은 문서가 실제로 그 ID를 부여했다는
        # 구조적 증거(고유번호 code_hint·"(SFR" 헤딩 패턴·본문 내 ID 언급)가 있을 때만 배정
        # 한다(_apply_canonical_tabs). 증거 없는 행을 gemma가 "내용이 비슷해 보여서" SFR로
        # 추측 배정하면, 총괄표에 없는 가짜 "SFR-048" 같은 코드가 생겨 정본 탭이 실제
        # ID 목록과 안 맞게 된다("SFR-001 처럼 진짜 ID만 들어가야" 피드백) — 증거 없으면
        # 일반사항으로 보수적으로 내린다(추측 금지, 재번호는 _reassign_codes 가 처리).
        rows = _apply_canonical_tabs(rows, canon)
        canon_set = {c["name"] for c in canon}
        for r in rows:
            if r["tab"] not in canon_set:
                r["tab"] = "일반사항"
        return _reassign_codes(rows)
    return _consolidate_tabs_llm(rows, canonical=canon)


def extract_fixed_rows(html: str, doc_name: str) -> list[dict]:
    """HTML → 고정칼럼 행 dict 리스트: {tab, code, name, level, detail}. gemma keep 적용."""
    units = build_units(html)
    keep = _judge_keep(units)
    return rows_from_units(
        units, keep,
        canonical=detect_canonical_categories(units, raw_text=re.sub(r"<[^>]+>", " ", html)))


def _extract_fixed_rows_legacy(html: str, doc_name: str) -> list[dict]:
    units = build_units(html)
    keep = _judge_keep(units)
    rows: list[dict] = []
    tab_counter: dict[str, int] = {}
    tab_prefix: dict[str, str] = {}

    def _slug(t: str) -> str:
        # 깔끔한 접두사: 대괄호 내용·번호·마커·목차점선 제거 → 한글/영문만, 짧게.
        t = re.sub(r"[\[\(（【][^\])）】]*[\])）】]", "", t or "")   # [..](..) 제거
        toks = re.findall(r"[A-Za-z가-힣]+", t)                     # 숫자 제외(제안서'2'→제안서)
        return ("".join(toks)[:8]) or "REQ"

    for i, u in enumerate(units):
        if not keep.get(i, True):
            continue
        if u.tab not in tab_prefix:
            tab_prefix[u.tab] = _slug(u.tab)
        pfx = tab_prefix[u.tab]
        details = u.details or [u.title]
        for d in details:
            d = re.sub(r"\s+", " ", d).strip()
            if not d:
                continue
            tab_counter[pfx] = tab_counter.get(pfx, 0) + 1
            rows.append({
                "tab": u.tab, "code": f"{pfx}-{tab_counter[pfx]:03d}",
                "name": u.title, "level": u.level_path, "detail": d,
            })
    return rows

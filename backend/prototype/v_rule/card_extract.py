"""카드 유닛 → (gemma 요구사항 판단) → 고정칼럼 행(요구사항ID/명/계위/상세).

흐름(사용자 재설계): HTML → 문서순 블록 → 章(상위)=탭 / 카드(가·나 등)=요구사항명 유닛,
그 아래 본문·표를 상세내용(atomic)으로. gemma 는 **카드가 요구사항인지 판단(keep)** 만 —
내용 생성·키워드 룰 없음. 탭별 ID 접두사(같은 탭=같은 접두사).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from .cards import iter_blocks, marker_level, strip_marker
from .table_extract import split_items, table_to_reqs


# 헤딩 오인 방지: 마커가 붙어도 '긴 완결 문장'은 헤딩이 아니라 내용(상세)이다.
# 예: '다. 제출한 제안서와 발표내용은 동일해야 하며…' → 헤딩 아님(스캔 catch-all 방지)
_SENT_END = re.compile(r"(?:한다|하다|해야|하며|되며|바람|함|음|됨|된다|같다|없다|있다|한다\.|것|임)\s*[.]?\s*$")


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
    units: list[Unit] = []
    cur: Unit | None = None
    # 가로 요구표에서 뽑은 카테고리(구분열) 탭 — 문서 전체에 걸쳐 정규화 키로 병합.
    # (표 블록마다 새로 만들면 같은 카테고리가 다른 위치의 표에 다시 나올 때 별도 탭이 됨)
    tab_units: dict[str, Unit] = {}

    def ensure_cur() -> Unit:
        nonlocal cur
        if cur is None:
            cur = Unit(tab="요구사항", marker="", title="", level_path="")
            units.append(cur)
        return cur

    def attach_isolated(items: list, use_item_tab: bool = True) -> None:
        """ambient(cur) 탭/계위를 공유하되 항목마다 독립 유닛(keep 판정 격리).
        표·다항목 셀은 본질적으로 여러 요구사항이라, 한 유닛에 몰아넣으면 그중
        하나가 junk 라 판정될 때 옆의 진짜 요구사항까지 통째로 drop된다(대한항공 recall 원인).

        use_item_tab: 표에서 뽑힌 항목이 자기 카테고리(_tab)를 갖고 있을 때 그걸 탭으로 쓸지.
        큰 표(행 많음, 각 행=서로 다른 컴포넌트 — SIP/CTI/챗봇 등)는 True 로 각자 탭을 줘야
        기아 43행이 '상담석 규모' 같은 무관 ambient 헤딩에 오분류되지 않는다. 반대로 작은
        표(≤8행, 평균응답시간/평균처리시간/동시처리 같은 한 화제의 하위항목)까지 항목마다
        탭을 쪼개면 사람이 안 만들 만큼 탭이 폭발한다(기아 161탭) — 이런 경우는 False 로
        ambient 탭 하나에 묶되, keep 판정 격리는 그대로 유지."""
        base_tab = cur.tab if cur else "요구사항"
        base_title = cur.title if cur else ""
        base_level = cur.level_path if cur else ""
        for it in items:
            item_tab = it.get("_tab") if (use_item_tab and isinstance(it, dict)) else None
            tab = item_tab[:40] if item_tab else base_tab
            units.append(Unit(tab=tab, marker="", title=base_title, level_path=base_level, details=[it]))

    def open_heading(text: str, lvl: int) -> None:
        nonlocal cur, stack
        title = strip_marker(text)[:60]
        stack = [(l, t) for (l, t) in stack if l < lvl] + [(lvl, title)]
        tab = (title or (stack[0][1] if stack else ""))[:40] or "요구사항"
        mk = text.split()[0] if text.split() else ""
        cur = Unit(tab=tab, marker=mk, title=title, level_path=" > ".join(t for _, t in stack))
        units.append(cur)

    n = len(blocks)
    for i, b in enumerate(blocks):
        if b.kind == "table":
            reqs = table_to_reqs(b.grid)
            if reqs is None:
                # 요구표 판정 불가(1열 요구표 등) → 행마다 독립 유닛(표는 본질적으로 다중
                # 요구사항 나열이라 한 유닛에 몰아넣으면 keep 판정이 콜래터럴 드롭을 낸다).
                pieces = [p for line in _table_details(b.grid) for p in split_items(line)]
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
                    # 정규화 키로 문서 전체에서 병합 — 같은 카테고리가 다른 표에 다시 나와도 한 탭 유지.
                    for r in reqs:
                        t = (r.get("_tab") or (cur.tab if cur else "요구사항"))[:40] or "요구사항"
                        key = _norm_tab_key(t)
                        tu = tab_units.get(key)
                        if tu is None:
                            tu = Unit(tab=t, marker="", title=t,
                                      level_path=(cur.level_path + " > " + t if cur and cur.level_path else t))
                            units.append(tu)
                            tab_units[key] = tu
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
                attach_isolated(pieces)              # 다항목 한 블록 → 항목별 독립 유닛(keep 격리)
            elif lvl is not None and lvl >= 4:
                # 레벨4/5 마커(가)/나)/1)/2)/❍/- 등)는 나열형 리스트의 개별 항목이다.
                # 마커 없는 연속 평문과 달리 방치하면 여러 항목이 한 유닛에 쌓여, 그중
                # 하나가 junk 판정될 때 옆의 진짜 요구사항까지 통째로 drop된다(신한은행/경기도 실측).
                attach_isolated([base])
            else:
                ensure_cur().details.append(base)
    return [u for u in units if u.details]   # 내용 있는 유닛만(빈 章 헤딩 제외)


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
    tab_counter: dict[str, int] = {}
    tab_prefix: dict[str, str] = {}
    out = []
    for r in rows:
        tab = r["tab"]
        if tab not in tab_prefix:
            tab_prefix[tab] = _slug_tab(tab)
        pfx = tab_prefix[tab]
        tab_counter[pfx] = tab_counter.get(pfx, 0) + 1
        out.append({**r, "code": f"{pfx}-{tab_counter[pfx]:03d}"})
    return out


def rows_from_units(units: list[Unit], keep: dict[int, bool]) -> list[dict]:
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
            txt = re.sub(r"\s+", " ", _detail_text(d)).strip()
            if not txt or _is_junk_detail(txt):
                continue
            if isinstance(d, dict):
                cleaned.append({"detail": txt,
                                "name": _clean_toc(d.get("name") or u.title),
                                "level": _clean_toc(d.get("level") or u.level_path)})
            else:
                cleaned.append({"detail": txt, "name": _clean_toc(u.title),
                                "level": _clean_toc(u.level_path)})
        if not cleaned:               # 실내용 없는 카드(표지·페이지번호뿐)는 올리지 않음
            continue
        if u.tab not in tab_prefix:
            tab_prefix[u.tab] = _slug(u.tab)
        pfx = tab_prefix[u.tab]
        tab = _clean_toc(u.tab) or "요구사항"
        for it in cleaned:
            tab_counter[pfx] = tab_counter.get(pfx, 0) + 1
            rows.append({"tab": tab, "code": f"{pfx}-{tab_counter[pfx]:03d}",
                         "name": it["name"], "level": it["level"], "detail": it["detail"]})
    return _consolidate_small_tabs(rows)


def extract_fixed_rows(html: str, doc_name: str) -> list[dict]:
    """HTML → 고정칼럼 행 dict 리스트: {tab, code, name, level, detail}. gemma keep 적용."""
    units = build_units(html)
    keep = _judge_keep(units)
    return rows_from_units(units, keep)


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

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GroupedVirtuoso } from "react-virtuoso";
import {
  RequirementView,
  DocumentMeta,
  listRequirements,
  editRequirement,
  deleteRequirement,
  deleteRequirementsBatch,
  mergeRequirements,
  splitRequirement,
  regroupRequirements,
  exportFixedUrl,
  eventStreamUrl,
  ensurePipeline,
  fetchDocumentMeta,
  startAiReview,
} from "@/lib/api";
import { PdfViewerPane } from "@/components/PdfViewerPane";

type ColKey = "code" | "name" | "level" | "source" | "ai" | "action";
const DEFAULT_W: Record<ColKey, number> = { code: 120, name: 150, level: 130, source: 60, ai: 40, action: 236 };
type Card = { category: string; prefix: string; name: string; reqIds: string[]; count: number };
const AI_BADGE: Record<string, { bg: string; fg: string }> = {
  O: { bg: "#e6f4ea", fg: "#1e7e34" },
  X: { bg: "#fdecea", fg: "#c8102e" },
  "△": { bg: "#fff8e1", fg: "#9a7300" },
};

export default function EditTableClient({ docId }: { docId: string }) {
  const [rows, setRows] = useState<RequirementView[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const [w, setW] = useState<Record<ColKey, number>>(DEFAULT_W);
  const [pfxEdit, setPfxEdit] = useState<Record<string, string>>({});
  const [delim, setDelim] = useState("●");   // 분해 기준 기호(기본값)
  const [rowDelim, setRowDelim] = useState<Record<string, string>>({});  // #2 행(셀)별 분해기호 오버라이드
  const [hoverCard, setHoverCard] = useState<number | null>(null);       // #3 카드헤더 호버시 삭제 X

  // 출처(페이지) 클릭 → 우측 PDF/원문 뷰어로 점프. 뷰어는 표 레이아웃을 잠식하지 않는
  // 오버레이 드로어(사이드바)라 기본은 닫힘 — "출처" 클릭 시에만 슬라이드로 열린다.
  const [docMeta, setDocMeta] = useState<DocumentMeta | null>(null);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerPage, setViewerPage] = useState<number | null>(null);
  const [viewerTableIdx, setViewerTableIdx] = useState<number | null>(null);
  const [viewerAnchor, setViewerAnchor] = useState<string | null>(null);
  const [jumpNonce, setJumpNonce] = useState(0);
  useEffect(() => { void fetchDocumentMeta(docId).then(setDocMeta).catch(() => {}); }, [docId]);

  // "AI 검토 시작" — 사람이 카드 정리를 마친 뒤 수동으로 누르는 버튼(자동 실행 아님).
  const [aiReviewing, setAiReviewing] = useState(false);
  const [aiProgress, setAiProgress] = useState<{ done: number; total: number } | null>(null);
  async function onStartAiReview() {
    setAiReviewing(true);
    setAiProgress(null);
    try { await startAiReview(docId, true); flash("AI 검토 시작됨"); }
    catch (e) { setMsg(`AI 검토 시작 실패: ${String(e)}`); setAiReviewing(false); }
  }
  function jumpTo(r: RequirementView["requirement"]) {
    const p = r.source_page != null ? Number(r.source_page) : null;
    setViewerPage(p && !Number.isNaN(p) ? p : null);
    // 음수(-1 등)는 "위치 없음" 센티널로 쓰인 이력이 있어 방어적으로 걸러낸다 —
    // 실제 배열 인덱스처럼 보이지만 JS는 음수 인덱스를 wrap하지 않아 조용히 실패한다.
    const ti = r.source_table_index;
    setViewerTableIdx(ti != null && ti >= 0 ? ti : null);
    setViewerAnchor(r.detail || r.name || null);
    setViewerOpen(true);
    setJumpNonce((n) => n + 1);
  }
  const hasSourceLoc = (r: RequirementView["requirement"]) =>
    r.source_page != null || (r.source_table_index != null && r.source_table_index >= 0);
  const editorId = useMemo(
    () => (typeof window === "undefined" ? "" : (localStorage.getItem("rfp-editor") ||
      (() => { const id = "u" + Math.random().toString(36).slice(2, 8); localStorage.setItem("rfp-editor", id); return id; })())),
    [],
  );

  const load = useCallback(async () => {
    try { setRows(await listRequirements(docId)); }
    catch (e) { setMsg(`불러오기 실패: ${String(e)}`); }
    finally { setLoading(false); }
  }, [docId]);

  // 마운트 시 첫 로드. 요건이 "이미 있으면" 절대 파이프라인을 건드리지 않는다(재추출로
  // 기존 조견표가 초기화되는 것 방지 — ensure-pipeline 은 캐시 무효+요건존재 시 reset 함).
  // 요건이 0건일 때만(아직 추출 안 된 문서) 파이프라인을 트리거한다.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const initial = await listRequirements(docId);
        if (!alive) return;
        setRows(initial);
        if (initial.length === 0) void ensurePipeline(docId).catch(() => { /* 이미 실행중이면 무시 */ });
      } catch (e) { if (alive) setMsg(`불러오기 실패: ${String(e)}`); }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
  }, [docId]);

  // 추출 진행 중(아직 요건 0건)이면 완료를 잡기 위해 폴링 — 요건이 생기면 자동 중단.
  useEffect(() => {
    if (rows.length > 0) return;
    let tries = 0;
    const t = setInterval(() => {
      tries += 1;
      if (tries > 60) { clearInterval(t); return; }   // ~3분 상한
      void load();
    }, 3000);
    return () => clearInterval(t);
  }, [rows.length, load]);

  useEffect(() => {
    const src = new EventSource(eventStreamUrl(docId));
    src.addEventListener("REQUIREMENT_EDITED", (e: MessageEvent) => {
      try {
        const p = JSON.parse(e.data);
        if (p.editor_id === editorId) return;
        setRows((prev) => prev.map((v) => v.requirement.id === p.requirement_id
          ? { ...v, requirement: { ...v.requirement, ...p.fields } } : v));
      } catch { /* ignore */ }
    });
    src.addEventListener("REQUIREMENTS_CHANGED", () => { void load(); });
    // AI 검토 진행 — RECOMMENDING 은 진행률만 갱신(행 재조회 없음, 잦은 이벤트라 무겁다),
    // RECOMMENDED 에서 최종 1회 재조회해 각 행의 ai_risk(O/X/△) 를 반영.
    src.addEventListener("RECOMMENDING", (e: MessageEvent) => {
      try {
        const p = JSON.parse(e.data);
        setAiReviewing(true);
        if (typeof p.payload?.done === "number" && typeof p.payload?.total === "number") {
          setAiProgress({ done: p.payload.done, total: p.payload.total });
        }
      } catch { /* ignore */ }
    });
    src.addEventListener("RECOMMENDED", () => {
      setAiReviewing(false);
      setAiProgress(null);
      void load();
    });
    return () => src.close();
  }, [docId, editorId, load]);

  // 탭(카테고리) 단위로 모아 보여준다 — Excel(export-fixed 의 _fixed_excel)과 동일한
  // allCards = 탭바용(항상 전체 탭 목록+총 행수), 문서순 rows 를 훑되 "연속 구간"이 아니라
  // "카테고리별로 몇 개씩 있나"만 본다(탭이 문서 여러 곳에 흩어져 있어도 여기선 상관없음).
  const allCards = useMemo<Card[]>(() => {
    const byCat = new Map<string, Card>();
    const order: string[] = [];
    for (const v of rows) {
      const cat = v.requirement.category || "요구사항";
      let c = byCat.get(cat);
      if (!c) {
        const pfx = (v.requirement.code || "").replace(/-\d+\s*$/, "");
        c = { category: cat, prefix: pfx, name: v.requirement.name || "", reqIds: [], count: 0 };
        byCat.set(cat, c);
        order.push(cat);
      }
      c.reqIds.push(v.requirement.id);
      c.count++;
    }
    return order.map((cat) => byCat.get(cat)!);
  }, [rows]);

  const [activeTab, setActiveTab] = useState<string | null>(null);   // null = "전체" 의사-탭
  // activeTab 이 사라진 탭을 가리키면(행 편집으로 카테고리가 바뀌는 등) 전체보기로 복귀.
  useEffect(() => {
    if (activeTab != null && !allCards.some((c) => c.category === activeTab)) setActiveTab(null);
  }, [activeTab, allCards]);

  // "전체" 는 rows 를 그대로(문서/페이지 순서 보존) — 탭을 고르면 그 카테고리만 필터(순서는
  // 그대로 유지되므로 원문과의 페이지 대조가 깨지지 않는다). 예전엔 "전체"도 탭별로 미리
  // 재정렬(버킷팅)해서 보여줬는데, 그러면 "전체" 볼 때 서로 다른 탭이 번갈아 나오면서
  // 페이지 번호가 문서 순서와 무관하게 뒤죽박죽으로 보였다(실측 피드백) — 버킷팅은
  // 탭바의 총 개수 집계에만 쓰고, 실제로 그리는 목록은 항상 원래 순서를 지킨다.
  const sortedRows = useMemo(
    () => (activeTab == null ? rows
      : rows.filter((v) => (v.requirement.category || "요구사항") === activeTab)),
    [rows, activeTab],
  );
  // cards = sortedRows 안에서 카테고리가 "연속"으로 이어지는 구간(GroupedVirtuoso 용).
  // "전체"에서는 같은 탭이 문서 여러 곳에 흩어지면 카드가 여러 번 나올 수 있다(정상 —
  // 페이지 순서를 지키려면 어쩔 수 없다). 탭 하나만 볼 때는 필터링된 결과라 보통 카드 1개.
  const cards = useMemo<Card[]>(() => {
    const list: Card[] = [];
    let cur: Card | null = null;
    for (const v of sortedRows) {
      const cat = v.requirement.category || "요구사항";
      const pfx = (v.requirement.code || "").replace(/-\d+\s*$/, "");
      if (!cur || cur.category !== cat) {
        cur = { category: cat, prefix: pfx, name: v.requirement.name || "", reqIds: [], count: 0 };
        list.push(cur);
      }
      cur.reqIds.push(v.requirement.id);
      cur.count++;
    }
    return list;
  }, [sortedRows]);

  const groupCounts = useMemo(() => cards.map((c) => c.count), [cards]);
  const distinctTabCount = allCards.length;

  async function saveCell(reqId: string, field: "code" | "name" | "definition" | "detail", value: string) {
    // #4 요구사항명 편집은 카드 전체에 전파. 카테고리 이름이 아니라 reqId 소속으로 카드를
    // 찾는다 — 같은 이름의 카드가 문서 안에 여러 번(비연속) 있을 수 있어(페이지순 보존),
    // 이름으로 찾으면 엉뚱한(먼저 나온) 카드가 잡혀 실제 편집한 카드엔 반영이 안 될 수 있다.
    if (field === "name") {
      const card = cards.find((c) => c.reqIds.includes(reqId));
      setRows((prev) => prev.map((v) => (card?.reqIds.includes(v.requirement.id) ? { ...v, requirement: { ...v.requirement, name: value } } : v)));
      try { if (card) setRows(await regroupRequirements(docId, card.reqIds, { name: value })); }
      catch (e) { setMsg(`이름 전파 실패: ${String(e)}`); }
      return;
    }
    setRows((prev) => prev.map((v) => v.requirement.id === reqId
      ? { ...v, requirement: { ...v.requirement, [field]: value } } : v));
    try { await editRequirement(reqId, { [field]: value }, editorId); }
    catch (e) { setMsg(`편집 실패: ${String(e)}`); }
  }
  async function onDelete(reqId: string) {
    try { setRows(await deleteRequirement(docId, reqId)); flash("삭제됨"); }
    catch (e) { setMsg(`삭제 실패: ${String(e)}`); }
  }
  async function onMergeUp(index: number) {
    if (index <= 0) return;
    const cur = sortedRows[index], prev = sortedRows[index - 1];
    if (cur.requirement.category !== prev.requirement.category) { setMsg("같은 카드 안에서만 행 병합"); return; }
    try { setRows(await mergeRequirements(docId, prev.requirement.id, cur.requirement.id)); flash("행 병합됨"); }
    catch (e) { setMsg(`병합 실패: ${String(e)}`); }
  }
  async function onSplit(reqId: string) {
    const d = (rowDelim[reqId] ?? delim).trim();   // #2 행별 기호 우선, 없으면 기본값
    if (!d) { setMsg("분해 기호를 입력하세요"); return; }
    try { setRows(await splitRequirement(docId, reqId, d)); flash(`'${d}'로 분해됨`); }
    catch (e) { setMsg(`분해 실패: ${String(e)}`); }
  }
  async function onDeleteCard(card: Card) {   // #3 카드(탭) 통째 삭제
    try { setRows(await deleteRequirementsBatch(docId, card.reqIds)); flash(`카드 삭제됨 (${card.count}행)`); }
    catch (e) { setMsg(`카드 삭제 실패: ${String(e)}`); }
  }
  async function onSplitHere(index: number) {   // 여기서부터 새 카드로 분리
    const cur = sortedRows[index];
    const cat = cur.requirement.category || "요구사항";
    const tail = sortedRows.filter((v, i) => i >= index && v.requirement.category === cat).map((v) => v.requirement.id);
    if (tail.length === 0) return;
    const newCat = `${cat} · ${cur.requirement.code}~`;   // 결정적 고유 카드명
    const newPfx = ((cur.requirement.code || "REQ").replace(/-\d+$/, "")) + "b";
    try { setRows(await regroupRequirements(docId, tail, { category: newCat, prefix: newPfx })); flash("이 지점에서 카드 분해됨"); }
    catch (e) { setMsg(`분리 실패: ${String(e)}`); }
  }
  async function applyPrefix(card: Card) {
    const p = (pfxEdit[card.category] ?? card.prefix).trim();
    if (!p) return;
    try { setRows(await regroupRequirements(docId, card.reqIds, { prefix: p })); flash(`ID 일괄적용 ${p}-001…`); }
    catch (e) { setMsg(`ID 적용 실패: ${String(e)}`); }
  }
  async function mergeIntoPrev(gi: number) {
    if (gi <= 0) return;
    const prev = cards[gi - 1], cur = cards[gi];
    try { setRows(await regroupRequirements(docId, [...prev.reqIds, ...cur.reqIds], { category: prev.category, prefix: prev.prefix })); flash(`카드 병합 → ${prev.category}`); }
    catch (e) { setMsg(`카드 병합 실패: ${String(e)}`); }
  }

  function flash(t: string) { setMsg(t); setTimeout(() => setMsg(""), 1500); }
  function startResize(col: ColKey, e: React.MouseEvent) {
    e.preventDefault();
    const sx = e.clientX, sw = w[col];
    const mv = (ev: MouseEvent) => setW((p) => ({ ...p, [col]: Math.max(60, sw + ev.clientX - sx) }));
    const up = () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
  }
  const gridCols = `${w.code}px ${w.name}px ${w.level}px 1fr ${w.source}px ${w.ai}px ${w.action}px`;
  const hdrCell: React.CSSProperties = { padding: "5px 6px", fontSize: 12, fontWeight: 700, color: "#fff", position: "relative", borderRight: "1px solid #555" };
  const handle: React.CSSProperties = { position: "absolute", right: 0, top: 0, width: 6, height: "100%", cursor: "col-resize" };
  const gi_of = useMemo(() => { const m = new Map(cards.map((c, i) => [c.category, i])); return m; }, [cards]);

  const canPreview = !!docMeta && docMeta.preview_kind !== "none" && docMeta.has_preview !== false;

  return (
    // height:100%(뷰포트 아님) — 이 컴포넌트는 AppShell 의 상단 고정헤더 아래 <main> 안에
    // 얹힌다. 100vh 를 쓰면 헤더 높이만큼 실제 가용영역보다 더 커져 아래로 삐져나갔다.
    <div style={{ padding: 16, height: "100%", boxSizing: "border-box", position: "relative" }}>
    {/* min-height:0 필수 — column 자식은 기본 min-height:auto 라 내용(Virtuoso) 높이만큼
        부모가 늘어나려 하고, react-virtuoso 는 부모가 측정가능한 높이를 못 받으면 행을
        0개로 렌더링한다(전형적 중첩 flexbox 함정). */}
    <div style={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6, flexWrap: "wrap" }}>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>조견표 편집</h1>
        <span style={{ fontSize: 12, color: "#888" }} title="탭 = 요구사항 분류(엑셀 시트 단위)">
          doc {docId.slice(0, 8)} · {rows.length}행 · 탭 {distinctTabCount}
        </span>
        <label style={{ fontSize: 12, color: "#555" }} title="각 행에서 개별 지정 가능(행 옆 작은 칸). 여기는 기본값.">기본 분해기호:
          <input value={delim} onChange={(e) => setDelim(e.target.value)} style={{ width: 44, marginLeft: 4, border: "1px solid #bbb", borderRadius: 3, padding: "1px 4px", textAlign: "center" }} />
        </label>
        {msg && <span style={{ fontSize: 12, color: "#c8102e" }}>{msg}</span>}
        {!viewerOpen && canPreview && (
          <button onClick={() => setViewerOpen(true)} style={{ marginLeft: "auto", padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>◂ 원문 뷰어</button>
        )}
        {/* AI 검토 — 자동 실행 아님. 카드 정리를 마친 뒤 사람이 직접 눌러 시작(강제 재평가). */}
        <button onClick={() => void onStartAiReview()} disabled={aiReviewing}
          title="편집을 마친 뒤 눌러 AI 판정(O/X/△)을 최신 상태로 재평가합니다"
          style={{ marginLeft: viewerOpen || !canPreview ? "auto" : undefined, padding: "6px 12px", borderRadius: 6, fontSize: 13, opacity: aiReviewing ? 0.6 : 1 }}>
          {aiReviewing ? `AI 검토 중… ${aiProgress ? `${aiProgress.done}/${aiProgress.total}` : ""}` : "AI 검토 시작"}
        </button>
        <a href={exportFixedUrl(docId)} style={{ background: "#c8102e", color: "#fff", padding: "6px 14px", borderRadius: 6, fontSize: 13, textDecoration: "none" }}>Excel 다운로드</a>
        <button onClick={() => void load()} style={{ padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>새로고침</button>
      </div>

      {/* #5 탭바 — 엑셀 시트탭처럼: 누른 탭 하나만 화면에 보이고(필터), 활성탭은 아래
          표와 이어붙은 것처럼 흰 배경으로 튀어나와 보인다. "전체"는 문서순 전체보기. */}
      <div style={{ display: "flex", gap: 2, overflowX: "auto", overflowY: "hidden", flexShrink: 0,
        borderBottom: "2px solid #c8102e", scrollbarWidth: "thin" }}>
        <button onClick={() => setActiveTab(null)} title="문서순으로 전체 보기"
          style={{
            fontSize: 12, padding: "6px 14px", whiteSpace: "nowrap", cursor: "pointer", flexShrink: 0,
            borderTop: "1px solid #c8102e", borderLeft: "1px solid #c8102e", borderRight: "1px solid #c8102e",
            borderBottom: "none", borderRadius: "6px 6px 0 0",
            background: activeTab == null ? "#c8102e" : "#eee",
            color: activeTab == null ? "#fff" : "#888",
            fontWeight: activeTab == null ? 700 : 400,
            position: "relative", top: 1,
          }}>
          전체 {rows.length}
        </button>
        {allCards.map((c, i) => {
          const active = activeTab === c.category;
          const edgeColor = active ? "#c8102e" : "#ccd";
          return (
            <button key={c.reqIds[0] ?? i} onClick={() => setActiveTab(c.category)} title={c.category}
              style={{
                fontSize: 12, padding: "6px 14px", whiteSpace: "nowrap", cursor: "pointer", flexShrink: 0,
                maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis",
                borderTop: `1px solid ${edgeColor}`, borderLeft: `1px solid ${edgeColor}`,
                borderRight: `1px solid ${edgeColor}`, borderBottom: "none",
                borderRadius: "6px 6px 0 0",
                background: active ? "#fff" : "#eee",
                color: active ? "#1a1a1a" : "#888",
                fontWeight: active ? 700 : 400,
                position: "relative", top: 1, zIndex: active ? 1 : 0,
              }}>
              {c.category} {c.count}
            </button>
          );
        })}
      </div>

      {/* 고정 컬럼 헤더 */}
      <div style={{ display: "grid", gridTemplateColumns: gridCols, background: "#404040", borderRadius: "4px 4px 0 0" }}>
        <div style={hdrCell}>요구사항 ID<span style={handle} onMouseDown={(e) => startResize("code", e)} /></div>
        <div style={hdrCell}>요구사항명<span style={handle} onMouseDown={(e) => startResize("name", e)} /></div>
        <div style={hdrCell}>계위<span style={handle} onMouseDown={(e) => startResize("level", e)} /></div>
        <div style={hdrCell}>상세내용</div>
        <div style={hdrCell} title="원문 페이지 — 클릭하면 우측 뷰어로 이동">출처<span style={handle} onMouseDown={(e) => startResize("source", e)} /></div>
        <div style={hdrCell} title="AI 판정(O=적합/△=일부/X=부적합) — 'AI 검토 시작' 버튼으로 갱신">AI<span style={handle} onMouseDown={(e) => startResize("ai", e)} /></div>
        <div style={{ ...hdrCell, borderRight: "none" }}>작업</div>
      </div>
      {loading ? <p>불러오는 중…</p> : (
        <GroupedVirtuoso
          style={{ flex: 1, border: "1px solid #ddd", borderTop: "none" }}
          groupCounts={groupCounts}
          groupContent={(gi) => {
            const c = cards[gi];
            return (
              <div id={`cardhdr-${gi}`}
                onMouseEnter={() => setHoverCard(gi)} onMouseLeave={() => setHoverCard((h) => (h === gi ? null : h))}
                style={{ display: "flex", alignItems: "center", gap: 6, background: "#eef1f5", borderTop: "2px solid #c8102e", borderBottom: "1px solid #ccd", padding: "4px 8px", flexWrap: "wrap" }}>
                <b style={{ fontSize: 12, maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={c.category}>📁 {c.category}</b>
                <span style={{ fontSize: 11, color: "#888" }}>{c.count}행</span>
                <span style={{ marginLeft: 6, fontSize: 11, color: "#666" }}>ID:</span>
                <input value={pfxEdit[c.category] ?? c.prefix}
                  onChange={(e) => setPfxEdit((p) => ({ ...p, [c.category]: e.target.value }))}
                  style={{ width: 110, fontSize: 11, border: "1px solid #bbb", borderRadius: 3, padding: "1px 4px" }} />
                <button onClick={() => void applyPrefix(c)} style={{ fontSize: 11, padding: "2px 8px" }}>ID 일괄적용</button>
                <button onClick={() => void mergeIntoPrev(gi)} disabled={gi === 0} style={{ fontSize: 11, padding: "2px 8px" }}>↑ 위 카드와 병합</button>
                {/* #3 카드(탭) 통째 삭제 — 헤더 호버 시 우상단 X */}
                <button onClick={() => void onDeleteCard(c)} title="이 카드(탭) 통째 삭제"
                  style={{ marginLeft: "auto", fontSize: 11, padding: "1px 8px", color: "#c8102e", border: "1px solid #e3b0b6", borderRadius: 4, background: "#fff", cursor: "pointer", opacity: hoverCard === gi ? 1 : 0.2, transition: "opacity .12s" }}>
                  ✕ 카드삭제
                </button>
              </div>
            );
          }}
          itemContent={(index) => {
            const v = sortedRows[index]; if (!v) return null;
            const r = v.requirement;
            return (
              <div style={{ display: "grid", gridTemplateColumns: gridCols, borderBottom: "1px solid #eee", background: "#fff" }}>
                <Cell value={r.code} onSave={(x) => saveCell(r.id, "code", x)} />
                <Cell value={r.name} onSave={(x) => saveCell(r.id, "name", x)} />
                <Cell value={r.definition ?? ""} onSave={(x) => saveCell(r.id, "definition", x)} />
                <Cell value={r.detail} onSave={(x) => saveCell(r.id, "detail", x)} multiline />
                <div style={{ padding: "2px 4px", borderRight: "1px solid #eee", display: "flex", alignItems: "flex-start" }}>
                  {hasSourceLoc(r) ? (
                    <button onClick={() => jumpTo(r)} title="원문에서 보기"
                      style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, border: "1px solid #c8d0e0", background: "#eef3ff", color: "#2a4d8f", cursor: "pointer" }}>
                      {r.source_page != null ? `p.${r.source_page}` : "원문"}
                    </button>
                  ) : <span style={{ fontSize: 11, color: "#ccc" }}>-</span>}
                </div>
                <div style={{ padding: "2px 4px", borderRight: "1px solid #eee", display: "flex", alignItems: "flex-start", justifyContent: "center" }}>
                  {v.recommendation?.ai_risk ? (
                    <span title={v.recommendation.ai_reason || ""}
                      style={{ fontSize: 11, fontWeight: 700, width: 20, height: 20, borderRadius: "50%",
                        display: "flex", alignItems: "center", justifyContent: "center",
                        background: AI_BADGE[v.recommendation.ai_risk]?.bg ?? "#eee",
                        color: AI_BADGE[v.recommendation.ai_risk]?.fg ?? "#888" }}>
                      {v.recommendation.ai_risk}
                    </span>
                  ) : <span style={{ fontSize: 11, color: "#ccc" }}>-</span>}
                </div>
                <div style={{ padding: "3px 4px", display: "flex", gap: 3, flexWrap: "wrap", alignItems: "flex-start" }}>
                  <button onClick={() => void onMergeUp(index)} disabled={index === 0} style={{ fontSize: 10, padding: "2px 4px" }}>병합↑</button>
                  {/* #2 이 행 분해 기호(비우면 상단 기본값 사용) */}
                  <input value={rowDelim[r.id] ?? delim} onChange={(e) => setRowDelim((p) => ({ ...p, [r.id]: e.target.value }))}
                    title="이 행 분해 기호" style={{ width: 26, fontSize: 10, border: "1px solid #ccc", borderRadius: 3, textAlign: "center", padding: "1px 2px" }} />
                  <button onClick={() => void onSplit(r.id)} style={{ fontSize: 10, padding: "2px 4px" }}>분해</button>
                  <button onClick={() => void onSplitHere(index)} title="이 지점에서 카드 분해(이 행부터 새 카드)" style={{ fontSize: 10, padding: "2px 4px" }}>↓카드분해</button>
                  <button onClick={() => void onDelete(r.id)} style={{ fontSize: 10, padding: "2px 4px", color: "#c8102e" }}>삭제</button>
                </div>
              </div>
            );
          }}
        />
      )}
    </div>
    {/* 뷰어 = 우측에서 슬라이드로 나오는 오버레이 드로어. 표 레이아웃(flex 너비)을
        전혀 잠식하지 않는다 — 이전엔 flex 형제라 좁은 화면에서 표가 거의 안 보였다. */}
    {viewerOpen && canPreview && (
      <>
        <div onClick={() => setViewerOpen(false)}
          style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(15,15,20,0.25)" }} />
        <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, zIndex: 41, width: 460, maxWidth: "92vw", padding: 12, boxSizing: "border-box" }}>
          <PdfViewerPane
            docId={docId}
            kind={docMeta?.preview_kind === "html" ? "html" : "pdf"}
            page={viewerPage}
            tableIndex={viewerTableIdx}
            anchorText={viewerAnchor}
            jumpNonce={jumpNonce}
            sourceFilename={docMeta?.source_filename}
            onCollapse={() => setViewerOpen(false)}
          />
        </div>
      </>
    )}
    </div>
  );
}

function Cell({ value, onSave, multiline = false }: { value: string; onSave: (v: string) => void; multiline?: boolean }) {
  const [v, setV] = useState(value);
  const focused = useRef(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (!focused.current) setV(value); }, [value]);
  const autosize = useCallback(() => { const el = ref.current; if (!el) return; el.style.height = "auto"; el.style.height = `${el.scrollHeight}px`; }, []);
  useEffect(() => { autosize(); }, [v, autosize]);
  return (
    <div style={{ borderRight: "1px solid #eee", padding: "2px 5px" }}>
      <textarea ref={ref} value={v} rows={1}
        onFocus={() => (focused.current = true)}
        onChange={(e) => setV(e.target.value)} onInput={autosize}
        onBlur={() => { focused.current = false; if (v !== value) onSave(v); }}
        style={{ width: "100%", border: "none", background: "transparent", fontSize: 13, resize: "none", overflow: "hidden", fontFamily: "inherit", lineHeight: 1.4, whiteSpace: multiline ? "pre-wrap" : "normal" }} />
    </div>
  );
}

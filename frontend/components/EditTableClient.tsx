"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GroupedVirtuoso, type GroupedVirtuosoHandle } from "react-virtuoso";
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
} from "@/lib/api";
import { PdfViewerPane } from "@/components/PdfViewerPane";

type ColKey = "code" | "name" | "level" | "source" | "action";
const DEFAULT_W: Record<ColKey, number> = { code: 120, name: 150, level: 130, source: 60, action: 236 };
type Card = { category: string; prefix: string; name: string; reqIds: string[]; count: number };

export default function EditTableClient({ docId }: { docId: string }) {
  const [rows, setRows] = useState<RequirementView[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const [w, setW] = useState<Record<ColKey, number>>(DEFAULT_W);
  const [pfxEdit, setPfxEdit] = useState<Record<string, string>>({});
  const [delim, setDelim] = useState("●");   // 분해 기준 기호(기본값)
  const [rowDelim, setRowDelim] = useState<Record<string, string>>({});  // #2 행(셀)별 분해기호 오버라이드
  const [hoverCard, setHoverCard] = useState<number | null>(null);       // #3 카드헤더 호버시 삭제 X
  const vref = useRef<GroupedVirtuosoHandle>(null);                      // #1/#5 빠른 카드 이동

  // 출처(페이지) 클릭 → 우측 PDF/원문 뷰어로 점프.
  const [docMeta, setDocMeta] = useState<DocumentMeta | null>(null);
  const [viewerOpen, setViewerOpen] = useState(true);
  const [viewerPage, setViewerPage] = useState<number | null>(null);
  const [viewerTableIdx, setViewerTableIdx] = useState<number | null>(null);
  const [viewerAnchor, setViewerAnchor] = useState<string | null>(null);
  const [jumpNonce, setJumpNonce] = useState(0);
  useEffect(() => { void fetchDocumentMeta(docId).then(setDocMeta).catch(() => {}); }, [docId]);
  function jumpTo(r: RequirementView["requirement"]) {
    const p = r.source_page != null ? Number(r.source_page) : null;
    setViewerPage(p && !Number.isNaN(p) ? p : null);
    setViewerTableIdx(r.source_table_index ?? null);
    setViewerAnchor(r.detail || r.name || null);
    setViewerOpen(true);
    setJumpNonce((n) => n + 1);
  }
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
    return () => src.close();
  }, [docId, editorId, load]);

  const cards = useMemo<Card[]>(() => {
    const map = new Map<string, Card>();
    for (const v of rows) {
      const cat = v.requirement.category || "요구사항";
      const pfx = (v.requirement.code || "").replace(/-\d+\s*$/, "");
      if (!map.has(cat)) map.set(cat, { category: cat, prefix: pfx, name: v.requirement.name || "", reqIds: [], count: 0 });
      const c = map.get(cat)!; c.reqIds.push(v.requirement.id); c.count++;
    }
    return [...map.values()];
  }, [rows]);

  const sortedRows = useMemo(() => {
    const order = new Map(cards.map((c, i) => [c.category, i]));
    return [...rows].sort((a, b) =>
      order.get(a.requirement.category || "요구사항")! - order.get(b.requirement.category || "요구사항")!);
  }, [rows, cards]);
  const groupCounts = useMemo(() => cards.map((c) => c.count), [cards]);

  async function saveCell(reqId: string, field: "code" | "name" | "definition" | "detail", value: string) {
    // #4 요구사항명 편집은 카드 전체에 전파
    if (field === "name") {
      const cat = rows.find((v) => v.requirement.id === reqId)?.requirement.category || "요구사항";
      const card = cards.find((c) => c.category === cat);
      setRows((prev) => prev.map((v) => v.requirement.category === cat ? { ...v, requirement: { ...v.requirement, name: value } } : v));
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
  function scrollToCard(gi: number) {
    // 가상화 리스트라 DOM 스크롤은 화면 밖 카드에 안 먹힘 → Virtuoso 인덱스 점프(즉시).
    let idx = 0;
    for (let i = 0; i < gi; i++) idx += groupCounts[i];
    vref.current?.scrollToIndex({ index: idx, align: "start" });
  }

  const gridCols = `${w.code}px ${w.name}px ${w.level}px 1fr ${w.source}px ${w.action}px`;
  const hdrCell: React.CSSProperties = { padding: "5px 6px", fontSize: 12, fontWeight: 700, color: "#fff", position: "relative", borderRight: "1px solid #555" };
  const handle: React.CSSProperties = { position: "absolute", right: 0, top: 0, width: 6, height: "100%", cursor: "col-resize" };
  const gi_of = useMemo(() => { const m = new Map(cards.map((c, i) => [c.category, i])); return m; }, [cards]);

  const canPreview = !!docMeta && docMeta.preview_kind !== "none" && docMeta.has_preview !== false;

  return (
    <div style={{ padding: 16, height: "100vh", display: "flex", gap: 12, boxSizing: "border-box" }}>
    {/* min-height:0 필수 — row flex 안 column 자식은 기본 min-height:auto 라 내용(Virtuoso)
        높이만큼 부모가 늘어나려 하고, react-virtuoso 는 부모가 측정가능한 높이를 못 받으면
        행을 0개로 렌더링한다(전형적 중첩 flexbox 함정). */}
    <div style={{ flex: 1, minWidth: 0, minHeight: 0, height: "100%", display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6, flexWrap: "wrap" }}>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>조견표 편집</h1>
        <span style={{ fontSize: 12, color: "#888" }}>doc {docId.slice(0, 8)} · {rows.length}행 · 카드 {cards.length}</span>
        <label style={{ fontSize: 12, color: "#555" }} title="각 행에서 개별 지정 가능(행 옆 작은 칸). 여기는 기본값.">기본 분해기호:
          <input value={delim} onChange={(e) => setDelim(e.target.value)} style={{ width: 44, marginLeft: 4, border: "1px solid #bbb", borderRadius: 3, padding: "1px 4px", textAlign: "center" }} />
        </label>
        {msg && <span style={{ fontSize: 12, color: "#c8102e" }}>{msg}</span>}
        {!viewerOpen && canPreview && (
          <button onClick={() => setViewerOpen(true)} style={{ marginLeft: "auto", padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>◂ 원문 뷰어</button>
        )}
        <a href={exportFixedUrl(docId)} style={{ marginLeft: viewerOpen || !canPreview ? "auto" : undefined, background: "#c8102e", color: "#fff", padding: "6px 14px", borderRadius: 6, fontSize: 13, textDecoration: "none" }}>Excel 다운로드</a>
        <button onClick={() => void load()} style={{ padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>새로고침</button>
      </div>

      {/* #5 개요 — 카드 목록 + 카드별 행수 (클릭 시 이동) */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8, maxHeight: 62, overflowY: "auto", padding: "2px 0" }}>
        {cards.map((c, i) => (
          <button key={c.category} onClick={() => scrollToCard(i)} title={c.category}
            style={{ fontSize: 11, padding: "2px 8px", borderRadius: 12, border: "1px solid #ccd", background: "#eef1f5", cursor: "pointer", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            📁 {c.category} <b>{c.count}</b>
          </button>
        ))}
      </div>

      {/* 고정 컬럼 헤더 */}
      <div style={{ display: "grid", gridTemplateColumns: gridCols, background: "#404040", borderRadius: "4px 4px 0 0" }}>
        <div style={hdrCell}>요구사항 ID<span style={handle} onMouseDown={(e) => startResize("code", e)} /></div>
        <div style={hdrCell}>요구사항명<span style={handle} onMouseDown={(e) => startResize("name", e)} /></div>
        <div style={hdrCell}>계위<span style={handle} onMouseDown={(e) => startResize("level", e)} /></div>
        <div style={hdrCell}>상세내용</div>
        <div style={hdrCell} title="원문 페이지 — 클릭하면 우측 뷰어로 이동">출처<span style={handle} onMouseDown={(e) => startResize("source", e)} /></div>
        <div style={{ ...hdrCell, borderRight: "none" }}>작업</div>
      </div>
      {loading ? <p>불러오는 중…</p> : (
        <GroupedVirtuoso
          ref={vref}
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
                  {(r.source_page != null || r.source_table_index != null) ? (
                    <button onClick={() => jumpTo(r)} title="원문에서 보기"
                      style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, border: "1px solid #c8d0e0", background: "#eef3ff", color: "#2a4d8f", cursor: "pointer" }}>
                      {r.source_page != null ? `p.${r.source_page}` : "원문"}
                    </button>
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
    {viewerOpen && canPreview && (
      <div style={{ width: 460, flexShrink: 0 }}>
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

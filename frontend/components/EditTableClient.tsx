"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GroupedVirtuoso } from "react-virtuoso";
import {
  RequirementView,
  listRequirements,
  editRequirement,
  deleteRequirement,
  mergeRequirements,
  splitRequirement,
  regroupRequirements,
  exportFixedUrl,
  eventStreamUrl,
} from "@/lib/api";

type ColKey = "code" | "name" | "level" | "action";
const DEFAULT_W: Record<ColKey, number> = { code: 120, name: 150, level: 130, action: 150 };
type Card = { category: string; prefix: string; reqIds: string[]; count: number };

export default function EditTableClient({ docId }: { docId: string }) {
  const [rows, setRows] = useState<RequirementView[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const [w, setW] = useState<Record<ColKey, number>>(DEFAULT_W);
  const [pfxEdit, setPfxEdit] = useState<Record<string, string>>({});
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
  useEffect(() => { void load(); }, [load]);

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
      if (!map.has(cat)) map.set(cat, { category: cat, prefix: pfx, reqIds: [], count: 0 });
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
    try { setRows(await mergeRequirements(docId, sortedRows[index - 1].requirement.id, sortedRows[index].requirement.id)); flash("행 병합"); }
    catch (e) { setMsg(`병합 실패: ${String(e)}`); }
  }
  async function onSplit(reqId: string) {
    const delim = prompt("어떤 기호로 분해할까요? (예: ●, -, 1))");
    if (!delim) return;
    try { setRows(await splitRequirement(docId, reqId, delim)); flash("분해됨"); }
    catch (e) { setMsg(`분해 실패: ${String(e)}`); }
  }
  async function applyPrefix(card: Card) {
    const p = (pfxEdit[card.category] ?? card.prefix).trim();
    if (!p) return;
    try { setRows(await regroupRequirements(docId, card.reqIds, { prefix: p })); flash(`ID 일괄적용 ${p}-001…`); }
    catch (e) { setMsg(`ID 적용 실패: ${String(e)}`); }
  }
  async function mergeIntoPrev(gi: number) {   // 이 카드를 위 카드와 합침
    if (gi <= 0) return;
    const prev = cards[gi - 1], cur = cards[gi];
    try {
      setRows(await regroupRequirements(docId, [...prev.reqIds, ...cur.reqIds], { category: prev.category, prefix: prev.prefix }));
      flash(`카드 병합 → ${prev.category}`);
    } catch (e) { setMsg(`카드 병합 실패: ${String(e)}`); }
  }

  function flash(t: string) { setMsg(t); setTimeout(() => setMsg(""), 1400); }
  function startResize(col: ColKey, e: React.MouseEvent) {
    e.preventDefault();
    const sx = e.clientX, sw = w[col];
    const mv = (ev: MouseEvent) => setW((p) => ({ ...p, [col]: Math.max(60, sw + ev.clientX - sx) }));
    const up = () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
  }

  const gridCols = `${w.code}px ${w.name}px ${w.level}px 1fr ${w.action}px`;
  const hdrCell: React.CSSProperties = { padding: "5px 6px", fontSize: 12, fontWeight: 700, color: "#fff", position: "relative", borderRight: "1px solid #555" };
  const handle: React.CSSProperties = { position: "absolute", right: 0, top: 0, width: 6, height: "100%", cursor: "col-resize" };

  return (
    <div style={{ padding: 16, height: "100vh", display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>조견표 편집</h1>
        <span style={{ fontSize: 12, color: "#888" }}>doc {docId.slice(0, 8)} · {rows.length}행 · 카드 {cards.length} · {editorId}</span>
        {msg && <span style={{ fontSize: 12, color: "#c8102e" }}>{msg}</span>}
        <a href={exportFixedUrl(docId)} style={{ marginLeft: "auto", background: "#c8102e", color: "#fff", padding: "6px 14px", borderRadius: 6, fontSize: 13, textDecoration: "none" }}>Excel 다운로드</a>
        <button onClick={() => void load()} style={{ padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>새로고침</button>
      </div>
      <p style={{ fontSize: 12, color: "#666", marginBottom: 6 }}>
        칸 클릭→수정(실시간) · 헤더 경계 드래그로 너비조절 · 카드마다 개별 표 · [병합↑]행결합 [분해]기호로쪼갬 [삭제]즉시 · 카드헤더에서 ID일괄·위카드와 병합.
      </p>
      {/* 고정 컬럼 헤더 */}
      <div style={{ display: "grid", gridTemplateColumns: gridCols, background: "#404040", borderRadius: "4px 4px 0 0" }}>
        <div style={hdrCell}>요구사항 ID<span style={handle} onMouseDown={(e) => startResize("code", e)} /></div>
        <div style={hdrCell}>요구사항명<span style={handle} onMouseDown={(e) => startResize("name", e)} /></div>
        <div style={hdrCell}>계위<span style={handle} onMouseDown={(e) => startResize("level", e)} /></div>
        <div style={hdrCell}>상세내용</div>
        <div style={{ ...hdrCell, borderRight: "none" }}>작업</div>
      </div>
      {loading ? <p>불러오는 중…</p> : (
        <GroupedVirtuoso
          style={{ flex: 1, border: "1px solid #ddd", borderTop: "none" }}
          groupCounts={groupCounts}
          groupContent={(gi) => {
            const c = cards[gi];
            return (
              <div style={{ display: "flex", alignItems: "center", gap: 6, background: "#eef1f5", borderTop: "2px solid #c8102e", borderBottom: "1px solid #ccd", padding: "4px 8px" }}>
                <b style={{ fontSize: 12 }}>📁 {c.category}</b>
                <span style={{ fontSize: 11, color: "#888" }}>{c.count}행</span>
                <span style={{ marginLeft: 8, fontSize: 11, color: "#666" }}>ID:</span>
                <input value={pfxEdit[c.category] ?? c.prefix}
                  onChange={(e) => setPfxEdit((p) => ({ ...p, [c.category]: e.target.value }))}
                  style={{ width: 110, fontSize: 11, border: "1px solid #bbb", borderRadius: 3, padding: "1px 4px" }} />
                <button onClick={() => void applyPrefix(c)} style={{ fontSize: 11, padding: "2px 8px" }}>ID 일괄적용</button>
                <button onClick={() => void mergeIntoPrev(gi)} disabled={gi === 0} style={{ fontSize: 11, padding: "2px 8px" }}>↑ 위 카드와 병합</button>
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
                <div style={{ padding: "3px 4px", display: "flex", gap: 3, flexWrap: "wrap", alignItems: "flex-start" }}>
                  <button onClick={() => void onMergeUp(index)} disabled={index === 0} style={{ fontSize: 10, padding: "2px 4px" }}>병합↑</button>
                  <button onClick={() => void onSplit(r.id)} style={{ fontSize: 10, padding: "2px 4px" }}>분해</button>
                  <button onClick={() => void onDelete(r.id)} style={{ fontSize: 10, padding: "2px 4px", color: "#c8102e" }}>삭제</button>
                </div>
              </div>
            );
          }}
        />
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

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TableVirtuoso } from "react-virtuoso";
import {
  RequirementView,
  listRequirements,
  editRequirement,
  deleteRequirement,
  mergeRequirements,
  regroupRequirements,
  exportFixedUrl,
  eventStreamUrl,
} from "@/lib/api";

type ColKey = "code" | "name" | "level" | "action";
const DEFAULT_W: Record<ColKey, number> = { code: 120, name: 150, level: 130, action: 84 };

type Card = { category: string; prefix: string; reqIds: string[]; count: number };

export default function EditTableClient({ docId }: { docId: string }) {
  const [rows, setRows] = useState<RequirementView[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const [w, setW] = useState<Record<ColKey, number>>(DEFAULT_W);
  const [sel, setSel] = useState<Set<string>>(new Set());        // 병합 선택된 카드(category)
  const [pfxEdit, setPfxEdit] = useState<Record<string, string>>({}); // 카드별 접두사 입력
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

  // 카드(=탭/category) 그룹 — 등장순 유지
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

  // 표는 카드(등장순)로 묶여 보이도록 정렬
  const sortedRows = useMemo(() => {
    const order = new Map(cards.map((c, i) => [c.category, i]));
    return [...rows].sort((a, b) =>
      (order.get(a.requirement.category || "요구사항")! - order.get(b.requirement.category || "요구사항")!));
  }, [rows, cards]);

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
    try { setRows(await mergeRequirements(docId, sortedRows[index - 1].requirement.id, sortedRows[index].requirement.id)); flash("행 병합됨"); }
    catch (e) { setMsg(`병합 실패: ${String(e)}`); }
  }
  async function applyPrefix(card: Card) {
    const p = (pfxEdit[card.category] ?? card.prefix).trim();
    if (!p) return;
    try { setRows(await regroupRequirements(docId, card.reqIds, { prefix: p })); flash(`ID 일괄적용: ${p}-001…`); }
    catch (e) { setMsg(`ID 적용 실패: ${String(e)}`); }
  }
  async function mergeCards() {
    const chosen = cards.filter((c) => sel.has(c.category));
    if (chosen.length < 2) { setMsg("카드 2개 이상 선택"); return; }
    const first = chosen[0];
    const ids = chosen.flatMap((c) => c.reqIds);
    const prefix = (pfxEdit[first.category] ?? first.prefix).trim() || first.prefix;
    try {
      setRows(await regroupRequirements(docId, ids, { category: first.category, prefix }));
      setSel(new Set()); flash(`카드 ${chosen.length}개 병합 → ${first.category} (${prefix}-…)`);
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

  const cell: React.CSSProperties = { border: "1px solid #e2e2e2", padding: "3px 6px", verticalAlign: "top", fontSize: 13, background: "#fff", overflow: "hidden" };
  const th: React.CSSProperties = { ...cell, background: "#404040", color: "#fff", fontSize: 12, position: "relative", userSelect: "none" };
  const handle: React.CSSProperties = { position: "absolute", right: 0, top: 0, width: 6, height: "100%", cursor: "col-resize" };

  return (
    <div style={{ padding: 16, height: "100vh", display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>조견표 편집</h1>
        <span style={{ fontSize: 12, color: "#888" }}>doc {docId.slice(0, 8)} · {rows.length}행 · 카드 {cards.length} · {editorId}</span>
        <a href={exportFixedUrl(docId)} style={{ marginLeft: "auto", background: "#c8102e", color: "#fff", padding: "6px 14px", borderRadius: 6, fontSize: 13, textDecoration: "none" }}>Excel 다운로드(편집 반영)</a>
        <button onClick={() => void load()} style={{ padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>새로고침</button>
      </div>

      {/* 카드 관리: ID 접두사 일괄편집 + 카드 병합 */}
      <div style={{ marginBottom: 8, border: "1px solid #eee", borderRadius: 6, padding: 8, maxHeight: 130, overflowY: "auto", background: "#fafafa" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <b style={{ fontSize: 12 }}>카드({cards.length}) — ID 접두사 편집 후 [적용], 여러 카드 체크 후 [선택 병합]</b>
          <button onClick={() => void mergeCards()} disabled={sel.size < 2}
            style={{ fontSize: 12, padding: "3px 10px", background: sel.size >= 2 ? "#c8102e" : "#ccc", color: "#fff", borderRadius: 5 }}>
            선택 카드 병합 ({sel.size})
          </button>
          {msg && <span style={{ fontSize: 12, color: "#c8102e" }}>{msg}</span>}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {cards.map((c) => (
            <div key={c.category} style={{ display: "flex", alignItems: "center", gap: 4, border: "1px solid #ddd", borderRadius: 5, padding: "2px 6px", background: "#fff" }}>
              <input type="checkbox" checked={sel.has(c.category)}
                onChange={(e) => setSel((s) => { const n = new Set(s); e.target.checked ? n.add(c.category) : n.delete(c.category); return n; })} />
              <span style={{ fontSize: 11, color: "#666", maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={c.category}>{c.category}</span>
              <input value={pfxEdit[c.category] ?? c.prefix}
                onChange={(e) => setPfxEdit((p) => ({ ...p, [c.category]: e.target.value }))}
                style={{ width: 90, fontSize: 11, border: "1px solid #ccc", borderRadius: 3, padding: "1px 3px" }} />
              <button onClick={() => void applyPrefix(c)} style={{ fontSize: 11, padding: "1px 6px" }}>적용</button>
              <span style={{ fontSize: 10, color: "#aaa" }}>{c.count}행</span>
            </div>
          ))}
        </div>
      </div>

      <p style={{ fontSize: 12, color: "#666", marginBottom: 6 }}>
        칸 클릭→수정(실시간) · 헤더 경계 드래그 너비조절 · [병합↑] 행 상세 결합 · [삭제] 즉시.
      </p>
      {loading ? <p>불러오는 중…</p> : (
        <TableVirtuoso
          style={{ flex: 1, border: "1px solid #ddd", width: "100%" }}
          data={sortedRows}
          components={{ Table: (props) => <table {...props} style={{ ...props.style, width: "100%", tableLayout: "fixed", borderCollapse: "collapse" }} /> }}
          fixedHeaderContent={() => (
            <tr>
              <th style={{ ...th, width: w.code }}>요구사항 ID<span style={handle} onMouseDown={(e) => startResize("code", e)} /></th>
              <th style={{ ...th, width: w.name }}>요구사항명<span style={handle} onMouseDown={(e) => startResize("name", e)} /></th>
              <th style={{ ...th, width: w.level }}>계위<span style={handle} onMouseDown={(e) => startResize("level", e)} /></th>
              <th style={th}>상세내용</th>
              <th style={{ ...th, width: w.action }}>작업</th>
            </tr>
          )}
          itemContent={(index, v) => {
            const r = v.requirement;
            return (
              <>
                <EditCell value={r.code} width={w.code} onSave={(x) => saveCell(r.id, "code", x)} />
                <EditCell value={r.name} width={w.name} onSave={(x) => saveCell(r.id, "name", x)} />
                <EditCell value={r.definition ?? ""} width={w.level} onSave={(x) => saveCell(r.id, "definition", x)} />
                <EditCell value={r.detail} onSave={(x) => saveCell(r.id, "detail", x)} multiline />
                <td style={{ ...cell, width: w.action }}>
                  <button onClick={() => void onMergeUp(index)} disabled={index === 0} style={{ fontSize: 11, marginRight: 4, padding: "2px 5px" }}>병합↑</button>
                  <button onClick={() => void onDelete(r.id)} style={{ fontSize: 11, padding: "2px 5px", color: "#c8102e" }}>삭제</button>
                </td>
              </>
            );
          }}
        />
      )}
    </div>
  );
}

function EditCell({ value, onSave, width, multiline = false }: {
  value: string; onSave: (v: string) => void; width?: number; multiline?: boolean;
}) {
  const [v, setV] = useState(value);
  const focused = useRef(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (!focused.current) setV(value); }, [value]);
  const autosize = useCallback(() => {
    const el = ref.current; if (!el) return;
    el.style.height = "auto"; el.style.height = `${el.scrollHeight}px`;
  }, []);
  useEffect(() => { autosize(); }, [v, autosize]);
  const cell: React.CSSProperties = { border: "1px solid #e2e2e2", padding: "3px 6px", verticalAlign: "top", background: "#fff", ...(width ? { width } : {}) };
  return (
    <td style={cell}>
      <textarea ref={ref} value={v} rows={1}
        onFocus={() => (focused.current = true)}
        onChange={(e) => setV(e.target.value)} onInput={autosize}
        onBlur={() => { focused.current = false; if (v !== value) onSave(v); }}
        style={{ width: "100%", border: "none", background: "transparent", fontSize: 13, resize: "none",
          overflow: "hidden", fontFamily: "inherit", lineHeight: 1.4, whiteSpace: multiline ? "pre-wrap" : "normal" }} />
    </td>
  );
}

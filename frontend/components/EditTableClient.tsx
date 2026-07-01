"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TableVirtuoso } from "react-virtuoso";
import {
  RequirementView,
  listRequirements,
  editRequirement,
  deleteRequirement,
  mergeRequirements,
  exportFixedUrl,
  eventStreamUrl,
} from "@/lib/api";

type ColKey = "code" | "name" | "level" | "action";
const DEFAULT_W: Record<ColKey, number> = { code: 120, name: 150, level: 130, action: 84 };

/** 고정칼럼 편집표 — react-virtuoso(수백행) + 실시간(SSE) + 리사이즈/자동높이/전체너비. */
export default function EditTableClient({ docId }: { docId: string }) {
  const [rows, setRows] = useState<RequirementView[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const [w, setW] = useState<Record<ColKey, number>>(DEFAULT_W);
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

  async function saveCell(reqId: string, field: "code" | "name" | "definition" | "detail", value: string) {
    setRows((prev) => prev.map((v) => v.requirement.id === reqId
      ? { ...v, requirement: { ...v.requirement, [field]: value } } : v));
    try { await editRequirement(reqId, { [field]: value }, editorId); }
    catch (e) { setMsg(`편집 실패: ${String(e)}`); }
  }
  async function onDelete(reqId: string) {   // #6: 확인창 없이 즉시 삭제
    try { setRows(await deleteRequirement(docId, reqId)); flash("삭제됨"); }
    catch (e) { setMsg(`삭제 실패: ${String(e)}`); }
  }
  async function onMergeUp(idx: number) {
    if (idx <= 0) return;
    try { setRows(await mergeRequirements(docId, rows[idx - 1].requirement.id, rows[idx].requirement.id)); flash("병합됨"); }
    catch (e) { setMsg(`병합 실패: ${String(e)}`); }
  }
  function flash(t: string) { setMsg(t); setTimeout(() => setMsg(""), 1000); }

  // 칼럼 리사이즈(#1) — 헤더 우측 핸들 드래그
  function startResize(col: ColKey, e: React.MouseEvent) {
    e.preventDefault();
    const startX = e.clientX; const startW = w[col];
    const move = (ev: MouseEvent) => setW((p) => ({ ...p, [col]: Math.max(60, startW + ev.clientX - startX) }));
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  }

  const cell: React.CSSProperties = { border: "1px solid #e2e2e2", padding: "3px 6px", verticalAlign: "top", fontSize: 13, background: "#fff", overflow: "hidden" };
  const th: React.CSSProperties = { ...cell, background: "#404040", color: "#fff", fontSize: 12, position: "relative", userSelect: "none" };
  const handle: React.CSSProperties = { position: "absolute", right: 0, top: 0, width: 6, height: "100%", cursor: "col-resize" };

  return (
    <div style={{ padding: 16, height: "100vh", display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>조견표 편집</h1>
        <span style={{ fontSize: 12, color: "#888" }}>doc {docId.slice(0, 8)} · {rows.length}행 · {editorId}</span>
        <a href={exportFixedUrl(docId)} style={{ marginLeft: "auto", background: "#c8102e", color: "#fff", padding: "6px 14px", borderRadius: 6, fontSize: 13, textDecoration: "none" }}>Excel 다운로드(편집 반영)</a>
        <button onClick={() => void load()} style={{ padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>새로고침</button>
      </div>
      <p style={{ fontSize: 12, color: "#666", marginBottom: 6 }}>
        칸 클릭→수정→포커스아웃 저장(실시간) · 헤더 경계 드래그로 너비조절 · [병합↑] 상세 줄바꿈결합 · [삭제] 즉시.
        {msg && <b style={{ color: "#c8102e" }}> · {msg}</b>}
      </p>
      {loading ? <p>불러오는 중…</p> : (
        <TableVirtuoso
          style={{ flex: 1, border: "1px solid #ddd", width: "100%" }}
          data={rows}
          components={{
            Table: (props) => <table {...props} style={{ ...props.style, width: "100%", tableLayout: "fixed", borderCollapse: "collapse" }} />,
          }}
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

/** 자동높이(#5) — textarea 가 내용에 맞춰 늘어남(병합 시 눈에 보임). */
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
      <textarea
        ref={ref} value={v} rows={1}
        onFocus={() => (focused.current = true)}
        onChange={(e) => setV(e.target.value)}
        onInput={autosize}
        onBlur={() => { focused.current = false; if (v !== value) onSave(v); }}
        style={{ width: "100%", border: "none", background: "transparent", fontSize: 13, resize: "none",
          overflow: "hidden", fontFamily: "inherit", lineHeight: 1.4,
          whiteSpace: multiline ? "pre-wrap" : "normal" }}
      />
    </td>
  );
}

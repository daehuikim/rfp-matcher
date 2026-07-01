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

/** 고정칼럼 편집 테이블 — react-virtuoso(수백행 성능) + 실시간 동시편집(SSE 즉시 반영). */
export default function EditTableClient({ docId }: { docId: string }) {
  const [rows, setRows] = useState<RequirementView[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const editorId = useMemo(
    () => (typeof window === "undefined" ? "" : (localStorage.getItem("rfp-editor") ||
      (() => { const id = "u" + Math.random().toString(36).slice(2, 8); localStorage.setItem("rfp-editor", id); return id; })())),
    [],
  );

  const load = useCallback(async () => {
    try {
      setRows(await listRequirements(docId));
    } catch (e) {
      setMsg(`불러오기 실패: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => { void load(); }, [load]);

  // 실시간: 다른 사용자의 편집(REQUIREMENT_EDITED)은 해당 칸만 즉시 반영, 구조변경은 재로드.
  useEffect(() => {
    const src = new EventSource(eventStreamUrl(docId));
    src.addEventListener("REQUIREMENT_EDITED", (e: MessageEvent) => {
      try {
        const p = JSON.parse(e.data);
        if (p.editor_id === editorId) return; // 본인 편집은 skip(echo 방지)
        setRows((prev) => prev.map((v) =>
          v.requirement.id === p.requirement_id
            ? { ...v, requirement: { ...v.requirement, ...p.fields } } : v));
        setMsg("다른 사용자의 편집 반영됨");
        setTimeout(() => setMsg(""), 1000);
      } catch { /* ignore */ }
    });
    src.addEventListener("REQUIREMENTS_CHANGED", () => { void load(); });
    return () => src.close();
  }, [docId, editorId, load]);

  async function saveCell(reqId: string, field: "code" | "name" | "definition" | "detail", value: string) {
    setRows((prev) => prev.map((v) => v.requirement.id === reqId
      ? { ...v, requirement: { ...v.requirement, [field]: value } } : v)); // optimistic
    try {
      await editRequirement(reqId, { [field]: value }, editorId);
    } catch (e) {
      setMsg(`편집 실패: ${String(e)}`);
    }
  }

  async function onDelete(reqId: string) {
    if (!confirm("이 줄을 삭제하고 이후 ID를 재정렬할까요?")) return;
    try { setRows(await deleteRequirement(docId, reqId)); setFlash("삭제 + ID 재정렬"); }
    catch (e) { setMsg(`삭제 실패: ${String(e)}`); }
  }

  async function onMergeUp(idx: number) {
    if (idx <= 0) return;
    try {
      setRows(await mergeRequirements(docId, rows[idx - 1].requirement.id, rows[idx].requirement.id));
      setFlash("윗줄과 병합(상세 줄바꿈 결합)");
    } catch (e) { setMsg(`병합 실패: ${String(e)}`); }
  }

  function setFlash(t: string) { setMsg(t); setTimeout(() => setMsg(""), 1200); }

  const cell: React.CSSProperties = { border: "1px solid #e2e2e2", padding: "3px 6px", verticalAlign: "top", fontSize: 13, background: "#fff" };
  const th: React.CSSProperties = { ...cell, background: "#404040", color: "#fff", fontSize: 12, position: "sticky", top: 0 };

  return (
    <div style={{ padding: 16, height: "100vh", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>조견표 편집</h1>
        <span style={{ fontSize: 12, color: "#888" }}>doc {docId.slice(0, 8)} · {rows.length}행 · 편집자 {editorId}</span>
        <a href={exportFixedUrl(docId)} style={{ marginLeft: "auto", background: "#c8102e", color: "#fff", padding: "6px 14px", borderRadius: 6, fontSize: 13, textDecoration: "none" }}>Excel 다운로드(편집 반영)</a>
        <button onClick={() => void load()} style={{ padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>새로고침</button>
      </div>
      <p style={{ fontSize: 12, color: "#666", marginBottom: 6 }}>
        칸 클릭→수정→포커스아웃 저장(실시간 동기화) · [병합↑] 윗줄에 상세 줄바꿈결합 · [삭제] 행+ID재정렬.
        {msg && <b style={{ color: "#c8102e" }}> · {msg}</b>}
      </p>
      {loading ? <p>불러오는 중…</p> : (
        <TableVirtuoso
          style={{ flex: 1, border: "1px solid #ddd" }}
          data={rows}
          fixedHeaderContent={() => (
            <tr>
              <th style={{ ...th, width: 120 }}>요구사항 ID</th>
              <th style={{ ...th, width: 170 }}>요구사항명</th>
              <th style={{ ...th, width: 150 }}>계위</th>
              <th style={th}>상세내용</th>
              <th style={{ ...th, width: 110 }}>작업</th>
            </tr>
          )}
          itemContent={(index, v) => {
            const r = v.requirement;
            return (
              <>
                <EditCell value={r.code} style={cell} onSave={(x) => saveCell(r.id, "code", x)} />
                <EditCell value={r.name} style={cell} onSave={(x) => saveCell(r.id, "name", x)} />
                <EditCell value={r.definition ?? ""} style={cell} onSave={(x) => saveCell(r.id, "definition", x)} />
                <EditCell value={r.detail} style={cell} multiline onSave={(x) => saveCell(r.id, "detail", x)} />
                <td style={cell}>
                  <button onClick={() => void onMergeUp(index)} disabled={index === 0} style={{ fontSize: 11, marginRight: 4, padding: "2px 6px" }}>병합↑</button>
                  <button onClick={() => void onDelete(r.id)} style={{ fontSize: 11, padding: "2px 6px", color: "#c8102e" }}>삭제</button>
                </td>
              </>
            );
          }}
        />
      )}
    </div>
  );
}

function EditCell({ value, onSave, style, multiline = false }: {
  value: string; onSave: (v: string) => void; style: React.CSSProperties; multiline?: boolean;
}) {
  const [v, setV] = useState(value);
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setV(value); }, [value]); // 실시간 반영(편집중 아닐 때만)
  const common = { value: v, onFocus: () => (focused.current = true),
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setV(e.target.value),
    onBlur: () => { focused.current = false; if (v !== value) onSave(v); },
    style: { width: "100%", border: "none", background: "transparent", fontSize: 13 } as React.CSSProperties };
  return <td style={style}>{multiline
    ? <textarea {...common} style={{ ...common.style, resize: "vertical", minHeight: 30 }} />
    : <input {...common} />}</td>;
}

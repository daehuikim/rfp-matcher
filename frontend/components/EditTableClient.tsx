"use client";

import { useCallback, useEffect, useState } from "react";
import {
  RequirementView,
  listRequirements,
  editRequirement,
  deleteRequirement,
  mergeRequirements,
  exportFixedUrl,
} from "@/lib/api";

/** 고정칼럼(요구사항ID/명/계위/상세) 편집 테이블 — 편집·병합·삭제 테스트용. FE→BE→Excel 반영. */
export default function EditTableClient({ docId }: { docId: string }) {
  const [rows, setRows] = useState<RequirementView[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listRequirements(docId));
    } catch (e) {
      setMsg(`불러오기 실패: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveCell(reqId: string, field: "code" | "name" | "definition" | "detail", value: string) {
    try {
      await editRequirement(reqId, { [field]: value });
      setMsg("저장됨");
      setTimeout(() => setMsg(""), 800);
    } catch (e) {
      setMsg(`편집 실패: ${String(e)}`);
    }
  }

  async function onDelete(reqId: string) {
    if (!confirm("이 줄을 삭제하고 이후 ID를 재정렬할까요?")) return;
    try {
      setRows(await deleteRequirement(docId, reqId));
      setMsg("삭제 + ID 재정렬 완료");
      setTimeout(() => setMsg(""), 1200);
    } catch (e) {
      setMsg(`삭제 실패: ${String(e)}`);
    }
  }

  async function onMergeUp(idx: number) {
    if (idx <= 0) return;
    const top = rows[idx - 1].requirement.id;
    const bottom = rows[idx].requirement.id;
    try {
      setRows(await mergeRequirements(docId, top, bottom));
      setMsg("윗줄과 병합(상세 줄바꿈 결합) 완료");
      setTimeout(() => setMsg(""), 1200);
    } catch (e) {
      setMsg(`병합 실패: ${String(e)}`);
    }
  }

  const cellStyle: React.CSSProperties = {
    border: "1px solid #e2e2e2", padding: "4px 6px", verticalAlign: "top", fontSize: 13,
  };

  return (
    <div style={{ padding: 20, maxWidth: 1200, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>조견표 편집 (병합/삭제/편집)</h1>
        <span style={{ fontSize: 12, color: "#888" }}>doc: {docId.slice(0, 8)} · {rows.length}행</span>
        <a href={exportFixedUrl(docId)} style={{ marginLeft: "auto", background: "#c8102e", color: "#fff",
          padding: "6px 14px", borderRadius: 6, fontSize: 13, textDecoration: "none" }}>
          Excel 다운로드(편집 반영)
        </a>
        <button onClick={() => void load()} style={{ padding: "6px 12px", borderRadius: 6, fontSize: 13 }}>새로고침</button>
      </div>
      <p style={{ fontSize: 12, color: "#666", marginBottom: 8 }}>
        칸을 클릭해 편집(포커스 벗어나면 저장) · [병합↑] 윗줄에 상세 줄바꿈 결합 · [삭제] 행 제거+ID 재정렬. {msg && <b style={{ color: "#c8102e" }}> {msg}</b>}
      </p>
      {loading ? (
        <p>불러오는 중…</p>
      ) : (
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr style={{ background: "#404040", color: "#fff", fontSize: 12 }}>
              <th style={{ ...cellStyle, width: 130, color: "#fff" }}>요구사항 ID</th>
              <th style={{ ...cellStyle, width: 180, color: "#fff" }}>요구사항명</th>
              <th style={{ ...cellStyle, width: 150, color: "#fff" }}>계위</th>
              <th style={{ ...cellStyle, color: "#fff" }}>상세내용</th>
              <th style={{ ...cellStyle, width: 110, color: "#fff" }}>작업</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v, i) => {
              const r = v.requirement;
              return (
                <tr key={r.id}>
                  <EditableCell value={r.code} style={cellStyle} onSave={(val) => saveCell(r.id, "code", val)} />
                  <EditableCell value={r.name} style={cellStyle} onSave={(val) => saveCell(r.id, "name", val)} />
                  <EditableCell value={r.definition ?? ""} style={cellStyle} onSave={(val) => saveCell(r.id, "definition", val)} />
                  <EditableCell value={r.detail} style={cellStyle} multiline onSave={(val) => saveCell(r.id, "detail", val)} />
                  <td style={cellStyle}>
                    <button onClick={() => void onMergeUp(i)} disabled={i === 0}
                      style={{ fontSize: 11, marginRight: 4, padding: "2px 6px" }}>병합↑</button>
                    <button onClick={() => void onDelete(r.id)}
                      style={{ fontSize: 11, padding: "2px 6px", color: "#c8102e" }}>삭제</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function EditableCell({
  value, onSave, style, multiline = false,
}: {
  value: string;
  onSave: (v: string) => void;
  style: React.CSSProperties;
  multiline?: boolean;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <td style={style}>
      {multiline ? (
        <textarea value={v} onChange={(e) => setV(e.target.value)}
          onBlur={() => v !== value && onSave(v)}
          style={{ width: "100%", border: "none", resize: "vertical", minHeight: 32, fontSize: 13, background: "transparent" }} />
      ) : (
        <input value={v} onChange={(e) => setV(e.target.value)}
          onBlur={() => v !== value && onSave(v)}
          style={{ width: "100%", border: "none", fontSize: 13, background: "transparent" }} />
      )}
    </td>
  );
}

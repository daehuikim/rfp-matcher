"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Judgement,
  RequirementView,
  categorySourceLabel,
  patchJudgement,
} from "@/lib/api";
import { formatRequirementDetail } from "@/lib/formatDetail";
import { AiVerdictModal } from "./AiVerdictModal";
import { CatalogAuditPanel } from "./CatalogAuditPanel";
import { JudgementToggle } from "./JudgementToggle";

type RemoteMap = Record<string, { mark: Judgement; note: string; editor_id: string }>;

/** 그룹 내 실제 값이 있는 칼럼만 노출 (표마다 스키마 상이 대응) */
type ColumnFlags = {
  subcategory: boolean;
  deliverables: boolean;
  source: boolean;
  aiReason: boolean;
  solutions: boolean;
  missing: boolean;
  consortium: boolean;
};

function computeColumns(rows: RequirementView[], hasTableJump: boolean): ColumnFlags {
  const flags: ColumnFlags = {
    subcategory: false,
    deliverables: false,
    source: false,
    aiReason: false,
    solutions: false,
    missing: false,
    consortium: false,
  };
  for (const v of rows) {
    const r = v.requirement;
    const rec = v.recommendation;
    if (r.subcategory) flags.subcategory = true;
    if (r.deliverables && r.deliverables.trim()) flags.deliverables = true;
    if (r.source_page || r.source_section) flags.source = true;
    // HTML 미리보기에서 표 인덱스로 위치 이동 가능하면 출처 칼럼 노출
    if (hasTableJump && r.source_table_index != null) flags.source = true;
    if (rec?.ai_reason) flags.aiReason = true;
    if ((rec?.matched_solution_skus?.length ?? 0) > 0 || (rec?.matched_solutions?.length ?? 0) > 0)
      flags.solutions = true;
    if ((rec?.missing_tech?.length ?? 0) > 0) flags.missing = true;
    if (rec?.consortium_need) flags.consortium = true;
  }
  return flags;
}

/** source_page(number|string|null) → 1-based 정수 페이지 또는 null */
function toPageNumber(v: number | string | null | undefined): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : parseInt(String(v), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function riskBadge(r: Judgement): { cls: string; label: string } {
  switch (r) {
    case "O":
      return { cls: "border-ktteal-300 bg-ktteal-50 text-ktteal-600", label: "가능" };
    case "△":
      return { cls: "border-amber-300 bg-amber-50 text-amber-700", label: "조건부" };
    case "X":
      return { cls: "border-ktred-300 bg-ktred-50 text-ktred-700", label: "리스크" };
    default:
      return { cls: "border-neutral-200 bg-neutral-50 text-neutral-500", label: "—" };
  }
}

function colCount(flags: ColumnFlags): number {
  // 코드 + 요구사항 + (옵션들) + AI판정 + 판정 + 메모
  let n = 2;
  if (flags.subcategory) n++;
  if (flags.deliverables) n++;
  if (flags.source) n++;
  n++; // AI 판정
  if (flags.aiReason) n++;
  if (flags.solutions) n++;
  if (flags.missing) n++;
  if (flags.consortium) n++;
  n += 2; // 판정 + 메모
  return n;
}

export function RequirementTable({
  rows,
  editorId,
  remoteByReqId,
  categoryFilterLabel,
  onOpenPage,
  onOpenTable,
}: {
  rows: RequirementView[];
  editorId: string;
  remoteByReqId: RemoteMap;
  categoryFilterLabel: string;
  onOpenPage?: (page: number) => void;
  onOpenTable?: (tableIndex: number, anchor?: string) => void;
}) {
  const groups = useMemo(() => {
    const map = new Map<string, RequirementView[]>();
    for (const v of rows) {
      const cat = v.requirement.category || "미분류";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(v);
    }
    return [...map.entries()]
      .sort(([a], [b]) => a.localeCompare(b, "ko"))
      .map(([name, items]) => ({ name, items }));
  }, [rows]);

  const [activeTab, setActiveTab] = useState<string>("__all__");

  // rows 변동 시 사라진 탭이면 전체로 복귀
  useEffect(() => {
    if (activeTab !== "__all__" && !groups.some((g) => g.name === activeTab)) {
      setActiveTab("__all__");
    }
  }, [groups, activeTab]);

  const visibleGroups =
    activeTab === "__all__" ? groups : groups.filter((g) => g.name === activeTab);

  return (
    <div>
      {/* 카테고리 탭 스트립 */}
      {groups.length > 1 && (
        <div className="panel mb-4 p-2">
          <div className="sheet-scroll flex items-center gap-1 pb-0.5">
            <button
              type="button"
              onClick={() => setActiveTab("__all__")}
              className={`tab ${activeTab === "__all__" ? "tab-active" : "tab-idle"}`}
            >
              전체 {rows.length}
            </button>
            {groups.map((g) => (
              <button
                key={g.name}
                type="button"
                title={g.name}
                onClick={() => setActiveTab(g.name)}
                className={`tab max-w-[16rem] truncate ${
                  activeTab === g.name ? "tab-active" : "tab-idle"
                }`}
              >
                {g.name} {g.items.length}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-5">
        {visibleGroups.map((g) => (
          <SheetBox
            key={g.name}
            title={g.name}
            categoryFilterLabel={categoryFilterLabel}
            rows={g.items}
            editorId={editorId}
            remoteByReqId={remoteByReqId}
            onOpenPage={onOpenPage}
            onOpenTable={onOpenTable}
          />
        ))}
      </div>
    </div>
  );
}

function SheetBox({
  title,
  rows,
  editorId,
  remoteByReqId,
  categoryFilterLabel,
  onOpenPage,
  onOpenTable,
}: {
  title: string;
  rows: RequirementView[];
  editorId: string;
  remoteByReqId: RemoteMap;
  categoryFilterLabel: string;
  onOpenPage?: (page: number) => void;
  onOpenTable?: (tableIndex: number, anchor?: string) => void;
}) {
  const flags = useMemo(() => computeColumns(rows, !!onOpenTable), [rows, onOpenTable]);
  const source = rows[0]?.requirement.category_source ?? "document_table";
  const showSource = source !== "document_table";

  return (
    <section className="panel overflow-hidden">
      <header className="flex flex-wrap items-center gap-2 border-b border-neutral-100 px-3 py-1.5">
        <span className="text-[9px] font-semibold uppercase tracking-wider text-neutral-400">
          {categoryFilterLabel}
        </span>
        <h3 className="text-[13px] font-semibold text-ink-900">{title}</h3>
        <span className="pill border-neutral-200 bg-neutral-50 text-neutral-500">
          {rows.length}건
        </span>
        {showSource && (
          <span
            className="pill border-amber-200 bg-amber-50 text-amber-800"
            title="원문 「요건 구분」 조견표가 아닌 자동 부여 분류"
          >
            {categorySourceLabel(source)}
          </span>
        )}
      </header>

      <div className="sheet-scroll">
        <table className="sheet-table">
          <thead>
            <tr>
              <th className="col-sticky w-[5.5rem]">코드</th>
              <th className="min-w-[15rem]">요구사항</th>
              {flags.subcategory && <th className="min-w-[7rem]">소분류</th>}
              {flags.deliverables && <th className="min-w-[9rem]">산출물</th>}
              {flags.source && <th className="w-[5.5rem]">출처</th>}
              {flags.solutions && <th className="min-w-[8rem]">KT 보유 기술</th>}
              {flags.missing && <th className="min-w-[7rem]">부족 기술</th>}
              <th className="w-[5.5rem]">AI 판정</th>
              {flags.aiReason && <th className="min-w-[13rem]">AI 설명</th>}
              {flags.consortium && <th className="min-w-[8rem]">컨소시엄</th>}
              <th className="w-[7rem]">판정</th>
              <th className="min-w-[7rem]">메모</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <SheetRow
                key={v.requirement.id}
                view={v}
                flags={flags}
                editorId={editorId}
                remoteUpdate={remoteByReqId[v.requirement.id] ?? null}
                totalCols={colCount(flags)}
                onOpenPage={onOpenPage}
                onOpenTable={onOpenTable}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SheetRow({
  view,
  flags,
  editorId,
  remoteUpdate,
  totalCols,
  onOpenPage,
  onOpenTable,
}: {
  view: RequirementView;
  flags: ColumnFlags;
  editorId: string;
  remoteUpdate: { mark: Judgement; note: string; editor_id: string } | null;
  totalCols: number;
  onOpenPage?: (page: number) => void;
  onOpenTable?: (tableIndex: number, anchor?: string) => void;
}) {
  const { requirement: r, recommendation: rec } = view;
  const [mark, setMark] = useState<Judgement>(view.judgement?.mark ?? "");
  const [note, setNote] = useState<string>(view.judgement?.note ?? "");
  const [saving, setSaving] = useState<"idle" | "saving" | "saved" | "err" | "remote">("idle");
  const [expanded, setExpanded] = useState(false);
  const [verdictOpen, setVerdictOpen] = useState(false);

  useEffect(() => {
    if (!remoteUpdate) return;
    if (remoteUpdate.editor_id === editorId) return;
    setMark(remoteUpdate.mark);
    setNote(remoteUpdate.note);
    setSaving("remote");
    const t = setTimeout(() => setSaving("idle"), 1500);
    return () => clearTimeout(t);
  }, [remoteUpdate, editorId]);

  async function commit(nextMark: Judgement, nextNote: string) {
    setSaving("saving");
    try {
      await patchJudgement(r.id, nextMark, nextNote, editorId);
      setSaving("saved");
      setTimeout(() => setSaving("idle"), 800);
    } catch {
      setSaving("err");
    }
  }

  const body = formatRequirementDetail(r.detail || r.name);
  const pageNum = toPageNumber(r.source_page);
  const tableIndex = typeof r.source_table_index === "number" ? r.source_table_index : null;
  const badge = rec ? riskBadge(rec.ai_risk) : riskBadge("");
  const matchedSkus = rec?.matched_solution_skus ?? [];
  const matchedNames = rec?.matched_solutions ?? [];

  return (
    <>
      <tr className="sheet-row">
        <td className="col-sticky font-mono text-[11px] text-neutral-500">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 text-left hover:text-ink-900"
            title="상세 펼치기/접기"
          >
            <span className="text-neutral-300">{expanded ? "▾" : "▸"}</span>
            <span className="truncate">{r.code || "—"}</span>
          </button>
        </td>

        <td>
          <p className="font-medium leading-snug text-ink-900">{body.title || r.name}</p>
          {body.bullets.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {body.bullets.slice(0, expanded ? body.bullets.length : 2).map((b, i) => (
                <li key={i} className="flex gap-1 text-[11px] text-neutral-600">
                  <span className="shrink-0 text-neutral-300">•</span>
                  <span>{b}</span>
                </li>
              ))}
              {!expanded && body.bullets.length > 2 && (
                <li className="text-[11px] text-neutral-400">
                  외 {body.bullets.length - 2}개 · 펼치기 ▸
                </li>
              )}
            </ul>
          )}
        </td>

        {flags.subcategory && (
          <td className="text-[11px] text-neutral-600">{r.subcategory || "—"}</td>
        )}

        {flags.deliverables && (
          <td className="text-[11px] leading-snug text-neutral-600">{r.deliverables || "—"}</td>
        )}

        {flags.source && (
          <td className="text-[11px] text-neutral-500">
            {pageNum != null && onOpenPage ? (
              <button
                type="button"
                onClick={() => onOpenPage(pageNum)}
                className="pill border-ktred-200 bg-ktred-50 font-medium text-ktred-700 transition hover:border-ktred-300 hover:bg-ktred-100"
                title={`원본 ${pageNum}페이지로 이동`}
              >
                p.{pageNum} ↗
              </button>
            ) : tableIndex != null && onOpenTable ? (
              <button
                type="button"
                onClick={() => onOpenTable(tableIndex, r.name || body.title)}
                className="pill border-ktred-200 bg-ktred-50 font-medium text-ktred-700 transition hover:border-ktred-300 hover:bg-ktred-100"
                title="원본 문서에서 이 요건이 나온 표로 이동"
              >
                원문 ↗
              </button>
            ) : pageNum != null ? (
              <span>p.{pageNum}</span>
            ) : null}
            {(pageNum != null || (tableIndex != null && onOpenTable)) && r.source_section ? " " : ""}
            {r.source_section ? (
              <span title={r.source_section}>{r.source_section.slice(0, 24)}</span>
            ) : (
              ""
            )}
            {pageNum == null && tableIndex == null && !r.source_section ? "—" : ""}
          </td>
        )}

        {flags.solutions && (
          <td>
            {matchedSkus.length > 0 || matchedNames.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {matchedSkus.length > 0
                  ? matchedSkus.map((s) => (
                      <span
                        key={s.catalog_id}
                        className="pill border-ktteal-200 bg-ktteal-50 text-ktteal-600"
                      >
                        {s.sku_label || s.solution_name}
                      </span>
                    ))
                  : matchedNames.map((t, i) => (
                      <span
                        key={`${t}-${i}`}
                        className="pill border-ktteal-200 bg-ktteal-50 text-ktteal-600"
                      >
                        {t}
                      </span>
                    ))}
              </div>
            ) : (
              <span className="text-[11px] text-neutral-400">—</span>
            )}
          </td>
        )}

        {flags.missing && (
          <td>
            {(rec?.missing_tech?.length ?? 0) > 0 ? (
              <div className="flex flex-wrap gap-1">
                {rec!.missing_tech.map((t, i) => (
                  <span
                    key={`${t}-${i}`}
                    className="pill border-ktred-200 bg-ktred-50 text-ktred-700"
                  >
                    {t}
                  </span>
                ))}
              </div>
            ) : (
              <span className="text-[11px] text-neutral-400">—</span>
            )}
          </td>
        )}

        <td>
          <button
            type="button"
            onClick={() => rec && setVerdictOpen(true)}
            disabled={!rec}
            className={`pill ${badge.cls} ${rec ? "cursor-pointer transition hover:brightness-95" : "cursor-default"}`}
            title={rec ? "클릭 — 판정 근거·검색 내역 보기" : "AI 분석 대기"}
          >
            {rec ? `${rec.ai_risk || "?"} ${badge.label}` : "대기"}
            {rec && <span className="ml-0.5 opacity-60">ⓘ</span>}
          </button>
          <AiVerdictModal
            open={verdictOpen}
            onClose={() => setVerdictOpen(false)}
            requirement={r}
            recommendation={rec}
          />
        </td>

        {flags.aiReason && (
          <td className="text-[11px] leading-relaxed text-neutral-700">
            {rec?.ai_reason ? (
              expanded ? (
                rec.ai_reason
              ) : (
                <span className="line-clamp-2">{rec.ai_reason}</span>
              )
            ) : (
              <span className="text-neutral-400">분석 중</span>
            )}
          </td>
        )}

        {flags.consortium && (
          <td className="text-[11px] text-neutral-600">{rec?.consortium_need || "—"}</td>
        )}

        <td>
          <JudgementToggle
            variant="square"
            size="compact"
            value={mark}
            onChange={(next) => {
              setMark(next);
              void commit(next, note);
            }}
          />
        </td>

        <td>
          <div className="flex items-center gap-1">
            <input
              className="min-w-[6rem] flex-1 rounded-md border border-neutral-200 bg-white px-1.5 py-1 text-[11px] placeholder:text-neutral-400 focus:border-ktred-400 focus:outline-none focus:ring-1 focus:ring-ktred-100"
              value={note}
              placeholder="메모"
              onChange={(e) => setNote(e.target.value)}
              onBlur={() => void commit(mark, note)}
            />
            <span className="w-3 shrink-0 text-[10px]">
              {saving === "saving" && "…"}
              {saving === "saved" && <span className="text-ktteal-600">✓</span>}
              {saving === "remote" && <span className="text-ktred-600">↻</span>}
              {saving === "err" && <span className="text-ktred-600">!</span>}
            </span>
          </div>
        </td>
      </tr>

      {expanded && (rec?.catalog_audit?.length || matchedSkus.length) ? (
        <tr>
          <td className="col-sticky bg-white" />
          <td colSpan={totalCols - 1} className="bg-neutral-50/60">
            <CatalogAuditPanel
              audit={rec?.catalog_audit ?? []}
              matchedSolutions={matchedSkus}
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}

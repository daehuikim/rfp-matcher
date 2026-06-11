"use client";

import { assetUrl, type Requirement } from "@/lib/api";
import { formatRequirementDetail, isPipeTable, parsePipeTable } from "@/lib/formatDetail";

const PLACEHOLDER_MARKERS = ["[표]", "[관련 화면(안)]"];

function isImagePlaceholder(detail: string): boolean {
  const t = detail.trim();
  return PLACEHOLDER_MARKERS.some((m) => t === m || t.startsWith(m));
}

export function RequirementDetailContent({
  docId,
  requirement,
  expanded = true,
  bulletLimit,
}: {
  docId: string;
  requirement: Pick<Requirement, "detail" | "name" | "detail_images">;
  expanded?: boolean;
  bulletLimit?: number;
}) {
  const detail = (requirement.detail || requirement.name || "").trim();
  const images = requirement.detail_images ?? [];

  if (isPipeTable(detail)) {
    const table = parsePipeTable(detail);
    if (table) {
      return (
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse text-[11px]">
            <thead>
              <tr>
                {table.headers.map((h, i) => (
                  <th
                    key={i}
                    className="border border-neutral-200 bg-neutral-50 px-2 py-1 text-left font-semibold text-neutral-700"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className="border border-neutral-100 px-2 py-1 align-top text-neutral-800"
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
  }

  const body = formatRequirementDetail(detail);
  const showText = detail && !(isImagePlaceholder(detail) && images.length > 0);
  const limit = bulletLimit ?? (expanded ? undefined : 2);

  return (
    <div className="space-y-2">
      {showText && body.title && (
        <p className="font-medium leading-snug text-ink-900">{body.title}</p>
      )}
      {showText && body.bullets.length > 0 && (
        <ul className="space-y-0.5">
          {body.bullets.slice(0, limit ?? body.bullets.length).map((b, i) => (
            <li key={i} className="flex gap-1 text-[11px] text-neutral-600">
              <span className="shrink-0 text-neutral-300">•</span>
              <span>{b}</span>
            </li>
          ))}
          {!expanded && limit != null && body.bullets.length > limit && (
            <li className="text-[11px] text-neutral-400">
              외 {body.bullets.length - limit}개 · 펼치기 ▸
            </li>
          )}
        </ul>
      )}
      {isImagePlaceholder(detail) && images.length === 0 && (
        <p className="text-[11px] italic text-neutral-400">{detail}</p>
      )}
      {images.length > 0 && (
        <div className="flex flex-col gap-2">
          {isImagePlaceholder(detail) && (
            <p className="text-[10px] font-medium text-neutral-500">{detail}</p>
          )}
          {images.map((rel, i) => (
            <div
              key={`${rel}-${i}`}
              className="relative max-w-full overflow-hidden rounded-lg border border-neutral-200 bg-white"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={assetUrl(docId, rel)}
                alt={detail || "요건 첨부"}
                className="h-auto max-h-80 w-full object-contain"
                loading="lazy"
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

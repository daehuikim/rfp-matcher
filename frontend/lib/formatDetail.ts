/** PDF/HTML 추출 본문 — 문장 중간 줄바꿈만 합치고, • 불릿은 줄 단위로 유지 */
export function normalizeMidWordBreaks(text: string): string {
  return text
    .replace(/(\S)\n(?=[가-힣a-z])/g, "$1")
    .replace(/(해야)\n(합니다\.?)/g, "$1 $2");
}

export type FormattedDetail = {
  title: string;
  bullets: string[];
};

/**
 * 인라인 `제목 • 항목1 • 항목2` 또는 줄 단위 `•` 불릿을 구조화.
 */
export function formatRequirementDetail(text: string): FormattedDetail {
  const normalized = normalizeMidWordBreaks(text).trim();
  if (!normalized) return { title: "", bullets: [] };

  if (normalized.includes("•")) {
    const parts = normalized
      .split(/\s*•\s*/)
      .map((p) => p.trim())
      .filter(Boolean);
    if (parts.length > 1) {
      return { title: parts[0], bullets: parts.slice(1) };
    }
  }

  const lines = normalized.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  if (lines.length > 1 && lines.some((l) => l.startsWith("•"))) {
    const title = lines.find((l) => !l.startsWith("•")) ?? lines[0];
    const bullets = lines
      .filter((l) => l.startsWith("•"))
      .map((l) => l.replace(/^•\s*/, "").trim());
    return { title: title.replace(/^•\s*/, "").trim(), bullets };
  }

  return { title: normalized, bullets: [] };
}

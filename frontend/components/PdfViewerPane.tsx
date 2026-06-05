"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { documentPreviewUrl } from "@/lib/api";

/**
 * 우측 원본 뷰어 — 브라우저 내장 뷰어(iframe)로 미리보기를 표시.
 * 서버 /preview 가 포맷에 맞춰 PDF(원본·변환) 또는 변환 HTML 을 내려준다.
 *
 * - PDF(kind="pdf"): 조견표 source_page → `#page=N` 으로 이동(jump마다 iframe 재마운트).
 * - HTML(kind="html"): 조견표 source_table_index → iframe 내 N번째 <table> 로
 *   스크롤 + 하이라이트(같은 출처라 contentDocument 접근 가능, 재마운트 없이 이동).
 */
export function PdfViewerPane({
  docId,
  kind,
  page,
  tableIndex,
  jumpNonce,
  sourceFilename,
  onClose,
}: {
  docId: string;
  kind: "pdf" | "html";
  page: number | null;
  tableIndex: number | null;
  jumpNonce: number;
  sourceFilename?: string | null;
  onClose?: () => void;
}) {
  const previewUrl = documentPreviewUrl(docId);
  const isPdf = kind === "pdf";
  const pageHash =
    isPdf && page != null ? `#page=${page}&view=FitH&toolbar=1&navpanes=0` : "";
  const src = `${previewUrl}${pageHash}`;
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadedNonce, setLoadedNonce] = useState(0); // iframe load 신호 (html scroll 트리거)
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  // HTML 모드: 지정한 <table> 로 스크롤 + 하이라이트
  const scrollToTable = useCallback((idx: number) => {
    const frame = iframeRef.current;
    if (!frame) return false;
    let doc: Document | null = null;
    try {
      doc = frame.contentDocument;
    } catch {
      return false; // cross-origin (이론상 동일 출처라 발생 안 함)
    }
    if (!doc || !doc.body) return false;
    const tables = doc.querySelectorAll("table");
    const el = tables[idx] as HTMLElement | undefined;
    if (!el) return false;
    // 변환 표는 매우 클 수 있어(레이아웃 표) 'start'로 상단에 맞춰야 유용함
    el.scrollIntoView({ behavior: "auto", block: "start" });
    // 상단에 약간 여백(헤더 가림 방지)
    try {
      const win = el.ownerDocument.defaultView;
      if (win && win.scrollY > 24) win.scrollBy(0, -16);
    } catch {
      /* noop */
    }
    const prevOutline = el.style.outline;
    const prevBg = el.style.backgroundColor;
    const prevTransition = el.style.transition;
    el.style.outline = "3px solid #e0282f";
    el.style.outlineOffset = "3px";
    el.style.backgroundColor = "rgba(224,40,47,0.10)";
    el.style.transition = "background-color 0.6s ease, outline-color 0.6s ease";
    window.setTimeout(() => {
      // 부드럽게 사라지도록 색만 먼저 투명 처리 후 원복
      el.style.outline = "3px solid rgba(224,40,47,0)";
      el.style.backgroundColor = prevBg;
      window.setTimeout(() => {
        el.style.outline = prevOutline;
        el.style.transition = prevTransition;
      }, 700);
    }, 2600);
    return true;
  }, []);

  // HTML 모드에서 jump(또는 최초 로드) 시 스크롤.
  // 변환 HTML(특히 DOCX)은 base64 이미지가 늦게 디코드되며 레이아웃이 계속 밀리므로,
  // ResizeObserver로 reflow가 멈출 때까지 재스크롤하고 일정 시간 뒤 정리한다.
  useEffect(() => {
    if (isPdf || tableIndex == null) return;
    let cancelled = false;
    const cleanups: Array<() => void> = [];

    const rescroll = () => {
      if (!cancelled) scrollToTable(tableIndex);
    };

    // 즉시 + 단계별 보정
    [0, 150, 400, 800].forEach((ms) => {
      const t = window.setTimeout(rescroll, ms);
      cleanups.push(() => window.clearTimeout(t));
    });

    try {
      const frame = iframeRef.current;
      const win = frame?.contentWindow as (Window & typeof globalThis) | null;
      const doc = frame?.contentDocument;
      if (win && doc?.documentElement) {
        const RO = (win as unknown as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver;
        if (RO) {
          const ro = new RO(() => rescroll());
          ro.observe(doc.documentElement);
          // reflow가 멎는 데 충분한 시간 후 관찰 종료
          const stop = window.setTimeout(() => ro.disconnect(), 3000);
          cleanups.push(() => {
            ro.disconnect();
            window.clearTimeout(stop);
          });
        }
        // 모든 리소스 로드 완료(load) 후 최종 보정
        const onLoad = () => rescroll();
        win.addEventListener("load", onLoad, { once: true });
        cleanups.push(() => win.removeEventListener("load", onLoad));
      }
    } catch {
      /* cross-origin (이론상 동일 출처라 발생 안 함) */
    }

    return () => {
      cancelled = true;
      cleanups.forEach((c) => c());
    };
  }, [isPdf, tableIndex, jumpNonce, loadedNonce, scrollToTable]);

  useEffect(() => {
    setLoadFailed(false);
  }, [jumpNonce]);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-white shadow-[0_1px_2px_rgba(26,26,26,0.04)]">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-100 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
            원본
          </span>
          {isPdf && page != null && (
            <span className="pill border-ktred-200 bg-ktred-50 text-ktred-700">p.{page}</span>
          )}
          {!isPdf && (
            <span
              className="pill border-amber-200 bg-amber-50 text-amber-800"
              title="원본을 PDF로 변환할 수 없어 변환 미리보기(HTML)로 표시 — 클릭한 요건의 표로 이동·강조합니다."
            >
              변환 미리보기
            </span>
          )}
          {sourceFilename && (
            <span className="truncate text-[11px] text-neutral-500" title={sourceFilename}>
              {sourceFilename}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <a
            href={src}
            target="_blank"
            rel="noreferrer"
            className="rounded-md px-2 py-1 text-[11px] font-medium text-neutral-500 hover:bg-neutral-50 hover:text-ink-900"
            title="새 탭에서 열기"
          >
            ↗ 새 탭
          </a>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-2 py-1 text-[11px] font-medium text-neutral-500 hover:bg-neutral-50 hover:text-ink-900"
              title="뷰어 닫기"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      <div className="relative flex-1 bg-neutral-100">
        {loadFailed ? (
          <div className="grid h-full place-items-center p-6 text-center">
            <div>
              <p className="text-sm font-medium text-ink-900">미리보기를 표시할 수 없습니다</p>
              <p className="mt-1 text-xs text-neutral-500">
                브라우저 내장 뷰어가 비활성화되어 있을 수 있습니다.
              </p>
              <a
                href={src}
                target="_blank"
                rel="noreferrer"
                className="btn-secondary mt-3 inline-flex"
              >
                새 탭에서 원본 열기 ↗
              </a>
            </div>
          </div>
        ) : (
          <iframe
            // PDF는 jump마다 재마운트(#page 적용), HTML은 안정 유지(스크롤로 이동)
            key={isPdf ? `${docId}-${jumpNonce}` : `${docId}-html`}
            ref={iframeRef}
            src={src}
            title="원본 미리보기"
            className="absolute inset-0 h-full w-full"
            onLoad={() => setLoadedNonce((n) => n + 1)}
            onError={() => setLoadFailed(true)}
          />
        )}
      </div>
    </div>
  );
}

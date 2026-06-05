"use client";

import { useEffect, useRef, useState } from "react";
import { documentPreviewUrl } from "@/lib/api";

/**
 * 우측 원본 뷰어 — 브라우저 내장 뷰어(iframe)로 미리보기를 표시.
 * 서버 /preview 가 포맷에 맞춰 PDF(원본·변환) 또는 변환 HTML 을 내려준다.
 *
 * PDF 문서는 조견표 source_page(1-based) 클릭 시 `#page=N` 으로 이동.
 * 같은 문서에서 hash만 바뀌면 내장 뷰어가 재이동하지 않으므로,
 * jump마다 nonce를 키에 넣어 iframe을 재마운트해 해당 페이지로 로드한다.
 */
export function PdfViewerPane({
  docId,
  page,
  jumpNonce,
  sourceFilename,
  isPdf = true,
  onClose,
}: {
  docId: string;
  page: number | null;
  jumpNonce: number;
  sourceFilename?: string | null;
  isPdf?: boolean;
  onClose?: () => void;
}) {
  const previewUrl = documentPreviewUrl(docId);
  // PDF만 페이지 프래그먼트 사용. 변환 PDF/HTML 폴백은 그대로 표시.
  const pageHash =
    isPdf && page != null ? `#page=${page}&view=FitH&toolbar=1&navpanes=0` : "";
  const src = `${previewUrl}${pageHash}`;
  const [loadFailed, setLoadFailed] = useState(false);
  const lastNonce = useRef(jumpNonce);

  useEffect(() => {
    if (jumpNonce !== lastNonce.current) {
      lastNonce.current = jumpNonce;
      setLoadFailed(false);
    }
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
              title="원본을 PDF로 변환할 수 없어 변환 미리보기로 표시 — 레이아웃이 원본과 다를 수 있습니다."
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
            key={`${docId}-${jumpNonce}`}
            src={src}
            title="원본 미리보기"
            className="absolute inset-0 h-full w-full"
            onError={() => setLoadFailed(true)}
          />
        )}
      </div>
    </div>
  );
}

"use client";

import { useRef, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { SampleFile, createFromSample, listSamples, uploadDocument } from "@/lib/api";

function bytesHuman(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function FileBadge({ ext }: { ext: string }) {
  const tone =
    ext === "pdf" ? "bg-rose-500" : ext === "hwpx" || ext === "hwp" ? "bg-sky-500" : "bg-indigo-500";
  return (
    <span
      className={`grid h-10 w-10 place-items-center rounded-lg ${tone} text-[10px] font-bold uppercase text-white`}
    >
      {ext}
    </span>
  );
}

export default function HomePage() {
  const router = useRouter();
  const { data: samples, error, isLoading } = useSWR<SampleFile[]>("samples", listSamples);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  async function pickSample(s: SampleFile) {
    setBusy(s.name);
    setErr("");
    try {
      const { doc_id } = await createFromSample(s.name);
      router.push(`/review/${doc_id}`);
    } catch (e) {
      setErr(`처리 실패: ${String(e)}`);
      setBusy(null);
    }
  }

  async function onUpload(file: File) {
    setBusy("upload");
    setErr("");
    try {
      const { doc_id } = await uploadDocument(file);
      router.push(`/review/${doc_id}`);
    } catch (e) {
      setErr(`업로드 실패: ${String(e)}`);
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <section className="panel mb-8 p-8">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-indigo-600">
          B2B · B2G 수주 영업
        </p>
        <h1 className="mt-2 text-3xl font-bold leading-tight tracking-tight md:text-4xl">
          RFP를 올리면{" "}
          <span className="text-indigo-600">조견표가 한 줄씩</span> 정리됩니다
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-slate-600">
          PDF·DOC·HWPX에서 요구사항 표를 추출하고, KT AI 솔루션과 매칭해 O/△/X 리스크를
          자동으로 채웁니다. 조견표가 나오면 바로 Excel로 받을 수 있습니다.
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          {["HTML 변환", "조견표 탐지", "atomic 분해", "AI 매칭", "Excel 3종"].map((t) => (
            <span key={t} className="pill border-slate-200 bg-slate-50 text-slate-600">
              {t}
            </span>
          ))}
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="panel p-6">
          <h2 className="text-sm font-semibold text-slate-900">파일 업로드</h2>
          <p className="mt-1 text-xs text-slate-500">PDF / DOC / HWPX</p>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={!!busy}
            className="mt-4 grid w-full place-items-center rounded-xl border-2 border-dashed border-indigo-200 bg-indigo-50/30 px-4 py-12 text-center transition hover:border-indigo-400 hover:bg-indigo-50/60 disabled:opacity-60"
          >
            <span className="text-sm font-medium text-indigo-700">
              {busy === "upload" ? "업로드 중…" : "클릭하여 파일 선택"}
            </span>
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.doc,.docx,.hwpx"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onUpload(f);
            }}
          />
        </section>

        <section className="panel p-6">
          <h2 className="text-sm font-semibold text-slate-900">샘플로 시연</h2>
          <p className="mt-1 text-xs text-slate-500">data/raw 폴더</p>
          {isLoading && <div className="mt-4 h-24 animate-pulse rounded-lg bg-slate-100" />}
          {error && (
            <p className="mt-4 text-sm text-rose-600">백엔드(8000) 연결을 확인하세요.</p>
          )}
          {samples && samples.length === 0 && (
            <p className="mt-4 text-sm text-slate-500">샘플 파일이 없습니다.</p>
          )}
          {samples && samples.length > 0 && (
            <ul className="mt-4 space-y-2">
              {samples.map((s) => (
                <li key={s.name}>
                  <button
                    type="button"
                    onClick={() => pickSample(s)}
                    disabled={!!busy}
                    className="flex w-full items-center gap-3 rounded-lg border border-slate-100 p-3 text-left transition hover:border-indigo-200 hover:bg-indigo-50/40 disabled:opacity-60"
                  >
                    <FileBadge ext={s.ext} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{s.display}</p>
                      <p className="text-[11px] text-slate-400">{bytesHuman(s.size_bytes)}</p>
                    </div>
                    <span className="text-xs text-indigo-600">
                      {busy === s.name ? "…" : "→"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {err && (
        <p className="panel mt-6 border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{err}</p>
      )}
    </div>
  );
}

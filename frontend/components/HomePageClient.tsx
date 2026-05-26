"use client";

import Link from "next/link";
import { useRef, useState, startTransition } from "react";
import { useRouter } from "next/navigation";
import { SampleFile, uploadDocument } from "@/lib/api";
import { useWorkspace } from "@/context/WorkspaceProvider";

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
      className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg ${tone} text-[10px] font-bold uppercase text-white`}
    >
      {ext}
    </span>
  );
}

function SampleCard({ sample }: { sample: SampleFile }) {
  return (
    <Link
      href={`/demo?sample=${encodeURIComponent(sample.name)}`}
      className="flex h-full min-h-[88px] flex-col justify-between rounded-xl border border-slate-100 bg-white p-4 text-left transition hover:border-indigo-200 hover:bg-indigo-50/40"
    >
      <div className="flex items-start gap-3">
        <FileBadge ext={sample.ext} />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm font-medium leading-snug text-slate-900">{sample.display}</p>
          <p className="mt-1 text-[11px] text-slate-400">{bytesHuman(sample.size_bytes)}</p>
        </div>
      </div>
      <span className="mt-3 text-xs font-medium text-indigo-600">클릭하여 시연 →</span>
    </Link>
  );
}

function pickGridSamples(samples: SampleFile[]): SampleFile[] {
  const featured = samples.filter((s) => s.featured);
  return (featured.length > 0 ? featured : samples).slice(0, 6);
}

const START_ERRORS: Record<string, string> = {
  "sample-missing": "샘플 파일명이 없습니다.",
  "sample-start": "샘플 시연을 시작하지 못했습니다. 백엔드(8000)와 data/raw 파일을 확인하세요.",
  "session-expired": "프로젝트 세션이 만료되었습니다. 아래 샘플을 다시 시작하세요.",
  "cache-missing": "캐시가 없습니다. 홈에서 샘플을 다시 실행하세요.",
};

type Props = {
  initialSamples: SampleFile[];
  startError?: string;
};

export default function HomePageClient({ initialSamples, startError }: Props) {
  const router = useRouter();
  const { backendReachable } = useWorkspace();
  const gridSamples = pickGridSamples(initialSamples);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState(startError ? START_ERRORS[startError] ?? "시연 시작 실패" : "");
  const fileRef = useRef<HTMLInputElement | null>(null);

  async function onUpload(file: File) {
    setBusy("upload");
    setErr("");
    try {
      const { doc_id } = await uploadDocument(file);
      startTransition(() => {
        router.push(`/review/${doc_id}`);
      });
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

      <section className="panel mb-8 p-6">
        <h2 className="text-sm font-semibold text-slate-900">파일 업로드</h2>
        <p className="mt-1 text-xs text-slate-500">PDF / DOC / HWPX</p>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={!!busy}
          className="mt-4 grid w-full place-items-center rounded-xl border-2 border-dashed border-indigo-200 bg-indigo-50/30 px-4 py-10 text-center transition hover:border-indigo-400 hover:bg-indigo-50/60 disabled:opacity-60"
        >
          <span className="text-sm font-medium text-indigo-700">
            {busy === "upload" ? "업로드 중…" : "클릭하여 파일 선택"}
          </span>
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.doc,.docx,.hwp,.hwpx"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void onUpload(f);
          }}
        />
      </section>

      <section className="panel p-6">
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">샘플로 시연</h2>
            <p className="mt-1 text-xs text-slate-500">data/raw 폴더 · PoC RFP 6종</p>
          </div>
          {gridSamples.length > 0 && (
            <span className="text-[11px] text-slate-400">{gridSamples.length}개</span>
          )}
        </div>

        {gridSamples.length === 0 && (
          <p className="mt-4 text-sm text-slate-500">
            {!backendReachable
              ? "백엔드(8000)에 연결되지 않았습니다. backend에서 uvicorn을 실행한 뒤 새로고침하세요."
              : "data/raw에 샘플 파일을 넣으면 여기에 2×3 그리드로 표시됩니다."}
          </p>
        )}

        {gridSamples.length > 0 && (
          <div className="mt-4 grid grid-cols-2 gap-3">
            {gridSamples.map((s) => (
              <SampleCard key={s.name} sample={s} />
            ))}
          </div>
        )}
      </section>

      {err && (
        <p className="panel mt-6 border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{err}</p>
      )}
    </div>
  );
}

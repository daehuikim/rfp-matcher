#!/usr/bin/env python3
"""
추출 파이프라인 단계별 소요 시간 측정.

사용:
  cd backend && source .venv/bin/activate
  PYTHONPATH=. python scripts/benchmark_extraction.py ../data/raw/하나.pdf
  PYTHONPATH=. python scripts/benchmark_extraction.py ../data/raw/하나.pdf --llm fake
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# 프로젝트 config/.env 로드
ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "config" / ".env").exists():
    for line in (ROOT / "config" / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from app.core.config import get_settings  # noqa: E402
from app.core.container import build_container  # noqa: E402
from app.domain.enums import DocumentMime, PipelineStage  # noqa: E402
from app.phase1.converters.registry import build_pdf_converter, select_converter  # noqa: E402
from app.phase1.extraction.classifier import select_classifier  # noqa: E402
from app.phase1.extraction.row_atomizer import ParagraphAtomizer, RowAtomizer  # noqa: E402
from app.phase1.extraction.table_locator import TableLocator  # noqa: E402
from app.phase1.loaders.base import select_loader  # noqa: E402
from app.services.event_bus import EventBus  # noqa: E402
from app.services.pipeline import Pipeline  # noqa: E402


def _fmt(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}m {s % 60}s ({ms}ms)"


async def benchmark(path: Path, llm_provider: str) -> None:
    get_settings.cache_clear()
    os.environ["LLM_PROVIDER"] = llm_provider
    settings = get_settings()

    print(f"\n{'=' * 60}")
    print(f"파일: {path.name} ({path.stat().st_size // 1024} KB)")
    print(f"PDF 컨버터: {settings.pdf_converter}")
    print(f"LLM: {llm_provider}")
    print(f"{'=' * 60}\n")

    bus = EventBus()
    pipeline = Pipeline(bus)
    timings: list[tuple[str, int, int]] = []

    container = await build_container()
    container.event_bus = bus

    loader = select_loader(path)
    t0 = time.perf_counter()
    document = await loader.load(path)
    load_ms = int((time.perf_counter() - t0) * 1000)
    print(f"{'로더':20} {_fmt(load_ms)}")

    async def _listen(doc_id: str) -> None:
        async for ev in bus.subscribe(doc_id):
            if ev.stage == PipelineStage.FAILED:
                break
            ms = int(ev.payload.get("elapsed_ms", 0))
            total = int(ev.payload.get("elapsed_total_ms", 0))
            timings.append((ev.stage.value, ms, total))

    listener = asyncio.create_task(_listen(document.id))

    await pipeline.emit(document.id, PipelineStage.UPLOADED)

    # 1) 변환
    await pipeline.emit(document.id, PipelineStage.CONVERTING)
    t0 = time.perf_counter()
    out_dir = settings.storage_root / "bench" / document.id
    if document.mime == DocumentMime.PDF:
        converter = build_pdf_converter(settings)
    else:
        converter = select_converter(document.mime, settings)
    html_doc = await converter.convert(document, out_dir)
    convert_ms = int((time.perf_counter() - t0) * 1000)
    print(
        f"{'HTML 변환':20} {_fmt(convert_ms)}  "
        f"(tables={html_doc.table_count}, paragraphs={html_doc.paragraph_count})"
    )
    await pipeline.emit(
        document.id,
        PipelineStage.CONVERTED,
        payload={"tables": html_doc.table_count},
    )

    # 2) 탐지
    await pipeline.emit(document.id, PipelineStage.LOCATING)
    t0 = time.perf_counter()
    refs = await TableLocator(container.llm).locate(document.id, html_doc.html_path)
    locate_ms = int((time.perf_counter() - t0) * 1000)
    print(f"{'조견표 탐지':20} {_fmt(locate_ms)}  (tables={len(refs)})")
    await pipeline.emit(document.id, PipelineStage.LOCATED, payload={"tables": len(refs)})

    # 3) atomic
    await pipeline.emit(document.id, PipelineStage.ATOMIZING)
    t0 = time.perf_counter()
    atoms = []
    if refs:
        atomizer = RowAtomizer(container.llm)
        parts = await asyncio.gather(
            *[atomizer.atomize(document.id, html_doc.html_path, ref) for ref in refs]
        )
        for p in parts:
            atoms.extend(p)
    else:
        atoms = await ParagraphAtomizer().atomize(document.id, html_doc.html_path)
    atom_ms = int((time.perf_counter() - t0) * 1000)
    print(f"{'atomic 분해':20} {_fmt(atom_ms)}  (atoms={len(atoms)})")
    await pipeline.emit(document.id, PipelineStage.ATOMIZED, payload={"atoms": len(atoms)})

    # 4) 분류
    await pipeline.emit(document.id, PipelineStage.CLASSIFYING)
    t0 = time.perf_counter()
    classifier = select_classifier(atoms, container.llm)
    categories = await classifier.classify(atoms)
    class_ms = int((time.perf_counter() - t0) * 1000)
    print(
        f"{'분류 ({})'.format(type(classifier).__name__):20} {_fmt(class_ms)}  "
        f"(categories={len(set(categories))})"
    )
    await pipeline.emit(document.id, PipelineStage.CLASSIFIED)
    await pipeline.emit(
        document.id,
        PipelineStage.READY_FOR_REVIEW,
        payload={"requirements": len(atoms)},
    )

    total_ms = sum(x[1] for x in timings) + load_ms
    print(f"\n{'─' * 60}")
    print(f"추출 합계(측정):     {_fmt(convert_ms + locate_ms + atom_ms + class_ms)}")
    print(f"병목:               ", end="")
    stages = [
        ("HTML 변환", convert_ms),
        ("조견표 탐지", locate_ms),
        ("atomic 분해", atom_ms),
        ("분류", class_ms),
    ]
    bottleneck = max(stages, key=lambda x: x[1])
    print(f"{bottleneck[0]} ({_fmt(bottleneck[1])})")

    if settings.pdf_converter == "docling":
        print(
            "\n⚠ Docling은 layout/OCR ML 모델(CPU/MPS)을 매 forward마다 돌려 느립니다.\n"
            "  → config/.env 에 PDF_CONVERTER=pdfplumber 로 바꾸면 보통 10~50배 빠릅니다.\n"
            "  pdf2htmlEX는 macOS 네이티브 설치가 까다로워 pdfplumber를 기본으로 둡니다."
        )
    else:
        print(
            "\n✓ pdfplumber 사용 중 — GPU/MPS 불필요, 순수 Python 표 추출.\n"
            "  느리면 atomic 분해·분류 단계의 OpenAI API 호출이 병목일 가능성이 큽니다."
        )

    listener.cancel()
    try:
        await listener
    except asyncio.CancelledError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="추출 파이프라인 벤치마크")
    parser.add_argument("file", type=Path, help="PDF/DOC/HWPX 경로")
    parser.add_argument("--llm", default=os.environ.get("LLM_PROVIDER", "openai"))
    args = parser.parse_args()
    if not args.file.is_file():
        sys.exit(f"파일 없음: {args.file}")
    asyncio.run(benchmark(args.file.resolve(), args.llm))


if __name__ == "__main__":
    main()

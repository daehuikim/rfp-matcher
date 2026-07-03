from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features

logger = logging.getLogger(__name__)


def _resolve_hwp5html(explicit: str | None) -> str:
    """hwp5html 실행파일 경로 — PATH 미등록(venv 미활성)이어도 venv/bin 에서 찾는다.

    BE 를 `venv/bin/uvicorn` 으로 (활성화 없이) 띄우면 venv/bin 이 PATH 에 없어
    shutil.which 가 실패한다. pip 가 설치한 venv/bin/hwp5html 을 폴백으로 본다.
    """
    if explicit:
        return explicit
    found = shutil.which("hwp5html")
    if found:
        return found
    venv_bin = Path(sys.executable).parent / "hwp5html"  # pip 설치 위치(현재 인터프리터 옆)
    if venv_bin.is_file():
        return str(venv_bin)
    return "hwp5html"


class HwpConverter(HtmlConverter):
    """구형 .hwp → HTML (pyhwp `hwp5html`). macOS LibreOffice는 .hwp 로드 불가."""

    def __init__(self, hwp5html_bin: str | None = None) -> None:
        self._bin = _resolve_hwp5html(hwp5html_bin)

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        work = out_dir / "_hwp5"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            self._bin,
            "--output",
            str(work),
            str(document.src_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"hwp5html 변환 실패 (rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:400]}"
            )

        produced = work / "index.xhtml"
        if not produced.is_file():
            raise RuntimeError(
                f"hwp5html 출력 없음: {produced}; stdout={stdout!r}"
            )

        target = out_dir / f"{document.id}.html"
        shutil.copyfile(produced, target)
        html_text = target.read_text(encoding="utf-8", errors="replace")
        _, paragraphs = count_html_features(html_text)
        logger.info(
            "HWP→HTML 완료 doc=%s tables=%d paragraphs≈%d",
            document.id,
            html_text.count("<table"),
            paragraphs,
        )
        return HtmlDoc(
            doc_id=document.id,
            html_path=target,
            table_count=html_text.count("<table"),
            paragraph_count=paragraphs,
        )

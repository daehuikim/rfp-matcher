from __future__ import annotations

import shutil
from pathlib import Path


def resolve_soffice_bin(explicit: str | None = None) -> str:
    """
    LibreOffice headless 실행 파일 경로.

    macOS 앱 번들·PATH 모두 탐색. 없으면 FileNotFoundError(한국어 안내).
    """
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return str(path)
        raise FileNotFoundError(f"soffice 경로 없음: {explicit}")

    candidates: list[str | None] = [
        shutil.which("soffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/opt/homebrew/bin/soffice",
        "/usr/local/bin/soffice",
        "/usr/bin/soffice",
        shutil.which("libreoffice"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c

    raise FileNotFoundError(
        "LibreOffice(soffice)가 설치되어 있지 않습니다. "
        "DOC/DOCX/구형 .hwp 변환에 필요합니다. "
        "macOS: `brew install --cask libreoffice` 설치 후 백엔드를 재시작하세요. "
        "HWPX(.hwpx)는 LibreOffice 없이도 동작합니다."
    )

from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
from pathlib import Path


class OpenDataLoaderError(RuntimeError):
    """Raised when OpenDataLoader execution or output handling fails."""


def ensure_opendataloader_available() -> None:
    if sys.version_info < (3, 10):
        raise OpenDataLoaderError(
            "OpenDataLoader PDF는 Python 3.10+가 필요합니다. "
            f"현재 인터프리터는 Python {sys.version_info.major}.{sys.version_info.minor} 입니다."
        )

    try:
        importlib.import_module("opendataloader_pdf")
    except ImportError as exc:
        raise OpenDataLoaderError(
            "OpenDataLoader PDF 패키지를 찾지 못했습니다. "
            "`pip install -U opendataloader-pdf` 후 다시 실행해주세요."
        ) from exc


def run_opendataloader(pdf_path: Path, output_root: Path, use_hybrid: bool = False) -> Path:
    ensure_opendataloader_available()
    module = importlib.import_module("opendataloader_pdf")
    output_root.mkdir(parents=True, exist_ok=True)

    kwargs: dict[str, object] = {
        "input_path": [str(pdf_path)],
        "output_dir": str(output_root),
        "format": "html,markdown",
    }
    if use_hybrid:
        kwargs["hybrid"] = "docling-fast"

    try:
        # Some OpenDataLoader/pdf stack components write progress logs directly to
        # stdout/stderr. In Streamlit or redirected execution environments, that
        # can surface as BrokenPipeError. Capture those streams during conversion.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module.convert(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise OpenDataLoaderError(f"OpenDataLoader 실행에 실패했습니다: {exc}") from exc

    return output_root


def _find_first(pattern: str, root: Path) -> Path | None:
    matches = sorted(root.rglob(pattern))
    return matches[0] if matches else None


def locate_outputs(output_root: Path) -> dict[str, Path | None]:
    return {
        "html": _find_first("*.html", output_root),
        "markdown": _find_first("*.md", output_root),
        "txt": _find_first("*.txt", output_root),
    }


def load_json_output(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("elements", "content", "blocks", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []

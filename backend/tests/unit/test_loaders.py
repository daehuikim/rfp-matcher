from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.enums import DocumentMime
from app.phase1.loaders.base import select_loader


@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        (".pdf", DocumentMime.PDF),
        (".doc", DocumentMime.DOC),
        (".docx", DocumentMime.DOCX),
        (".hwp", DocumentMime.HWP),
        (".hwpx", DocumentMime.HWPX),
    ],
)
def test_select_loader_maps_extension_to_mime(ext: str, expected: DocumentMime) -> None:
    loader = select_loader(Path(f"sample{ext}"))
    assert loader.mime == expected


def test_select_loader_rejects_unknown_extension() -> None:
    with pytest.raises(ValueError):
        select_loader(Path("nope.xyz"))

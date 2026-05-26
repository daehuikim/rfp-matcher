"""samples 목록 + from-sample 진입을 검증.

PoC 시연 핵심 경로: data/raw 안에 있는 파일을 클릭으로 선택해 파이프라인을 시작.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_list_samples_returns_supported_files(tmp_path, monkeypatch) -> None:
    # raw 디렉토리 격리 — 진짜 data/raw에 의존하지 않게 임시 폴더로 override
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "test.pdf").write_bytes(b"%PDF-1.4\n%fake")
    (raw / "test.hwpx").write_bytes(b"fake hwpx")
    (raw / "ignore.txt").write_text("not supported")
    (raw / ".hidden").write_bytes(b"")

    monkeypatch.setenv("RAW_DATA_DIR", str(raw))
    # settings는 lru_cache이므로 캐시 무효화
    from app.core import config

    config.get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/documents/samples")
        assert r.status_code == 200
        body = r.json()
        names = {s["name"] for s in body}
        assert "test.pdf" in names
        assert "test.hwpx" in names
        # 지원하지 않는 .txt, hidden은 제외
        assert "ignore.txt" not in names
        assert ".hidden" not in names
        # 각 항목 메타 점검
        pdf = next(s for s in body if s["name"] == "test.pdf")
        assert pdf["ext"] == "pdf"
        assert pdf["size_bytes"] > 0
        assert pdf["display"]


def test_from_sample_creates_document(tmp_path, monkeypatch) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    sample = raw / "sample.pdf"
    sample.write_bytes(b"%PDF-1.4\nminimal")

    monkeypatch.setenv("RAW_DATA_DIR", str(raw))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    from app.core import config

    config.get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        r = client.post("/documents/from-sample", json={"name": "sample.pdf"})
        assert r.status_code == 200
        doc_id = r.json()["doc_id"]
        assert doc_id


def test_from_sample_rejects_path_traversal(tmp_path, monkeypatch) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setenv("RAW_DATA_DIR", str(raw))
    from app.core import config

    config.get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        r = client.post("/documents/from-sample", json={"name": "../../etc/passwd"})
        assert r.status_code == 404

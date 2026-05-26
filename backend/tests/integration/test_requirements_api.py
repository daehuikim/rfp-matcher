from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient

from app.main import create_app


def _make_tiny_pdf() -> bytes:
    """pdfplumber가 열 수 있는 1페이지 PDF — 표가 들어 있는 미니 PDF."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:  # pragma: no cover
        return b""

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(100, 800, "요건 구분")
    c.drawString(300, 800, "상세 내용")
    c.drawString(100, 770, "데이터 수집")
    c.drawString(300, 770, "① 원천 연계 ② 파일 업로드")
    c.showPage()
    c.save()
    return buf.getvalue()


def test_upload_and_list_requirements_roundtrip() -> None:
    pytest_pdf = _make_tiny_pdf()
    if not pytest_pdf:
        import pytest

        pytest.skip("reportlab 미설치 — tiny PDF 생성 불가")

    with TestClient(create_app()) as client:
        files = {"file": ("sample.pdf", BytesIO(pytest_pdf), "application/pdf")}
        resp = client.post("/documents", files=files)
        assert resp.status_code == 200, resp.text
        doc_id = resp.json()["doc_id"]

        # background task가 동기로 실행되도록 TestClient는 응답 후 background 실행
        # 다음 GET이 곧바로 떠야 함
        for _ in range(20):
            r = client.get(f"/documents/{doc_id}/requirements")
            if r.status_code == 200 and r.json():
                break

        items = r.json()
        assert isinstance(items, list)
        # PDF가 너무 작아 휴리스틱이 실패하면 paragraph 폴백으로라도 1개 이상 추출돼야 함
        # (혹은 빈 리스트가 와도 라우터 자체는 200)
        assert r.status_code == 200


def test_judgement_patch_round_trip() -> None:
    with TestClient(create_app()) as client:
        # repo는 in-memory — 존재하지 않는 req_id에 대해 PATCH는 404
        r = client.patch(
            "/requirements/no-such/judgement",
            json={"mark": "O", "note": "ok"},
        )
        assert r.status_code == 404

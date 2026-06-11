from __future__ import annotations

import json
from pathlib import Path

from app.services.pipeline_logger import PipelineLogSession, write_step_readme


def test_pipeline_log_session_writes_steps(tmp_path: Path) -> None:
    src = tmp_path / "input.pdf"
    src.write_bytes(b"%PDF-1.4")
    raw_html = tmp_path / "raw.html"
    raw_html.write_text("<html><body><p>raw</p></body></html>", encoding="utf-8")
    post_html = tmp_path / "out.html"
    post_html.write_text("<html><body><p>clean</p></body></html>", encoding="utf-8")
    xlsx = tmp_path / "req.xlsx"
    xlsx.write_bytes(b"xlsx")

    session = PipelineLogSession(
        tmp_path / "logs",
        run_id="test-run",
        source_path=src,
        engine="v2",
    )
    session.record_source()
    session.record_convert_raw(converter="opendataloader", raw_html=raw_html, input_ref=src)
    session.record_convert_postprocessed(post_html=post_html, raw_html=raw_html, converter="test")
    session.record_extract(mode="llm", pipeline_steps=["step-a", "step-b"], row_count=3)
    session.record_output(xlsx_path=xlsx, report={"extracted_rows": 3})
    manifest_path = session.finalize()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["run_id"] == "test-run"
    assert len(data["steps"]) == 5
    assert (tmp_path / "logs" / "test-run" / "steps" / "00_source" / src.name).is_file()
    assert (tmp_path / "logs" / "test-run" / "steps" / "04_output" / "requirements.xlsx").is_file()

    write_step_readme(session.log_dir)
    readme = (session.log_dir / "README.md").read_text(encoding="utf-8")
    assert "01_convert_raw" in readme
    assert "step-a" in readme

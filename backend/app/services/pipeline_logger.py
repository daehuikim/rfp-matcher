"""
파이프라인 단계별 input/output 영속 로깅.

data/packages/logs/<run_id>/ 에 단계별 산출물과 pipeline.json 을 저장한다.
앱 아티팩트 캐시(data/artifacts/<hash>/)에도 logs/ 하위로 동일 구조를 복제한다.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOG_VERSION = "2"

_active_session: "PipelineLogSession | None" = None


def set_active_session(session: "PipelineLogSession | None") -> None:
    global _active_session
    _active_session = session


def get_active_session() -> "PipelineLogSession | None":
    return _active_session


def record_llm_io(
    purpose: str,
    *,
    prompt: str,
    response: Any,
    meta: dict[str, Any] | None = None,
) -> None:
    """V2 LLM 호출 시 prompt/response 스냅샷 (활성 세션 있을 때만)."""
    session = get_active_session()
    if session is not None:
        session.record_llm_call(purpose, prompt=prompt, response=response, meta=meta)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_meta(path: Path, *, role: str, step: str) -> dict[str, Any]:
    st = path.stat()
    return {
        "path": str(path),
        "name": path.name,
        "role": role,
        "step": step,
        "size_bytes": st.st_size,
        "exists": True,
    }


def _ref_meta(path: str | Path, *, role: str, step: str, note: str = "") -> dict[str, Any]:
    p = Path(path)
    meta: dict[str, Any] = {
        "path": str(p),
        "name": p.name,
        "role": role,
        "step": step,
        "exists": p.is_file(),
    }
    if p.is_file():
        meta["size_bytes"] = p.stat().st_size
    if note:
        meta["note"] = note
    return meta


@dataclass
class StepRecord:
    id: str
    name: str
    description: str
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_utc_now)


class PipelineLogSession:
    """한 문서 처리 실행의 단계별 I/O 기록."""

    def __init__(
        self,
        root: Path,
        *,
        run_id: str,
        source_path: Path | None = None,
        source_name: str = "",
        content_hash: str | None = None,
        engine: str = "v2",
    ) -> None:
        self.run_id = run_id
        self.log_dir = root / run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.source_path = source_path
        self.source_name = source_name or (source_path.name if source_path else run_id)
        self.content_hash = content_hash
        self.engine = engine
        self._steps: list[StepRecord] = []
        self._llm_calls: list[dict[str, Any]] = []
        self._started = _utc_now()

    def step_dir(self, step_id: str) -> Path:
        d = self.log_dir / "steps" / step_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def copy_file(
        self,
        src: Path,
        step_id: str,
        dest_name: str | None = None,
        *,
        role: str,
    ) -> Path | None:
        if not src.is_file():
            return None
        dest = self.step_dir(step_id) / (dest_name or src.name)
        if src.resolve() == dest.resolve():
            return dest
        shutil.copy2(src, dest)
        return dest

    def record_step(
        self,
        step_id: str,
        name: str,
        *,
        description: str = "",
        inputs: list[dict[str, Any]] | None = None,
        outputs: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._steps.append(
            StepRecord(
                id=step_id,
                name=name,
                description=description,
                inputs=inputs or [],
                outputs=outputs or [],
                meta=meta or {},
            )
        )

    def record_source(self) -> dict[str, Any] | None:
        if self.source_path is None or not self.source_path.is_file():
            return None
        dest = self.copy_file(self.source_path, "00_source", role="input")
        if dest is None:
            return None
        inp = _file_meta(dest, role="input", step="00_source")
        self.record_step(
            "00_source",
            "원본 업로드",
            description="파이프라인 입력 파일",
            inputs=[_ref_meta(self.source_path, role="input", step="00_source", note="original")],
            outputs=[inp],
            meta={"mime_guess": self.source_path.suffix.lower()},
        )
        return inp

    def record_convert_raw(
        self,
        *,
        converter: str,
        raw_html: Path | None = None,
        raw_json: Path | None = None,
        input_ref: Path | None = None,
    ) -> None:
        outputs: list[dict[str, Any]] = []
        if raw_json and raw_json.is_file():
            copied = self.copy_file(raw_json, "01_convert_raw", role="output")
            if copied:
                outputs.append(_file_meta(copied, role="output", step="01_convert_raw"))
        if raw_html and raw_html.is_file():
            copied = self.copy_file(raw_html, "01_convert_raw", role="output")
            if copied:
                outputs.append(_file_meta(copied, role="output", step="01_convert_raw"))

        inputs = []
        if input_ref and input_ref.is_file():
            inputs.append(_ref_meta(input_ref, role="input", step="01_convert_raw"))

        self.record_step(
            "01_convert_raw",
            "변환 (raw)",
            description=f"{converter} → HTML/JSON (후처리 전)",
            inputs=inputs,
            outputs=outputs,
            meta={"converter": converter},
        )

    def record_convert_postprocessed(
        self,
        *,
        post_html: Path,
        raw_html: Path | None = None,
        converter: str = "",
    ) -> None:
        outputs: list[dict[str, Any]] = []
        copied = self.copy_file(post_html, "02_convert_postprocessed", role="output")
        if copied:
            outputs.append(_file_meta(copied, role="output", step="02_convert_postprocessed"))

        inputs: list[dict[str, Any]] = []
        if raw_html and raw_html.is_file():
            raw_in_log = self.copy_file(raw_html, "02_convert_postprocessed", "raw.html", role="input")
            if raw_in_log:
                inputs.append(_file_meta(raw_in_log, role="input", step="02_convert_postprocessed"))
        elif post_html.with_suffix(".raw.html").is_file():
            raw_src = post_html.with_suffix(".raw.html")
            raw_in_log = self.copy_file(raw_src, "02_convert_postprocessed", "raw.html", role="input")
            if raw_in_log:
                inputs.append(_file_meta(raw_in_log, role="input", step="02_convert_postprocessed"))

        self.record_step(
            "02_convert_postprocessed",
            "HTML 후처리",
            description="스타일·폰트·장식 제거 후 추출용 HTML",
            inputs=inputs,
            outputs=outputs,
            meta={"converter": converter, "postprocess": "compact_html"},
        )

    def record_llm_call(
        self,
        purpose: str,
        *,
        prompt: str,
        response: Any,
        meta: dict[str, Any] | None = None,
    ) -> Path | None:
        seq = len(self._llm_calls) + 1
        slug = purpose.replace("/", "_")[:40]
        sub = self.step_dir("03_extract") / "llm" / f"{seq:03d}_{slug}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "prompt.txt").write_text(prompt, encoding="utf-8")
        if hasattr(response, "model_dump"):
            resp_obj: Any = response.model_dump()
        else:
            resp_obj = response
        (sub / "response.json").write_text(
            json.dumps(resp_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        entry = {
            "seq": seq,
            "purpose": purpose,
            "prompt_path": str(sub / "prompt.txt"),
            "response_path": str(sub / "response.json"),
            "prompt_chars": len(prompt),
            **(meta or {}),
        }
        self._llm_calls.append(entry)
        return sub

    def record_extract(
        self,
        *,
        mode: str,
        pipeline_steps: list[str],
        row_count: int,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.record_step(
            "03_extract",
            "요구사항 추출",
            description=f"V2 추출 엔진 ({mode})",
            meta={
                "mode": mode,
                "extracted_rows": row_count,
                "pipeline_steps": pipeline_steps,
                "llm_calls": self._llm_calls,
                **(meta or {}),
            },
        )

    def record_output(
        self,
        *,
        xlsx_path: Path,
        report: dict[str, Any] | None = None,
        extra: dict[str, Path] | None = None,
    ) -> None:
        outputs: list[dict[str, Any]] = []
        copied = self.copy_file(xlsx_path, "04_output", "requirements.xlsx", role="output")
        if copied:
            outputs.append(_file_meta(copied, role="output", step="04_output"))
        if extra:
            for name, p in extra.items():
                c = self.copy_file(p, "04_output", name, role="output")
                if c:
                    outputs.append(_file_meta(c, role="output", step="04_output"))

        self.record_step(
            "04_output",
            "최종 산출",
            description="요구사항 조견표 Excel",
            outputs=outputs,
            meta={"report": report or {}},
        )

    def finalize(self, *, extra: dict[str, Any] | None = None) -> Path:
        manifest = {
            "log_version": LOG_VERSION,
            "run_id": self.run_id,
            "source_name": self.source_name,
            "content_hash": self.content_hash,
            "engine": self.engine,
            "started_at": self._started,
            "finished_at": _utc_now(),
            "log_dir": str(self.log_dir),
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "inputs": s.inputs,
                    "outputs": s.outputs,
                    "meta": s.meta,
                    "ts": s.ts,
                }
                for s in self._steps
            ],
            "llm_calls": self._llm_calls,
            **(extra or {}),
        }
        path = self.log_dir / "pipeline.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("파이프라인 로그 저장: %s (%d steps)", self.log_dir, len(self._steps))
        return path

    def mirror_to(self, dest_root: Path) -> Path | None:
        """아티팩트 버킷 등 다른 위치에 동일 로그 트리 복제."""
        if not self.log_dir.is_dir():
            return None
        dest = dest_root / "logs"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.log_dir, dest)
        return dest


def write_step_readme(log_dir: Path) -> None:
    """사람이 읽기 쉬운 단계 요약 마크다운."""
    manifest_path = log_dir / "pipeline.json"
    if not manifest_path.is_file():
        return
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    lines = [
        f"# 파이프라인 로그 — {data.get('source_name', '')}",
        "",
        f"- run_id: `{data.get('run_id')}`",
        f"- engine: `{data.get('engine')}`",
        f"- started: {data.get('started_at')}",
        f"- finished: {data.get('finished_at')}",
        "",
        "## 단계별 I/O",
        "",
    ]
    for step in data.get("steps", []):
        lines.append(f"### {step['id']} {step['name']}")
        if step.get("description"):
            lines.append(f"> {step['description']}")
        lines.append("")
        if step.get("inputs"):
            lines.append("**Input**")
            for inp in step["inputs"]:
                lines.append(f"- `{inp.get('name')}` ({inp.get('size_bytes', '?')} bytes)")
            lines.append("")
        if step.get("outputs"):
            lines.append("**Output**")
            for out in step["outputs"]:
                lines.append(f"- `{out.get('name')}` ({out.get('size_bytes', '?')} bytes)")
            lines.append("")
        if step.get("meta"):
            meta = step["meta"]
            if "pipeline_steps" in meta:
                lines.append("**처리 내역**")
                for s in meta["pipeline_steps"]:
                    lines.append(f"- {s}")
                lines.append("")
            if meta.get("llm_calls"):
                lines.append("**LLM 호출**")
                for c in meta["llm_calls"]:
                    lines.append(
                        f"- `{c.get('purpose')}` — prompt {c.get('prompt_chars')} chars "
                        f"→ `{Path(c['response_path']).name}`"
                    )
                lines.append("")
            elif meta and "pipeline_steps" not in meta:
                lines.append(f"**meta**: `{json.dumps(meta, ensure_ascii=False)}`")
                lines.append("")
    (log_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")

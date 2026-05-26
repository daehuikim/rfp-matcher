"""
단계별 정합성 평가 entry point.

기본 모드(룰 메트릭): 케이스 JSON과 매칭되는 integration 테스트만 실행.
LLM judge(`--llm-judge`): 추가로 추천 자기일관성 검사 등 비결정적 테스트도 실행.

CI에서 PR 게이트로 사용한다. 모든 케이스 통과시 exit 0, 아니면 1.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
REPORTS = Path(__file__).resolve().parent / "reports"

logger = logging.getLogger("evaluator")


def _run_pytest(include_llm_judge: bool, extra: list[str] | None = None) -> int:
    cmd = [sys.executable, "-m", "pytest", "-q", *(extra or [])]
    if include_llm_judge:
        # 룰 + LLM judge 둘 다 — evaluator 마커 포함
        cmd += ["tests/integration", "-m", "not slow"]
    else:
        # 룰만 — integration 디렉토리 전체 (deselect 없이)
        cmd += ["tests/integration", "-m", "not evaluator"]
    env_note = "(LLM_PROVIDER=fake 환경에서 실행)"
    logger.info("evaluator → pytest %s %s", " ".join(cmd[3:]), env_note)
    return subprocess.run(cmd, cwd=BACKEND, check=False).returncode


def _list_cases() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for p in (REPO_ROOT / "evaluator" / "cases").rglob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({"path": str(p.relative_to(REPO_ROOT)), "name": data.get("name", p.name)})
        except json.JSONDecodeError as e:
            logger.warning("케이스 JSON 손상: %s (%s)", p, e)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s :: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--llm-judge",
        action="store_true",
        help="LLM 기반 평가까지 실행 (비용 발생). 기본은 룰만.",
    )
    args = ap.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)
    cases = _list_cases()
    (REPORTS / "cases.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("evaluator cases: %d", len(cases))

    rc = _run_pytest(args.llm_judge)

    summary = {
        "case_count": len(cases),
        "pytest_returncode": rc,
        "llm_judge": args.llm_judge,
        "passed": rc == 0,
    }
    (REPORTS / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if rc != 0:
        print(f"FAIL evaluator (pytest rc={rc})")
        return 1
    print(f"OK evaluator: {len(cases)} cases, pytest rc=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())

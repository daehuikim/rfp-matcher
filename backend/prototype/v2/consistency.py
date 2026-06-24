"""
추출 결과 정합성 검증 — recall 100% 유지하며 노이즈·메타 불일치 탐지.

파이프라인 마지막에 실행해 로그·manifest 에 이슈를 남긴다 (행 삭제 없음).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .extract import Req


@dataclass
class ConsistencyIssue:
  kind: str
  tab: str
  row_index: int
  message: str


@dataclass
class ConsistencyReport:
  issues: list[ConsistencyIssue] = field(default_factory=list)

  @property
  def ok(self) -> bool:
    return not self.issues

  def summary(self) -> dict:
    from collections import Counter

    c = Counter(i.kind for i in self.issues)
    return {"total": len(self.issues), "by_kind": dict(c)}


def validate_reqs(reqs: list[Req]) -> ConsistencyReport:
  report = ConsistencyReport()
  seen_rid: set[str] = set()

  for i, r in enumerate(reqs):
    tab = r.tab or "?"
    if not r.detail.strip():
      report.issues.append(
        ConsistencyIssue("empty_detail", tab, i, "상세요건(detail) 비어 있음")
      )
    if not r.tab.strip():
      report.issues.append(
        ConsistencyIssue("empty_tab", tab, i, "탭 미배정")
      )
    if r.rid and r.rid in seen_rid:
      report.issues.append(
        ConsistencyIssue("duplicate_rid", tab, i, f"중복 ID: {r.rid}")
      )
    if r.rid:
      seen_rid.add(r.rid)

    # LLM 생성인데 바로 위 행과 동일한 top/mid → carry 누락 신호
    if i > 0 and reqs[i - 1].tab == r.tab:
      prev = reqs[i - 1]
      if r.gen_top and r.top.strip() and r.top.strip() == prev.top.strip():
        report.issues.append(
          ConsistencyIssue(
            "redundant_gen_top",
            tab,
            i,
            "이전 행과 동일한 항목명인데 LLM 생성(gen_top)",
          )
        )
      if r.gen_mid and r.mid.strip() and r.mid.strip() == prev.mid.strip():
        report.issues.append(
          ConsistencyIssue(
            "redundant_gen_mid",
            tab,
            i,
            "이전 행과 동일한 요구사항인데 LLM 생성(gen_mid)",
          )
        )

  return report

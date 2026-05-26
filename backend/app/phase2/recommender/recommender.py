from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.domain.enums import Judgement
from app.domain.models import CatalogCandidateAudit, MatchedSolutionSku, Recommendation, Requirement
from app.llm.base import AsyncLlmClient, Message
from app.phase2.recommender.rubric import DIMENSIONS, RubricScores
from app.phase2.vectorstore.base import SearchHit

if TYPE_CHECKING:
    from app.llm.usage import LlmUsageTracker
    from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever

logger = logging.getLogger(__name__)

RETRIEVAL_TOP_K = 10
DEFAULT_BATCH_SIZE = 5

_FEW_SHOT_VERIFICATION = """
[검증 few-shot 예시]

예시 A — 관련 솔루션 선별
  요구사항: HWP/PDF 비정형 문서에서 표·이미지·텍스트 구조 추출
  후보: DocuSee, 믿:음 K 2.0 Base, IntelliSearch, Agent Pattern 문서처리, ...
  → verified_ids: ["k-rag-docusee-문서-ocr-표-차트-인식"]
  → reason: "문서 OCR·표 추출에 DocuSee가 직접 대응합니다."
  → excluded_notes: (나머지 후보는 LLM/검색용이라 문서 파싱과 무관)

예시 B — 후보는 있으나 실제 무관
  요구사항: 원천 시스템(API/DB/파일) 연계 ETL·수집 스케줄링
  후보: 믿:음 K 2.0, IntelliSearch, 워크플로 Agent Pattern, ...
  → verified_ids: []
  → reason: "KT 솔루션 카탈로그에서 데이터 수집·ETL에 직접 맞는 항목을 찾기 어렵습니다."
  → excluded_notes: 각 id별 1문장 (왜 해당 요구와 기능이 다른지)

예시 C — 부분 관련 (동일 브랜드·다른 SKU)
  요구사항: 벡터 검색·하이브리드 검색·임베딩 인덱스 운영
  후보 SKU 예:
    - k-rag-intellisearch-임베딩-검색 | IntelliSearch · 임베딩 검색 | 한국어·영어 임베딩
    - k-rag-intellisearch-하이브리드-검색 | IntelliSearch · 하이브리드 검색 | BM25+벡터
    - k-rag-intellisearch-리랭커 | IntelliSearch · 리랭커 | Cross-encoder 재랭킹
  → verified_ids: ["k-rag-intellisearch-임베딩-검색", "k-rag-intellisearch-하이브리드-검색"]
  → reason: "임베딩·하이브리드 검색 SKU는 해당. 리랭커 SKU는 재랭킹만 담당해 제외."
  → excluded_notes: 리랭커 catalog_id에 「재랭킹만 해당, 벡터DB 운영과 무관」 등 SKU별 사유
"""


class _ExcludedNote(BaseModel):
    catalog_id: str
    reason: str


class _BatchVerdictItem(BaseModel):
    batch_index: int | None = None
    requirement_id: str
    verified_ids: list[str] = Field(default_factory=list)
    excluded_notes: list[_ExcludedNote] = Field(default_factory=list)
    rubric: RubricScores
    reason: str
    missing_tech: list[str] = Field(default_factory=list)
    consortium_need: str | None = None


class _BatchVerdict(BaseModel):
    results: list[_BatchVerdictItem]


def _sku_label(meta: dict[str, str], fallback_id: str) -> str:
    name = meta.get("솔루션명") or fallback_id
    sub = meta.get("소분류") or ""
    return f"{name} · {sub}" if sub else name


def _hit_to_sku(hit: SearchHit) -> MatchedSolutionSku:
    meta = hit.metadata
    return MatchedSolutionSku(
        catalog_id=hit.id,
        solution_name=meta.get("솔루션명", hit.id),
        sku_label=_sku_label(meta, hit.id),
        category_major=meta.get("대분류", ""),
        category_mid=meta.get("중분류", ""),
        category_sub=meta.get("소분류", ""),
        description=meta.get("설명", ""),
    )


def _matched_solution_labels(verified: list[SearchHit]) -> list[str]:
    """SKU 라벨 — 동일 브랜드·다른 소분류 구분."""
    labels: list[str] = []
    seen: dict[str, int] = {}
    for h in verified:
        sku = _hit_to_sku(h)
        label = sku.sku_label
        if label in seen:
            seen[label] += 1
            labels.append(f"{label} ({h.id[:8]})")
        else:
            seen[label] = 1
            labels.append(label)
    return labels


def _format_candidate_for_llm(hit: SearchHit) -> str:
    meta = hit.metadata
    sku = _sku_label(meta, hit.id)
    major = meta.get("대분류", "?")
    mid = meta.get("중분류", "?")
    sub = meta.get("소분류", "?")
    desc = (meta.get("설명") or "").strip() or "(설명 없음)"
    return (
        f"    - catalog_id={hit.id}\n"
        f"      SKU: {sku}\n"
        f"      계층: {major} > {mid} > {sub}\n"
        f"      기능: {desc[:160]}\n"
        f"      검색점수={hit.score:.2f}"
    )


class RequirementRecommender:
    """
    BM25 top-k 검색 → LLM 검증(진짜 유관한 솔루션만) → rubric 배치 평가.

    batch_size(기본 5)건씩 LLM 1회 — 누락 시 개별 재시도.
    """

    def __init__(
        self,
        llm: AsyncLlmClient,
        catalog: Bm25CatalogRetriever,
        top_k: int = RETRIEVAL_TOP_K,
        batch_size: int = DEFAULT_BATCH_SIZE,
        tracker: LlmUsageTracker | None = None,
    ) -> None:
        self._llm = llm
        self._catalog = catalog
        self._k = top_k
        self._batch_size = batch_size
        self._tracker = tracker

    async def recommend(self, req: Requirement) -> Recommendation:
        results = await self.recommend_batch([req])
        return results[0]

    async def recommend_batch(self, reqs: list[Requirement]) -> list[Recommendation]:
        if not reqs:
            return []

        hits_by_req: dict[str, list[SearchHit]] = {}
        for req in reqs:
            hits_by_req[req.id] = await self._catalog.search(req.detail, k=self._k)

        verdicts = await self._fetch_verdicts_with_retry(reqs, hits_by_req)

        out: list[Recommendation] = []
        for req in reqs:
            item = verdicts.get(req.id)
            hits = hits_by_req.get(req.id, [])
            if item is None:
                logger.error("LLM verdict 최종 누락 requirement_id=%s — heuristic fallback", req.id)
                out.append(self._heuristic_fallback(req, hits))
                continue
            verified = self._resolve_verified(hits, item.verified_ids)
            excluded_map = {n.catalog_id: n.reason for n in item.excluded_notes}
            sku_matches = [_hit_to_sku(h) for h in verified]
            out.append(
                Recommendation(
                    requirement_id=req.id,
                    ai_risk=item.rubric.to_judgement(),
                    ai_reason=item.reason,
                    missing_tech=item.missing_tech,
                    consortium_need=item.consortium_need,
                    matched_solutions=_matched_solution_labels(verified),
                    matched_solution_skus=sku_matches,
                    rubric_scores=item.rubric.as_dict(),
                    catalog_audit=self._build_catalog_audit(hits, item.verified_ids, excluded_map),
                )
            )
        return out

    async def _fetch_verdicts_with_retry(
        self,
        reqs: list[Requirement],
        hits_by_req: dict[str, list[SearchHit]],
    ) -> dict[str, _BatchVerdictItem]:
        verdicts = await self._llm_batch_verdict(reqs, hits_by_req)
        missing = [r for r in reqs if r.id not in verdicts]
        if missing:
            logger.warning(
                "배치 LLM %d/%d 누락 — 개별 재시도",
                len(missing),
                len(reqs),
            )
            for req in missing:
                retry = await self._llm_batch_verdict([req], hits_by_req)
                verdicts.update(retry)
        return verdicts

    async def _llm_batch_verdict(
        self,
        reqs: list[Requirement],
        hits_by_req: dict[str, list[SearchHit]],
    ) -> dict[str, _BatchVerdictItem]:
        prompt = self._build_batch_prompt(reqs, hits_by_req)
        max_tokens = max(4096, len(reqs) * 700)
        try:
            batch = await self._llm.structured_output(
                [Message(role="user", content=prompt)],
                _BatchVerdict,
                purpose=f"recommend:batch:{len(reqs)}",
                tracker=self._tracker,
                max_tokens=max_tokens,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("배치 LLM 호출 실패 (%d건): %s", len(reqs), e)
            return {}
        mapped = self._map_verdicts(reqs, batch)
        if len(mapped) < len(reqs):
            logger.warning(
                "배치 LLM results %d/%d — missing=%s",
                len(mapped),
                len(reqs),
                [r.id[:8] for r in reqs if r.id not in mapped],
            )
        return mapped

    @staticmethod
    def _map_verdicts(
        reqs: list[Requirement],
        batch: _BatchVerdict,
    ) -> dict[str, _BatchVerdictItem]:
        """requirement_id · batch_index · 순서 · 부분일치로 매핑."""
        by_id: dict[str, _BatchVerdictItem] = {}
        by_index: dict[int, _BatchVerdictItem] = {}
        for item in batch.results:
            by_id[item.requirement_id] = item
            if item.batch_index is not None:
                by_index[item.batch_index] = item

        mapped: dict[str, _BatchVerdictItem] = {}

        for i, req in enumerate(reqs):
            if req.id in by_id:
                mapped[req.id] = by_id[req.id]
                continue
            if i in by_index:
                mapped[req.id] = by_index[i]
                continue
            for item in batch.results:
                rid = item.requirement_id
                if rid == req.id or req.id.startswith(rid) or rid.startswith(req.id[:16]):
                    mapped[req.id] = item
                    break
            if req.id in mapped:
                continue
            if len(batch.results) == len(reqs) and i < len(batch.results):
                candidate = batch.results[i]
                mapped[req.id] = candidate

        return mapped

    @staticmethod
    def _heuristic_fallback(req: Requirement, hits: list[SearchHit]) -> Recommendation:
        """LLM 완전 실패 시 BM25 상위 후보만으로 보수적 △/X."""
        if not hits:
            return Recommendation(
                requirement_id=req.id,
                ai_risk=Judgement.NO,
                ai_reason="카탈로그에서 유사 솔루션을 찾기 어렵습니다. 수동 검토가 필요합니다.",
                missing_tech=["수동 기술 검토"],
            )
        top = hits[0]
        name = top.metadata.get("솔루션명", top.id)
        if top.score >= 8.0:
            return Recommendation(
                requirement_id=req.id,
                ai_risk=Judgement.PARTIAL,
                ai_reason=(
                    f"자동 평가 재시도 후 '{name}' 등 후보가 검색되었으나 "
                    "AI 상세 검증은 실패했습니다. 수동 확인을 권장합니다."
                ),
                missing_tech=[],
                matched_solutions=[name],
                catalog_audit=RequirementRecommender._build_catalog_audit(hits, [], {}),
            )
        return Recommendation(
            requirement_id=req.id,
            ai_risk=Judgement.NO,
            ai_reason="AI 자동 검증에 실패했으며, 카탈로그 유사도도 낮습니다. 수동 검토가 필요합니다.",
            missing_tech=["수동 기술 검토"],
            catalog_audit=RequirementRecommender._build_catalog_audit(hits, [], {}),
        )

    @staticmethod
    def _build_catalog_audit(
        hits: list[SearchHit],
        verified_ids: list[str],
        excluded_notes: dict[str, str],
    ) -> list[CatalogCandidateAudit]:
        verified_set = set(verified_ids)
        audit: list[CatalogCandidateAudit] = []
        for h in hits:
            selected = h.id in verified_set
            exclusion = None
            if not selected:
                exclusion = excluded_notes.get(h.id) or (
                    "요구사항 본문과 솔루션 기능이 직접적으로 맞지 않아 제외했습니다."
                )
            audit.append(
                CatalogCandidateAudit(
                    catalog_id=h.id,
                    solution_name=h.metadata.get("솔루션명", h.id),
                    sku_label=_sku_label(h.metadata, h.id),
                    category_major=h.metadata.get("대분류", ""),
                    category_mid=h.metadata.get("중분류", ""),
                    category_sub=h.metadata.get("소분류", ""),
                    description=(h.metadata.get("설명") or "")[:200],
                    similarity_score=round(float(h.score), 4),
                    selected=selected,
                    exclusion_reason=exclusion,
                )
            )
        return audit

    @staticmethod
    def _resolve_verified(hits: list[SearchHit], verified_ids: list[str]) -> list[SearchHit]:
        if not verified_ids:
            return []
        hit_map = {h.id: h for h in hits}
        return [hit_map[v] for v in verified_ids if v in hit_map]

    def _build_batch_prompt(
        self,
        reqs: list[Requirement],
        hits_by_req: dict[str, list[SearchHit]],
    ) -> str:
        dim_block = "\n".join(f"- {d.key}: {d.description}" for d in DIMENSIONS)
        blocks: list[str] = []
        for i, req in enumerate(reqs):
            hits = hits_by_req.get(req.id, [])
            cand_lines = (
                "\n".join(_format_candidate_for_llm(h) for h in hits) or "    (후보 없음)"
            )
            blocks.append(
                f"### batch_index={i} requirement_id={req.id}\n"
                f"분류: {req.category}\n"
                f"본문:\n{req.detail[:900]}\n"
                f"카탈로그 탐색 후보 top-{self._k}:\n{cand_lines}\n"
                f"→ verified_ids: 후보 id 중 **요구사항을 실제로 커버하는 것만** (없으면 [])\n"
                f"→ excluded_notes: verified_ids에 넣지 않은 후보 각각 catalog_id+제외 사유 1문장"
            )

        schema_hint = (
            '{"results": [{"batch_index": 0, "requirement_id": "<id 그대로>", '
            '"verified_ids": ["<catalog_id>", ...], '
            '"excluded_notes": [{"catalog_id": "<id>", "reason": "..."}], '
            '"rubric": {"기술적합도":1, "데이터요건":1, "컴플라이언스":1, "레퍼런스":1, "컨소시엄":1}, '
            '"reason":"한국어 1~2문장", "missing_tech":[], "consortium_need": null}]}'
        )
        n = len(reqs)
        return (
            f"RFP 요구사항 {n}건에 대해 KT AI 솔루션 카탈로그 매칭을 **보수적으로** 평가하라.\n\n"
            "**필수**: results 배열 길이는 정확히 "
            f"{n}개. batch_index=0..{n - 1}, requirement_id는 입력값을 **한 글자도 바꾸지 말고** 복사.\n\n"
            "규칙:\n"
            "1) verified_ids에는 **catalog_id(SKU) 단위**로 넣는다. 솔루션명(브랜드)만 같다고 동일 제품이 아니다.\n"
            "2) 동일 솔루션명이라도 소분류·기능(설명)이 다르면 **별개 SKU** — 요구와 맞는 id만 선택.\n"
            "3) 키워드만 겹치고 SKU 기능이 다르면 넣지 않는다. 없으면 verified_ids=[] 허용.\n"
            "4) rubric 1~5 (낮을수록 리스크 작음), 애매하면 △/X 쪽.\n"
            "5) missing_tech는 verified_ids로도 못 채우는 갭.\n"
            "6) excluded_notes에는 **제외한 각 catalog_id**와 SKU별 제외 사유 1문장.\n"
            "7) reason·excluded_notes에는 BM25/embedding/벡터DB/키워드매칭 등 **내부 기술 용어 금지**.\n"
            "8) reason은 사용자용 문장: 연관 기술 탐색이 어렵다고 **완곡하게** (확정적으로 '없다' 금지).\n"
            f"{_FEW_SHOT_VERIFICATION}\n\n"
            "[평가 차원]\n"
            f"{dim_block}\n\n"
            "[요구사항 배치]\n"
            + "\n\n".join(blocks)
            + f"\n\n입력 {n}건 각각 results 1개. JSON만:\n{schema_hint}"
        )

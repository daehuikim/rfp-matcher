from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.domain.enums import Judgement
from app.domain.models import CatalogCandidateAudit, Recommendation, Requirement
from app.llm.base import AsyncLlmClient, Message
from app.phase2.recommender.rubric import DIMENSIONS, RubricScores
from app.phase2.vectorstore.base import SearchHit

if TYPE_CHECKING:
    from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever

logger = logging.getLogger(__name__)

RETRIEVAL_TOP_K = 10
DEFAULT_BATCH_SIZE = 10

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

예시 C — 부분 관련
  요구사항: 벡터 검색·하이브리드 검색·임베딩 인덱스 운영
  → verified_ids: ["k-rag-intellisearch-임베딩-검색", "k-rag-intellisearch-하이브리드-검색"]
  → reason: "검색·임베딩 일부는 IntelliSearch로 커버 가능하나, 대규모 벡터DB 운영은 별도 검토가 필요합니다."
"""


class _ExcludedNote(BaseModel):
    catalog_id: str
    reason: str


class _BatchVerdictItem(BaseModel):
    requirement_id: str
    verified_ids: list[str] = Field(default_factory=list)
    excluded_notes: list[_ExcludedNote] = Field(default_factory=list)
    rubric: RubricScores
    reason: str
    missing_tech: list[str] = Field(default_factory=list)
    consortium_need: str | None = None


class _BatchVerdict(BaseModel):
    results: list[_BatchVerdictItem]


class RequirementRecommender:
    """
    BM25 top-k 검색 → LLM 검증(진짜 유관한 솔루션만) → rubric 배치 평가.

    96건 문서도 batch_size(기본 10) 단위로 LLM 호출 수를 ~1/10로 줄인다.
    """

    def __init__(
        self,
        llm: AsyncLlmClient,
        catalog: Bm25CatalogRetriever,
        top_k: int = RETRIEVAL_TOP_K,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._llm = llm
        self._catalog = catalog
        self._k = top_k
        self._batch_size = batch_size

    async def recommend(self, req: Requirement) -> Recommendation:
        results = await self.recommend_batch([req])
        return results[0]

    async def recommend_batch(self, reqs: list[Requirement]) -> list[Recommendation]:
        if not reqs:
            return []

        # 1) 요건별 BM25 top-10
        hits_by_req: dict[str, list[SearchHit]] = {}
        for req in reqs:
            hits_by_req[req.id] = await self._catalog.search(req.detail, k=self._k)

        # 2) LLM 배치 검증 + rubric
        prompt = self._build_batch_prompt(reqs, hits_by_req)
        batch = await self._llm.structured_output(
            [Message(role="user", content=prompt)],
            _BatchVerdict,
        )
        by_id = {item.requirement_id: item for item in batch.results}

        out: list[Recommendation] = []
        for req in reqs:
            item = by_id.get(req.id)
            hits = hits_by_req.get(req.id, [])
            if item is None:
                logger.warning("배치 LLM 응답에 requirement_id=%s 누락 — fallback X", req.id)
                out.append(
                    Recommendation(
                        requirement_id=req.id,
                        ai_risk=Judgement.NO,
                        ai_reason="AI 배치 응답 누락",
                        missing_tech=["AI 응답 오류"],
                    )
                )
                continue
            verified = self._resolve_verified(hits, item.verified_ids)
            excluded_map = {n.catalog_id: n.reason for n in item.excluded_notes}
            out.append(
                Recommendation(
                    requirement_id=req.id,
                    ai_risk=item.rubric.to_judgement(),
                    ai_reason=item.reason,
                    missing_tech=item.missing_tech,
                    consortium_need=item.consortium_need,
                    matched_solutions=[h.metadata.get("솔루션명", h.id) for h in verified],
                    rubric_scores=item.rubric.as_dict(),
                    catalog_audit=self._build_catalog_audit(hits, item.verified_ids, excluded_map),
                )
            )
        return out

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
                    category_major=h.metadata.get("대분류", ""),
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
        for req in reqs:
            hits = hits_by_req.get(req.id, [])
            cand_lines = (
                "\n".join(
                    f"    - id={h.id} | [{h.metadata.get('대분류', '?')} > {h.metadata.get('소분류', '?')}] "
                    f"{h.metadata.get('솔루션명', h.id)} (유사도점수={h.score:.2f})"
                    for h in hits
                )
                or "    (후보 없음)"
            )
            blocks.append(
                f"### requirement_id={req.id}\n"
                f"분류: {req.category}\n"
                f"본문:\n{req.detail[:1200]}\n"
                f"카탈로그 탐색 후보 top-{self._k}:\n{cand_lines}\n"
                f"→ verified_ids: 후보 id 중 **요구사항을 실제로 커버하는 것만** (없으면 [])\n"
                f"→ excluded_notes: verified_ids에 넣지 않은 후보 각각 catalog_id+제외 사유 1문장"
            )

        schema_hint = (
            '{"results": [{"requirement_id": "<id>", "verified_ids": ["<catalog_id>", ...], '
            '"excluded_notes": [{"catalog_id": "<id>", "reason": "..."}], '
            '"rubric": {"기술적합도":1~5,...}, "reason":"한국어 1~2문장", '
            '"missing_tech":[], "consortium_need": null}]}'
        )
        return (
            "RFP 요구사항 묶음에 대해 KT AI 솔루션 카탈로그 매칭을 **보수적으로** 평가하라.\n\n"
            "규칙:\n"
            "1) 탐색 후보 중 verified_ids에는 **진짜로 해당 요구를 커버하는 id만** 넣는다.\n"
            "2) 키워드만 겹치고 기능이 다르면 넣지 않는다. 없으면 verified_ids=[] 허용.\n"
            "3) rubric 1~5 (낮을수록 리스크 작음), 애매하면 △/X 쪽.\n"
            "4) missing_tech는 verified_ids로도 못 채우는 갭.\n"
            "5) reason·excluded_notes에는 BM25/embedding/벡터DB/키워드매칭 등 **내부 기술 용어 금지**.\n"
            "6) reason은 사용자용 문장: 연관 기술 탐색이 어렵다고 **완곡하게** (확정적으로 '없다' 금지).\n"
            f"{_FEW_SHOT_VERIFICATION}\n\n"
            "[평가 차원]\n"
            f"{dim_block}\n\n"
            "[요구사항 배치]\n"
            + "\n\n".join(blocks)
            + f"\n\n입력 requirement_id마다 results 1개. JSON만:\n{schema_hint}"
        )

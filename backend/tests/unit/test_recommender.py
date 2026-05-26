from __future__ import annotations

import pytest

from app.domain.enums import Judgement
from app.domain.models import Requirement
from app.llm.fake_client import FakeLlmClient
from app.phase2.catalog.store import CatalogStore
from app.phase2.recommender.recommender import RequirementRecommender
from app.phase2.recommender.rubric import RubricScores
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever
from app.services.catalog_indexer import CatalogIndexer
from app.services.catalog_seed import synthesize_seed_catalog
from tests.unit.recommender_helpers import batch_no_handler, batch_yes_handler


@pytest.mark.asyncio
async def test_recommend_low_risk_yields_yes_judgement(tmp_path) -> None:
    store = CatalogStore(tmp_path / "cat.json")
    store.replace(synthesize_seed_catalog())
    retriever = Bm25CatalogRetriever()
    await CatalogIndexer(retriever).index(store)

    rec = await RequirementRecommender(
        FakeLlmClient(structured_handler=batch_yes_handler), retriever
    ).recommend(
        Requirement(
            id="r1",
            doc_id="d",
            category="데이터 수집",
            code="DATA-001",
            name="원천 시스템 연계",
            detail="다양한 원천 시스템(API/파일/DB)에서 데이터 수집",
        )
    )
    assert rec.ai_risk == Judgement.YES
    assert "카탈로그" in rec.ai_reason or "커버" in rec.ai_reason


@pytest.mark.asyncio
async def test_recommend_high_risk_yields_no_with_consortium(tmp_path) -> None:
    store = CatalogStore(tmp_path / "cat.json")
    store.replace(synthesize_seed_catalog()[:3])
    retriever = Bm25CatalogRetriever()
    await CatalogIndexer(retriever).index(store)

    rec = await RequirementRecommender(
        FakeLlmClient(structured_handler=batch_no_handler), retriever
    ).recommend(
        Requirement(
            id="r2",
            doc_id="d",
            category="진단 영상",
            code="MED-001",
            name="X-ray 자동 판독",
            detail="X-ray 영상을 자동으로 판독하여 결과를 리포트",
        )
    )
    assert rec.ai_risk == Judgement.NO
    assert "의료영상 라벨링" in rec.missing_tech
    assert rec.consortium_need == "의료 AI 전문 SI"


def test_rubric_threshold_boundary() -> None:
    s_yes = RubricScores(기술적합도=2, 데이터요건=2, 컴플라이언스=2, 레퍼런스=2, 컨소시엄=3)
    assert s_yes.average() == pytest.approx(2.2)
    assert s_yes.to_judgement() == Judgement.YES

    s_partial = RubricScores(기술적합도=3, 데이터요건=3, 컴플라이언스=3, 레퍼런스=3, 컨소시엄=3)
    assert s_partial.to_judgement() == Judgement.PARTIAL

    s_no = RubricScores(기술적합도=5, 데이터요건=4, 컴플라이언스=4, 레퍼런스=4, 컨소시엄=4)
    assert s_no.to_judgement() == Judgement.NO

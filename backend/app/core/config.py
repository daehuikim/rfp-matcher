from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / "config" / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="",
    )

    app_name: str = "rfp-matcher"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    llm_provider: Literal["openai", "anthropic", "gemma", "fake"] = "gemma"
    # 추출 엔진: v2(prototype/v2 — results_final 산출 엔진) / legacy(기존 HTML 파이프라인)
    extraction_engine: Literal["v2", "legacy"] = "v2"
    v2_tab_mode: Literal["cluster", "ordered"] = "ordered"
    llm_model_openai: str = "gpt-4o"
    llm_model_anthropic: str = "claude-opus-4-7"
    llm_model_gemma: str = Field(default="gemma-4-26B-4aB-it", alias="LLM_MODEL_GEMMA")
    gemma_base_url: str = Field(
        default="https://m2.geniemars.kt.co.kr:10601/v1",
        alias="GEMMA_BASE_URL",
    )
    gemma_verify_ssl: bool = Field(default=False, alias="GEMMA_VERIFY_SSL")
    llm_concurrency: int = Field(default=16, alias="LLM_CONCURRENCY")
    recommend_batch_size: int = 5

    # deprecated — OpenAI embedding 미사용 (BM25로 대체)
    embedding_provider: Literal["openai", "local", "bm25"] = "bm25"
    embedding_model_openai: str = "text-embedding-3-large"
    embedding_model_local: str = "jhgan/ko-sroberta-multitask"

    vectorstore_path: Path = PROJECT_ROOT / "data" / "vectorstore"
    catalog_path: Path = PROJECT_ROOT / "data" / "catalog" / "kt_solutions.json"
    catalog_bm25_path: Path = PROJECT_ROOT / "data" / "catalog" / "bm25_index"

    storage_root: Path = PROJECT_ROOT / "data" / "storage"
    # 파이프라인 중간·최종 산출물 (동일 원본 재업로드 시 LLM/변환 스킵)
    artifact_cache_dir: Path = PROJECT_ROOT / "data" / "artifacts"
    # 단계별 input/output 로그 (packages/logs + 앱 실행 시 동일 구조)
    pipeline_logs_dir: Path = PROJECT_ROOT / "data" / "packages" / "logs"
    category_taxonomy_path: Path = PROJECT_ROOT / "data" / "taxonomy" / "categories.json"
    catalog_aliases_path: Path = PROJECT_ROOT / "data" / "catalog" / "catalog_aliases.json"
    catalog_near_duplicate_threshold: float = 0.92
    db_path: Path = PROJECT_ROOT / "data" / "app.sqlite"
    raw_data_dir: Path = PROJECT_ROOT / "data" / "raw"
    samples_manifest_path: Path = PROJECT_ROOT / "data" / "samples.manifest.json"
    final_manifest_path: Path = PROJECT_ROOT / "data" / "final.manifest.json"
    artifacts_final_dir: Path = PROJECT_ROOT / "data" / "artifacts_final"

    # PDF 변환 백엔드. **기본 pymupdf** — C 네이티브, 대용량 PDF도 수 초.
    # pdfplumber: 순수 Python, 느림. docling: ML OCR, MPS/mac에서 자주 실패·매우 느림.
    # pdf2html: Node+Java(Tika) 필요 — brew install openjdk 후 PDF_CONVERTER=pdf2html
    pdf_converter: Literal["pymupdf", "pdfplumber", "pdf2html", "docling"] = "pymupdf"
    # Word: mammoth(.docx 순수 Python) | libreoffice | auto(mammoth→LO 폴백)
    # 구형 .doc 은 mammoth 모드에서 LO로 docx 거친 뒤 mammoth
    word_converter: Literal["mammoth", "libreoffice", "auto"] = "auto"
    # HF 모델 영구 캐시 위치 (재시작에도 재다운로드 X).
    # 기본은 HF SDK 표준인 ~/.cache/huggingface — 이미 받아둔 글로벌 캐시를 재사용한다.
    # 프로젝트별로 격리하고 싶으면 .env에 HF_HOME=… 또는 HF_CACHE_DIR=… 명시.
    hf_cache_dir: Path = Path.home() / ".cache" / "huggingface"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

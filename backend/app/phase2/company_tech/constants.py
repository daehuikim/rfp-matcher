from __future__ import annotations

import re

DEFAULT_COLLECTION = "company_tech_docs"

_TECH_VOCAB_SPLIT_RE = re.compile(r"[,.\n·/]| 및 ")


def _build_tech_vocab() -> frozenset[str]:
    terms: set[str] = set()
    for hint in CATEGORY_HINTS.values():
        for part in _TECH_VOCAB_SPLIT_RE.split(hint):
            term = part.strip()
            if len(term) >= 2:
                terms.add(term.casefold())
    for label in SOURCE_LABELS.values():
        terms.add(label.casefold())
    return frozenset(terms)

CATEGORY_HINTS: dict[str, str] = {
    "agent.txt": (
        "K Agent. 자율형 AI 에이전트, Planner, Memory, Agent/Tool Execution, Generation, "
        "도구 호출과 업무 자동화, 워크플로우 자동화, 복잡 추론, 멀티 에이전트 협업, "
        "Knowledge Search/Processing, Conversational AI, 개인화 추천, 예측/의사결정, "
        "문서 처리/정보 추출, 음성/비전 처리, Agent Pattern 프레임워크, Well-Architected 템플릿, "
        "IaC, 프롬프트 관리, 로깅, 모니터링, 성능 평가, Multi-Model Orchestrator, "
        "실시간 모델 라우팅, 비용 최적화, OpenAI Spec 연동"
    ),
    "cloud.txt": (
        "K Cloud, Secure Public Cloud, Cloud Automation. 보안 퍼블릭 클라우드, KT-MS 협력, "
        "소버린 클라우드, 데이터 주권, 국내 리전, 데이터 레지던시, 물리적 네트워크 격리, "
        "산업별 컴플라이언스, Sovereign Landing Zone, 암호화, TEE, CVM/Confidential VM, "
        "CMK, mHSM, 고객 관리형 키, 자원 소유권, 클라우드 전환/마이그레이션 자동화, "
        "사전 진단, 기술 스택 자동 식별, 영향도 분석, 서버/솔루션 설치 자동화, "
        "Kubernetes 원클릭 설치, 설계/구축 검증, 보안 취약점 점검, 산출물 품질 검사, "
        "Cloud Tx Q&A 챗봇/RAG"
    ),
    "intelligence-studio.txt": (
        "K intelligence Studio. 기업용 AX/GenAI 올인원 플랫폼, AI 서비스 구축/운영, "
        "LLM 선택과 비교, 플레이그라운드, RAG 구축, 기업 내부 데이터 기반 AI 검색, "
        "국내 고객 데이터센터와 보안 환경, Public/Multi Hybrid Cloud 배포, "
        "노코드/드래그앤드롭 생성형 AI 제작, HuggingFace/오픈소스 모델 카탈로그, "
        "고객 데이터 기반 파인튜닝과 운영, 모델 배포/검증, 표준 API, "
        "믿:음 K, SOTA K, Llama K, Solar LLM, Model Orchestrator"
    ),
    "kai_studio.txt": (
        "KAI Studio. 관리자/사용자 AI 플랫폼 기능, 슈퍼 관리자 대시보드, 사용량/학습/배포 통계, "
        "로그 데이터 관리 및 수집, "
        "배포 서비스 모니터링, API 호출량/지연시간/성공률, Provider 관리, 외부/내부 LLM 관리, "
        "vLLM/자체 구축 모델 등록, 모델 카탈로그, 모델 학습, 배포, GPU 모니터링, Loss 그래프, "
        "템플릿/파라미터 관리, 고객/구독/사용자/API Key 관리, My AI Service, Playground, "
        "데이터 관리, 프롬프트 관리, RAG 서비스, 지식 저장소, 파일 관리, 지식 변환, "
        "임베딩 모델/청킹/인덱싱, Top K/필터/정렬 검색 설정, RAG Playground/배포, "
        "검색기/모델/RAG 평가, Human Judge, 정량 평가, Open Benchmark, Agent Builder, "
        "워크플로우 저장/배포, RAG 노드, Tool 선택, Custom API, MCP Server, 로그, 템플릿"
    ),
    "model.txt": (
        "대규모 언어 모델 및 LLM 관련 "
        "K Model. 한국어 LLM 라인업, 자체 모델, 오픈소스 모델, 글로벌 SOTA 기반 한국어 특화 모델, "
        "도메인 특화 모델, 고품질 한국어 데이터, K Data Alliance, 데이터 주권, 안전성/투명성, "
        "믿:음 K 2.0 Mini, 믿:음 K 2.0 Base, 믿:음 K 2.5 Pro, 멀티모달 모델, "
        "SOTA K, Llama K, 법률/금융/교육/의료 특화 모델, 파인튜닝, 한국어/영어, "
        "텍스트 생성, 질의응답, 요약, 분류, 재작성, 추론, 코딩, 수학, 온디바이스, "
        "32K 컨텍스트, K RAG와 연계한 문서 질의응답, 모델 카드"
    ),
    "rag.txt": (
        "K RAG. 외부/내부 데이터 기반 검색증강생성, 정확한 답변, 정보 검색/검증 자동화, "
        "사내 지식 검색, 보고서 작성, DocuSee, IntelliSearch, OCR, 문서 구조 분석, "
        "HWP/PDF/Word/Excel/PPT/TXT/JPG 지원, 텍스트/표/이미지/레이아웃 추출, "
        "문단 순서, 표 구조, 문서 유형 분석, Table Detection/Recognition, Layout Analysis, "
        "Chart Recognition, Diagram Recognition, 수식/순서도 인식, D-Parser, "
        "CPU 기반 고속 문서 추출, Modular RAG, Agentic RAG, KT Embedding, KT Reranker, "
        "한국어/영어 검색, 메타데이터 보강, 하이브리드 키워드+벡터 검색, Query Reasoning, "
        "멀티쿼리, 질의 분해, Dynamic Search, Groundness Check, 출처 표시, 추천 질문, "
        "RAG 파이프라인 커스터마이징"
    ),
    "rai.txt": (
        "K RAI, Responsible AI. AI 거버넌스, 책임 있는 AI 프레임워크, AI 윤리, 안전성, "
        "신뢰성, 위험 관리, 전 생애주기 관리, ASTRI 원칙, Accountability, Sustainability, "
        "Transparency, Reliability, Inclusivity, 영향평가, 안전성 평가, 레드팀, RAI 심의, "
        "모델 카드, 리스크 식별/분석/평가/완화, 배포 후 모니터링, Data Cleansing Tool, "
        "민감정보/편향/유해 콘텐츠 제거, RAI Evaluation Tool, 강건성/안전성 벤치마크, "
        "Adversarial 평가, RAI Guardrail Tool, 입력/출력 차단과 제어, RAI Monitoring Tool, "
        "로그 분석, 유해 패턴 탐지, 알림, 교육/컨설팅/리포트"
    ),
}

SOURCE_LABELS: dict[str, str] = {
    "agent.txt": "K Agent",
    "cloud.txt": "K Cloud",
    "intelligence-studio.txt": "K intelligence Studio",
    "kai_studio.txt": "KAI Studio",
    "model.txt": "K Model",
    "rag.txt": "K RAG",
    "rai.txt": "K RAI",
}

TECH_VOCAB = _build_tech_vocab()

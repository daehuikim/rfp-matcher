"""rfpmatch — 외부 개발 RFP 조견표 엔진의 headless 이식 (v2/v_rule 과 완전 별개).

원본은 저장소 루트 rfpmatch/ 의 6단계(step1~6) Streamlit 마법사. 이 패키지는 그중
Streamlit/session_state 에 결합되지 않은 핵심 로직만 뽑아 순수 함수로 재구성한다.

흐름:
  문서 →(OpenDataLoader/hwp5txt 등)→ HTML
  → HTML 표 다페이지 병합·복구 (html_tables.py)
  → LLM 목차 생성 + 순수 복구 + 잔여 공백 자동 재질의 (toc.py, toc_normalize.py, toc_llm.py)
  → TOC 기준 섹션 분리 (sections.py) → 카드 생성 (cards.py) → 카드 분할 (partition.py)
  → 규칙 기반 행 추출 (rowbuild.py) + 신뢰도 낮은 섹션만 LLM 자동 검수/수정 (requirement_table.py)
  → v2 Req 리스트 (adapter.py) — 기존 app/phase1/writers 로 export.

원본 Streamlit 마법사의 "사람이 확인 후 다음 단계" 지점(TOC 수동편집, 행 병합/분리)은
전부 자동화했다 — 사람 개입 없이 파이프라인이 끝까지 수행된다.
"""

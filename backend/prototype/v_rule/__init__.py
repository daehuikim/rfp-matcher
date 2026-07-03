"""v_rule — 사용자 룰 스펙 전용 RFP 조견표 백엔드 (v2/v3 와 완전 별개).

흐름(스펙 그대로):
  PDF →(OpenDataLoader)→ HTML/Markdown/TXT
  → HTML 표 후처리(다페이지 병합·빈칸 윗병합·조각문장 결합·예외처리)
  → TXT→LLM 목차생성 → anchor → 카드 분할
  → 카드별 본문/표 분리 → 조견표 db(표룰·본문룰·특수룰, atomic) → Excel(Section=탭, 항목명/요구사항/상세요건)

LLM 은 스펙이 허용한 2곳만: ①목차 생성 ②5단 이상 표 해석.
"""

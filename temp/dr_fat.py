"""뚱뚱한 행(fat row) 측정 하네스 — 상세내용 길이 분포로 '의미단위 분해' 개선을 정량 추적.

keep=False(gemma keep 판정 생략)로 실행해 결정적(LLM 무관) 비교가 되게 한다.
사용: backend/.venv/bin/python temp/dr_fat.py [파일키워드...]   (없으면 8개 전부)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import os
os.chdir(Path(__file__).resolve().parents[1] / "backend")
# 결정적 측정 기본 — LLM 분해 폴백까지 포함해 재려면 VRULE_LLM_SPLIT=1 로 실행.
os.environ.setdefault("VRULE_LLM_SPLIT", "0")

RAW = Path("../data_real/raw")
FILES = {
    "JB": "pdf_JB금융.pdf", "기아": "pdf_기아자동차.pdf", "대한항공": "pdf_대한항공.pdf",
    "신한은행": "pdf_신한은행.pdf", "하나": "wod파일_하나은행.docx", "스캔": "스캔본_신한카드.pdf",
    "강원랜드": "한글파일_강원랜드.hwp", "경기도": "한글파일_경기도교육청.hwp",
}


def run_one(key: str):
    from prototype.v_rule.adapter import run_v_rule_reqs
    src = RAW / FILES[key]
    workdir = Path("/tmp/drfat") / key
    reqs = run_v_rule_reqs(src, workdir, keep=False)
    lens = sorted(len(r.detail or "") for r in reqs)
    if not lens:
        print(f"[{key}] 행 없음")
        return
    n = len(lens)
    med = lens[n // 2]
    p90 = lens[int(n * 0.9)]
    mx = lens[-1]
    o300 = sum(1 for x in lens if x > 300)
    o500 = sum(1 for x in lens if x > 500)
    o1000 = sum(1 for x in lens if x > 1000)
    print(f"[{key}] n={n} med={med} p90={p90} max={mx} | >300:{o300} >500:{o500} >1000:{o1000}")
    for r in sorted(reqs, key=lambda r: -len(r.detail or ""))[:2]:
        print(f"    FAT({len(r.detail or '')}자) {(r.detail or '')[:120]}")


if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if a in FILES] or list(FILES)
    for k in keys:
        try:
            run_one(k)
        except Exception as e:
            import traceback
            print(f"[{k}] 실패: {e}")
            traceback.print_exc()

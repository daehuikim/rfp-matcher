"""7개 샘플 end-to-end 재생성 + 엑셀 다운로드 (QA 루프용).

각 샘플: from-sample → 추출 완료(READY_FOR_REVIEW)까지 폴링 → /export 로 xlsx 저장.
추천(RECOMMENDING)은 백그라운드로 계속 — 추출(아토믹/탭) 검수만 블로킹.
진행상황을 data/qa_downloads/qa_progress.json 에 기록.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "qa_downloads"
OUT.mkdir(parents=True, exist_ok=True)
PROG = OUT / "qa_progress.json"

SAMPLES = [
    "하나.pdf",
    "(삼성카드) 제안요청서.pdf",
    "(신한라이프) AX HUB 구축_제안요청서.pdf.pdf",
    "(JB 금융그룹) 붙임2. 제안요청서(안).pdf",
    "법제처_생성형 AI 법령검색 시스템 구축_RFI_20260116.hwpx",
    "LLM기반 자율전투체계 AI 에이전트 기술 개발_국방기술진흥연구소(RFP).pdf",
    "하나.doc",
]
DONE_STAGES = {"READY_FOR_REVIEW", "RECOMMENDING", "RECOMMENDED"}


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read())


def _post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _safe(name):
    return re.sub(r'[\\/:*?"<>|()]+', "_", name).strip("_")


def load_prog():
    if PROG.is_file():
        return json.loads(PROG.read_text())
    return {}


def save_prog(p):
    PROG.write_text(json.dumps(p, ensure_ascii=False, indent=2))


def download_excel(doc_id, name):
    q = urllib.parse.urlencode({"mode": "both", "layout": "cluster",
                                "filename": _safe(name)})
    url = f"{BASE}/documents/{doc_id}/export?{q}"
    dest = OUT / f"{_safe(name)}.xlsx"
    with urllib.request.urlopen(url, timeout=180) as r:
        dest.write_bytes(r.read())
    return dest


def run_one(name, prog):
    rec = prog.get(name, {})
    if rec.get("excel") and Path(rec["excel"]).is_file():
        return rec  # 이미 완료
    print(f"[{name}] from-sample…", flush=True)
    resp = _post("/documents/from-sample", {"name": name})
    doc_id = resp["doc_id"]
    rec = {"doc_id": doc_id, "stage": "queued", "started": time.strftime("%H:%M:%S")}
    prog[name] = rec
    save_prog(prog)

    deadline = time.time() + 900  # 15분 상한
    stage = "UPLOADED"
    while time.time() < deadline:
        try:
            st = _get(f"/documents/{doc_id}/pipeline")
            stage = st.get("stage", "?")
        except Exception as e:  # noqa
            stage = f"poll_err:{e}"
        rec["stage"] = stage
        save_prog(prog)
        if stage in DONE_STAGES:
            break
        if stage == "FAILED":
            rec["error"] = st.get("error")
            save_prog(prog)
            return rec
        time.sleep(5)
    else:
        rec["error"] = "timeout(extraction)"
        save_prog(prog)
        return rec

    # 추출 완료 → 엑셀 다운로드
    try:
        dest = download_excel(doc_id, name)
        rec["excel"] = str(dest)
        rec["excel_bytes"] = dest.stat().st_size
        rec["finished"] = time.strftime("%H:%M:%S")
    except Exception as e:  # noqa
        rec["error"] = f"export:{e}"
    save_prog(prog)
    print(f"[{name}] stage={stage} excel={rec.get('excel_bytes')}B", flush=True)
    return rec


def main():
    prog = load_prog()
    for name in SAMPLES:
        try:
            run_one(name, prog)
        except Exception as e:  # noqa
            prog.setdefault(name, {})["error"] = f"fatal:{e}"
            save_prog(prog)
            print(f"[{name}] FATAL {e}", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()

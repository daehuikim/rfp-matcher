#!/bin/zsh
# 오버나이트 전체 배치 — data_real/raw 10파일 순차 업로드(v_rule) → 완료 폴링 →
# requirements JSON + export-fixed xlsx 저장. BE(8001)는 미리 떠 있어야 함.
set -u
ROOT="/Users/daehuikim/Desktop/rfp-matcher"
OUT="$ROOT/temp/overnight"
XLSX="$ROOT/data_real/final3"
mkdir -p "$OUT" "$XLSX"
TSV="$OUT/batch.tsv"
: > "$TSV"

FILES=(
  "pdf_기아자동차.pdf"
  "한글파일_양형.hwp"
  "법제처_생성형 AI 법령검색 시스템 구축_RFI_20260116.pdf"
  "한글파일_경기도교육청.hwp"
  "한글파일_강원랜드.hwp"
  "pdf_JB금융.pdf"
  "pdf_대한항공.pdf"
  "pdf_신한은행.pdf"
  "wod파일_하나은행.docx"
  "스캔본_신한카드.pdf"
)

for f in "${FILES[@]}"; do
  src="$ROOT/data_real/raw/$f"
  stem="${f%.*}"
  echo "[$(date +%H:%M:%S)] upload: $f"
  resp=$(curl -s -X POST http://localhost:8001/documents -F "file=@$src" -F "engine=v_rule")
  doc_id=$(echo "$resp" | /usr/bin/python3 -c "import json,sys; print(json.load(sys.stdin).get('doc_id',''))" 2>/dev/null)
  if [ -z "$doc_id" ]; then
    echo "[$(date +%H:%M:%S)] UPLOAD FAIL: $f — $resp"
    echo "$stem\tFAIL_UPLOAD\t" >> "$TSV"
    continue
  fi
  # 완료 폴링 (최대 40분/문서)
  stage=""
  for i in $(seq 1 240); do
    sleep 10
    stage=$(curl -s "http://localhost:8001/documents/$doc_id/pipeline" | /usr/bin/python3 -c "import json,sys; print(json.load(sys.stdin).get('stage',''))" 2>/dev/null)
    if [ "$stage" = "READY_FOR_REVIEW" ] || [ "$stage" = "FAILED" ]; then break; fi
  done
  echo "[$(date +%H:%M:%S)] $f → $doc_id ($stage)"
  echo "$stem\t$doc_id\t$stage" >> "$TSV"
  if [ "$stage" = "READY_FOR_REVIEW" ]; then
    curl -s "http://localhost:8001/documents/$doc_id/requirements" > "$OUT/${stem}_reqs.json"
    curl -s -o "$XLSX/${stem}.xlsx" "http://localhost:8001/documents/$doc_id/export-fixed"
  fi
done
echo "[$(date +%H:%M:%S)] BATCH DONE"

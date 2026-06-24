#!/usr/bin/env bash
# 워크스페이스 완전 초기화 — 사이드바 프로젝트 목록을 처음 상태로 되돌림
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"

echo "==> rfp-matcher 워크스페이스 초기화"
echo "    (artifacts + storage + 백엔드 in-memory 세션)"
echo ""

# 1) 백엔드 재시작 — in-memory repo 초기화
pids=$(lsof -tiTCP:"${BACKEND_PORT}" -sTCP:LISTEN 2>/dev/null || true)
if [[ -n "$pids" ]]; then
  echo "→ 백엔드(port ${BACKEND_PORT}) 중지: ${pids}"
  # shellcheck disable=SC2086
  kill ${pids} 2>/dev/null || true
  sleep 1
else
  echo "→ 백엔드 미실행 (in-memory 세션 없음)"
fi

# 2) 디스크 캐시·작업 디렉터리 삭제
for dir in "$ROOT/data/artifacts" "$ROOT/data/storage"; do
  if [[ -d "$dir" ]]; then
    count=$(find "$dir" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')
    rm -rf "${dir:?}/"*
    echo "→ ${dir} 비움 (${count}개 항목 삭제)"
  fi
done

echo ""
echo "완료. 다음 단계:"
echo "  1. ./scripts/dev.sh 로 백엔드·프론트 재시작 (또는 uvicorn만 재실행)"
echo "  2. 브라우저에서 http://localhost:3000 새로고침"
echo "     (localStorage는 서버·캐시가 비면 자동 정리됨)"
echo "  3. 그래도 남으면 DevTools 콘솔에서:"
echo "     localStorage.removeItem('rfp-matcher-workspace-v1')"

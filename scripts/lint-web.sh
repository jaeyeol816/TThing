#!/usr/bin/env bash
# 웹 UI 정적 검사 (U4 BR-U-05 / BR-U-12).
#
# 리뷰 매너가 아니라 CI 가 잡는다.
# U4 는 Day 4 에 만들어지므로 파일이 없으면 조용히 통과한다.

set -uo pipefail

WEB="src/mesh/web"
fail=0

check() {  # check <설명> <규칙ID> <grep 패턴> <파일...>
  local desc="$1" rule="$2" pattern="$3"; shift 3
  local existing=()
  for f in "$@"; do [[ -f "$f" ]] && existing+=("$f"); done
  [[ ${#existing[@]} -eq 0 ]] && return 0

  if grep -nE "$pattern" "${existing[@]}" 2>/dev/null; then
    echo "  ✗ $rule  $desc"
    fail=1
  fi
}

if [[ ! -d "$WEB" ]] || [[ -z "$(ls -A "$WEB" 2>/dev/null)" ]]; then
  echo "lint-web: $WEB 이 비어 있다 (U4 는 Day 4). 생략."
  exit 0
fi

echo "lint-web: 웹 UI 정적 검사"

# BR-U-05 — 인용에 경로를 표시하지 않는다. 응답에 필드가 없으므로 참조도 불가
check "internal_path 참조 (권한 우회)" "BR-U-05" 'internal_path' "$WEB/app.js"

# BR-U-12 — XSS 와 CSP
check "innerHTML 사용 (XSS)" "BR-U-12" 'innerHTML' "$WEB/app.js"
check "인라인 스크립트·스타일·onclick (CSP 위반)" "BR-U-12" \
      '(onclick=|<script>|<style>)' "$WEB/index.html"
check "외부 CDN 참조 (SRI N/A 근거가 깨진다)" "BR-U-12" \
      'https?://' "$WEB/index.html" "$WEB/app.js" "$WEB/style.css"
check "eval / new Function" "BR-U-12" '(\beval\(|new Function\()' "$WEB/app.js"

# BR-U-01 — 미리보기 페이로드는 전문을 보여준다. 생략 금지
check "페이로드 접기·생략" "BR-U-01" '<details' "$WEB/index.html" "$WEB/app.js"

if [[ $fail -eq 0 ]]; then
  echo "  ✓ 통과"
fi
exit $fail

#!/usr/bin/env python3
"""웹 UI 정적 검사 (U4 BR-U-01 / BR-U-05 / BR-U-12).

리뷰 매너가 아니라 CI 가 잡는다.

──────────────────────────────────────────────────────────────────────
왜 bash grep 을 버렸나
──────────────────────────────────────────────────────────────────────

`scripts/lint-web.sh` 는 문자열 grep 이었고, 화면을 만들자마자 **오탐 3건**을 냈다.

    ✗ internal_path   -> 주석 2건 ("internal_path 를 참조하지 않는다")
    ✗ innerHTML       -> 주석 1건 ("innerHTML 을 쓰지 않는다")
    ✗ <details>       -> 붙여넣기 폼을 접는 것 (페이로드가 아니다)

Day 2·Day 3 에서 같은 문제를 두 번 겪었다 (`store` 의 `classify` 검사,
`main` 의 `StaticFiles` 검사). **문자열 검사는 규칙을 설명하는 주석까지 잡는다.**
그러면 사람이 주석을 지우거나 검사를 약화시키는데, 둘 다 나쁘다.

그래서 이 스크립트는
  1. 주석을 먼저 제거하고 검사한다
  2. 의도된 예외를 **줄 단위 마커**로 허용한다 (`lint-web: allow <규칙>`)
  3. 검사 대상 영역을 좁힌다 (`<details>` 는 미리보기 모달 안에서만 금지)

마커를 쓰면 새 위반이 생겼을 때 여전히 잡힌다 — 예외가 명시적이고 세어진다.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "mesh" / "web"

ALLOW_MARKER = "lint-web: allow"

#: 예외 허용 상한. 늘어나면 규칙이 무의미해지므로 숫자로 못 박는다.
MAX_ALLOWED_EXCEPTIONS = 2


# ══════════════════════════════════════════════════════════════════════
# 주석 제거
# ══════════════════════════════════════════════════════════════════════


def strip_js_comments(text: str) -> list[str]:
    """줄 번호를 유지하면서 주석을 공백으로 바꾼다.

    문자열 리터럴 안의 `//` 를 주석으로 오인하지 않도록 상태 기계로 훑는다.
    정규식으로 하면 `"https://…"` 같은 문자열이 주석으로 잘린다.
    """
    out: list[str] = []
    i, n = 0, len(text)
    buf: list[str] = []
    state = "code"  # code | line | block | s | d | t
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        match state:
            case "code":
                if ch == "/" and nxt == "/":
                    state, i = "line", i + 2
                    continue
                if ch == "/" and nxt == "*":
                    state, i = "block", i + 2
                    continue
                if ch in "'\"`":
                    state = {"'": "s", '"': "d", "`": "t"}[ch]
                buf.append(ch)
            case "line":
                if ch == "\n":
                    state = "code"
                    buf.append(ch)
                else:
                    buf.append(" ")
            case "block":
                if ch == "*" and nxt == "/":
                    state, i = "code", i + 2
                    buf.append("  ")
                    continue
                buf.append("\n" if ch == "\n" else " ")
            case _:  # 문자열 안
                buf.append(ch)
                if ch == "\\":
                    if i + 1 < n:
                        buf.append(text[i + 1])
                    i += 2
                    continue
                closer = {"s": "'", "d": '"', "t": "`"}[state]
                if ch == closer:
                    state = "code"
        i += 1
    out = "".join(buf).splitlines()
    return out


def strip_html_comments(text: str) -> list[str]:
    """`<!-- -->` 를 공백으로. 줄 번호를 유지한다."""

    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return re.sub(r"<!--.*?-->", blank, text, flags=re.DOTALL).splitlines()


# ══════════════════════════════════════════════════════════════════════
# 검사
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)

    def add(self, path: Path, lineno: int, rule: str, desc: str, line: str, raw: str) -> None:
        """`raw` 는 주석을 제거하지 **않은** 원본 줄이다.

        허용 마커(`lint-web: allow`)는 주석에 쓰므로, 제거된 줄에서 찾으면
        절대 발견되지 않는다. 처음 이 실수를 해서 마커가 동작하지 않았다.
        """
        where = f"{path.relative_to(REPO)}:{lineno}"
        if ALLOW_MARKER in raw:
            self.allowed.append(f"{where}  {rule}  (허용됨)")
            return
        self.violations.append(f"{where}  {rule}  {desc}\n      {line.strip()[:110]}")


def scan(
    report: Report,
    path: Path,
    lines: list[str],
    raw_lines: list[str],
    rule: str,
    desc: str,
    pattern: str,
    *,
    within: tuple[int, int] | None = None,
) -> None:
    rx = re.compile(pattern)
    for i, line in enumerate(lines, start=1):
        if within and not (within[0] <= i <= within[1]):
            continue
        if rx.search(line):
            raw = raw_lines[i - 1] if i - 1 < len(raw_lines) else line
            report.add(path, i, rule, desc, line, raw)


def region(lines: list[str], start_rx: str, end_rx: str) -> tuple[int, int] | None:
    """`start_rx` 부터 `end_rx` 까지의 줄 범위. 없으면 `None`."""
    start = end = None
    for i, line in enumerate(lines, start=1):
        if start is None and re.search(start_rx, line):
            start = i
        elif start is not None and re.search(end_rx, line):
            end = i
            break
    return (start, end) if start and end else None


def main() -> int:
    if not WEB.is_dir() or not any(WEB.iterdir()):
        print("lint-web: src/mesh/web 이 비어 있다. 생략.")
        return 0

    print("lint-web: 웹 UI 정적 검사")
    report = Report()

    html_path, js_path, css_path = WEB / "index.html", WEB / "app.js", WEB / "style.css"
    missing = [p.name for p in (html_path, js_path, css_path) if not p.is_file()]
    if missing:
        print(f"  ✗ 필수 파일이 없다: {', '.join(missing)}")
        return 1

    html_raw = html_path.read_text(encoding="utf-8").splitlines()
    js_raw = js_path.read_text(encoding="utf-8").splitlines()
    html = strip_html_comments("\n".join(html_raw))
    js = strip_js_comments("\n".join(js_raw))
    css_raw = css_path.read_text(encoding="utf-8")
    css_raw_lines = css_raw.splitlines()
    css = re.sub(
        r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), css_raw, flags=re.DOTALL
    )
    css_lines = css.splitlines()

    # ── BR-U-12 · XSS 와 CSP ──────────────────────────────────────────
    scan(
        report,
        js_path,
        js,
        js_raw,
        "BR-U-12",
        "HTML 문자열 주입 — 답변에는 문서 원문이 들어 있다",
        r"\b(innerHTML|outerHTML|insertAdjacentHTML)\b",
    )
    scan(report, js_path, js, js_raw, "BR-U-12", "document.write", r"document\.write\s*\(")
    scan(
        report,
        js_path,
        js,
        js_raw,
        "BR-U-12",
        "eval / new Function",
        r"(\beval\s*\(|new\s+Function\s*\()",
    )
    scan(
        report,
        html_path,
        html,
        html_raw,
        "BR-U-12",
        "인라인 이벤트 핸들러 (CSP 가 막는다)",
        r"\son[a-z]+\s*=\s*[\"']",
    )
    scan(report, html_path, html, html_raw, "BR-U-12", "인라인 style 속성", r"\sstyle\s*=\s*[\"']")

    # 외부 CDN 0건 — SRI 를 N/A 로 둔 근거이자 오프라인 데모의 전제
    for path, lines, raw_lines in (
        (html_path, html, html_raw),
        (js_path, js, js_raw),
        (css_path, css_lines, css_raw_lines),
    ):
        scan(report, path, lines, raw_lines, "SECURITY-10", "외부 CDN 참조", r"https?://")

    # ── BR-U-05 · 인용에 경로를 표시하지 않는다 ───────────────────────
    scan(
        report,
        js_path,
        js,
        js_raw,
        "BR-U-05",
        "internal_path 참조 (권한 우회). 소유자 문서 관리 화면만 예외",
        r"\binternal_path\b",
    )

    # ── BR-U-01 · 미리보기 페이로드는 전문을 보여준다 ─────────────────
    modal = region(html, r'id="preview-modal"', r"</dialog>")
    if modal:
        scan(
            report,
            html_path,
            html,
            html_raw,
            "BR-U-01",
            "미리보기 안에서 접기 요소",
            r"<details",
            within=modal,
        )
    scan(
        report,
        js_path,
        js,
        js_raw,
        "BR-U-01",
        "페이로드 절단 (slice/substring)",
        r"payload(?!_sha256)\w*[^\n]*\.(slice|substring|substr)\s*\(",
    )
    # `max-height`/`overflow: hidden` 이 .payload 에 붙으면 전문이 잘린다
    payload_css = region(css_lines, r"^\.payload\s*\{", r"^\}")
    if payload_css:
        scan(
            report,
            css_path,
            css_lines,
            css_raw_lines,
            "BR-U-01",
            "페이로드 영역 높이 제한",
            r"(max-height|overflow\s*:\s*hidden)",
            within=payload_css,
        )

    # ── 접근성 ────────────────────────────────────────────────────────
    if not any("aria-live" in ln for ln in html):
        report.violations.append("index.html  A11Y  aria-live 영역이 없다")
    if not any('role="tablist"' in ln for ln in html):
        report.violations.append('index.html  A11Y  role="tablist" 이 없다')
    if ":focus-visible" not in css:
        report.violations.append("style.css  A11Y  :focus-visible 스타일이 없다")

    # ── 결과 ──────────────────────────────────────────────────────────
    for line in report.allowed:
        print(f"  · {line}")
    if len(report.allowed) > MAX_ALLOWED_EXCEPTIONS:
        report.violations.append(
            f"허용 예외가 {len(report.allowed)}건이다 (상한 {MAX_ALLOWED_EXCEPTIONS}). "
            "예외가 늘어나면 규칙이 무의미해진다"
        )

    if report.violations:
        print()
        for v in report.violations:
            print(f"  ✗ {v}")
        print(f"\n  위반 {len(report.violations)}건")
        return 1

    print(f"  ✓ 통과 (허용 예외 {len(report.allowed)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

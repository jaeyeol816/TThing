"""`scripts/lint_web.py` 자체를 검사한다 (U4).

**검사기를 테스트하지 않으면 검사기가 조용히 아무것도 안 할 수 있다.**

이 프로젝트는 이미 그 실패를 겪었다. 이전 버전(`scripts/lint-web.sh`)은 bash
grep 이었고 규칙을 설명하는 *주석*까지 위반으로 잡았다 (오탐 3건). 그래서
주석 제거 단계를 넣었는데, 그 다음엔 허용 마커를 **주석 제거된 줄**에서 찾아
마커가 전혀 동작하지 않았다.

두 실패의 공통점: 검사기가 "통과"를 출력했지만 그 통과가 아무 의미가 없었다.
그래서 여기서 세 가지를 각각 고정한다.

  ① 주석 제거가 문자열 리터럴을 망가뜨리지 않는다
  ② 허용 마커가 실제로 동작한다 (원본 줄에서 찾는다)
  ③ 심어둔 위반을 잡는다 — 규칙별로 하나씩
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def lw():
    """`scripts/lint_web.py` 를 모듈로 로드한다 (패키지가 아니라 스크립트다)."""
    spec = importlib.util.spec_from_file_location(
        "lint_web_script", REPO / "scripts" / "lint_web.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["lint_web_script"] = module
    spec.loader.exec_module(module)
    return module


# ══════════════════════════════════════════════════════════════════════
# ① 주석 제거 — 문자열 리터럴을 건드리지 않는다
# ══════════════════════════════════════════════════════════════════════


def test_문자열_안의_슬래시두개를_주석으로_보지_않는다(lw) -> None:
    """`"https://…"` 가 주석으로 잘리면 SECURITY-10 검사가 무력화된다.

    정확히 뒤집힌 위험이다: 외부 CDN 참조를 잡는 규칙이 그 참조를 주석으로
    오인해 지워버리면, 검사기는 "외부 URL 0건"이라고 보고한다.
    """
    src = 'const u = "https://cdn.example.com/x.js";\n'
    assert lw.strip_js_comments(src) == [src.rstrip("\n")]


@pytest.mark.parametrize(
    "quote",
    ["'", '"', "`"],
    ids=["단일", "이중", "템플릿"],
)
def test_모든_인용부호_안에서_주석을_보존한다(lw, quote: str) -> None:
    src = f"const a = {quote}// not a comment{quote};\n"
    assert "// not a comment" in lw.strip_js_comments(src)[0]


def test_줄_주석을_제거한다(lw) -> None:
    out = lw.strip_js_comments("const a = 1; // innerHTML 을 쓰지 않는다\n")
    assert "innerHTML" not in out[0]
    assert "const a = 1;" in out[0]


def test_블록_주석을_제거하고_줄수를_유지한다(lw) -> None:
    """줄 번호가 밀리면 위반 위치를 잘못 보고한다 — 고치러 갔더니 없는 줄이다."""
    src = "const a = 1;\n/* innerHTML\n   internal_path */\nconst b = 2;\n"
    out = lw.strip_js_comments(src)
    assert len(out) == 4
    assert "innerHTML" not in "".join(out)
    assert "internal_path" not in "".join(out)
    assert "const b = 2;" in out[3]


def test_문자열_안의_이스케이프된_인용부호를_넘긴다(lw) -> None:
    """`"a\\"b" // c` 에서 문자열이 일찍 닫혔다고 오판하면 그 뒤가 다 깨진다."""
    src = 'const a = "he said \\"hi\\""; // innerHTML\n'
    out = lw.strip_js_comments(src)[0]
    assert "innerHTML" not in out
    assert 'he said \\"hi\\"' in out


def test_줄수는_언제나_보존된다(lw) -> None:
    src = "a\n// b\nc\n/* d\ne */\nf\n"
    assert len(lw.strip_js_comments(src)) == len(src.splitlines())


def test_html_주석을_제거하고_줄수를_유지한다(lw) -> None:
    src = "<p>a</p>\n<!-- <details> 는\n     여기서 금지 -->\n<p>b</p>\n"
    out = lw.strip_html_comments(src)
    assert len(out) == 4
    assert "<details>" not in "".join(out)
    assert "<p>b</p>" in out[3]


# ══════════════════════════════════════════════════════════════════════
# region — 영역 한정
# ══════════════════════════════════════════════════════════════════════


def test_region_이_시작과_끝을_찾는다(lw) -> None:
    lines = ["a", 'id="preview-modal"', "b", "</dialog>", "c"]
    assert lw.region(lines, r'id="preview-modal"', r"</dialog>") == (2, 4)


def test_region_이_끝을_못_찾으면_None(lw) -> None:
    assert lw.region(["a", 'id="preview-modal"', "b"], r'id="preview-modal"', r"</dialog>") is None


def test_region_이_시작을_못_찾으면_None(lw) -> None:
    assert lw.region(["a", "</dialog>"], r'id="preview-modal"', r"</dialog>") is None


# ══════════════════════════════════════════════════════════════════════
# ② 허용 마커 — 원본 줄에서 찾는다
# ══════════════════════════════════════════════════════════════════════


def test_허용_마커가_위반을_예외로_옮긴다(lw) -> None:
    report = lw.Report()
    report.add(
        REPO / "src" / "mesh" / "web" / "app.js",
        7,
        "BR-U-05",
        "설명",
        "doc.internal_path",  # 주석이 제거된 줄
        "doc.internal_path; // lint-web: allow BR-U-05",  # 원본 줄
    )
    assert report.violations == []
    assert len(report.allowed) == 1


def test_마커가_없으면_위반이다(lw) -> None:
    report = lw.Report()
    report.add(
        REPO / "src" / "mesh" / "web" / "app.js",
        7,
        "BR-U-05",
        "설명",
        "doc.internal_path",
        "doc.internal_path;",
    )
    assert len(report.violations) == 1
    assert report.allowed == []


def test_마커를_주석제거된_줄에서_찾지_않는다(lw) -> None:
    """마커는 주석에 쓴다. 제거된 줄에는 절대 남아 있지 않다.

    이 테스트가 회귀 방지의 핵심이다 — 처음 구현에서 `line` 을 봤고,
    그래서 모든 마커가 무시됐다.
    """
    report = lw.Report()
    report.add(
        REPO / "src" / "mesh" / "web" / "app.js",
        7,
        "BR-U-05",
        "설명",
        f"doc.internal_path  {lw.ALLOW_MARKER} BR-U-05",  # 여기 있어도 소용없어야 한다
        "doc.internal_path;",  # 원본에는 마커가 없다
    )
    assert len(report.violations) == 1, "원본 줄에 없는 마커를 인정하면 안 된다"


# ══════════════════════════════════════════════════════════════════════
# ③ 심은 위반을 잡는다 — 규칙별
# ══════════════════════════════════════════════════════════════════════

MINIMAL_HTML = """<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><title>t</title><link rel="stylesheet" href="/style.css"></head>
<body>
  <div role="tablist"><button role="tab" id="t1">A</button></div>
  <p aria-live="polite" id="status"></p>
  <dialog id="preview-modal"><pre class="payload"></pre></dialog>
  <script src="/app.js"></script>
</body>
</html>
"""

MINIMAL_CSS = """.payload { white-space: pre-wrap; }
:focus-visible { outline: 2px solid #58a6ff; }
"""

MINIMAL_JS = """const el = document.getElementById("status");
el.textContent = "ok";
"""


@pytest.fixture
def web(tmp_path: Path, lw, monkeypatch):
    """검사 대상 디렉터리를 tmp 로 바꾼다. 통과하는 최소 화면을 심는다."""
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text(MINIMAL_HTML, encoding="utf-8")
    (root / "style.css").write_text(MINIMAL_CSS, encoding="utf-8")
    (root / "app.js").write_text(MINIMAL_JS, encoding="utf-8")
    monkeypatch.setattr(lw, "WEB", root)
    monkeypatch.setattr(lw, "REPO", tmp_path)
    return root


def test_최소_화면은_통과한다(lw, web) -> None:
    """기준선이 통과하지 않으면 아래 위반 테스트가 무엇을 잡는지 알 수 없다."""
    assert lw.main() == 0


@pytest.mark.parametrize(
    ("target", "snippet", "rule"),
    [
        ("app.js", "el.innerHTML = answer;", "BR-U-12"),
        ("app.js", 'el.insertAdjacentHTML("beforeend", answer);', "BR-U-12"),
        ("app.js", "document.write(answer);", "BR-U-12"),
        ("app.js", "eval(answer);", "BR-U-12"),
        ("app.js", 'const f = new Function("return 1");', "BR-U-12"),
        ("app.js", 'fetch("https://api.example.com/x");', "SECURITY-10"),
        ("app.js", "render(doc.internal_path);", "BR-U-05"),
        ("app.js", "const s = payloadText.slice(0, 100);", "BR-U-01"),
        ("index.html", '  <button onclick="go()">x</button>', "BR-U-12"),
        ("index.html", '  <p style="color:red">x</p>', "BR-U-12"),
        ("index.html", '  <script src="https://cdn.example.com/x.js"></script>', "SECURITY-10"),
        ("style.css", ".x { background: url(https://cdn.example.com/a.png); }", "SECURITY-10"),
    ],
    ids=[
        "innerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval",
        "new-Function",
        "js-외부URL",
        "internal_path",
        "페이로드-절단",
        "인라인-핸들러",
        "인라인-style",
        "html-외부URL",
        "css-외부URL",
    ],
)
def test_심은_위반을_잡는다(lw, web, capsys, target: str, snippet: str, rule: str) -> None:
    path = web / target
    path.write_text(path.read_text(encoding="utf-8") + snippet + "\n", encoding="utf-8")
    assert lw.main() == 1
    assert rule in capsys.readouterr().out


def test_주석_안의_위반은_잡지_않는다(lw, web, capsys) -> None:
    """오탐 회귀 방지. 규칙을 설명하는 주석 때문에 CI 가 빨개지면
    사람이 주석을 지우거나 검사를 약화시킨다. 둘 다 나쁘다."""
    (web / "app.js").write_text(
        MINIMAL_JS
        + "// innerHTML 을 쓰지 않는다. internal_path 도 참조하지 않는다\n"
        + "/* eval / document.write / https://cdn.example.com 도 금지 */\n",
        encoding="utf-8",
    )
    (web / "index.html").write_text(
        MINIMAL_HTML.replace(
            "<body>",
            '<body>\n  <!-- onclick="x" 과 style="y" 는 쓰지 않는다 -->',
        ),
        encoding="utf-8",
    )
    assert lw.main() == 0, capsys.readouterr().out


def test_허용_마커가_붙은_위반은_통과한다(lw, web, capsys) -> None:
    (web / "app.js").write_text(
        MINIMAL_JS + f"render(doc.internal_path); // {lw.ALLOW_MARKER} BR-U-05\n",
        encoding="utf-8",
    )
    assert lw.main() == 0
    assert "허용됨" in capsys.readouterr().out


def test_허용_예외가_상한을_넘으면_실패한다(lw, web) -> None:
    """예외가 늘어나면 규칙이 무의미해진다. 숫자로 못 박는다."""
    extra = "\n".join(
        f"render(doc.internal_path); // {lw.ALLOW_MARKER} BR-U-05"
        for _ in range(lw.MAX_ALLOWED_EXCEPTIONS + 1)
    )
    (web / "app.js").write_text(MINIMAL_JS + extra + "\n", encoding="utf-8")
    assert lw.main() == 1


# ── 영역 한정 ──────────────────────────────────────────────────────────


def test_미리보기_안의_접기요소는_잡는다(lw, web) -> None:
    (web / "index.html").write_text(
        MINIMAL_HTML.replace(
            '<dialog id="preview-modal"><pre class="payload"></pre></dialog>',
            '<dialog id="preview-modal">\n    <details><summary>s</summary></details>\n'
            '    <pre class="payload"></pre>\n  </dialog>',
        ),
        encoding="utf-8",
    )
    assert lw.main() == 1


def test_미리보기_밖의_접기요소는_허용한다(lw, web) -> None:
    """`<details>` 자체가 나쁜 게 아니다. **나갈 내용을 접는 것**이 나쁘다.

    붙여넣기 폼을 접는 데 쓰는 것은 막을 이유가 없다. 영역을 한정하지
    않았을 때 정확히 이 오탐이 났다.
    """
    (web / "index.html").write_text(
        MINIMAL_HTML.replace(
            "<body>", "<body>\n  <details><summary>문서 붙여넣기</summary></details>"
        ),
        encoding="utf-8",
    )
    assert lw.main() == 0


def test_payload_영역의_높이제한을_잡는다(lw, web) -> None:
    """전문을 보여준다고 하면서 CSS 로 자르면 거짓말이 된다 (BR-U-01)."""
    (web / "style.css").write_text(
        ".payload {\n  white-space: pre-wrap;\n  max-height: 200px;\n}\n"
        ":focus-visible { outline: 2px solid #58a6ff; }\n",
        encoding="utf-8",
    )
    assert lw.main() == 1


def test_payload_밖의_높이제한은_허용한다(lw, web) -> None:
    (web / "style.css").write_text(
        ".sidebar {\n  max-height: 200px;\n  overflow: hidden;\n}\n"
        ".payload { white-space: pre-wrap; }\n"
        ":focus-visible { outline: 2px solid #58a6ff; }\n",
        encoding="utf-8",
    )
    assert lw.main() == 0


def test_payload_sha256_은_절단_검사에_걸리지_않는다(lw, web) -> None:
    """해시를 자르는 것은 페이로드를 자르는 것이 아니다.

    `payload\\w*` 로 썼을 때 `payload_sha256.slice(0, 12)` 가 위반으로 잡혔다.
    화면에 해시 앞 12자만 보여주는 것은 정당하다.
    """
    (web / "app.js").write_text(
        MINIMAL_JS + "const short = preview.payload_sha256.slice(0, 12);\n", encoding="utf-8"
    )
    assert lw.main() == 0


# ── 접근성 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("target", "old", "new"),
    [
        ("index.html", 'aria-live="polite" ', ""),
        ("index.html", 'role="tablist"', 'class="tablist"'),
        ("style.css", ":focus-visible", ".focus-ring"),
    ],
    ids=["aria-live-없음", "tablist-없음", "focus-visible-없음"],
)
def test_접근성_요소가_빠지면_실패한다(lw, web, target: str, old: str, new: str) -> None:
    path = web / target
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    assert lw.main() == 1


# ── 파일 존재 ─────────────────────────────────────────────────────────


def test_필수_파일이_없으면_실패한다(lw, web) -> None:
    (web / "app.js").unlink()
    assert lw.main() == 1


def test_웹_디렉터리가_비어_있으면_생략한다(lw, tmp_path, monkeypatch) -> None:
    """화면이 아직 없는 시점에도 `make lint` 가 돌아야 한다."""
    empty = tmp_path / "empty-web"
    empty.mkdir()
    monkeypatch.setattr(lw, "WEB", empty)
    assert lw.main() == 0


# ══════════════════════════════════════════════════════════════════════
# 실물
# ══════════════════════════════════════════════════════════════════════


def test_실제_화면이_검사를_통과한다(lw, capsys) -> None:
    """`make lint` 와 같은 것을 본다.

    여기가 깨지면 `make lint` 도 깨져 있다 — 커밋 전에 잡힌다.
    """
    assert lw.main() == 0, capsys.readouterr().out

/* 대리 에이전트 메시 — 화면
 *
 * ──────────────────────────────────────────────────────────────────
 * 이 파일이 지키는 것
 * ──────────────────────────────────────────────────────────────────
 *
 *  1. `innerHTML` 을 쓰지 않는다. 전부 `document.createElement` +
 *     `textContent` 다. 답변에는 문서 원문이 들어 있고, 그것을 HTML 로
 *     해석하면 XSS 가 된다 (BR-U-12). CSP 가 인라인을 막지만 DOM 주입은
 *     CSP 로 막히지 않는다.
 *
 *  2. `internal_path` 를 참조하지 않는다. 답변·인용·감사 응답에 그 필드가
 *     없다 (FR-43). 유일한 예외는 `/api/documents` — 자기가 올린 문서의
 *     경로를 자기가 보는 화면이다.
 *
 *  3. 페이로드를 생략하지 않는다. `slice`·`…`·`<details>` 로 접지 않는다.
 *     사람이 전문을 보고 승인하는 것이 4번째 방어 겹이다 (BR-U-01).
 *
 *  4. 배지는 색상과 텍스트를 함께 쓴다. 색만으로 등급을 구분하지 않는다.
 *
 *  5. 병기된 답변을 정렬하지 않는다. 서버가 준 순서(요청 순서)를 유지한다.
 *     신뢰도로 정렬하면 사용자가 위쪽 답을 정답으로 읽는다 (BR-U-07).
 *
 * 목차
 *   §1  상태            §6  질문 · 미리보기
 *   §2  DOM 도구        §7  답변
 *   §3  표시 매핑       §8  문서 업로드
 *   §4  API             §9  인박스
 *   §5  헤더 · 탭       §10 감사
 */

"use strict";

// ══════════════════════════════════════════════════════════════════
// §1 상태
// ══════════════════════════════════════════════════════════════════

const state = {
  tab: "ask",
  currentUser: null,
  users: [],
  agents: [],
  presets: [],
  selected: [],           // 지목한 entity_id (최대 2)
  busy: false,
  health: null,

  prepared: null,         // PrepareResult
  modalQueue: [],         // 순차로 보여줄 PreparedCall
  approvedIds: [],
  fallbacks: [],          // 차단된 호출의 폴백 답변
  result: null,           // AskResult

  documents: [],
  inbox: [],
  audit: null,
  auditQuery: "",
};

const MAX_TARGETS = 2;
const MAX_QUESTION = 4000;
const QUICK_SEARCHES = ["REQ-4412", "EAP-AKA", "H社", "12억원", "session_binding"];

// ══════════════════════════════════════════════════════════════════
// §2 DOM 도구
// ══════════════════════════════════════════════════════════════════

const $ = (id) => document.getElementById(id);

/** 요소를 만든다. `text` 는 항상 `textContent` 로 들어간다. */
function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text !== undefined) node.textContent = String(opts.text);
  if (opts.attrs) {
    for (const [k, v] of Object.entries(opts.attrs)) {
      if (v !== null && v !== undefined && v !== false) node.setAttribute(k, String(v));
    }
  }
  if (opts.on) {
    for (const [evt, fn] of Object.entries(opts.on)) node.addEventListener(evt, fn);
  }
  for (const child of children) {
    if (child) node.appendChild(child);
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function badge(text, kind = "muted", extraAttrs = null) {
  return el("span", { class: `badge badge-${kind}`, text, attrs: extraAttrs });
}

function emptyRow(text) {
  return el("p", { class: "empty", text });
}

function setStatus(text, kind = "") {
  const node = $("status-line");
  node.className = `status ${kind}`.trim();
  node.textContent = text;
}

// ══════════════════════════════════════════════════════════════════
// §3 표시 매핑
// ══════════════════════════════════════════════════════════════════

const TIER = {
  secret:   { label: "기밀", kind: "secret" },
  internal: { label: "사내", kind: "internal" },
  open:     { label: "공개", kind: "open" },
};

const DISPOSITION = {
  auto:       { label: "자동 응답", kind: "ok" },
  unverified: { label: "미검증 — 담당자 확인 요청됨", kind: "warn" },
  escalate:   { label: "담당자에게 전달했습니다", kind: "warn" },
  blocked:    { label: "전송하지 않았습니다", kind: "bad" },
};

const ACTIVITY = {
  active:  { label: "활동 중", dot: "dot-active" },
  away:    { label: "자리 비움", dot: "dot-away" },
  offline: { label: "오프라인", dot: "dot-offline" },
};

const FRESHNESS = {
  live:    "실시간",
  stale:   "이전 기준",
  expired: "세션 오래됨",
};

const STAGE = {
  schema: "스키마", vocab: "어휘", range: "범위",
  banned: "금칙어", ngram: "원문대조", size: "크기",
};

const INBOX_STATUS = {
  open: { label: "대기", kind: "warn" },
  approved: { label: "승인됨", kind: "ok" },
  approved_with_edit: { label: "수정 후 승인", kind: "ok" },
  redirected: { label: "다른 담당자 지목", kind: "muted" },
};

const tierBadge = (tier) => {
  const t = TIER[tier] || TIER.internal;
  return badge(t.label, t.kind);
};

const fmtTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString("ko-KR", { hour12: false });
};

const fmtBytes = (n) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

// ══════════════════════════════════════════════════════════════════
// §4 API
// ══════════════════════════════════════════════════════════════════

/** 오류를 사람이 읽을 문장으로 바꾼다. 서버는 correlation_id 만 준다. */
const HTTP_MESSAGE = {
  410: "미리보기가 만료되었거나 이미 전송되었습니다. 다시 질문해 주세요.",
  422: "입력을 확인해 주세요.",
  404: "찾을 수 없습니다.",
  409: "이미 처리된 항목입니다.",
  429: "잠시 후 다시 시도해 주세요.",
  500: "서버에서 오류가 발생했습니다.",
};

class ApiError extends Error {
  constructor(status, body) {
    const base = HTTP_MESSAGE[status] || `요청이 실패했습니다 (${status}).`;
    const detail = body && body.detail ? ` ${body.detail}` : "";
    super(base + detail);
    this.status = status;
    this.correlationId = body ? body.correlation_id : null;
  }
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      method: options.method || "GET",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch (cause) {
    throw new Error("서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인해 주세요.");
  }
  if (!response.ok) {
    let body = null;
    try { body = await response.json(); } catch { /* 본문이 없을 수 있다 */ }
    throw new ApiError(response.status, body);
  }
  if (response.status === 204) return null;
  return response.json();
}

// ══════════════════════════════════════════════════════════════════
// §5 헤더 · 탭
// ══════════════════════════════════════════════════════════════════

function renderHealth() {
  const box = $("health-badges");
  clear(box);
  const h = state.health;
  if (!h) {
    box.appendChild(badge("상태를 읽을 수 없음", "bad"));
    return;
  }

  // 목업 모드를 숨기지 않는다 (BR-U-09).
  if (h.exaone_mode === "mock") {
    box.appendChild(badge("MOCK · 목업 모드", "warn", { "data-testid": "health-mode-badge" }));
  } else {
    box.appendChild(badge("LIVE · 실제 모델", "ok", { "data-testid": "health-mode-badge" }));
  }

  box.appendChild(badge(`Agent: ${h.agent_transport}`, "accent"));

  let host = h.trusted_zone_llm_base_url;
  try { host = new URL(h.trusted_zone_llm_base_url).host; } catch { /* 그대로 쓴다 */ }
  box.appendChild(badge(`원문 모델: ${host}`, "muted"));

  if (h.trust_boundary_simulated) {
    box.appendChild(el("button", {
      class: "badge badge-warn",
      text: "⚠ 경계 시뮬레이션 — 설명",
      attrs: { type: "button", "data-testid": "trust-boundary-badge" },
      on: { click: () => { $("trust-boundary-notice").hidden = false; } },
    }));
  }

  const rate = h.disposition_counts || {};
  const total = Object.values(rate).reduce((a, b) => a + b, 0);
  if (total > 0) {
    const auto = rate.auto || 0;
    box.appendChild(badge(`자동 응답 ${Math.round((auto / total) * 100)}% (${auto}/${total})`, "muted"));
  }
  $("tz-url").textContent = h.trusted_zone_llm_base_url;
}

function renderUsers() {
  const sel = $("user-select");
  clear(sel);
  for (const u of state.users) {
    sel.appendChild(el("option", { text: `${u.display_name}`, attrs: { value: u.entity_id } }));
  }
  if (state.currentUser) sel.value = state.currentUser;
}

function switchTab(name) {
  state.tab = name;
  for (const btn of document.querySelectorAll('[role="tab"]')) {
    const on = btn.dataset.tab === name;
    btn.setAttribute("aria-selected", String(on));
  }
  for (const panel of document.querySelectorAll('[role="tabpanel"]')) {
    panel.hidden = panel.id !== `panel-${name}`;
  }
  if (name === "docs") loadDocuments();
  if (name === "inbox") loadInbox();
  if (name === "audit") loadAudit();
}

// ══════════════════════════════════════════════════════════════════
// §6 지목 · 질문 · 미리보기
// ══════════════════════════════════════════════════════════════════

function renderAgents() {
  const grid = $("agent-grid");
  clear(grid);
  if (state.agents.length === 0) {
    grid.appendChild(emptyRow("에이전트가 없습니다. config/agents.yaml 을 확인해 주세요."));
    return;
  }

  for (const a of state.agents) {
    const picked = state.selected.includes(a.entity_id);
    const full = state.selected.length >= MAX_TARGETS && !picked;
    const blocked = a.daily_limit_reached;

    const meta = el("div", { class: "agent-meta" });

    // ⚠️ null 인 필드는 아예 렌더하지 않는다. "비공개" 라고 쓰지 않는다 —
    //    그 표시 자체가 정보다 (BR-U-08).
    if (a.activity_status) {
      const act = ACTIVITY[a.activity_status] || ACTIVITY.offline;
      const label = a.away_minutes ? `${act.label} · ${a.away_minutes}분 전` : act.label;
      meta.appendChild(el("span", { class: "badge badge-muted" }, [
        el("span", { class: `dot ${act.dot}` }), el("span", { text: label }),
      ]));
    }
    if (a.current_focus_summary) meta.appendChild(badge(a.current_focus_summary, "accent"));
    if (a.question_count_today !== null && a.question_count_today !== undefined) {
      meta.appendChild(badge(`오늘 ${a.question_count_today}건`, "muted"));
    }
    if (blocked) meta.appendChild(badge("일일 상한 도달", "bad"));

    grid.appendChild(el("li", {}, [
      el("button", {
        class: "agent-card",
        attrs: {
          type: "button",
          "aria-pressed": String(picked),
          "aria-disabled": String(blocked || full),
          "data-testid": `agent-card-${a.entity_id}`,
          title: full ? `최대 ${MAX_TARGETS}명까지 고를 수 있습니다` : "",
        },
        on: {
          click: () => {
            if (blocked) return;
            if (picked) state.selected = state.selected.filter((x) => x !== a.entity_id);
            else if (state.selected.length < MAX_TARGETS) state.selected.push(a.entity_id);
            renderAgents();
            refreshAskButton();
          },
        },
      }, [
        el("div", { class: "agent-head" }, [
          el("span", { class: "agent-name", text: a.display_name }),
          picked ? badge("지목됨", "accent") : null,
        ]),
        el("div", { class: "agent-expertise", text: a.expertise }),
        meta,
      ]),
    ]));
  }
}

function renderPresets() {
  const sel = $("preset-select");
  clear(sel);
  sel.appendChild(el("option", { text: "— 직접 입력하거나 데모 질문을 고르세요 —", attrs: { value: "" } }));
  state.presets.forEach((p, i) => {
    sel.appendChild(el("option", { text: p.label, attrs: { value: String(i) } }));
  });
}

function refreshAskButton() {
  const text = $("question-input").value;
  const counter = $("char-counter");
  counter.textContent = `${text.length} / ${MAX_QUESTION}`;
  counter.className = text.length > MAX_QUESTION ? "counter over" : "counter";
  $("ask-button").disabled =
    state.busy || state.selected.length === 0 || !text.trim() || text.length > MAX_QUESTION;
}

async function doPrepare() {
  const question = $("question-input").value.trim();
  state.busy = true;
  refreshAskButton();
  setStatus("무엇이 나갈지 준비하고 있습니다… (상대에게 알림이 가지 않습니다)", "busy");
  clear($("answers"));
  state.result = null;
  state.fallbacks = [];
  state.approvedIds = [];

  try {
    const prepared = await api("/api/ask/prepare", {
      method: "POST",
      body: { question, asker: state.currentUser, targets: state.selected },
    });
    state.prepared = prepared;

    // 차단된 호출은 미리보기 없이 폴백 답변을 바로 보여준다 (한 왕복에 끝난다).
    state.fallbacks = prepared.calls.filter((c) => c.disposition === "blocked");
    state.modalQueue = prepared.calls.filter((c) => c.disposition === "ready");

    if (state.modalQueue.length === 0) {
      setStatus("전송하지 않았습니다. 신뢰 구역 안에서 답했습니다.");
      renderAnswers();
      return;
    }
    setStatus(`미리보기 ${state.modalQueue.length}건을 확인해 주세요.`);
    showNextPreview();
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    state.busy = false;
    refreshAskButton();
  }
}

/** JSON 을 토큰별로 색칠한다. 문자열 조립이 아니라 노드 조립이다. */
function renderJson(target, value) {
  clear(target);
  const text = JSON.stringify(value, null, 2);
  const re = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(\b-?\d+(?:\.\d+)?\b)|(\btrue\b|\bfalse\b|\bnull\b)/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) target.appendChild(document.createTextNode(text.slice(last, m.index)));
    const cls = m[1] ? "tok-key" : m[2] ? "tok-str" : m[3] ? "tok-num" : "tok-bool";
    target.appendChild(el("span", { class: cls, text: m[0] }));
    last = m.index + m[0].length;
  }
  if (last < text.length) target.appendChild(document.createTextNode(text.slice(last)));
}

function showNextPreview() {
  const call = state.modalQueue[0];
  if (!call) { doSend(); return; }

  const p = call.preview;
  $("preview-target").textContent =
    `${call.agent_label} 에게 · ${TIER[call.tier].label} 등급 · ${p.representation}`;

  const metrics = $("preview-metrics");
  clear(metrics);
  metrics.appendChild(tierBadge(call.tier));
  metrics.appendChild(badge(
    `원문 문장 ${p.verbatim_sentence_count}개 (측정값)`,
    p.verbatim_sentence_count === 0 ? "ok" : "bad",
  ));
  metrics.appendChild(badge(`검증 ${p.validation_summary}`, "ok"));
  metrics.appendChild(badge(fmtBytes(p.size_bytes), "muted"));

  // 6단계를 개별로 보여준다. "6/6" 요약만 보여주지 않는다 (BR-U-01).
  const checks = $("preview-checks");
  clear(checks);
  for (const c of p.checks) {
    checks.appendChild(el("li", {
      class: `check ${c.passed ? "pass" : "fail"}`,
      attrs: { "data-testid": `preview-check-${c.stage}` },
    }, [
      el("span", { class: "check-mark", text: c.passed ? "✓" : "✕" }),
      el("span", { class: "check-name", text: STAGE[c.stage] || c.stage }),
      el("span", { class: "check-detail", text: c.detail }),
    ]));
  }

  renderJson($("preview-payload"), JSON.parse(p.payload_pretty));

  const exc = $("preview-exclusions");
  clear(exc);
  for (const name of p.excluded_categories) exc.appendChild(el("li", { text: name }));

  $("preview-queue").textContent =
    state.modalQueue.length > 1 ? `${state.modalQueue.length}건 중 1번째` : "";

  const modal = $("preview-modal");
  if (!modal.open) modal.showModal();
  $("preview-send").focus();
}

function onPreviewSend() {
  const call = state.modalQueue.shift();
  if (call && call.envelope_id) state.approvedIds.push(call.envelope_id);
  if (state.modalQueue.length > 0) { showNextPreview(); return; }
  $("preview-modal").close();
  doSend();
}

function onPreviewCancel() {
  state.modalQueue.shift();
  if (state.modalQueue.length > 0) { showNextPreview(); return; }
  $("preview-modal").close();
  if (state.approvedIds.length > 0) { doSend(); return; }
  // 취소하면 아무것도 보내지 않는다. 감사 레코드도 남지 않는다 (BR-U-03).
  setStatus("취소했습니다. 아무것도 전송되지 않았고 감사 로그에도 남지 않습니다.");
  renderAnswers();
}

async function doSend() {
  if (state.approvedIds.length === 0) { renderAnswers(); return; }
  state.busy = true;
  setStatus("전송했습니다. 답변을 기다리고 있습니다…", "busy");
  try {
    state.result = await api("/api/ask/send", {
      method: "POST",
      body: {
        request_id: state.prepared.request_id,
        envelope_ids: state.approvedIds,
        approved_by: state.currentUser,
      },
    });
    const d = DISPOSITION[state.result.merged.disposition];
    setStatus(d ? d.label : state.result.merged.disposition);
    renderAnswers();
    loadHealth();
    loadInbox();
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    state.busy = false;
    refreshAskButton();
  }
}

// ══════════════════════════════════════════════════════════════════
// §7 답변
// ══════════════════════════════════════════════════════════════════

function answerCard(a) {
  const head = el("div", { class: "answer-head" }, [
    el("span", { class: "answer-label", text: a.agent_label }),
    tierBadge(a.tier),
  ]);

  if (a.used_external_agent === false) {
    head.appendChild(badge("사내망 밖으로 나간 것 없음", "ok", { "data-testid": "fallback-notice" }));
  }
  head.appendChild(badge(`신뢰도 ${a.confidence.toFixed(2)}`,
    a.confidence >= 0.75 ? "ok" : a.confidence >= 0.45 ? "warn" : "bad"));
  if (a.freshness) {
    const label = FRESHNESS[a.freshness] || a.freshness;
    head.appendChild(badge(
      a.session_as_of ? `${label} · ${fmtTime(a.session_as_of)}` : label,
      a.freshness === "live" ? "ok" : "muted",
    ));
  }

  const card = el("div", {
    class: `answer-card${a.used_external_agent === false ? " local" : ""}`,
    attrs: { "data-testid": `answer-card-${a.entity_id}` },
  }, [head, el("p", { class: "answer-body", text: a.text })]);

  if (a.citations.length > 0) {
    const list = el("ul", { class: "citations" });
    for (const c of a.citations) {
      // ⚠️ internal_path 는 응답에 없다. 참조하지 않는다 (FR-43, BR-U-05).
      const marks = [TIER[c.tier].label];
      if (c.as_of) marks.push(c.as_of);
      if (c.formality === "informal") marks.push("비공식");
      list.appendChild(el("li", {
        class: "citation", attrs: { "data-testid": `citation-${c.ref}` },
      }, [
        el("span", { text: "근거" }),
        el("span", { class: "citation-title", text: c.display_title + (c.section ? ` · ${c.section}` : "") }),
        badge(marks.join(" · "), c.tier === "secret" ? "secret" : "muted"),
      ]));
    }
    card.appendChild(list);
  }

  if (a.unresolved_refs && a.unresolved_refs.length > 0) {
    card.appendChild(el("p", {
      class: "unresolved",
      text: `치환되지 않은 참조 기호가 있습니다: ${a.unresolved_refs.join(", ")}. `
          + "Agent 응답을 그대로 신뢰하지 않고 기호를 남겨 두었습니다.",
    }));
  }
  return card;
}

function renderAnswers() {
  const box = $("answers");
  clear(box);

  for (const call of state.fallbacks) {
    box.appendChild(el("div", { class: "verdict internal" }, [
      el("div", { class: "verdict-head" }, [
        el("strong", { text: "전송하지 않았습니다" }),
        tierBadge(call.tier),
        badge(call.agent_label, "muted"),
      ]),
      el("p", { class: "hint hint-tight", text: call.blocked_reason || "" }),
    ]));
    if (call.fallback) box.appendChild(answerCard(call.fallback));
  }

  const merged = state.result && state.result.merged;
  if (merged) {
    if (merged.divergent) {
      box.appendChild(el("div", { class: "divergence", attrs: { "data-testid": "divergence-note" } }, [
        el("h3", { text: "두 답변이 서로 다릅니다. 판단에 참고하세요." }),
        el("p", { text: merged.divergence_note || "" }),
      ]));
    }
    // ⚠️ 서버가 준 순서를 유지한다. 신뢰도로 정렬하지 않는다 (BR-U-07).
    for (const a of merged.answers) box.appendChild(answerCard(a));

    if (state.result.escalations.length > 0) {
      box.appendChild(el("p", {
        class: "hint",
        text: `담당자 ${state.result.escalations.length}명에게 확인을 요청했습니다. `
            + "인박스 탭에서 진행 상황을 볼 수 있습니다.",
      }));
    }

    box.appendChild(el("div", { class: "metrics" }, [
      el("span", {}, [el("strong", { text: `${state.result.elapsed_seconds}s` }), el("span", { text: " 소요" })]),
      el("span", {}, [el("strong", { text: String(state.result.interrupts_avoided) }), el("span", { text: "건 방해 회피" })]),
      el("span", {}, [el("strong", { text: `약 ${state.result.minutes_saved_estimate}분` }), el("span", { text: " 절약 (추정)" })]),
    ]));
  }

  if (box.childElementCount === 0) box.appendChild(emptyRow("아직 답변이 없습니다."));
}

// ══════════════════════════════════════════════════════════════════
// §8 문서 업로드
// ══════════════════════════════════════════════════════════════════

function verdictCard(result) {
  const doc = result.document;
  const t = TIER[doc.tier];
  const box = el("div", { class: `verdict ${doc.tier}` }, [
    el("div", { class: "verdict-head" }, [
      el("strong", { text: "판정 완료" }),
      tierBadge(doc.tier),
      el("span", { class: "verdict-file", text: doc.filename }),
      badge(fmtBytes(doc.size_bytes), "muted"),
      doc.attached ? badge("질의 후보에 추가됨", "accent") : badge("질의 후보 아님", "muted"),
    ]),
  ]);

  if (doc.tier_evidence.length > 0) {
    box.appendChild(el("h3", { text: `${t.label} 로 판정한 근거` }));
    const ul = el("ul");
    for (const ev of doc.tier_evidence) {
      ul.appendChild(el("li", {
        text: ev.rule > 0 ? `규칙 ${ev.rule}번 — ${ev.reason}` : ev.reason,
      }));
    }
    box.appendChild(ul);
  }
  for (const w of result.warnings) box.appendChild(el("p", { class: "note", text: w }));
  if (!result.in_scope) {
    box.appendChild(el("p", { class: "note", text: "⚠️ 이 경로는 지식 범위 밖입니다. Agent 가 읽지 못합니다." }));
  }
  return box;
}

async function uploadOne(filename, content) {
  const result = await api("/api/documents", {
    method: "POST",
    body: { owner: state.currentUser, filename, content, attach_to_session: true },
  });
  $("upload-result").appendChild(verdictCard(result));
}

async function handleFiles(files) {
  const box = $("upload-result");
  clear(box);
  box.appendChild(el("p", { class: "status busy", text: `${files.length}개 파일을 올리고 판정하는 중…` }));
  const results = [];
  for (const file of files) {
    try {
      const content = await file.text();
      results.push({ filename: file.name, content });
    } catch {
      results.push({ filename: file.name, error: "파일을 읽을 수 없습니다" });
    }
  }
  clear(box);
  for (const r of results) {
    if (r.error) { box.appendChild(el("p", { class: "status error", text: `${r.filename}: ${r.error}` })); continue; }
    try {
      await uploadOne(r.filename, r.content);
    } catch (err) {
      box.appendChild(el("p", { class: "status error", text: `${r.filename}: ${err.message}` }));
    }
  }
  loadDocuments();
}

function renderDocuments() {
  const list = $("doc-list");
  clear(list);
  if (state.documents.length === 0) {
    list.appendChild(emptyRow("문서가 없습니다. 위에서 올려 보세요."));
    return;
  }
  for (const d of state.documents) {
    const row = el("li", { class: "doc-item", attrs: { "data-testid": `doc-${d.document_id}` } }, [
      tierBadge(d.tier),
      el("span", { class: "doc-name", text: d.filename }),
      // 자기 문서 관리 화면이므로 경로를 보여준다. FR-43 이 막는 것은
      // *다른 사람의* 지식을 인용할 때 경로가 새는 것이다.
      el("span", { class: "doc-path", text: d.internal_path }), // lint-web: allow BR-U-05
      badge(fmtBytes(d.size_bytes), "muted"),
      d.attached ? badge("질의 후보", "accent") : badge("후보 아님", "muted"),
      d.seeded ? badge("샘플 문서", "muted") : null,
      el("span", { class: "doc-spacer" }),
    ]);
    if (!d.seeded) {
      row.appendChild(el("button", {
        class: "btn btn-sm btn-danger",
        text: "삭제",
        attrs: { type: "button", "data-testid": `doc-delete-${d.document_id}` },
        on: { click: () => deleteDocument(d) },
      }));
    }
    list.appendChild(row);
  }
}

async function loadDocuments() {
  if (!state.currentUser) return;
  try {
    const data = await api(`/api/documents?owner=${encodeURIComponent(state.currentUser)}`);
    state.documents = data.documents;
    renderDocuments();
  } catch (err) {
    clear($("doc-list"));
    $("doc-list").appendChild(el("p", { class: "status error", text: err.message }));
  }
}

async function deleteDocument(doc) {
  try {
    await api(`/api/documents/${encodeURIComponent(doc.document_id)}?owner=${encodeURIComponent(state.currentUser)}`,
      { method: "DELETE" });
    loadDocuments();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

// ══════════════════════════════════════════════════════════════════
// §9 인박스
// ══════════════════════════════════════════════════════════════════

function inboxItem(item) {
  const st = INBOX_STATUS[item.status] || INBOX_STATUS.open;
  const node = el("li", {
    class: `inbox-item${item.status === "open" ? "" : " resolved"}`,
    attrs: { "data-testid": `inbox-item-${item.item_id}` },
  }, [
    el("div", { class: "inbox-head" }, [
      badge(st.label, st.kind), tierBadge(item.tier),
      badge(`요청: ${item.asker}`, "muted"),
      badge(fmtTime(item.at), "muted"),
    ]),
    el("p", { class: "inbox-q", text: item.question_summary }),
  ]);

  const sec = (title, children) => el("div", { class: "inbox-sec" }, [el("h4", { text: title }), ...children]);

  node.appendChild(sec("요약", [el("p", { class: "hint hint-tight", text: item.draft.summary })]));
  if (item.draft.situation.length > 0) {
    const ul = el("ul");
    for (const s of item.draft.situation) ul.appendChild(el("li", { text: s }));
    node.appendChild(sec("지금까지 찾은 것", [ul]));
  }
  node.appendChild(sec("초안 — 그대로 승인할 수 있습니다", [
    el("p", { class: "inbox-draft", text: item.draft.draft_answer }),
  ]));
  if (item.draft.already_answered.length > 0) {
    const ul = el("ul");
    for (const s of item.draft.already_answered) ul.appendChild(el("li", { text: s }));
    node.appendChild(sec("Agent 가 이미 답한 것", [ul]));
  }
  if (item.citations.length > 0) {
    const ul = el("ul", { class: "citations" });
    for (const c of item.citations) {
      ul.appendChild(el("li", { class: "citation" }, [
        el("span", { class: "citation-title", text: c.display_title }), tierBadge(c.tier),
      ]));
    }
    node.appendChild(sec("근거", [ul]));
  }

  if (item.status !== "open") {
    if (item.resolution_text) {
      node.appendChild(sec("확정된 답변", [el("p", { class: "inbox-draft", text: item.resolution_text })]));
    }
    // 자동 재지목을 하지 않는다. 질문자가 다시 누른다 (BR-I-03).
    if (item.redirect_to) {
      node.appendChild(el("p", { class: "note hint hint-tight",
        text: `${item.owner_entity_id} 이 ${item.redirect_to} 를 지목했습니다. `
            + "시스템이 자동으로 다시 묻지 않습니다 — 질문자가 직접 다시 물어야 합니다." }));
    }
    return node;
  }

  const editBox = el("div", { class: "inbox-edit", attrs: { hidden: "hidden" } }, [
    el("textarea", { attrs: { rows: "4", "data-testid": `inbox-edit-text-${item.item_id}` } }),
  ]);
  const redirect = el("select", { attrs: { "data-testid": `inbox-redirect-${item.item_id}` } });
  redirect.appendChild(el("option", { text: "— 다른 담당자 —", attrs: { value: "" } }));
  for (const u of state.users) {
    if (u.entity_id === item.owner_entity_id) continue;
    redirect.appendChild(el("option", { text: u.display_name, attrs: { value: u.entity_id } }));
  }

  node.appendChild(el("div", { class: "inbox-actions" }, [
    el("button", {
      class: "btn btn-primary btn-sm", text: "승인",
      attrs: { type: "button", "data-testid": `inbox-approve-${item.item_id}` },
      on: { click: () => resolveInbox(item, { action: "approve" }) },
    }),
    el("button", {
      class: "btn btn-sm", text: "수정 후 승인",
      attrs: { type: "button", "data-testid": `inbox-edit-${item.item_id}` },
      on: {
        click: () => {
          const ta = editBox.querySelector("textarea");
          if (editBox.hidden) {
            editBox.hidden = false;
            ta.value = item.draft.draft_answer;
            ta.focus();
          } else {
            resolveInbox(item, { action: "approve_with_edit", edited_text: ta.value });
          }
        },
      },
    }),
    el("button", {
      class: "btn btn-sm", text: "내가 아님",
      attrs: { type: "button", "data-testid": `inbox-notme-${item.item_id}` },
      on: {
        click: () => {
          if (!redirect.value) { redirect.focus(); return; }
          resolveInbox(item, { action: "not_me", redirect_to: redirect.value });
        },
      },
    }),
    redirect,
  ]));
  node.appendChild(editBox);
  return node;
}

async function loadInbox() {
  if (!state.currentUser) return;
  try {
    state.inbox = await api(`/api/inbox?owner=${encodeURIComponent(state.currentUser)}`);
  } catch {
    state.inbox = [];
  }
  const list = $("inbox-list");
  clear(list);
  if (state.inbox.length === 0) {
    list.appendChild(emptyRow("확인 요청이 없습니다. Agent 가 처리했다는 뜻입니다."));
  } else {
    for (const item of state.inbox) list.appendChild(inboxItem(item));
  }
  const open = state.inbox.filter((i) => i.status === "open").length;
  const count = $("inbox-count");
  count.hidden = open === 0;
  count.textContent = String(open);
}

async function resolveInbox(item, body) {
  try {
    await api(`/api/inbox/${encodeURIComponent(item.item_id)}/resolve`, { method: "POST", body });
    loadInbox();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

// ══════════════════════════════════════════════════════════════════
// §10 감사
// ══════════════════════════════════════════════════════════════════

function auditRow(r) {
  const payload = el("pre", { class: "payload", attrs: { hidden: "hidden", "data-testid": `audit-payload-${r.record_id}` } });
  const field = (label, value) => el("span", {}, [el("b", { text: `${label} ` }), el("span", { text: value })]);

  return el("li", { class: "audit-row", attrs: { "data-testid": `audit-row-${r.record_id}` } }, [
    el("div", { class: "audit-top" }, [
      el("span", { class: "audit-time", text: fmtTime(r.at) }),
      tierBadge(r.tier),
      badge(r.representation, "muted"),
      badge(`검증 ${r.validation_summary}`, "ok"),
      badge(fmtBytes(r.size_bytes), "muted"),
      badge(`전송 ${r.transport}`, "accent"),
      el("span", { class: "doc-spacer" }),
      el("button", {
        class: "btn btn-sm btn-ghost", text: "페이로드 보기",
        attrs: { type: "button" },
        on: {
          click: (e) => {
            payload.hidden = !payload.hidden;
            e.currentTarget.textContent = payload.hidden ? "페이로드 보기" : "접기";
            if (!payload.hidden) renderJson(payload, r.payload);
          },
        },
      }),
    ]),
    el("div", { class: "audit-fields" }, [
      field("대상", r.target_entity_id),
      field("승인자", r.approved_by),
      field("모델", r.model_id),
      field("원문 모델 엔드포인트", r.trusted_zone_llm_base_url),
      field("sha256", r.payload_sha256.slice(0, 24) + "…"),
      field("envelope", r.envelope_id),
    ]),
    payload,
  ]);
}

async function loadAudit() {
  const q = state.auditQuery;
  try {
    state.audit = await api(`/api/audit${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  } catch (err) {
    setStatus(err.message, "error");
    return;
  }
  const zero = $("audit-zero");
  clear(zero);
  const rows = state.audit.rows;

  // 0건이 이 화면의 핵심 기능이다 (BR-U-10).
  if (q && rows.length === 0) {
    zero.appendChild(el("div", { class: "zero-hit" }, [
      el("strong", { text: "0건" }),
      el("span", { text: `“${q}” 는 경계를 넘은 적이 없습니다.` }),
    ]));
  } else if (q) {
    zero.appendChild(el("p", { class: "hit-note", text: `“${q}” 를 포함한 레코드 ${rows.length}건입니다.` }));
  }

  $("audit-meta").textContent =
    `전체 ${state.audit.total_records}건 중 ${rows.length}건 표시` +
    (q ? "" : " · 검색어를 넣으면 그 문구가 나간 적이 있는지 확인할 수 있습니다");

  const list = $("audit-list");
  clear(list);
  if (rows.length === 0 && !q) {
    list.appendChild(emptyRow("경계를 넘은 것이 아직 없습니다."));
  }
  for (const r of rows) list.appendChild(auditRow(r));
}

function renderQuickSearches() {
  const row = $("audit-quick");
  clear(row);
  for (const term of QUICK_SEARCHES) {
    row.appendChild(el("button", {
      class: "badge badge-muted", text: term,
      attrs: { type: "button" },
      on: {
        click: () => {
          $("audit-search").value = term;
          state.auditQuery = term;
          loadAudit();
        },
      },
    }));
  }
}

// ══════════════════════════════════════════════════════════════════
// 기동
// ══════════════════════════════════════════════════════════════════

async function loadHealth() {
  try {
    state.health = await api("/api/health");
  } catch {
    state.health = null;
  }
  renderHealth();
}

async function loadAgents() {
  try {
    state.agents = await api("/api/agents");
  } catch (err) {
    state.agents = [];
    setStatus(err.message, "error");
  }
  renderAgents();
}

function wire() {
  for (const btn of document.querySelectorAll('[role="tab"]')) {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    btn.addEventListener("keydown", (e) => {
      const tabs = [...document.querySelectorAll('[role="tab"]')];
      const i = tabs.indexOf(btn);
      if (e.key === "ArrowRight") tabs[(i + 1) % tabs.length].focus();
      if (e.key === "ArrowLeft") tabs[(i - 1 + tabs.length) % tabs.length].focus();
    });
  }

  $("user-select").addEventListener("change", (e) => {
    state.currentUser = e.target.value;
    state.selected = state.selected.filter((x) => x !== state.currentUser);
    renderAgents();
    refreshAskButton();
    loadInbox();
    if (state.tab === "docs") loadDocuments();
  });

  $("question-input").addEventListener("input", refreshAskButton);
  $("preset-select").addEventListener("change", (e) => {
    const p = state.presets[Number(e.target.value)];
    if (!p) return;
    $("question-input").value = p.question;
    if (p.targets && p.targets.length > 0) state.selected = [...p.targets].slice(0, MAX_TARGETS);
    renderAgents();
    refreshAskButton();
    setStatus(p.note || "");
  });
  $("ask-button").addEventListener("click", doPrepare);

  $("preview-send").addEventListener("click", onPreviewSend);
  $("preview-cancel").addEventListener("click", onPreviewCancel);
  $("preview-modal").addEventListener("cancel", (e) => { e.preventDefault(); onPreviewCancel(); });
  $("notice-close").addEventListener("click", () => { $("trust-boundary-notice").hidden = true; });

  const dz = $("dropzone");
  const fi = $("file-input");
  dz.addEventListener("click", () => fi.click());
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fi.click(); } });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("over");
    if (e.dataTransfer.files.length > 0) handleFiles([...e.dataTransfer.files]);
  });
  fi.addEventListener("change", () => {
    if (fi.files.length > 0) handleFiles([...fi.files]);
    fi.value = "";
  });
  $("paste-submit").addEventListener("click", async () => {
    const name = $("paste-name").value.trim();
    const body = $("paste-body").value;
    const box = $("upload-result");
    clear(box);
    if (!name || !body.trim()) {
      box.appendChild(el("p", { class: "status error", text: "파일명과 내용을 모두 입력해 주세요." }));
      return;
    }
    try {
      await uploadOne(name, body);
      $("paste-body").value = "";
      loadDocuments();
    } catch (err) {
      box.appendChild(el("p", { class: "status error", text: err.message }));
    }
  });

  $("audit-search-btn").addEventListener("click", () => {
    state.auditQuery = $("audit-search").value.trim();
    loadAudit();
  });
  $("audit-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { state.auditQuery = e.target.value.trim(); loadAudit(); }
  });
  $("audit-clear").addEventListener("click", () => {
    $("audit-search").value = "";
    state.auditQuery = "";
    loadAudit();
  });
}

async function boot() {
  wire();
  renderQuickSearches();
  await loadHealth();
  try {
    state.users = await api("/api/users");
    if (state.users.length > 0) state.currentUser = state.users[0].entity_id;
  } catch { state.users = []; }
  try { state.presets = await api("/api/questions"); } catch { state.presets = []; }
  renderUsers();
  renderPresets();
  await loadAgents();
  refreshAskButton();
  loadInbox();
  setStatus("");
}

document.addEventListener("DOMContentLoaded", boot);

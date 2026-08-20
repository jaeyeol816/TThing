/* MIA; But AI got you — 채팅 인터페이스
 *
 * 규칙:
 *  1. innerHTML 을 쓰지 않는다 (XSS 방지, BR-U-12)
 *  2. 페이로드를 생략하지 않는다 (BR-U-01)
 *  3. 배지는 색상 + 텍스트 (BR-U-13)
 */

"use strict";

// ══════════════════════════════════════════════════════════════════
// 상태
// ══════════════════════════════════════════════════════════════════

const state = {
  currentUser: null,
  users: [],
  agents: [],
  selected: [],       // 지목한 entity_id (최대 2)
  busy: false,
  health: null,

  // ask flow
  prepared: null,
  preparedCalls: null,
  modalQueue: [],
  approvedIds: [],
  fallbacks: [],
  result: null,
};

const MAX_TARGETS = 2;
const MAX_QUESTION = 4000;

// ══════════════════════════════════════════════════════════════════
// DOM 도구
// ══════════════════════════════════════════════════════════════════

const $ = (id) => document.getElementById(id);

function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text !== undefined) node.textContent = String(opts.text);
  if (opts.html !== undefined) { /* 금지 */ }
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

function badge(text, kind = "muted") {
  return el("span", { class: `badge badge-${kind}`, text });
}

const fmtTime = (d) => {
  if (!d) d = new Date();
  if (typeof d === "string") d = new Date(d);
  return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
};

// ══════════════════════════════════════════════════════════════════
// 표시 매핑
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

const STAGE = {
  schema: "스키마", vocab: "어휘", range: "범위",
  banned: "금칙어", ngram: "원문대조", size: "크기",
};

const tierBadge = (tier) => {
  const t = TIER[tier] || TIER.internal;
  return badge(t.label, t.kind);
};

// ══════════════════════════════════════════════════════════════════
// API
// ══════════════════════════════════════════════════════════════════

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
    throw new Error("서버에 연결할 수 없습니다.");
  }
  if (!response.ok) {
    let body = null;
    try { body = await response.json(); } catch { /* pass */ }
    throw new ApiError(response.status, body);
  }
  if (response.status === 204) return null;
  return response.json();
}

// ══════════════════════════════════════════════════════════════════
// 채팅 메시지 렌더링
// ══════════════════════════════════════════════════════════════════

function addMessage(type, content, opts = {}) {
  const container = $("chat-messages");
  const msg = el("div", { class: `message message-${type}` });

  const contentDiv = el("div", { class: "message-content" });

  if (typeof content === "string") {
    const p = el("p", { text: content });
    contentDiv.appendChild(p);
  } else if (content instanceof HTMLElement) {
    contentDiv.appendChild(content);
  }

  if (opts.hint) {
    contentDiv.appendChild(el("p", { class: "message-hint", text: opts.hint }));
  }

  msg.appendChild(contentDiv);

  if (opts.time !== false) {
    msg.appendChild(el("span", { class: "message-time", text: fmtTime() }));
  }

  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
  return msg;
}

function addLoadingMessage() {
  const container = $("chat-messages");
  const msg = el("div", { class: "message message-assistant message-loading" });
  const contentDiv = el("div", { class: "message-content" }, [
    el("div", { class: "loading-dots" }, [
      el("span"), el("span"), el("span"),
    ]),
  ]);
  msg.appendChild(contentDiv);
  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
  return msg;
}

function removeMessage(msgEl) {
  if (msgEl && msgEl.parentNode) msgEl.parentNode.removeChild(msgEl);
}

// ══════════════════════════════════════════════════════════════════
// 조직도 패널
// ══════════════════════════════════════════════════════════════════

function renderOrgPanel() {
  const list = $("org-list");
  clear(list);

  if (state.agents.length === 0) {
    list.appendChild(el("p", { class: "message-hint", text: "에이전트가 없습니다." }));
    return;
  }

  for (const a of state.agents) {
    // currentUser(나 자신)는 조직도에 표시하지 않음
    if (a.entity_id === state.currentUser) continue;

    const act = ACTIVITY[a.activity_status] || ACTIVITY.offline;
    const initial = a.display_name.charAt(0);

    const card = el("div", {
      class: "org-card",
      attrs: { "data-testid": `org-card-${a.entity_id}` },
    }, [
      el("div", { class: "org-avatar", text: initial }),
      el("div", { class: "org-info" }, [
        el("div", { class: "org-name", text: a.display_name }),
        el("div", { class: "org-role", text: a.expertise || "" }),
        el("div", { class: "org-status" }, [
          el("span", { class: `dot ${act.dot}` }),
          el("span", { text: act.label }),
        ]),
        el("span", { class: "org-status-badge waiting", text: "" }),
        el("div", { class: "org-answer-preview" }),
      ]),
    ]);

    list.appendChild(card);
  }
}

// ══════════════════════════════════════════════════════════════════
// 사용자 전환
// ══════════════════════════════════════════════════════════════════

function renderUsers() {
  const sel = $("user-select");
  if (!sel) return;
  clear(sel);
  for (const u of state.users) {
    sel.appendChild(el("option", { text: u.display_name, attrs: { value: u.entity_id } }));
  }
  if (state.currentUser) sel.value = state.currentUser;
}

// ══════════════════════════════════════════════════════════════════
// 입력 · 전송
// ══════════════════════════════════════════════════════════════════

function refreshSendButton() {
  const text = $("message-input").value.trim();
  $("send-btn").disabled = state.busy || !text || text.length > MAX_QUESTION || !state.currentUser;
}

function autoResize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
}

// ══════════════════════════════════════════════════════════════════
// 질문 흐름 (prepare → preview → send)
// ══════════════════════════════════════════════════════════════════

async function doAsk() {
  const input = $("message-input");
  const question = input.value.trim();
  if (!question || !state.currentUser) return;

  addMessage("user", question);

  input.value = "";
  autoResize(input);
  state.busy = true;
  refreshSendButton();
  setOrgStatus("waiting");

  const loading = addLoadingMessage();

  try {
    const result = await api("/api/hub/ask", {
      method: "POST",
      body: { question, asker: state.currentUser },
    });

    removeMessage(loading);
    renderHubResult(result);
  } catch (err) {
    removeMessage(loading);
    addMessage("system", err.message);
    setOrgStatus("idle");
  } finally {
    state.busy = false;
    refreshSendButton();
  }
}
  }
}

function renderHubResult(result) {
  const content = el("div");
  content.appendChild(el("p", { text: result.answer }));

  // Gatekeeper collapsible
  content.appendChild(buildGkDetails({
    tier: "internal",
    disposition: result.disposition === "blocked" ? "blocked" : "ready",
    checks: null,
    representation: null,
    validation_summary: null,
  }));

  const msg = addMessage("assistant", content, {
    hint: result.used_tool ? "Agent + 조직도 참조" : "Agent",
  });

  if (result.disposition === "blocked") {
    msg.classList.add("gk-blocked");
  } else {
    msg.classList.add("gk-pass");
  }

  // 조직도 상태 업데이트
  if (result.agent_statuses && result.agent_statuses.length > 0) {
    updateOrgStatuses(result.agent_statuses);
  } else {
    setOrgStatus("idle");
  }
}

function setOrgStatus(status) {
  for (const agent of state.agents) {
    const card = document.querySelector(`[data-testid="org-card-${agent.entity_id}"]`);
    if (!card) continue;
    card.className = `org-card status-${status}`;
    const badge = card.querySelector(".org-status-badge");
    if (badge) {
      badge.className = `org-status-badge ${status}`;
      badge.textContent = status === "waiting" ? "대기중" : "";
    }
    const preview = card.querySelector(".org-answer-preview");
    if (preview) preview.textContent = "";
  }
}

function updateOrgStatuses(statuses) {
  const byId = {};
  for (const s of statuses) byId[s.entity_id] = s;

  for (const agent of state.agents) {
    const card = document.querySelector(`[data-testid="org-card-${agent.entity_id}"]`);
    if (!card) continue;
    const s = byId[agent.entity_id];
    const status = s ? s.status : "skipped";
    card.className = `org-card status-${status}`;

    const badge = card.querySelector(".org-status-badge");
    if (badge) {
      badge.className = `org-status-badge ${status}`;
      const labels = { answered: "답변", skipped: "해당없음", waiting: "대기중", error: "오류" };
      badge.textContent = labels[status] || status;
    }
    const preview = card.querySelector(".org-answer-preview");
    if (preview && s && s.answer) {
      preview.textContent = s.answer.slice(0, 80) + (s.answer.length > 80 ? "…" : "");
    }
  }
}

function renderFallbacks() {
  for (const fb of state.fallbacks) {
    const content = el("div");
    const text = fb.fallback ? fb.fallback.text : "";
    if (text) {
      content.appendChild(el("p", { text }));
    } else {
      content.appendChild(el("p", { text: "답변을 준비하고 있습니다." }));
    }

    // Gatekeeper collapsible details
    content.appendChild(buildGkDetails({
      tier: fb.tier,
      disposition: "blocked",
      blocked_reason: fb.blocked_reason,
      checks: null,
    }));

    const tierLabel = TIER[fb.tier] ? TIER[fb.tier].label : "사내";
    const msg = addMessage("assistant", content, {
      hint: `${fb.agent_label} · ${tierLabel} · 사내망 안에서 응답됨`,
    });
    msg.classList.add("gk-blocked");
  }
}

function renderResult(result) {
  const merged = result.merged;
  const disp = DISPOSITION[merged.disposition] || DISPOSITION.auto;

  for (const answer of merged.answers) {
    const content = el("div");

    content.appendChild(el("p", { text: answer.text }));

    if (answer.citations && answer.citations.length > 0) {
      const cites = el("div", { class: "answer-citations" });
      cites.appendChild(el("span", { text: "인용: " }));
      for (const c of answer.citations) {
        cites.appendChild(badge(c.label || c.ref || c.id, "muted"));
      }
      content.appendChild(cites);
    }

    // Gatekeeper details — preview 정보가 있으면 사용
    const callInfo = state.preparedCalls ? state.preparedCalls.find(
      (c) => c.target_entity_id === answer.entity_id || c.target_entity_id === answer.responder_entity_id
    ) : null;

    const gkStatus = answer.tier === "secret" ? "masked" : "pass";
    content.appendChild(buildGkDetails({
      tier: answer.tier,
      disposition: "ready",
      checks: callInfo && callInfo.preview ? callInfo.preview.checks : null,
      representation: callInfo && callInfo.preview ? callInfo.preview.representation : null,
      validation_summary: callInfo && callInfo.preview ? callInfo.preview.validation_summary : null,
    }));

    const agent = state.agents.find((a) => a.entity_id === answer.responder_entity_id);
    const label = agent ? agent.display_name : "Agent";
    const msg = addMessage("assistant", content, { hint: `${label} · ${disp.label}` });

    // 테두리 색상: SECRET만 노란색, 나머지는 초록
    if (answer.tier === "secret") {
      msg.classList.add("gk-masked");
    } else {
      msg.classList.add("gk-pass");
    }
  }

  if (merged.divergent && merged.divergence_note) {
    addMessage("system", `주의: ${merged.divergence_note}`, { time: false });
  }
}

// ══════════════════════════════════════════════════════════════════
// Gatekeeper 판단 경과 (collapsible)
// ══════════════════════════════════════════════════════════════════

function buildGkDetails(info) {
  const details = el("details", { class: "gk-details" });

  // Summary line
  const summary = el("summary");
  summary.appendChild(el("span", { class: "gk-arrow", text: "\u25B6" }));

  const statusText = info.disposition === "blocked"
    ? "Gatekeeper: 차단됨"
    : info.representation === "pseudonymized" || info.tier === "internal"
      ? "Gatekeeper: 가명화 처리됨"
      : info.tier === "secret"
        ? "Gatekeeper: 구조 추출됨"
        : "Gatekeeper: 통과";

  summary.appendChild(el("span", { text: statusText }));

  // Summary badges
  const badges = el("span", { class: "gk-summary-badges" });
  if (info.tier) badges.appendChild(tierBadge(info.tier));
  if (info.validation_summary) badges.appendChild(badge(`검증 ${info.validation_summary}`, "ok"));
  summary.appendChild(badges);

  details.appendChild(summary);

  // Body
  const body = el("div", { class: "gk-details-body" });

  if (info.blocked_reason) {
    body.appendChild(el("div", { class: "gk-row" }, [
      el("span", { class: "gk-icon fail", text: "\u2715" }),
      el("span", { class: "gk-stage", text: "사유" }),
      el("span", { class: "gk-detail-text", text: info.blocked_reason }),
    ]));
  }

  if (info.checks && info.checks.length > 0) {
    for (const c of info.checks) {
      body.appendChild(el("div", { class: "gk-row" }, [
        el("span", { class: `gk-icon ${c.passed ? "pass" : "fail"}`, text: c.passed ? "\u2713" : "\u2715" }),
        el("span", { class: "gk-stage", text: STAGE[c.stage] || c.stage }),
        el("span", { class: "gk-detail-text", text: c.detail }),
      ]));
    }
  } else if (!info.blocked_reason) {
    body.appendChild(el("div", { class: "gk-row" }, [
      el("span", { class: "gk-icon pass", text: "\u2713" }),
      el("span", { class: "gk-detail-text", text: "검증 통과 — 정상 처리됨" }),
    ]));
  }

  if (info.representation) {
    const repLabel = {
      structured: "구조 추출 (원문 0개)",
      pseudonymized: "가명화 (식별자 치환)",
      verbatim: "원문 그대로",
    }[info.representation] || info.representation;

    body.appendChild(el("div", { class: "gk-row" }, [
      el("span", { class: "gk-icon pass", text: "\u2139" }),
      el("span", { class: "gk-stage", text: "표현" }),
      el("span", { class: "gk-detail-text", text: repLabel }),
    ]));
  }

  details.appendChild(body);
  return details;
}

// ══════════════════════════════════════════════════════════════════
// 미리보기 모달
// ══════════════════════════════════════════════════════════════════

function showNextPreview() {
  const call = state.modalQueue[0];
  if (!call) { doSend(); return; }

  const p = call.preview;
  $("preview-target").textContent =
    `${call.agent_label} · ${TIER[call.tier].label} 등급 · ${p.representation}`;

  const metrics = $("preview-metrics");
  clear(metrics);
  metrics.appendChild(tierBadge(call.tier));
  metrics.appendChild(badge(
    `원문 문장 ${p.verbatim_sentence_count}개`,
    p.verbatim_sentence_count === 0 ? "ok" : "bad",
  ));
  metrics.appendChild(badge(`검증 ${p.validation_summary}`, "ok"));

  const checks = $("preview-checks");
  clear(checks);
  for (const c of p.checks) {
    checks.appendChild(el("li", {
      class: `check ${c.passed ? "pass" : "fail"}`,
    }, [
      el("span", { class: "check-mark", text: c.passed ? "\u2713" : "\u2715" }),
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
}

function onPreviewSend() {
  const call = state.modalQueue.shift();
  if (call && call.envelope_id) state.approvedIds.push(call.envelope_id);
  if (state.modalQueue.length === 0) {
    $("preview-modal").close();
    doSend();
  } else {
    showNextPreview();
  }
}

function onPreviewCancel() {
  state.modalQueue = [];
  $("preview-modal").close();
  if (state.approvedIds.length > 0) {
    doSend();
  } else {
    renderFallbacks();
    if (state.fallbacks.length === 0) {
      addMessage("system", "전송이 취소되었습니다.");
    }
  }
}

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

// ══════════════════════════════════════════════════════════════════
// 이벤트 바인딩
// ══════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════
// 보안 프로토콜 관리
// ══════════════════════════════════════════════════════════════════

const LEVEL_LABEL = { company: "전사", team: "팀", personal: "개인" };
const LEVEL_BADGE_CLASS = { company: "company", team: "team", personal: "personal" };

let _protocols = [];
let _editingLevel = null;
let _editingOwner = null;

async function openProtocolModal() {
  await loadProtocols();
  $("protocol-modal").showModal();
}

function closeProtocolModal() {
  $("protocol-modal").close();
}

async function loadProtocols() {
  try {
    _protocols = await api("/api/protocols");
  } catch { _protocols = []; }
  renderProtoLists();
}

function renderProtoLists() {
  for (const level of ["company", "team", "personal"]) {
    const list = $(`proto-list-${level}`);
    clear(list);
    const items = _protocols.filter((p) => p.level === level);
    for (const p of items) {
      const ruleCount = [
        ...p.secret_keywords, ...p.secret_patterns,
        ...p.secret_directories, ...p.secret_extensions,
      ].length;
      const btn = el("button", {
        class: `proto-item${_editingLevel === level && _editingOwner === p.owner ? " active" : ""}`,
        on: { click: () => openProtoEditor(p) },
      }, [
        el("span", { class: "proto-item-owner", text: p.owner }),
        ruleCount > 0 ? el("span", { class: "proto-item-count", text: String(ruleCount) }) : null,
      ]);
      list.appendChild(btn);
    }
  }
}

function openProtoEditor(proto) {
  _editingLevel = proto.level;
  _editingOwner = proto.owner;

  $("editor-empty").hidden = true;
  $("protocol-form").hidden = false;

  const levelBadge = $("form-level-badge");
  levelBadge.textContent = LEVEL_LABEL[proto.level] || proto.level;
  levelBadge.className = `proto-form-level-badge ${LEVEL_BADGE_CLASS[proto.level] || ""}`;

  $("form-level").value = proto.level;
  $("form-owner").value = proto.owner;
  $("form-owner").disabled = (proto.level === "company" && proto.owner === "all");
  $("form-description").value = proto.description || "";

  $("form-secret-keywords").value = (proto.secret_keywords || []).join("\n");
  $("form-secret-patterns").value = (proto.secret_patterns || []).join("\n");
  $("form-secret-dirs").value = (proto.secret_directories || []).join("\n");
  $("form-secret-exts").value = (proto.secret_extensions || []).join("\n");
  $("form-internal-keywords").value = (proto.internal_keywords || []).join("\n");
  $("form-internal-dirs").value = (proto.internal_directories || []).join("\n");
  $("form-open-dirs").value = (proto.open_directories || []).join("\n");
  $("form-exaone-hints").value = (proto.exaone_context_hints || []).join("\n");

  renderProtoLists();
}

function newProtoEditor(level, ownerHint) {
  _editingLevel = level;
  _editingOwner = null;

  const defaultOwner = level === "company" ? "all"
    : level === "personal" && state.currentUser ? state.currentUser
    : ownerHint || "";

  openProtoEditor({
    level,
    owner: defaultOwner,
    description: "",
    secret_keywords: [],
    secret_patterns: [],
    secret_directories: [],
    secret_extensions: [],
    secret_content_patterns: [],
    internal_keywords: [],
    internal_directories: [],
    internal_extensions: [],
    open_directories: [],
    exaone_context_hints: [],
  });
}

function splitLines(str) {
  return str.split("\n").map((s) => s.trim()).filter(Boolean);
}

async function saveProto(e) {
  e.preventDefault();
  const body = {
    level: $("form-level").value,
    owner: $("form-owner").value.trim(),
    description: $("form-description").value.trim(),
    secret_keywords: splitLines($("form-secret-keywords").value),
    secret_patterns: splitLines($("form-secret-patterns").value),
    secret_directories: splitLines($("form-secret-dirs").value),
    secret_extensions: splitLines($("form-secret-exts").value),
    secret_content_patterns: [],
    internal_keywords: splitLines($("form-internal-keywords").value),
    internal_directories: splitLines($("form-internal-dirs").value),
    internal_extensions: [],
    open_directories: splitLines($("form-open-dirs").value),
    exaone_context_hints: splitLines($("form-exaone-hints").value),
  };
  if (!body.owner) { alert("소유자를 입력하세요"); return; }
  try {
    await api("/api/protocols", { method: "POST", body });
    _editingOwner = body.owner;
    await loadProtocols();
    // 저장 후 에디터 갱신
    const saved = _protocols.find((p) => p.level === body.level && p.owner === body.owner);
    if (saved) openProtoEditor(saved);
  } catch (err) {
    alert(`저장 실패: ${err.message}`);
  }
}

async function deleteProto() {
  if (!_editingLevel || !_editingOwner) return;
  if (!confirm(`"${_editingOwner}" 프로토콜을 삭제할까요?`)) return;
  try {
    await api(`/api/protocols/${_editingLevel}/${encodeURIComponent(_editingOwner)}`, { method: "DELETE" });
    _editingLevel = null;
    _editingOwner = null;
    $("editor-empty").hidden = false;
    $("protocol-form").hidden = true;
    await loadProtocols();
  } catch (err) {
    alert(`삭제 실패: ${err.message}`);
  }
}

async function showMergedPreview() {
  const box = $("merged-preview");
  if (!box.hidden) { box.hidden = true; return; }
  try {
    const merged = await api("/api/protocols-merged");
    clear(box);

    const addSection = (label, items) => {
      if (!items || items.length === 0) return;
      const sec = el("div", { class: "merged-preview-section" });
      sec.appendChild(el("div", { class: "merged-preview-label", text: label }));
      items.slice(0, 8).forEach((item) => {
        sec.appendChild(el("div", { class: "merged-preview-item", text: item }));
      });
      if (items.length > 8) {
        sec.appendChild(el("div", { class: "merged-preview-item", text: `… 외 ${items.length - 8}개` }));
      }
      box.appendChild(sec);
    };

    addSection("키워드", merged.secret_keywords);
    addSection("패턴", merged.secret_patterns);
    addSection("SECRET 경로", merged.secret_path_globs);
    box.hidden = false;
  } catch (err) {
    alert(`불러오기 실패: ${err.message}`);
  }
}

function wireProtocol() {
  $("protocol-btn").addEventListener("click", openProtocolModal);
  $("protocol-modal-close").addEventListener("click", closeProtocolModal);
  $("protocol-modal").addEventListener("cancel", closeProtocolModal);
  $("protocol-form").addEventListener("submit", saveProto);
  $("proto-delete-btn").addEventListener("click", deleteProto);
  $("show-merged-btn").addEventListener("click", showMergedPreview);

  // "추가" 버튼들
  document.querySelectorAll(".proto-add-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      newProtoEditor(btn.dataset.level, btn.dataset.owner);
    });
  });
}

function wire() {
  wireProtocol();

  const input = $("message-input");
  const sendBtn = $("send-btn");  input.addEventListener("input", () => {
    autoResize(input);
    refreshSendButton();
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) doAsk();
    }
  });

  sendBtn.addEventListener("click", doAsk);

  $("preview-send").addEventListener("click", onPreviewSend);
  $("preview-cancel").addEventListener("click", onPreviewCancel);
  $("preview-modal").addEventListener("cancel", (e) => { e.preventDefault(); onPreviewCancel(); });
}

// ══════════════════════════════════════════════════════════════════
// 부팅
// ══════════════════════════════════════════════════════════════════

async function boot() {
  wire();

  try {
    state.users = await api("/api/users");
    if (state.users.length > 0) state.currentUser = state.users[0].entity_id;
  } catch { state.users = []; }
  renderUsers();

  try {
    state.agents = await api("/api/agents");
  } catch { state.agents = []; }
  renderOrgPanel();
  refreshSendButton();
}

document.addEventListener("DOMContentLoaded", boot);

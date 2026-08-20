/* MIA; But AI got you — 채팅 인터페이스
 *
 * 규칙:
 *  1. innerHTML 을 쓰지 않는다 (XSS 방지, BR-U-12)
 *  2. 페이로드를 생략하지 않는다 (BR-U-01)
 *  3. 배지는 색상 + 텍스트 (BR-U-13)
 *
 * ──────────────────────────────────────────────────────────────────
 * 화면의 흐름
 * ──────────────────────────────────────────────────────────────────
 *
 *   질문 입력 → /api/ask/broadcast → 조직도에 파동
 *            → 답할 수 있는 사람만 남고 나머지는 흐려진다
 *            → 사람을 누르면 그 사람과의 스레드가 왼쪽에 열린다
 *            → 그 스레드에서 /api/ask/prepare → /api/ask/send
 *
 * 지목이 사라진 것이 아니라 **뒤로 갔다.** 후보가 좁혀진 뒤에 사람이 고른다.
 *
 * 답변 말풍선의 "게이트키퍼 처리 경과" 는 /api/trace/{id} 를 **처음 펼칠 때만**
 * 가져온다. 말풍선마다 미리 받아 두면 쓰지도 않을 것을 6단계씩 들고 있게 된다.
 */

"use strict";

// ══════════════════════════════════════════════════════════════════
// 상태
// ══════════════════════════════════════════════════════════════════

const state = {
  currentUser: null,
  users: [],
  agents: [],            // AgentCardView[]
  agentsById: {},        // entity_id -> AgentCardView
  org: null,             // GET /api/org
  health: null,
  busy: false,

  // 브로드캐스트
  broadcast: null,       // 최근 BroadcastResult
  relevance: {},         // entity_id -> AgentRelevanceView
  pendingQuestion: "",   // 뿌려 놓고 아직 사람을 안 고른 질문

  // 대화
  activeThread: null,    // entity_id · null 이면 브로드캐스트 화면
  threads: {},           // entity_id -> 메시지 배열
  broadcastLog: [],      // 브로드캐스트 화면의 메시지 배열

  // 트레이스 캐시 (trace_id -> GatekeeperTrace)
  traces: {},
};

const MAX_QUESTION = 4000;

//: 파동이 최소한 이만큼은 보인다. 응답이 즉시 와도 사용자가
//: "전원에게 물었다" 는 사실을 볼 수 있어야 한다.
const WAVE_MIN_MS = 900;

//: 카드가 순서대로 반응하는 간격 (ms).
const PING_STAGGER_MS = 55;

// ══════════════════════════════════════════════════════════════════
// DOM 도구
// ══════════════════════════════════════════════════════════════════

const $ = (id) => document.getElementById(id);

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

function badge(text, kind = "muted") {
  return el("span", { class: `badge badge-${kind}`, text });
}

const fmtTime = (d) => {
  if (!d) d = new Date();
  if (typeof d === "string") d = new Date(d);
  return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* `**강조**` 만 해석해 <strong> 으로 만든다.
 *
 * 마크다운 파서를 붙이지 않는 것이 의도적이다. 트레이스 캡션은 서버가 준
 * 문자열이고, 파서를 붙이면 그 문자열이 HTML 이 될 수 있는 경로가 생긴다.
 * 여기서 만드는 것은 텍스트 노드와 <strong> 뿐이다 (BR-U-12). */
function richText(target, text) {
  const parts = String(text || "").split("**");
  parts.forEach((part, i) => {
    if (!part) return;
    if (i % 2 === 1) target.appendChild(el("strong", { text: part }));
    else target.appendChild(document.createTextNode(part));
  });
  return target;
}

function para(text, cls) {
  return richText(el("p", cls ? { class: cls } : {}), text);
}

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

/* 트레이스 단계의 상태 -> 아이콘·색. 서버가 주는 값이 전부 여기 있다. */
const STAGE_STATUS = {
  pass:    { mark: "✓", kind: "pass" },
  fail:    { mark: "✕", kind: "fail" },
  warn:    { mark: "▲", kind: "warn" },
  blocked: { mark: "■", kind: "fail" },
  skip:    { mark: "–", kind: "muted" },
  info:    { mark: "•", kind: "muted" },
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
// 스레드 — 사람마다 대화가 따로 쌓인다
// ══════════════════════════════════════════════════════════════════
//
// 메시지를 **데이터로** 들고 다시 그린다. DOM 을 들고 있으면 스레드를 오갈 때
// 어느 말풍선이 어느 스레드 것인지가 화면 상태에만 남고, 사용자를 전환하거나
// 조직도를 새로 그리는 순간 어긋난다.

const BROADCAST_VIEW = null;

function threadOf(entityId) {
  if (entityId === BROADCAST_VIEW) return state.broadcastLog;
  if (!state.threads[entityId]) state.threads[entityId] = [];
  return state.threads[entityId];
}

function pushMessage(entityId, message) {
  threadOf(entityId).push({ at: new Date().toISOString(), ...message });
  if (state.activeThread === entityId) renderThread();
}

function openThread(entityId) {
  state.activeThread = entityId;
  renderThreadBar();
  renderThread();
  renderOrgTree();
  $("message-input").focus();
}

function renderThreadBar() {
  const who = $("thread-who");
  clear(who);
  const back = $("thread-back");

  if (state.activeThread === BROADCAST_VIEW) {
    back.hidden = true;
    who.appendChild(el("span", { class: "thread-title", text: "전체에게 묻기" }));
    who.appendChild(el("span", {
      class: "thread-sub",
      text: "질문을 보내면 모든 사람의 Agent 가 먼저 스스로 판단합니다",
    }));
    $("message-input").placeholder = "질문을 입력하세요 — 답할 수 있는 사람을 찾아드립니다";
    $("input-hint").textContent = "Enter 전송 · Shift+Enter 줄바꿈 · 질문은 전원에게 방송됩니다";
    return;
  }

  const agent = state.agentsById[state.activeThread];
  const rel = state.relevance[state.activeThread];
  back.hidden = false;

  const title = el("span", { class: "thread-title" }, [
    el("span", { text: agent ? agent.display_name : state.activeThread }),
  ]);
  if (agent && agent.rank_badge) {
    title.appendChild(el("span", { class: "rank-badge", text: agent.rank_badge }));
  }
  who.appendChild(title);

  const bits = [];
  if (agent && agent.unit_path && agent.unit_path.length) bits.push(agent.unit_path.join(" · "));
  if (agent && agent.org_title) bits.push(agent.org_title);
  else if (agent && agent.expertise) bits.push(agent.expertise);
  who.appendChild(el("span", { class: "thread-sub", text: bits.join(" — ") }));

  if (rel && rel.reason) {
    who.appendChild(el("span", { class: "thread-reason", text: `선별 근거: ${rel.reason}` }));
  }

  $("message-input").placeholder =
    `${agent ? agent.display_name : "이 사람"}의 Agent 에게 질문하기`;
  $("input-hint").textContent = "Enter 전송 · Shift+Enter 줄바꿈 · 이 사람에게만 전달됩니다";
}

function renderThread() {
  const container = $("chat-messages");
  clear(container);
  const messages = threadOf(state.activeThread);

  if (messages.length === 0) {
    container.appendChild(emptyState());
    return;
  }
  for (const message of messages) container.appendChild(renderMessage(message));
  container.scrollTop = container.scrollHeight;
}

function emptyState() {
  const box = el("div", { class: "empty-state" });
  if (state.activeThread === BROADCAST_VIEW) {
    box.appendChild(el("p", { class: "empty-title", text: "누구에게 물어야 할지 모르겠다면" }));
    box.appendChild(para(
      "질문만 입력하세요. 모든 사람의 Agent 에게 방송되고, **답할 수 있는 사람만** " +
      "조직도에 남습니다. 이 단계에서는 문서를 읽지 않고 경계를 넘는 것도 없습니다.",
      "empty-body",
    ));
  } else {
    const agent = state.agentsById[state.activeThread];
    box.appendChild(el("p", {
      class: "empty-title",
      text: `${agent ? agent.display_name : "이 사람"}의 Agent`,
    }));
    box.appendChild(para(
      "질문을 입력하면 이 사람의 세션에서 근거를 찾아 게이트키퍼를 통과시킨 뒤 답합니다. " +
      "**본인을 깨우지 않습니다.**",
      "empty-body",
    ));
  }
  return box;
}

function renderMessage(message) {
  switch (message.kind) {
    case "user":      return userBubble(message);
    case "system":    return systemBubble(message);
    case "broadcast": return broadcastBubble(message);
    case "answer":    return answerBubble(message);
    default:          return systemBubble(message);
  }
}

function bubble(type, content, opts = {}) {
  const msg = el("div", { class: `message message-${type}` });
  const contentDiv = el("div", { class: "message-content" });
  if (typeof content === "string") contentDiv.appendChild(el("p", { text: content }));
  else if (content) contentDiv.appendChild(content);
  if (opts.hint) contentDiv.appendChild(el("p", { class: "message-hint", text: opts.hint }));
  msg.appendChild(contentDiv);
  msg.appendChild(el("span", { class: "message-time", text: fmtTime(opts.at) }));
  return msg;
}

function userBubble(message) {
  return bubble("user", message.text, { hint: message.hint, at: message.at });
}

function systemBubble(message) {
  return bubble("system", message.text, { at: message.at });
}

function addLoadingMessage() {
  const container = $("chat-messages");
  const msg = el("div", { class: "message message-assistant message-loading" }, [
    el("div", { class: "message-content" }, [
      el("div", { class: "loading-dots" }, [el("span"), el("span"), el("span")]),
    ]),
  ]);
  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
  return msg;
}

function removeMessage(node) {
  if (node && node.parentNode) node.parentNode.removeChild(node);
}

// ══════════════════════════════════════════════════════════════════
// 브로드캐스트 결과 말풍선
// ══════════════════════════════════════════════════════════════════

function broadcastBubble(message) {
  const result = message.result;
  const relevant = result.results.filter((r) => r.relevant);
  const content = el("div");

  const head = el("p", { class: "bc-head" });
  head.appendChild(el("span", {
    text: `${result.results.length}명의 Agent 가 질문을 받았고, ${relevant.length}명이 답할 수 있다고 판단했습니다.`,
  }));
  content.appendChild(head);

  if (relevant.length === 0) {
    content.appendChild(para(
      "겹치는 담당 영역을 찾지 못했습니다. 조직도에서 **전체 보기**를 눌러 직접 고르거나, " +
      "질문에 다루는 주제를 한 단어 더 넣어 보세요.",
      "bc-note",
    ));
  } else {
    const list = el("div", { class: "bc-list" });
    for (const r of relevant) {
      list.appendChild(el("button", {
        class: "bc-pick",
        attrs: { type: "button", "data-testid": `bc-pick-${r.entity_id}` },
        on: { click: () => pickPerson(r.entity_id) },
      }, [
        el("span", { class: "bc-pick-name", text: r.display_name }),
        el("span", { class: "bc-pick-reason", text: r.reason }),
        el("span", { class: "bc-pick-go", text: "대화 →" }),
      ]));
    }
    content.appendChild(list);
  }

  const meta = el("div", { class: "bc-meta" });
  meta.appendChild(badge(result.model_used ? "규칙 + EXAONE 선별" : "규칙만으로 선별", "accent"));
  meta.appendChild(badge("경계를 넘은 것 없음", "ok"));
  if (result.model_note) meta.appendChild(badge(result.model_note, "warn"));
  content.appendChild(meta);

  content.appendChild(para(
    "이 단계에서 사용한 것은 담당 영역·주제 키워드·조직도·현재 작업 라벨뿐입니다. " +
    "**문서는 한 글자도 읽지 않았고**, 아무에게도 알림이 가지 않았습니다.",
    "bc-note",
  ));

  return bubble("system", content, { at: message.at });
}

// ══════════════════════════════════════════════════════════════════
// 조직도 — 깊이는 데이터가 정한다
// ══════════════════════════════════════════════════════════════════
//
// 이 함수에는 "본부"·"센터"·"팀" 이라는 말이 하나도 없다. `GET /api/org` 이
// 준 트리를 재귀로 그릴 뿐이고, 층 이름은 `kind_label` 로 서버에서 온다.
// 층을 하나 더 넣는 변경이 `config/org.yaml` 편집으로 끝나는 이유다.

function renderOrgTree() {
  const tree = $("org-tree");
  clear(tree);

  if (state.agents.length === 0) {
    tree.appendChild(el("p", { class: "message-hint", text: "에이전트가 없습니다." }));
    return;
  }

  const org = state.org;
  if (!org || !org.roots || org.roots.length === 0) {
    // 조직도가 없어도 화면은 동작해야 한다 — 평평한 목록으로 떨어진다.
    tree.appendChild(memberList(state.agents.map((a) => a.entity_id)));
    return;
  }

  for (const root of org.roots) {
    const node = renderUnit(root);
    if (node) tree.appendChild(node);
  }

  const unplaced = (org.unplaced_member_ids || []).filter((id) => state.agentsById[id]);
  const known = new Set(collectMemberIds(org.roots));
  const missing = state.agents
    .map((a) => a.entity_id)
    .filter((id) => !known.has(id) && !unplaced.includes(id));
  const strays = [...unplaced, ...missing];
  if (strays.length > 0) {
    // 조용히 사라지는 것보다 눈에 띄는 편이 낫다 (org.py 의 unplaced 와 같은 이유).
    tree.appendChild(el("div", { class: "org-unit org-unit-stray" }, [
      el("div", { class: "org-unit-head" }, [
        el("span", { class: "org-unit-kind", text: "미배치" }),
        el("span", { class: "org-unit-name", text: "조직도에 자리가 없는 사람" }),
      ]),
      memberList(strays),
    ]));
  }
}

function collectMemberIds(units) {
  const out = [];
  for (const unit of units) {
    out.push(...(unit.member_ids || []));
    out.push(...collectMemberIds(unit.children || []));
  }
  return out;
}

function renderUnit(unit) {
  const empty = (unit.member_count_total || 0) === 0;
  const node = el("div", {
    class: `org-unit depth-${Math.min(unit.depth, 4)}${empty ? " org-unit-empty" : ""}`,
  });

  const head = el("div", { class: "org-unit-head" }, [
    el("span", { class: "org-unit-kind", text: unit.kind_label }),
    el("span", { class: "org-unit-name", text: unit.name }),
    el("span", { class: "org-unit-count", text: `${unit.member_count_total}명` }),
  ]);
  node.appendChild(head);

  if (unit.description && !empty) {
    node.appendChild(el("div", { class: "org-unit-desc", text: unit.description }));
  }

  if (empty) {
    // 사람이 없는 단위는 접어서 그린다. 구조가 데이터라는 것은 보이되
    // 화면을 차지하지는 않게.
    return node;
  }

  const members = (unit.member_ids || []).filter((id) => state.agentsById[id]);
  if (members.length > 0) node.appendChild(memberList(members));

  for (const child of unit.children || []) {
    const rendered = renderUnit(child);
    if (rendered) node.appendChild(rendered);
  }
  return node;
}

function memberList(entityIds) {
  const list = el("div", { class: "org-list" });
  for (const entityId of entityIds) {
    const card = memberCard(entityId);
    if (card) list.appendChild(card);
  }
  return list;
}

function memberCard(entityId) {
  const a = state.agentsById[entityId];
  if (!a) return null;
  if (a.entity_id === state.currentUser) return null;  // 자기 자신은 제외

  const rel = state.relevance[entityId];
  const hasVerdict = state.broadcast !== null && rel !== undefined;
  const dimmed = hasVerdict && !rel.relevant;
  const active = state.activeThread === entityId;
  const blocked = a.daily_limit_reached;

  const act = ACTIVITY[a.activity_status] || null;

  const classes = ["org-card"];
  if (hasVerdict && rel.relevant) classes.push("is-relevant");
  if (dimmed) classes.push("is-dimmed");
  if (active) classes.push("is-active");
  if (blocked) classes.push("is-blocked");

  const info = el("div", { class: "org-info" }, [
    el("div", { class: "org-name-row" }, [
      el("span", { class: "org-name", text: a.display_name }),
      a.rank_badge ? el("span", { class: "rank-badge", text: a.rank_badge }) : null,
    ]),
    el("div", { class: "org-role", text: a.org_title || a.expertise || "" }),
  ]);

  if (act) {
    info.appendChild(el("div", { class: "org-status" }, [
      el("span", { class: `dot ${act.dot}` }),
      el("span", { text: act.label }),
      a.current_focus_summary
        ? el("span", { class: "org-focus", text: `· ${a.current_focus_summary}` })
        : null,
    ]));
  }

  if (hasVerdict && rel.relevant) {
    info.appendChild(el("div", { class: "org-verdict", text: rel.reason }));
  }
  if (blocked) {
    info.appendChild(el("div", { class: "org-verdict org-verdict-bad", text: "오늘 한도 초과" }));
  }

  return el("button", {
    class: classes.join(" "),
    attrs: {
      type: "button",
      "aria-pressed": String(active),
      "aria-disabled": String(blocked),
      "data-entity": entityId,
      "data-testid": `org-card-${entityId}`,
    },
    on: { click: () => { if (!blocked) pickPerson(entityId); } },
  }, [
    el("div", { class: "org-avatar", text: a.display_name.charAt(0) }),
    info,
    el("span", { class: "org-go", text: "›" }),
  ]);
}

// ══════════════════════════════════════════════════════════════════
// 브로드캐스트 — 그래픽 효과 + 판정
// ══════════════════════════════════════════════════════════════════

function startWave() {
  const panel = $("org-panel");
  panel.classList.add("broadcasting");
  $("org-subtitle").textContent = "전원의 Agent 에게 질문을 보내는 중…";

  // 카드마다 시차를 둬 파동이 퍼지는 것처럼 보이게 한다.
  // 인라인 style 속성이 아니라 JS 로 지정한다 — HTML 에 style= 를 쓰지 않는다.
  const cards = document.querySelectorAll(".org-card");
  cards.forEach((card, i) => {
    card.classList.remove("is-dimmed", "is-relevant");
    card.style.setProperty("--ping-delay", `${i * PING_STAGGER_MS}ms`);
    card.classList.add("is-pinging");
  });
}

function stopWave() {
  $("org-panel").classList.remove("broadcasting");
  document.querySelectorAll(".org-card").forEach((card) => {
    card.classList.remove("is-pinging");
    card.style.removeProperty("--ping-delay");
  });
}

function applyBroadcast(result) {
  state.broadcast = result;
  state.relevance = {};
  for (const r of result.results) state.relevance[r.entity_id] = r;

  const relevant = result.results.filter((r) => r.relevant).length;
  $("org-subtitle").textContent = relevant > 0
    ? `${relevant}명이 답할 수 있다고 판단했습니다 — 눌러서 대화하세요`
    : "겹치는 담당 영역을 찾지 못했습니다 — 전체 보기로 직접 고를 수 있습니다";
  $("org-reset").hidden = false;
  renderOrgTree();
}

function resetBroadcast() {
  state.broadcast = null;
  state.relevance = {};
  $("org-subtitle").textContent = "질문을 보내면 답할 수 있는 사람이 남습니다";
  $("org-reset").hidden = true;
  renderOrgTree();
}

async function doBroadcast(question) {
  state.pendingQuestion = question;
  pushMessage(BROADCAST_VIEW, { kind: "user", text: question, hint: "→ 전원의 Agent" });

  startWave();
  const loading = addLoadingMessage();
  const startedAt = Date.now();

  try {
    const result = await api("/api/ask/broadcast", {
      method: "POST",
      body: { question, asker: state.currentUser },
    });
    // 응답이 즉시 와도 파동은 끝까지 보여준다 — 무엇이 일어났는지가 보여야 한다.
    const elapsed = Date.now() - startedAt;
    if (elapsed < WAVE_MIN_MS) await sleep(WAVE_MIN_MS - elapsed);

    removeMessage(loading);
    stopWave();
    applyBroadcast(result);
    pushMessage(BROADCAST_VIEW, { kind: "broadcast", result });
  } catch (err) {
    removeMessage(loading);
    stopWave();
    pushMessage(BROADCAST_VIEW, { kind: "system", text: err.message });
  }
}

/* 사람을 골랐다 — 그 사람의 스레드를 열고, 뿌려 둔 질문이 있으면 이어서 보낸다. */
function pickPerson(entityId) {
  const pending = state.pendingQuestion;
  openThread(entityId);
  if (pending) {
    state.pendingQuestion = "";
    askPerson(entityId, pending);
  }
}

// ══════════════════════════════════════════════════════════════════
// 한 사람에게 묻기 (prepare → send)
// ══════════════════════════════════════════════════════════════════

async function askPerson(entityId, question) {
  const agent = state.agentsById[entityId];
  pushMessage(entityId, {
    kind: "user",
    text: question,
    hint: `→ ${agent ? agent.display_name : entityId}의 Agent`,
  });

  state.busy = true;
  refreshSendButton();
  const loading = addLoadingMessage();

  try {
    const prepared = await api("/api/ask/prepare", {
      method: "POST",
      body: { question, asker: state.currentUser, targets: [entityId] },
    });

    const blocked = prepared.calls.filter((c) => c.disposition === "blocked");
    const ready = prepared.calls.filter((c) => c.disposition === "ready");

    removeMessage(loading);

    for (const call of blocked) {
      pushMessage(entityId, {
        kind: "answer",
        answer: call.fallback,
        agentLabel: call.agent_label,
        dispositionKey: "blocked",
        blockedReason: call.blocked_reason,
        traceId: call.trace_id,
        tier: call.tier,
      });
    }
    if (ready.length === 0) return;

    const sending = addLoadingMessage();
    const result = await api("/api/ask/send", {
      method: "POST",
      body: {
        request_id: prepared.request_id,
        envelope_ids: ready.map((c) => c.envelope_id),
        approved_by: state.currentUser,
      },
    });
    removeMessage(sending);

    const merged = result.merged;
    for (const answer of merged.answers) {
      pushMessage(entityId, {
        kind: "answer",
        answer,
        agentLabel: answer.agent_label,
        dispositionKey: merged.disposition,
        traceId: answer.trace_id,
        tier: answer.tier,
      });
    }
    if (merged.divergent && merged.divergence_note) {
      pushMessage(entityId, { kind: "system", text: `주의: ${merged.divergence_note}` });
    }
  } catch (err) {
    removeMessage(loading);
    pushMessage(entityId, { kind: "system", text: err.message });
  } finally {
    state.busy = false;
    refreshSendButton();
  }
}

function answerBubble(message) {
  const answer = message.answer || {};
  const disp = DISPOSITION[message.dispositionKey] || DISPOSITION.auto;
  const content = el("div");

  content.appendChild(el("p", { text: answer.text || "답변을 준비하고 있습니다." }));

  if (message.blockedReason) {
    content.appendChild(el("p", { class: "answer-blocked", text: message.blockedReason }));
  }

  if (answer.citations && answer.citations.length > 0) {
    const cites = el("div", { class: "answer-citations" });
    cites.appendChild(el("span", { text: "인용: " }));
    for (const c of answer.citations) {
      cites.appendChild(badge(c.display_title || c.ref, "muted"));
    }
    content.appendChild(cites);
  }

  if (answer.unresolved_refs && answer.unresolved_refs.length > 0) {
    content.appendChild(el("p", {
      class: "answer-warn",
      text: `되돌리지 못한 참조 기호 ${answer.unresolved_refs.length}개가 남아 있습니다 (그대로 표시).`,
    }));
  }

  content.appendChild(buildTraceBlock(message));

  const tierLabel = TIER[message.tier] ? TIER[message.tier].label : "사내";
  const node = bubble("assistant", content, {
    hint: `${message.agentLabel || "Agent"} · ${tierLabel} · ${disp.label}`,
    at: message.at,
  });
  if (message.dispositionKey === "blocked") node.classList.add("gk-blocked");
  else if (message.tier === "secret") node.classList.add("gk-masked");
  else node.classList.add("gk-pass");
  return node;
}

// ══════════════════════════════════════════════════════════════════
// 게이트키퍼 처리 경과 — 단계를 눌러 실제 자료를 본다
// ══════════════════════════════════════════════════════════════════
//
// 요약 한 줄만 접어 두던 것을 6단계로 펼친다. 각 단계는 그 단계가 실제로
// 손에 쥐고 있던 자료를 보여준다 (`src/mesh/trace.py`).
//
// 트레이스는 **처음 펼칠 때** 가져온다. 말풍선마다 미리 받으면 쓰지도 않을
// 6단계를 전부 들고 있게 된다.

function buildTraceBlock(message) {
  const strip = el("div", { class: "gk-strip" });

  const status = message.dispositionKey === "blocked"
    ? { text: "경계를 넘지 않았습니다", kind: "bad" }
    : message.tier === "secret"
      ? { text: "구조만 추출해 내보냈습니다", kind: "warn" }
      : { text: "검증을 통과해 내보냈습니다", kind: "ok" };

  strip.appendChild(el("span", { class: "gk-strip-label", text: "게이트키퍼" }));
  strip.appendChild(badge(status.text, status.kind));
  if (message.tier) strip.appendChild(tierBadge(message.tier));

  if (!message.traceId) {
    strip.appendChild(el("span", {
      class: "gk-strip-note",
      text: "처리 경과가 만료되었습니다 (기록은 30분 뒤 사라집니다)",
    }));
    return strip;
  }

  strip.appendChild(el("button", {
    class: "gk-strip-btn",
    attrs: { type: "button", "data-testid": `trace-open-${message.traceId}` },
    on: { click: () => openTraceModal(message.traceId) },
  }, [
    el("span", { text: "단계별로 보기" }),
    el("span", { class: "gk-strip-arrow", text: "▸" }),
  ]));
  return strip;
}

// ── 트레이스 모달 ─────────────────────────────────────────────────
//
// 트레이스는 **처음 열 때** 가져온다. 말풍선마다 미리 받아 두면 쓰지도 않을
// 6단계를 전부 들고 있게 된다. 한 번 받은 것은 캐시한다 (TTL 은 서버에 있다).

async function openTraceModal(traceId) {
  const modal = $("trace-modal");
  const tabs = $("trace-tabs");
  const panels = $("trace-panels");

  clear(tabs);
  clear(panels);
  $("trace-modal-sub").textContent = "처리 경과를 불러오는 중…";
  if (!modal.open) modal.showModal();

  let trace;
  try {
    trace = state.traces[traceId]
      || (state.traces[traceId] = await api(`/api/trace/${traceId}`));
  } catch (err) {
    $("trace-modal-sub").textContent = err.message;
    return;
  }
  renderTrace(trace, tabs, panels);
}

function renderTrace(trace, tabs, panels) {
  const sub = $("trace-modal-sub");
  clear(sub);
  richText(sub, trace.question || "");

  const meta = el("span", { class: "trace-meta" });
  if (trace.agent_label) meta.appendChild(badge(trace.agent_label, "muted"));
  if (trace.tier) meta.appendChild(tierBadge(trace.tier));
  meta.appendChild(badge(
    trace.crossed_boundary ? "경계를 넘었음" : "경계를 넘지 않음",
    trace.crossed_boundary ? "warn" : "ok",
  ));
  sub.appendChild(meta);

  clear(tabs);
  clear(panels);

  const stages = trace.stages || [];
  if (stages.length === 0) {
    panels.appendChild(el("p", { class: "gk-note", text: "기록된 단계가 없습니다." }));
    return;
  }

  const buttons = [];
  stages.forEach((stage, i) => {
    const status = STAGE_STATUS[stage.status] || STAGE_STATUS.info;
    const tab = el("button", {
      class: `trace-tab status-${status.kind}${stage.crosses_boundary ? " crosses" : ""}`,
      attrs: {
        type: "button",
        role: "tab",
        "aria-selected": "false",
        "aria-controls": "trace-panels",
        "data-testid": `trace-tab-${stage.stage_id}`,
      },
      on: { click: () => select(i) },
    }, [
      el("span", { class: "trace-tab-no", text: String(i + 1) }),
      el("span", { class: "trace-tab-title", text: stage.title }),
      el("span", { class: `trace-tab-mark ${status.kind}`, text: status.mark }),
      stage.elapsed_ms !== null && stage.elapsed_ms !== undefined
        ? el("span", { class: "trace-tab-ms", text: `${stage.elapsed_ms}ms` })
        : null,
    ]);
    buttons.push(tab);
    tabs.appendChild(tab);

    // 경계는 눈에 보이는 선이어야 한다. 위는 노트북 안, 아래는 바깥이다.
    if (stage.crosses_boundary) {
      tabs.appendChild(el("span", { class: "trace-boundary", text: "신뢰 경계" }));
    }
  });

  function select(index) {
    buttons.forEach((b, i) => {
      b.classList.toggle("is-open", i === index);
      b.setAttribute("aria-selected", String(i === index));
    });
    clear(panels);
    panels.appendChild(renderStage(stages[index]));
    panels.scrollTop = 0;
  }

  // 문제가 있던 단계를 먼저 연다. 없으면 첫 단계.
  const firstBad = stages.findIndex((s) => s.status === "fail" || s.status === "blocked");
  select(firstBad >= 0 ? firstBad : 0);
}

function renderStage(stage) {
  const box = el("div", { class: "trace-stage" });

  box.appendChild(el("div", { class: "trace-stage-head" }, [
    el("span", { class: "trace-stage-title", text: stage.title }),
    el("span", { class: "trace-stage-sub", text: stage.subtitle || "" }),
  ]));
  if (stage.summary) {
    box.appendChild(el("div", { class: "trace-stage-summary", text: stage.summary }));
  }

  for (const panel of stage.panels || []) box.appendChild(renderPanel(panel));
  return box;
}

function renderPanel(panel) {
  const box = el("div", { class: `trace-panel kind-${panel.kind}` });
  box.appendChild(el("div", { class: "trace-panel-label", text: panel.label }));
  if (panel.caption) box.appendChild(para(panel.caption, "trace-panel-caption"));

  switch (panel.kind) {
    case "json":
      box.appendChild(jsonBlock(panel.json_text));
      break;
    case "table":
      box.appendChild(tableBlock(panel));
      break;
    case "compare":
      box.appendChild(compareBlock(panel));
      break;
    case "list":
      box.appendChild(el("ul", { class: "trace-list" },
        (panel.items || []).map((item) => el("li", { text: item }))));
      break;
    default:
      box.appendChild(para(panel.text || "", "trace-note"));
  }

  if (panel.redacted_count > 0) {
    box.appendChild(el("div", {
      class: "trace-redacted",
      text: `${panel.redacted_count}건은 값을 싣지 않았습니다 — 숨겼다는 사실은 숨기지 않습니다.`,
    }));
  }
  return box;
}

/* JSON 을 토큰별로 색칠한다. **자르지 않는다** (BR-U-01) —
 * 전문을 보여준다고 하면서 일부만 보이면 그건 거짓말이다. */
function jsonBlock(text) {
  const pre = el("pre", { class: "payload trace-payload", attrs: { tabindex: "0" } });
  const source = String(text || "");
  const re = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(\b-?\d+(?:\.\d+)?\b)|(\btrue\b|\bfalse\b|\bnull\b)/g;
  let last = 0;
  let m;
  while ((m = re.exec(source)) !== null) {
    if (m.index > last) pre.appendChild(document.createTextNode(source.slice(last, m.index)));
    const cls = m[1] ? "tok-key" : m[2] ? "tok-str" : m[3] ? "tok-num" : "tok-bool";
    pre.appendChild(el("span", { class: cls, text: m[0] }));
    last = m.index + m[0].length;
  }
  if (last < source.length) pre.appendChild(document.createTextNode(source.slice(last)));
  return pre;
}

function tableBlock(panel) {
  const wrap = el("div", { class: "trace-table-wrap" });
  const table = el("table", { class: "trace-table" });

  if (panel.columns && panel.columns.length) {
    table.appendChild(el("thead", {}, [
      el("tr", {}, panel.columns.map((c) => el("th", { text: c }))),
    ]));
  }
  const tbody = el("tbody");
  for (const row of panel.rows || []) {
    tbody.appendChild(el("tr", { class: `row-${row.status}` },
      (row.cells || []).map((cell) => el("td", { text: cell }))));
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

/* 기호 답변 ↔ 복원된 답변. 왼쪽은 경계 밖 모델이 만든 그대로이고
 * 오른쪽은 신뢰 구역 안에서 기호를 되돌린 것이다. */
function compareBlock(panel) {
  return el("div", { class: "trace-compare" }, [
    el("div", { class: "trace-compare-side before" }, [
      el("div", { class: "trace-compare-label", text: panel.before_label || "변환 전" }),
      el("pre", { class: "trace-compare-text", text: panel.before_text || "" }),
    ]),
    el("div", { class: "trace-compare-arrow", text: "→" }),
    el("div", { class: "trace-compare-side after" }, [
      el("div", { class: "trace-compare-label", text: panel.after_label || "변환 후" }),
      el("pre", { class: "trace-compare-text", text: panel.after_text || "" }),
    ]),
  ]);
}

// ══════════════════════════════════════════════════════════════════
// 헤더 상태 · 사용자 전환 · 입력
// ══════════════════════════════════════════════════════════════════

function renderHealth() {
  const box = $("health-badges");
  clear(box);
  const h = state.health;
  if (!h) {
    box.appendChild(badge("연결 안 됨", "bad"));
    return;
  }
  box.appendChild(h.exaone_mode === "mock" ? badge("MOCK 모드", "warn") : badge("LIVE", "ok"));
  box.appendChild(badge(`Agent: ${h.agent_transport}`, "accent"));
}

function renderUsers() {
  const sel = $("user-select");
  clear(sel);
  for (const u of state.users) {
    sel.appendChild(el("option", { text: u.display_name, attrs: { value: u.entity_id } }));
  }
  if (state.currentUser) sel.value = state.currentUser;
}

function refreshSendButton() {
  const text = $("message-input").value.trim();
  $("send-btn").disabled = state.busy || !text || text.length > MAX_QUESTION;
}

function autoResize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
}

/* 입력창의 동작이 스레드에 따라 갈린다.
 *   브로드캐스트 화면  -> 전원에게 방송
 *   사람 스레드        -> 그 사람에게만 */
async function onSubmit() {
  const input = $("message-input");
  const question = input.value.trim();
  if (!question || state.busy) return;

  input.value = "";
  autoResize(input);
  refreshSendButton();

  if (state.activeThread === BROADCAST_VIEW) {
    state.busy = true;
    refreshSendButton();
    try {
      await doBroadcast(question);
    } finally {
      state.busy = false;
      refreshSendButton();
    }
    return;
  }
  await askPerson(state.activeThread, question);
}

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


// ══════════════════════════════════════════════════════════════════
// 이벤트 바인딩
// ══════════════════════════════════════════════════════════════════

function wire() {
  wireProtocol();

  const input = $("message-input");
  const sendBtn = $("send-btn");

  input.addEventListener("input", () => {
    autoResize(input);
    refreshSendButton();
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) onSubmit();
    }
  });

  sendBtn.addEventListener("click", onSubmit);

  $("trace-modal-close").addEventListener("click", () => $("trace-modal").close());

  $("thread-back").addEventListener("click", () => openThread(BROADCAST_VIEW));
  $("org-reset").addEventListener("click", resetBroadcast);
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

  try {
    state.agents = await api("/api/agents");
  } catch { state.agents = []; }
  state.agentsById = Object.fromEntries(state.agents.map((a) => [a.entity_id, a]));

  // 조직도는 표시용이다. 없어도 화면은 평평한 목록으로 떨어진다.
  try {
    state.org = await api("/api/org");
  } catch { state.org = null; }

  openThread(BROADCAST_VIEW);
  refreshSendButton();
}

document.addEventListener("DOMContentLoaded", boot);

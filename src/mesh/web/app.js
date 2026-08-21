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

/* 내 Agent 스레드의 키. 사람 `entity_id` 와 섞이지 않게 `@` 를 쓴다 —
 * `entity_id` 는 `person:kim` 꼴이라 `@` 로 시작할 수 없다.
 *
 * ⚠️ `state` 의 초기값이 이 상수를 참조하므로 **선언이 먼저 와야 한다.**
 *    뒤에 두면 모듈이 로드되는 순간 TDZ 오류로 화면 전체가 죽는다. */
const MY_AGENT = "@me";

const state = {
  me: null,              // GET /api/me — 내 Agent 의 주인
  agents: [],            // AgentCardView[]
  agentsById: {},        // entity_id -> AgentCardView
  org: null,             // GET /api/org
  busy: false,

  // 브로드캐스트 판정 (조직도 강조에 쓴다)
  broadcast: null,
  relevance: {},         // entity_id -> AgentRelevanceView

  // 대화
  activeThread: MY_AGENT,  // MY_AGENT 이거나 entity_id
  threads: {},             // 스레드 키 -> 메시지 배열

  // 트레이스 캐시 (trace_id -> GatekeeperTrace)
  traces: {},

  // 직접 선택 질의 대상 (Ctrl+클릭) — entity_id Set
  selectedTargets: new Set(),

  // SSE 실시간 소통 중 강조 (주황 테두리) — entity_id Set.
  // 상태로 들고 있어야 renderOrgTree 가 카드를 다시 그려도 강조가 유지된다.
  communicating: new Set(),

  // Broadcasting 토글 — true 면 항상 전원 방송, false 면 기존 동작(혼자 못 답하면 방송).
  forceBroadcast: false,
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
// 마크다운 — DOM 을 직접 짓는다
// ══════════════════════════════════════════════════════════════════
//
// 라이브러리를 쓰지 않는 이유가 두 가지 있다.
//
//   1. 이 화면은 외부 스크립트를 하나도 불러오지 않는다 (CSP + SECURITY-10).
//   2. 거의 모든 마크다운 라이브러리는 **HTML 문자열**을 만든다. 그 문자열을
//      화면에 넣으려면 `innerHTML` 이 필요하고, 그건 금지돼 있다 (BR-U-12).
//      여기서 만드는 것은 텍스트 노드와 정해진 태그뿐이라 주입 경로가 없다.
//
// 링크는 **일부러 만들지 않는다.** 답변 텍스트는 문서에서 온 것이고, 거기
// `[클릭](javascript:…)` 이 들어 있으면 클릭 가능한 실행 경로가 생긴다.
// 링크 문구는 글자로 남기고 URL 은 괄호에 그대로 둔다.

/* `_` 를 기울임으로 보려면 **단어 경계에 있어야 한다.**
 * 이 도메인의 텍스트에는 `max_session_hours` · `auth_mechanism_class` 처럼
 * snake_case 식별자가 널려 있고, 경계를 안 보면 그것들이 통째로 기울어진다
 * (실제로 답변이 "maxsessionhours" 로 뭉개졌다). */
const MD_INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|((?<![A-Za-z0-9_])_[^_\n]+_(?![A-Za-z0-9_]))/g;

function mdInline(target, text) {
  const source = String(text || "");
  let last = 0;
  let m;
  MD_INLINE.lastIndex = 0;
  while ((m = MD_INLINE.exec(source)) !== null) {
    if (m.index > last) target.appendChild(document.createTextNode(source.slice(last, m.index)));
    const token = m[0];
    if (m[1]) target.appendChild(el("code", { text: token.slice(1, -1) }));
    else if (m[2]) target.appendChild(el("strong", { text: token.slice(2, -2) }));
    else target.appendChild(el("em", { text: token.slice(1, -1) }));
    last = m.index + token.length;
  }
  if (last < source.length) target.appendChild(document.createTextNode(source.slice(last)));
  return target;
}

function mdTableRow(line) {
  return line.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

const MD_ALIGN_ROW = /^\s*\|?[\s:|-]+\|[\s:|-]*$/;

function renderMarkdown(container, text) {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  let list = null;      // 진행 중인 <ul>/<ol>
  let paragraph = [];   // 진행 중인 문단 줄들

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    container.appendChild(mdInline(el("p"), paragraph.join(" ")));
    paragraph = [];
  };
  const flushList = () => { list = null; };

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // 코드 블록
    if (trimmed.startsWith("```")) {
      flushParagraph(); flushList();
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) { body.push(lines[i]); i += 1; }
      i += 1;
      container.appendChild(el("pre", { class: "md-code" }, [el("code", { text: body.join("\n") })]));
      continue;
    }

    // 표
    if (trimmed.startsWith("|") && i + 1 < lines.length && MD_ALIGN_ROW.test(lines[i + 1])) {
      flushParagraph(); flushList();
      const head = mdTableRow(trimmed);
      i += 2;
      const body = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) { body.push(mdTableRow(lines[i].trim())); i += 1; }
      const table = el("table", { class: "md-table" }, [
        el("thead", {}, [el("tr", {}, head.map((c) => mdInline(el("th"), c)))]),
        el("tbody", {}, body.map((r) => el("tr", {}, r.map((c) => mdInline(el("td"), c))))),
      ]);
      container.appendChild(el("div", { class: "md-table-wrap" }, [table]));
      continue;
    }

    if (trimmed === "") { flushParagraph(); flushList(); i += 1; continue; }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushParagraph(); flushList();
      container.appendChild(el("hr", { class: "md-hr" }));
      i += 1; continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (heading) {
      flushParagraph(); flushList();
      const level = Math.min(heading[1].length + 2, 6);  // #→h3 (말풍선 안이다)
      container.appendChild(mdInline(el(`h${level}`, { class: "md-h" }), heading[2]));
      i += 1; continue;
    }

    if (trimmed.startsWith(">")) {
      flushParagraph(); flushList();
      const quote = el("blockquote", { class: "md-quote" });
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.appendChild(mdInline(el("p"), lines[i].trim().replace(/^>\s?/, "")));
        i += 1;
      }
      container.appendChild(quote);
      continue;
    }

    const bullet = /^[-*+]\s+(.*)$/.exec(trimmed);
    const numbered = /^\d+[.)]\s+(.*)$/.exec(trimmed);
    if (bullet || numbered) {
      flushParagraph();
      const wanted = bullet ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== wanted) {
        list = el(wanted, { class: "md-list" });
        container.appendChild(list);
      }
      list.appendChild(mdInline(el("li"), (bullet || numbered)[1]));
      i += 1; continue;
    }

    flushList();
    paragraph.push(trimmed);
    i += 1;
  }
  flushParagraph();
  return container;
}

function markdownBlock(text, cls = "md") {
  return renderMarkdown(el("div", { class: cls }), text);
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

/* 스레드 키는 두 종류다.
 *     MY_AGENT      내 Agent 와의 대화. 여기서 질문하면 대신 물어봐 준다
 *     <entity_id>   그 사람의 Agent 와의 1:1 대화
 */

function threadOf(key) {
  if (!state.threads[key]) state.threads[key] = [];
  return state.threads[key];
}

function pushMessage(key, message) {
  threadOf(key).push({ at: new Date().toISOString(), ...message });
  if (state.activeThread === key) renderThread();
}

function openThread(key) {
  state.activeThread = key;
  renderThreadBar();
  renderThread();
  renderOrgTree();
  $("message-input").focus();
}

function myName() {
  return state.me ? state.me.display_name : "나";
}

function renderThreadBar() {
  const who = $("thread-who");
  clear(who);
  const back = $("thread-back");

  if (state.activeThread === MY_AGENT) {
    back.hidden = true;
    who.appendChild(el("span", { class: "thread-title" }, [
      el("span", { text: "내 Agent" }),
      el("span", { class: "rank-badge", text: myName() }),
    ]));
    $("message-input").placeholder = "메시지를 입력하세요";
    $("input-hint").textContent =
      "Enter 전송 · Shift+Enter 줄바꿈";
    updateSelectionHint();
    return;
  }

  const agent = state.agentsById[state.activeThread];
  const rel = state.relevance[state.activeThread];
  back.hidden = false;

  const title = el("span", { class: "thread-title" }, [
    el("span", { text: agent ? `${agent.display_name}의 Agent` : state.activeThread }),
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
    `${agent ? agent.display_name : "이 사람"}의 Agent 에게 직접 질문하기`;
  $("input-hint").textContent = "Enter 전송 · Shift+Enter 줄바꿈";
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
  if (state.activeThread === MY_AGENT) {
    box.appendChild(el("p", { class: "empty-title", text: `${myName()}님의 Agent 입니다` }));
  } else {
    const agent = state.agentsById[state.activeThread];
    box.appendChild(el("p", {
      class: "empty-title",
      text: `${agent ? agent.display_name : "이 사람"}의 Agent 입니다`,
    }));
  }
  return box;
} 

function renderMessage(message) {
  switch (message.kind) {
    case "user":    return userBubble(message);
    case "system":  return systemBubble(message);
    case "digest":  return digestBubble(message);
    case "answer":  return answerBubble(message);
    default:        return systemBubble(message);
  }
}

/* 말풍선 하나. `label` 이 있으면 **말풍선 위에** 누구의 Agent 인지 적는다. */
function bubble(type, content, opts = {}) {
  const msg = el("div", { class: `message message-${type}` });

  if (opts.label) {
    const label = el("div", { class: "message-label" }, [
      el("span", { class: "message-label-name", text: opts.label }),
    ]);
    if (opts.labelBadge) {
      label.appendChild(el("span", { class: "rank-badge", text: opts.labelBadge }));
    }
    msg.appendChild(label);
  }

  const contentDiv = el("div", { class: "message-content" });
  if (typeof content === "string") contentDiv.appendChild(el("p", { text: content }));
  else if (content) contentDiv.appendChild(content);
  if (opts.hint) contentDiv.appendChild(el("p", { class: "message-hint", text: opts.hint }));
  msg.appendChild(contentDiv);
  msg.appendChild(el("span", { class: "message-time", text: fmtTime(opts.at) }));
  return msg;
}

function userBubble(message) {
  return bubble("user", message.text, { label: myName(), hint: message.hint, at: message.at });
}

function systemBubble(message) {
  return bubble("system", message.text, { at: message.at });
}

function addLoadingMessage(label) {
  const container = $("chat-messages");
  const msg = el("div", { class: "message message-assistant message-loading" });
  if (label) {
    msg.appendChild(el("div", { class: "message-label" }, [
      el("span", { class: "message-label-name", text: label }),
    ]));
  }
  msg.appendChild(el("div", { class: "message-content" }, [
    el("div", { class: "loading-dots" }, [el("span"), el("span"), el("span")]),
  ]));
  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
  return msg;
}

function removeMessage(node) {
  if (node && node.parentNode) node.parentNode.removeChild(node);
}

// ══════════════════════════════════════════════════════════════════
// 내 Agent 가 모아 온 답 (digest)
// ══════════════════════════════════════════════════════════════════

function digestBubble(message) {
  const result = message.result;
  const content = el("div");

  // ① 누구에게 물었나 — 정리보다 먼저 온다. 출처를 모르고 요약을 읽으면 안 된다.
  content.appendChild(consultHeader(result));

  // ② 내 Agent 의 정리
  if (result.digest) {
    content.appendChild(markdownBlock(result.digest, "md digest-body"));
  }

  if (result.divergent && result.divergence_note) {
    content.appendChild(para(`주의: ${result.divergence_note}`, "digest-warn"));
  }

  // ③ 오케스트레이션 전체 과정 — 브로드캐스트 선별 + 질의 결과를 트리로.
  content.appendChild(orchDetailBlock(result));

  // ④ 사람별 원답변. 정리만 주면 그 정리를 검증할 방법이 없다.
  if (result.answers && result.answers.length > 0) {
    const details = el("details", { class: "digest-details" });
    details.appendChild(el("summary", {}, [
      el("span", { class: "gk-arrow", text: "▶" }),
      el("span", { text: `각 Agent 의 답변 원문 ${result.answers.length}건` }),
    ]));
    const body = el("div", { class: "digest-answers" });
    for (const answer of result.answers) body.appendChild(answerCard(answer));
    details.appendChild(body);
    content.appendChild(details);
  }

  const node = bubble("assistant", content, {
    label: "내 Agent",
    labelBadge: myName(),
    at: message.at,
  });
  node.classList.add("message-digest");
  return node;
}

/* 오케스트레이션 전체 과정 트리 (U2) — 브로드캐스트 선별 + 질의 결과. */
function orchDetailBlock(result) {
  const details = el("details", { class: "digest-details" });
  details.appendChild(el("summary", {}, [
    el("span", { class: "gk-arrow", text: "▶" }),
    el("span", { text: "오케스트레이션 전체 과정 보기" }),
  ]));

  const tree = el("div", { class: "orch-tree" });

  // 답변을 entity_id 로 바로 찾을 수 있게 인덱스를 만든다.
  const answersById = {};
  (result.answers || []).forEach((a) => { answersById[a.entity_id] = a; });

  // ─ 브로드캐스트 선별 단계 ────────────────────────────────────────
  if (result.broadcast) {
    const bc = result.broadcast;
    const relevant = bc.results.filter((r) => r.relevant);
    const phase = el("div", { class: "orch-phase" });

    phase.appendChild(el("div", { class: "orch-section-head" }, [
      el("span", { text: "① 브로드캐스트 선별" }),
      el("span", { class: "orch-meta",
        text: `${bc.results.length}명 검토 · ${relevant.length}명 선별` }),
      bc.model_used ? badge("모델 사용", "warn") : badge("규칙만 사용", "muted"),
    ]));

    for (const r of bc.results) {
      const row = el("div", { class: `orch-row${r.relevant ? "" : " is-dimmed"}` });
      row.appendChild(el("span", {
        class: `orch-dot ${r.relevant ? "orch-dot-ok" : "orch-dot-dim"}`,
      }));
      row.appendChild(el("span", { class: "orch-row-name", text: r.display_name }));
      row.appendChild(badge(r.relevant ? "선별" : "제외", r.relevant ? "ok" : "bad"));
      if (r.reason) {
        row.appendChild(el("span", { class: "orch-reason", text: r.reason }));
      }
      if (r.relevant && r.score) {
        row.appendChild(badge(`점수 ${r.score.toFixed(2)}`, "muted"));
      }
      phase.appendChild(row);
    }
    tree.appendChild(phase);
  }

  // ─ Agent 질의 단계 ────────────────────────────────────────────────
  const consulted = result.consulted || [];
  const skipped = result.skipped || [];

  if (consulted.length > 0 || skipped.length > 0) {
    const phase = el("div", { class: "orch-phase" });
    phase.appendChild(el("div", { class: "orch-section-head" }, [
      el("span", { text: "② Agent 질의" }),
      el("span", { class: "orch-meta",
        text: `${consulted.length}명 답변` + (skipped.length ? ` · ${skipped.length}명 생략` : "") }),
    ]));

    for (const id of consulted) {
      const a = answersById[id];
      const agent = state.agentsById[id];
      const row = el("div", { class: "orch-row" });

      row.appendChild(el("span", { class: "orch-dot orch-dot-ok" }));
      row.appendChild(el("span", {
        class: "orch-row-name",
        text: agent ? agent.display_name : id,
      }));

      if (a) {
        row.appendChild(tierBadge(a.tier));
        row.appendChild(badge(`신뢰도 ${Number(a.confidence).toFixed(2)}`, "muted"));
        if (!a.used_external_agent) row.appendChild(badge("경계 안", "ok"));
        if (a.trace_id) {
          row.appendChild(el("button", {
            class: "gk-strip-btn orch-trace-btn",
            attrs: { type: "button" },
            on: { click: () => openTraceModal(a.trace_id) },
          }, [el("span", { text: "경과 ▸" })]));
        }
      }
      phase.appendChild(row);
    }

    for (const id of skipped) {
      const agent = state.agentsById[id];
      const row = el("div", { class: "orch-row is-dimmed" });
      row.appendChild(el("span", { class: "orch-dot orch-dot-dim" }));
      row.appendChild(el("span", {
        class: "orch-row-name",
        text: agent ? agent.display_name : id,
      }));
      row.appendChild(badge("상한 초과 — 생략", "bad"));
      phase.appendChild(row);
    }

    tree.appendChild(phase);
  }

  // 아무것도 없으면 안내 텍스트
  if (!result.broadcast && consulted.length === 0) {
    tree.appendChild(el("p", { class: "orch-empty", text: "과정 정보가 없습니다." }));
  }

  details.appendChild(tree);
  return details;
}

/* 브로드캐스트 결과 요약 줄 — 몇 명이 받았고 누가 답했는지. */
function consultHeader(result) {
  const box = el("div", { class: "consult-head" });
  const bc = result.broadcast;
  const consulted = result.consulted || [];

  const nameOf = (id) => (state.agentsById[id] ? state.agentsById[id].display_name : id);

  if (bc) {
    const relevant = bc.results.filter((r) => r.relevant);
    box.appendChild(para(
      `**${bc.results.length}명**의 Agent 에게 질문을 전달했고, ` +
      `**${relevant.length}명**이 답할 수 있다고 판단했습니다.`,
      "consult-line",
    ));
  }

  if (consulted.length > 0) {
    const chips = el("div", { class: "consult-chips" });
    for (const id of consulted) {
      const rel = state.relevance[id];
      chips.appendChild(el("button", {
        class: "consult-chip",
        attrs: { type: "button", title: rel ? rel.reason : "", "data-testid": `consult-chip-${id}` },
        on: { click: () => openThread(id) },
      }, [
        el("span", { class: "consult-chip-name", text: nameOf(id) }),
        el("span", { class: "consult-chip-go", text: "대화 →" }),
      ]));
    }
    box.appendChild(chips);
  }

  if (result.skipped && result.skipped.length > 0) {
    box.appendChild(para(
      `이번엔 묻지 않았지만 후보였던 분: ${result.skipped.map(nameOf).join(", ")}. ` +
      "이름을 누르면 직접 물어볼 수 있어요.",
      "consult-note",
    ));
  }

  const meta = el("div", { class: "consult-meta" });
  if (bc && !bc.model_used) meta.appendChild(badge("규칙으로만 선별", "warn"));
  if (result.elapsed_seconds) {
    meta.appendChild(badge(`${result.elapsed_seconds.toFixed(1)}초`, "muted"));
  }
  box.appendChild(meta);
  return box;
}

/* 사람 한 명의 원답변 카드. 이름 · 등급 · 신뢰도 · 인용 · 처리 경과. */
function answerCard(answer) {
  const card = el("div", { class: "answer-card" });

  const head = el("div", { class: "answer-card-head" }, [
    el("span", { class: "answer-card-name", text: answer.agent_label || "Agent" }),
    tierBadge(answer.tier),
    badge(`신뢰도 ${Number(answer.confidence).toFixed(2)}`, "muted"),
  ]);
  if (!answer.used_external_agent) {
    head.appendChild(badge("사내망 밖으로 유출된 내용 없음", "ok"));
  }
  head.appendChild(el("button", {
    class: "answer-card-open",
    attrs: { type: "button" },
    on: { click: () => openThread(answer.entity_id) },
  }, [el("span", { text: "이 Agent 와 대화 →" })]));
  card.appendChild(head);

  card.appendChild(markdownBlock(answer.text, "md answer-card-body"));
  card.appendChild(citationsRow(answer));
  card.appendChild(buildTraceBlock({
    tier: answer.tier,
    traceId: answer.trace_id,
    dispositionKey: answer.used_external_agent ? "auto" : "blocked",
  }));
  return card;
}

function citationsRow(answer) {
  const box = el("div");
  if (answer.citations && answer.citations.length > 0) {
    const cites = el("div", { class: "answer-citations" });
    cites.appendChild(el("span", { text: "인용: " }));
    for (const c of answer.citations) {
      cites.appendChild(badge(c.display_title || c.ref, "muted"));
    }
    box.appendChild(cites);
  }
  if (answer.unresolved_refs && answer.unresolved_refs.length > 0) {
    box.appendChild(el("p", {
      class: "answer-warn",
      text: `복원하지 못한 기호 ${answer.unresolved_refs.length}개가 그대로 남아 있어요.`,
    }));
  }
  return box;
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

  // 나도 목록에 있다 — 기본 화면이 나와 내 Agent 의 대화이기 때문이다.
  // 다만 브로드캐스트 대상은 아니므로 판정 표시가 붙지 않는다.
  const isMe = state.me && a.entity_id === state.me.entity_id;

  const rel = state.relevance[entityId];
  const hasVerdict = !isMe && state.broadcast !== null && rel !== undefined;
  const dimmed = hasVerdict && !rel.relevant;
  const active = isMe ? state.activeThread === MY_AGENT : state.activeThread === entityId;
  const blocked = !isMe && a.daily_limit_reached;

  const classes = ["org-card"];
  if (hasVerdict && rel.relevant) classes.push("is-relevant");
  if (dimmed) classes.push("is-dimmed");
  if (active) classes.push("is-active");
  if (blocked) classes.push("is-blocked");
  if (isMe) classes.push("is-me");
  if (!isMe && state.selectedTargets.has(entityId)) classes.push("is-selected");
  if (!isMe && state.communicating.has(entityId)) classes.push("communicating");

  const info = el("div", { class: "org-info" }, [
    el("div", { class: "org-name-row" }, [
      el("span", { class: "org-name", text: a.display_name }),
      isMe ? el("span", { class: "rank-badge badge-me", text: "나" }) : null,
      a.rank_badge ? el("span", { class: "rank-badge", text: a.rank_badge }) : null,
    ]),
    el("div", { class: "org-role", text: isMe ? "내 Agent 와 대화합니다" : (a.org_title || a.expertise || "") }),
  ]);

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
    on: { click: (e) => {
      if (blocked) return;
      if (isMe) { openThread(MY_AGENT); return; }
      if (e.ctrlKey || e.metaKey || state.selectedTargets.size > 0) {
        toggleSelect(entityId);
      } else {
        openThread(entityId);
      }
    } },
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
  $("org-subtitle").textContent = "모든 Agent 에게 질문을 보내는 중…";

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
    ? `${relevant}명이 답할 수 있을 것 같아요`
    : "겹치는 담당자를 찾지 못했어요. 전체 보기에서 직접 고를 수 있습니다";
  $("org-reset").hidden = false;
  renderOrgTree();
}

function resetBroadcast() {
  state.broadcast = null;
  state.relevance = {};
  $("org-subtitle").textContent = "";
  $("org-reset").hidden = true;
  renderOrgTree();
}

// ══════════════════════════════════════════════════════════════════
// 직접 선택 질의 (U3) — Ctrl+클릭으로 조직도에서 대상을 고른다
// ══════════════════════════════════════════════════════════════════

function toggleSelect(entityId) {
  if (state.selectedTargets.has(entityId)) {
    state.selectedTargets.delete(entityId);
  } else {
    state.selectedTargets.add(entityId);
  }
  renderOrgTree();
  updateSelectionHint();
}

function clearSelection() {
  if (state.selectedTargets.size === 0) return;
  state.selectedTargets.clear();
  renderOrgTree();
  updateSelectionHint();
}

function updateSelectionHint() {
  if (state.activeThread !== MY_AGENT) return;
  const hint = $("input-hint");
  if (!hint) return;
  if (state.selectedTargets.size > 0) {
    const names = [...state.selectedTargets]
      .map((id) => (state.agentsById[id] ? state.agentsById[id].display_name : id))
      .join(", ");
    hint.textContent = `선택 ${state.selectedTargets.size}명에게만 질의합니다: ${names} · ESC 또는 Ctrl+클릭으로 해제`;
  } else if (state.forceBroadcast) {
    hint.textContent = "Always broadcasting";
  } else {
    hint.textContent = "Enter 전송 · Shift+Enter 줄바꿈";
  }
}

// ══════════════════════════════════════════════════════════════════
// SSE — 실시간 소통 표시 (U1)
// ══════════════════════════════════════════════════════════════════

function connectSSE() {
  const es = new EventSource("/api/hub/events");
  es.onmessage = (e) => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    handleSseEvent(data);
  };
}

function handleSseEvent(data) {
  const type = data.type;
  if (type === "broadcast_start") {
    (data.agents || []).forEach((id) => setCommunicating(id, true));
  } else if (type === "agent_responded") {
    setCommunicating(data.entity_id, false);
  } else if (type === "broadcast_end") {
    (data.agents || []).forEach((id) => setCommunicating(id, false));
    state.communicating.clear();  // 전원 종료 시 set 초기화 (남은 것이 없게)
  }
}

function setCommunicating(entityId, active) {
  // state 를 먼저 갱신 → renderOrgTree 가 카드를 다시 그려도 클래스가 유지된다.
  // 이전처럼 DOM 을 직접 건드리면 렌더링이 State 를 덮어써 일부만 적용된다.
  if (active) {
    state.communicating.add(entityId);
  } else {
    state.communicating.delete(entityId);
  }
  // 현재 DOM 에 이미 있는 카드도 즉시 반영한다 (renderOrgTree 를 안 불러도 됨).
  document.querySelectorAll("[data-entity]").forEach((card) => {
    if (card.dataset.entity === entityId) {
      card.classList.toggle("communicating", active);
    }
  });
}

/* 내 Agent 에게 질문을 맡긴다.
 *
 * 서버가 브로드캐스트 → 사람별 prepare/send → 정리까지 한 번에 한다.
 * 화면이 그 사이를 잇지 않는 이유: 사람마다 두 왕복이 필요한데 그걸 브라우저가
 * 이으면 중간에 창을 닫았을 때 봉투가 붕 뜬다. 잇는 일은 서버가 한다. */
async function doConsult(question) {
  const targets = state.selectedTargets.size > 0 ? [...state.selectedTargets] : null;
  const hintSuffix = targets
    ? `→ 선택 ${targets.length}명에게 직접 질의`
    : "→ 내 Agent";
  pushMessage(MY_AGENT, { kind: "user", text: question, hint: hintSuffix });

  const loading = addLoadingMessage("내 Agent");

  // 대상을 지목하지 않은 경우 = 전원 방송. 응답을 기다리는 **동안** 파동을
  // 켜 둔다 (응답이 온 뒤에 켜면 이미 늦어 0ms 만에 사라진다). 특정 인원을
  // 지목했으면 방송이 아니므로 파동을 켜지 않는다.
  if (!targets) startWave();

  try {
    const reqBody = { question, asker: state.me.entity_id };
    if (targets) reqBody.targets = targets;
    if (state.forceBroadcast) reqBody.force_broadcast = true;
    const result = await api("/api/ask/consult", { method: "POST", body: reqBody });

    stopWave();
    // 실제로 방송해 후보가 남은 경우에만 조직도에 판정 강조를 입힌다.
    // 혼자 답했으면(broadcast 없음) 강조하지 않는다.
    if (result.broadcast) applyBroadcast(result.broadcast);

    removeMessage(loading);
    pushMessage(MY_AGENT, { kind: "digest", result });
    clearSelection();
  } catch (err) {
    removeMessage(loading);
    stopWave();
    pushMessage(MY_AGENT, { kind: "system", text: err.message });
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
      body: { question, asker: state.me.entity_id, targets: [entityId] },
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
        approved_by: state.me.entity_id,
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
  const content = el("div");

  content.appendChild(markdownBlock(answer.text || "답변을 준비하고 있습니다.", "md"));

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

  const node = bubble("assistant", content, {
    label: message.agentLabel || "Agent",
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
    ? message.tier === "secret"
      ? { text: "기밀 — 사내 AI 가 직접 답했습니다", kind: "warn" }
      : { text: "경계를 넘지 않았습니다", kind: "bad" }
    : message.tier === "secret"
      ? { text: "구조만 추출해 내보냈습니다", kind: "warn" }
      : { text: "보안성 검토를 통과해 Agent에게 전달하였습니다", kind: "ok" };

  strip.appendChild(el("span", { class: "gk-strip-label", text: "게이트키퍼" }));
  if (message.tier && message.tier !== "secret") {
    strip.appendChild(badge(status.text, status.kind));
    strip.appendChild(tierBadge(message.tier));
  }

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
    trace.crossed_boundary ? "Agent에게 전달 됨" : "Agent에게 전달되지 않음",
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
      tabs.appendChild(el("span", { class: "trace-boundary", text: "사내망 경계" }));
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

  // classify 단계 특수 처리: summary 패널 각 행에 해당 문서의 steps 토글을 포함한다.
  const panels = stage.panels || [];
  if (stage.stage_id === "classify") {
    const summaryPanel = panels.find((p) => p.panel_id === "classify-summary");
    const stepPanels = panels.filter((p) => p.panel_id && p.panel_id.startsWith("classify-steps-"));
    const questionSteps = panels.find((p) => p.panel_id === "classify-question-steps");
    const rest = panels.filter((p) =>
      p !== summaryPanel && !stepPanels.includes(p) && p !== questionSteps);

    if (summaryPanel) {
      box.appendChild(renderClassifySummaryPanel(summaryPanel, questionSteps, stepPanels));
    }
    for (const panel of rest) box.appendChild(renderPanel(panel));
    return box;
  }

  for (const panel of panels) box.appendChild(renderPanel(panel));
  return box;
}

/* 1단계 등급 판정 "한눈에" — 각 행에 해당 문서의 판정 과정 표를 토글로 포함한다. */
function renderClassifySummaryPanel(summaryPanel, questionStepsPanel, stepPanels) {
  const box = el("div", { class: `trace-panel kind-${summaryPanel.kind}` });
  box.appendChild(el("div", { class: "trace-panel-label", text: summaryPanel.label }));
  if (summaryPanel.caption) box.appendChild(para(summaryPanel.caption, "trace-panel-caption"));

  const wrap = el("div", { class: "trace-table-wrap" });
  const table = el("table", { class: "trace-table classify-summary-table" });
  if (summaryPanel.columns && summaryPanel.columns.length) {
    table.appendChild(el("thead", {}, [
      el("tr", {}, summaryPanel.columns.map((c) => el("th", { text: c }))),
    ]));
  }

  const tbody = el("tbody");
  const rows = summaryPanel.rows || [];

  // 첫 행은 "질문 문장" — 질문 판정 과정과 짝짓는다.
  rows.forEach((row, i) => {
    const tr = el("tr", { class: `row-${row.status}` },
      (row.cells || []).map((cell) => el("td", { text: cell })));
    tbody.appendChild(tr);

    // 질문 문장 행 (i===0) → questionStepsPanel, 나머지 → stepPanels[i-1]
    const stepsPanel = i === 0 ? questionStepsPanel : stepPanels[i - 1];
    if (stepsPanel) {
      const subDet = el("details", { class: "classify-steps-toggle" });
      const subSum = el("summary", {}, [
        el("span", { class: "gk-arrow", text: "▶" }),
        el("span", { text: "판정 과정 보기" }),
      ]);
      subDet.appendChild(subSum);
      subDet.appendChild(renderPanel(stepsPanel));
      const subTr = el("tr", { class: "classify-steps-row" });
      const subTd = el("td", { attrs: { colspan: String((summaryPanel.columns || []).length) } });
      subTd.appendChild(subDet);
      subTr.appendChild(subTd);
      tbody.appendChild(subTr);
    }
  });

  table.appendChild(tbody);
  wrap.appendChild(table);
  box.appendChild(wrap);
  return box;
}

function renderPanel(panel) {
  const box = el("div", { class: `trace-panel kind-${panel.kind}` });
  box.appendChild(el("div", { class: "trace-panel-label", text: panel.label }));
  if (panel.caption) box.appendChild(para(panel.caption, "trace-panel-caption"));

  switch (panel.kind) {
    case "json":
      box.appendChild(jsonBlock(panel.json_text, panel.highlight || []));
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
      text: `값을 감춘 항목이 ${panel.redacted_count}건 있어요. 값은 보여드리지 않고 건수만 표시합니다.`,
    }));
  }
  return box;
}

/* JSON 을 토큰별로 색칠한다. **자르지 않는다** (BR-U-01) —
 * 전문을 보여준다고 하면서 일부만 보이면 그건 거짓말이다.
 *
 * `highlight` 는 치환으로 들어간 **기호**들이다. 붉게 칠해서 "원래 값이 있던
 * 자리" 가 한눈에 보이게 한다 — 무엇이 나갔는가만큼 **무엇이 안 나갔는가**가
 * 이 화면의 요점이기 때문이다. */
function jsonBlock(text, highlight = []) {
  const pre = el("pre", { class: "payload trace-payload", attrs: { tabindex: "0" } });
  const source = String(text || "");
  const re = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(\b-?\d+(?:\.\d+)?\b)|(\btrue\b|\bfalse\b|\bnull\b)/g;
  let last = 0;
  let m;
  while ((m = re.exec(source)) !== null) {
    if (m.index > last) pre.appendChild(document.createTextNode(source.slice(last, m.index)));
    const cls = m[1] ? "tok-key" : m[2] ? "tok-str" : m[3] ? "tok-num" : "tok-bool";
    appendWithSymbols(pre, m[0], cls, highlight);
    last = m.index + m[0].length;
  }
  if (last < source.length) pre.appendChild(document.createTextNode(source.slice(last)));
  return pre;
}

/* 토큰 하나를 넣되, 그 안에 치환 기호가 있으면 그 부분만 떼어 붉게 칠한다.
 * 긴 기호부터 찾는다 — `<SYS_1>` 과 `<SYS_11>` 이 함께 있을 때 짧은 쪽을 먼저
 * 잡으면 잘못 쪼개진다 (재수화의 BR-P-04 와 같은 이유). */
function appendWithSymbols(target, token, cls, highlight) {
  const symbols = [...highlight].sort((a, b) => b.length - a.length);
  let rest = token;
  let guard = 0;

  while (rest && guard < 200) {
    guard += 1;
    let bestAt = -1;
    let bestSymbol = "";
    for (const symbol of symbols) {
      if (!symbol) continue;
      const at = rest.indexOf(symbol);
      if (at !== -1 && (bestAt === -1 || at < bestAt)) { bestAt = at; bestSymbol = symbol; }
    }
    if (bestAt === -1) break;
    if (bestAt > 0) target.appendChild(el("span", { class: cls, text: rest.slice(0, bestAt) }));
    target.appendChild(el("span", { class: "tok-sub", text: bestSymbol }));
    rest = rest.slice(bestAt + bestSymbol.length);
  }
  if (rest) target.appendChild(el("span", { class: cls, text: rest }));
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

/* 비식별 기호 답변 ↔ 식별화된 답변. 왼쪽은 경계 밖 모델이 만든 그대로이고
 * 오른쪽은 신뢰 구역 안에서 기호를 실제 이름으로 되돌린 것이다. */
function compareBlock(panel) {
  // 기호(<SYM_N> 패턴)를 bold로 강조한다. 양쪽 모두 적용.
  function preWithBold(text) {
    const pre = el("pre", { class: "trace-compare-text" });
    const re = /<[A-Z][A-Z0-9_]*_\d+>/g;
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) pre.appendChild(document.createTextNode(text.slice(last, m.index)));
      pre.appendChild(el("strong", { class: "compare-symbol", text: m[0] }));
      last = m.index + m[0].length;
    }
    if (last < text.length) pre.appendChild(document.createTextNode(text.slice(last)));
    return pre;
  }

  return el("div", { class: "trace-compare" }, [
    el("div", { class: "trace-compare-side before" }, [
      el("div", { class: "trace-compare-label", text: panel.before_label || "변환 전" }),
      preWithBold(panel.before_text || ""),
    ]),
    el("div", { class: "trace-compare-arrow", text: "→" }),
    el("div", { class: "trace-compare-side after" }, [
      el("div", { class: "trace-compare-label", text: panel.after_label || "변환 후" }),
      preWithBold(panel.after_text || ""),
    ]),
  ]);
}

// ══════════════════════════════════════════════════════════════════
// 헤더 상태 · 사용자 전환 · 입력
// ══════════════════════════════════════════════════════════════════

/* 헤더에서 상태 배지를 뺐다.
 *
 * "LIVE" 나 "Agent: direct" 는 **개발자용 진단**이지 시연에서 읽을 정보가
 * 아니다. 목업 모드 표시는 남길 이유가 있었지만(심사자를 속이지 않는다),
 * 그 사실은 답변마다 처리 경과의 ⑤ 경계 통과에서 경로·모델·엔드포인트로
 * 더 정확하게 드러난다 — 배지 하나보다 그쪽이 검증 가능하다. */

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

  if (state.activeThread === MY_AGENT) {
    state.busy = true;
    refreshSendButton();
    try {
      await doConsult(question);
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
    : level === "personal" && state.me ? state.me.entity_id
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
    $("protocol-form").hidden = true;
    await loadProtocols();
  } catch (err) {
    alert(`삭제 실패: ${err.message}`);
  }
}

function wireProtocol() {
  // 요소가 없을 수 있다 (UI 개편으로 버튼이 빠질 수 있음).
  // 하나가 없어서 wire() 전체가 죽으면 조직도까지 안 뜬다.
  const on = (id, evt, fn) => {
    const node = $(id);
    if (node) node.addEventListener(evt, fn);
  };

  on("protocol-btn", "click", openProtocolModal);
  on("protocol-modal-close", "click", closeProtocolModal);
  on("protocol-modal", "cancel", closeProtocolModal);
  on("protocol-form", "submit", saveProto);
  on("proto-delete-btn", "click", deleteProto);

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

  // 요소가 없어도 나머지 배선이 계속되게 한다.
  const on = (id, evt, fn) => {
    const node = $(id);
    if (node) node.addEventListener(evt, fn);
  };

  const input = $("message-input");
  const sendBtn = $("send-btn");

  if (input) {
    input.addEventListener("input", () => {
      autoResize(input);
      refreshSendButton();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (sendBtn && !sendBtn.disabled) onSubmit();
      }
    });
  }

  on("send-btn", "click", onSubmit);

  // Broadcasting 토글 배선
  const broadcastToggle = $("broadcast-toggle");
  if (broadcastToggle) {
    broadcastToggle.addEventListener("change", () => {
      state.forceBroadcast = broadcastToggle.checked;
      updateSelectionHint();
    });
  }
  on("trace-modal-close", "click", () => {
    const m = $("trace-modal");
    if (m) m.close();
  });
  on("thread-back", "click", () => openThread(MY_AGENT));
  on("org-reset", "click", resetBroadcast);

  on("org-refresh-btn", "click", async () => {
    const btn = $("org-refresh-btn");
    btn.disabled = true;
    btn.style.opacity = "0.5";
    try {
      state.agents = await api("/api/agents/refresh", { method: "POST" });
      state.agentsById = Object.fromEntries(state.agents.map((a) => [a.entity_id, a]));
      // 조직도 트리도 다시 읽는다 — 새 사람이 미배치로 들어올 수 있다
      try {
        state.org = await api("/api/org");
      } catch { state.org = null; }
      renderOrgTree();
    } catch (err) {
      console.error("조직도 새로고침 실패:", err);
    } finally {
      btn.disabled = false;
      btn.style.opacity = "";
    }
  });

  on("trace-modal", "cancel", () => {
    const m = $("trace-modal");
    if (m) m.close();
  });
  on("restart-btn", "click", restartDemo);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") clearSelection();
  });
}

// ══════════════════════════════════════════════════════════════════
// 부팅
// ══════════════════════════════════════════════════════════════════

/* 데모를 처음 상태로. 대화·판정·트레이스 캐시를 전부 버린다.
 *
 * 새로고침으로도 되지만 버튼을 두는 이유: 시연 중에 주소창을 건드리는 것보다
 * 안전하고, "무엇이 초기화되는지" 를 코드로 한 곳에 적어 둘 수 있다. */
function restartDemo() {
  state.threads = {};
  state.traces = {};
  resetBroadcast();
  openThread(MY_AGENT);
}

async function boot() {
  wire();
  connectSSE();

  // 내가 누구인지 서버에 묻는다. 짐작하면 `agents.yaml` 순서가 바뀌는 날 틀린다.
  try {
    state.me = await api("/api/me");
  } catch { state.me = null; }

  try {
    state.agents = await api("/api/agents");
  } catch { state.agents = []; }
  state.agentsById = Object.fromEntries(state.agents.map((a) => [a.entity_id, a]));

  // 조직도는 표시용이다. 없어도 화면은 평평한 목록으로 떨어진다.
  try {
    state.org = await api("/api/org");
  } catch { state.org = null; }

  openThread(MY_AGENT);
  renderOrgTree();
  refreshSendButton();

  if (!state.me) {
    pushMessage(MY_AGENT, {
      kind: "system",
      text: "내 Agent 를 확인하지 못했습니다. 서버가 떠 있는지 확인해 주세요.",
    });
  }
}

document.addEventListener("DOMContentLoaded", boot);

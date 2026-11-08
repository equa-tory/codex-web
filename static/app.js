"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const state = {
  archived: false,
  activeOnly: false,
  csrf: "",
  installation: "",
  thread: null,
  turns: [],
  pending: [],
  historyCursor: null,
  sessionsCursor: null,
  mode: "default",
  model: null,
  permissions: null,
  effort: null,
  options: null,
  connected: false,
  sending: false,
  restoring: false,
  socket: null,
  loading: false,
  eventBuffer: [],
  epoch: 0,
  refreshTimer: null,
};
const commands = {
  new: "New session",
  resume: "Browse sessions",
  model: "Choose model",
  plan: "Toggle Plan / Code",
  permissions: "Choose permissions",
  status: "Session status",
  compact: "Compact context",
  help: "Help & Home Screen",
};
function storageKey(name) {
  return `codex_web_${state.installation}_${name}`;
}
function readLocal(name, fallback = null) {
  try {
    return JSON.parse(localStorage.getItem(storageKey(name))) ?? fallback;
  } catch {
    return fallback;
  }
}
function writeLocal(name, value) {
  try {
    localStorage.setItem(storageKey(name), JSON.stringify(value));
  } catch {}
}
function notice(text = "") {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}
function fail(error) {
  notice(error.message || String(error));
}
async function api(path, body, method) {
  const response = await fetch("/api" + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error("Server unavailable. Refresh before retrying.");
  }
  if (!response.ok) {
    if (response.status === 401) {
      $("login").hidden = false;
      $("app").hidden = true;
    }
    const error = new Error(
      typeof result.detail === "string"
        ? result.detail
        : JSON.stringify(result.detail),
    );
    error.status = response.status;
    throw error;
  }
  return result;
}
function on(id, event, callback) {
  $(id).addEventListener(event, async (e) => {
    try {
      await callback(e);
    } catch (error) {
      fail(error);
    }
  });
}
function button(text, callback, cls = "") {
  const b = el("button", cls, text);
  b.type = "button";
  b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      await callback();
    } catch (error) {
      fail(error);
    } finally {
      b.disabled = false;
    }
  });
  return b;
}
function md(text) {
  const node = el("div", "markdown");
  node.innerHTML = DOMPurify.sanitize(
    marked.parse(text || "", { breaks: true }),
    {
      FORBID_TAGS: ["img", "style", "input", "form", "iframe"],
      FORBID_ATTR: ["style"],
    },
  );
  for (const a of node.querySelectorAll("a")) {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  }
  return node;
}
function dialog(title) {
  $("dialogTitle").textContent = title;
  $("dialogBody").replaceChildren();
  if (!$("dialog").open) $("dialog").showModal();
  return $("dialogBody");
}
function isActive() {
  return (
    state.thread?.status?.type === "active" ||
    state.turns.some((t) => t.status === "inProgress")
  );
}
function statusText(thread) {
  if (state.pending.some((p) => p.params?.threadId === thread.id))
    return "Waiting for you";
  const s = thread.status;
  return s?.type === "active"
    ? s.activeFlags?.length
      ? "Waiting for you"
      : "Running"
    : s?.type === "systemError"
      ? "Error"
      : "Ready";
}
function controls() {
  const active = isActive();
  $("send").disabled =
    state.sending ||
    state.restoring ||
    state.loading ||
    active ||
    state.archived ||
    !state.connected ||
    !$("message").value.trim();
  $("stop").hidden = !active;
  $("unarchive").hidden = !state.archived;
  $("archivedNote").hidden = !state.archived;
  $("send").hidden = state.archived;
  $("message").disabled = state.archived;
  $("stop").disabled = !state.connected;
  $("returnMessage").disabled =
    active ||
    state.loading ||
    state.archived ||
    state.thread?.historyMode !== "paginated";
  $("modeLabel").textContent = state.mode === "plan" ? "Plan" : "Code";
  $("codeMode").classList.toggle("selected", state.mode === "default");
  $("planMode").classList.toggle("selected", state.mode === "plan");
  $("modelLabel").textContent =
    (state.model || state.thread?.model || "Default model") +
    (state.permissions ? " · " + state.permissions.replace(":", "") : "");
  if (state.thread)
    $("status").textContent = state.archived
      ? "Archived"
      : statusText(state.thread);
}
function connection(connected, error) {
  state.connected = connected;
  $("browserConnection").hidden = connected;
  $("browserConnection").textContent = error || "Reconnecting to Codex daemon…";
  $("connection").hidden = connected;
  $("connection").textContent =
    error || "Reconnecting… Your turn continues on the computer.";
  controls();
}
let listRequest = 0;
async function loadSessions(more = false) {
  const request = ++listRequest;
  const q = $("search").value;
  const result = await api(
    `/threads?q=${encodeURIComponent(q)}&archived=${$("archived").checked}&active=${state.activeOnly}${more && state.sessionsCursor ? "&cursor=" + encodeURIComponent(state.sessionsCursor) : ""}`,
  );
  if (request !== listRequest) return;
  if (!more) $("sessions").replaceChildren();
  state.sessionsCursor = result.nextCursor;
  $("moreSessions").hidden = !result.nextCursor;
  for (const thread of result.data) {
    const b = el("div", "session");
    b.tabIndex = 0;
    b.setAttribute("role", "button");
    b.setAttribute("aria-label", `Open ${thread.name || thread.preview?.split("\n")[0] || "session"}`);
    b.addEventListener("click", () => openThread(thread.id).catch(fail));
    b.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openThread(thread.id).catch(fail); } });
    b.classList.toggle("active", thread.id === state.thread?.id);
    b.append(
      el(
        "h3",
        "",
        thread.name || thread.preview?.split("\n")[0] || "Untitled session",
      ),
      el("div", "path", thread.cwd),
    );
    const meta = el("div", "session-meta");
    meta.append(
      el(
        "time",
        "",
        new Date(thread.updatedAt * 1000).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }),
      ),
      el("span", "session-state", statusText(thread)),
    );
    b.append(meta);
    if (thread.status?.type === "active") {
      const stop = button("■ Stop", async () => {
        await api(`/threads/${thread.id}/stop`, {});
        await loadSessions();
      }, "session-stop danger");
      stop.addEventListener("click", (event) => event.stopPropagation());
      b.append(stop);
    }
    if (state.activeOnly) {
      const close = button("×", async () => {
        if (thread.status?.type === "active") return;
        await api(`/threads/${thread.id}/close`, {});
        await loadSessions();
      }, "session-close");
      close.title = "Close session (keeps its conversation)";
      close.setAttribute("aria-label", close.title);
      close.disabled = thread.status?.type === "active";
      close.addEventListener("click", (event) => event.stopPropagation());
      b.append(close);
    }
    $("sessions").append(b);
  }
  if (!more && !result.data.length)
    $("sessions").append(el("p", "muted", "No sessions found."));
}
function draftName() {
  return `draft_${state.thread?.id}`;
}
function saveDraft() {
  if (state.thread) writeLocal(draftName(), $("message").value);
}
function saveSettings() {
  if (state.thread)
    writeLocal(`settings_${state.thread.id}`, {
      mode: state.mode,
      model: state.model,
      permissions: state.permissions,
      effort: state.effort,
    });
  controls();
}
function ingest(snapshot, older = false) {
  state.thread = snapshot.thread;
  if (snapshot.archived !== undefined) state.archived = snapshot.archived;
  const incoming = [...snapshot.history.data].reverse();
  state.turns = older
    ? [
        ...incoming,
        ...state.turns.filter((t) => !incoming.some((x) => x.id === t.id)),
      ]
    : incoming;
  state.historyCursor = snapshot.history.nextCursor;
  state.pending = snapshot.pending || [];
  $("title").textContent =
    state.thread.name || state.thread.preview?.split("\n")[0] || "New session";
  $("cwd").textContent = state.thread.cwd;
  $("cwd").title = state.thread.cwd;
  $("older").hidden = !state.historyCursor;
  render();
  renderPrompts();
  controls();
}
async function openThread(id) {
  saveDraft();
  state.epoch++;
  const epoch = state.epoch;
  state.loading = true;
  state.thread = { id, status: { type: "idle" } };
  state.turns = [];
  state.pending = [];
  state.eventBuffer = [];
  state.options = null;
  state.archived = false;
  $("message").value = "";
  $("messages").replaceChildren();
  $("pending").replaceChildren();
  $("app").classList.add("chat-open");
  $("chat").hidden = false;
  $("welcome").hidden = true;
  notice();
  controls();
  writeLocal("thread", id);
  state.socket?.readyState === WebSocket.OPEN &&
    state.socket.send(JSON.stringify({ type: "subscribe", threadId: id }));
  try {
    const snapshot = await api(`/threads/${id}/open`, {});
    if (epoch !== state.epoch) return;
    const saved = readLocal(`settings_${id}`, {});
    state.mode = saved.mode || snapshot.collaborationMode?.mode || "default";
    state.model = saved.model || snapshot.model || snapshot.thread.model;
    state.permissions = saved.permissions || snapshot.permissions?.id || null;
    state.effort = saved.effort || null;
    $("message").value = readLocal(draftName(), "");
    ingest(snapshot);
    scrollBottom();
    await loadOptions();
  } finally {
    if (epoch === state.epoch) {
      state.loading = false;
      const buffered = state.eventBuffer;
      state.eventBuffer = [];
      buffered.forEach(handleEvent);
      controls();
    }
  }
}
async function refresh(resume = false) {
  if (!state.thread || state.loading || state.restoring) return;
  const id = state.thread.id,
    epoch = state.epoch;
  state.loading = true;
  state.eventBuffer = [];
  try {
    const result = resume
      ? await api(`/threads/${id}/open`, {})
      : await api(`/threads/${id}/history`);
    if (epoch === state.epoch) ingest(result);
  } finally {
    if (epoch === state.epoch) {
      state.loading = false;
      const buffered = state.eventBuffer;
      state.eventBuffer = [];
      buffered.forEach(handleEvent);
      controls();
    }
  }
}
function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => refresh().catch(fail), 250);
}
function scrollBottom() {
  requestAnimationFrame(() => {
    $("conversation").scrollTop = $("conversation").scrollHeight;
  });
}
function render() {
  const container = $("conversation");
  const near =
    container.scrollHeight - container.scrollTop - container.clientHeight < 160;
  const opened = new Set(
    [...$("messages").querySelectorAll("details[open]")].map(
      (n) => n.dataset.item,
    ),
  );
  const fragment = document.createDocumentFragment();
  for (const turn of state.turns) {
    for (const item of turn.items || []) {
      let n;
      if (item.type === "userMessage") {
        n = el("article", "message user");
        n.append(
          el("div", "message-label", "YOU"),
          el(
            "div",
            "",
            item.content.map((c) => c.text || `[${c.type}]`).join("\n"),
          ),
        );
      } else if (item.type === "agentMessage" || item.type === "plan") {
        n = el("article", "message assistant");
        const label = el("div", "message-label");
        label.append(
          el("span", "dot"),
          el("span", "", item.type === "plan" ? "PLAN" : "CODEX"),
        );
        n.append(label, md(item.text));
      } else {
        n = el("details", "tool");
        n.dataset.item = item.id;
        n.open = opened.has(item.id);
        const title =
          item.type === "commandExecution"
            ? `Terminal · ${item.command || "command"}`
            : item.type === "mcpToolCall"
              ? `${item.server} · ${item.tool}`
              : item.type === "fileChange"
                ? `Files · ${(item.changes || []).map((c) => c.path).join(", ")}`
                : item.type === "reasoning"
                  ? "Thinking"
                  : item.type;
        n.append(
          el("summary", "", title + (item.status ? " · " + item.status : "")),
          el(
            "pre",
            "",
            item.aggregatedOutput ||
              (item.type === "reasoning"
                ? (item.summary || []).join("\n")
                : JSON.stringify(item, null, 2)),
          ),
        );
      }
      fragment.append(n);
    }
    if (turn.error)
      fragment.append(
        el("p", "turn-error", turn.error.message || JSON.stringify(turn.error)),
      );
    if (turn.status === "interrupted")
      fragment.append(el("p", "muted", "Turn stopped"));
  }
  $("messages").replaceChildren(fragment);
  $("empty").hidden = state.turns.length > 0;
  if (near) scrollBottom();
}
let renderTimer;
function scheduleRender() {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(() => {
    render();
    controls();
  }, 50);
}
function handleEvent(event) {
  const method = event.method,
    p = event.params || {};
  if (method === "web/disconnected") {
    connection(false, p.error);
    return;
  }
  if (method === "web/connected") {
    connection(true);
    if (state.thread) refresh(true).catch(fail);
    else loadSessions().catch(fail);
    return;
  }
  if (method === "web/resync") {
    scheduleRefresh();
    return;
  }
  if (state.loading) {
    state.eventBuffer.push(event);
    return;
  }
  if (p.threadId && p.threadId !== state.thread?.id) return;
  if (event.id !== undefined) {
    const key = event.key || String(event.id);
    if (!state.pending.some((x) => x.key === key))
      state.pending.push({ ...event, key });
    renderPrompts();
    controls();
    return;
  }
  if (method === "serverRequest/resolved") {
    state.pending = state.pending.filter((x) => x.id !== p.requestId);
    renderPrompts();
    controls();
    return;
  }
  if (method === "thread/status/changed" && state.thread) {
    state.thread.status = p.status;
    controls();
    return;
  }
  if (method === "turn/started") {
    if (!state.turns.some((t) => t.id === p.turn.id)) state.turns.push(p.turn);
    if (state.thread) state.thread.status = { type: "active", activeFlags: [] };
    scheduleRender();
    return;
  }
  if (method === "turn/completed") {
    const t = state.turns.find((t) => t.id === p.turn.id);
    if (t) Object.assign(t, p.turn);
    if (state.thread) state.thread.status = { type: "idle" };
    scheduleRender();
    scheduleRefresh();
    return;
  }
  if (method === "thread/reverted") {
    scheduleRefresh();
    return;
  }
  if (method === "error") {
    notice(p.error?.message || p.message || "Codex reported an error");
    return;
  }
  const turn = state.turns.find((t) => t.id === p.turnId);
  if (method === "item/started" || method === "item/completed") {
    if (turn) {
      const index = turn.items.findIndex((i) => i.id === p.item.id);
      if (index < 0) turn.items.push(p.item);
      else turn.items[index] = p.item;
      scheduleRender();
    } else scheduleRefresh();
    return;
  }
  if ((method.endsWith("/delta") || method.endsWith("Delta")) && turn) {
    let item = turn.items.find((i) => i.id === p.itemId);
    if (!item && method === "item/agentMessage/delta") {
      item = { id: p.itemId, type: "agentMessage", text: "" };
      turn.items.push(item);
    }
    if (item) {
      if (method === "item/agentMessage/delta" || method === "item/plan/delta")
        item.text = (item.text || "") + p.delta;
      else if (method === "item/commandExecution/outputDelta")
        item.aggregatedOutput = (item.aggregatedOutput || "") + p.delta;
      else if (method.startsWith("item/reasoning/")) {
        item.summary = item.summary || [];
        const i = p.summaryIndex || 0;
        item.summary[i] = (item.summary[i] || "") + p.delta;
      }
      scheduleRender();
    }
  }
}
function connect() {
  if (state.socket) state.socket.close();
  const ws = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
  );
  state.socket = ws;
  ws.onopen = () => {
    if (state.thread)
      ws.send(JSON.stringify({ type: "subscribe", threadId: state.thread.id }));
  };
  ws.onmessage = (e) => {
    try {
      handleEvent(JSON.parse(e.data));
    } catch (error) {
      fail(error);
    }
  };
  ws.onclose = () => {
    if (state.socket !== ws) return;
    connection(false);
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();
}
setInterval(() => {
  if (state.socket?.readyState === WebSocket.OPEN)
    state.socket.send(JSON.stringify({ type: "ping" }));
}, 20000);
async function loadOptions() {
  const id = state.thread?.id;
  const result = await api(
    "/options" +
      (state.thread?.cwd ? "?cwd=" + encodeURIComponent(state.thread.cwd) : ""),
  );
  if (id === state.thread?.id) state.options = result;
  return result;
}
function setMode(mode) {
  state.mode = mode;
  saveSettings();
  $("actionsMenu").hidden = true;
  $("actions").setAttribute("aria-expanded", "false");
}
function toggleMode() {
  setMode(state.mode === "plan" ? "default" : "plan");
}
async function chooseModel() {
  const opts = state.options || (await loadOptions());
  const body = dialog("Model for the next turn");
  const select = el("select");
  select.setAttribute("aria-label", "Model");
  for (const model of opts.models.filter((m) => !m.hidden)) {
    const option = el("option", "", model.displayName);
    option.value = model.model;
    select.append(option);
  }
  select.value = state.model || opts.models.find((m) => m.isDefault)?.model;
  const description = el("p", "muted");
  const efforts = el("select");
  efforts.setAttribute("aria-label", "Reasoning effort");
  function update() {
    const model = opts.models.find((m) => m.model === select.value);
    description.textContent = model?.description || "";
    efforts.replaceChildren();
    for (const entry of model?.supportedReasoningEfforts || []) {
      const option = el("option", "", entry.reasoningEffort);
      option.value = entry.reasoningEffort;
      efforts.append(option);
    }
    efforts.value = state.effort || model?.defaultReasoningEffort;
  }
  select.onchange = update;
  update();
  body.append(
    select,
    description,
    el("label", "", "Reasoning effort"),
    efforts,
    button(
      "Use model",
      () => {
        state.model = select.value;
        state.effort = efforts.value;
        saveSettings();
        $("dialog").close();
      },
      "primary",
    ),
  );
}
async function choosePermissions() {
  const opts = await loadOptions();
  const body = dialog("Permissions for the next turn");
  body.append(
    el(
      "p",
      "muted",
      "Choose a profile available from your Codex daemon. Full access lets Codex change files and run commands without a sandbox.",
    ),
  );
  for (const profile of opts.permissions) {
    const b = button(
      profile.id + (profile.description ? " · " + profile.description : ""),
      () => {
        state.permissions = profile.id;
        saveSettings();
        $("dialog").close();
      },
      "wide",
    );
    b.disabled = !profile.allowed;
    b.classList.toggle("selected", profile.id === state.permissions);
    body.append(b);
  }
}
async function newSession() {
  const opts = await loadOptions();
  const body = dialog("Start a new session");
  const form = el("form");
  const cwd = el("input");
  cwd.name = "cwd";
  cwd.required = true;
  cwd.value = state.thread?.cwd || "/home/equa";
  cwd.setAttribute("aria-label", "Working directory");
  const model = el("select");
  model.setAttribute("aria-label", "Model");
  for (const m of opts.models.filter((m) => !m.hidden)) {
    const option = el("option", "", m.displayName);
    option.value = m.model;
    model.append(option);
  }
  model.value = state.model || opts.models.find((m) => m.isDefault)?.model;
  const permissions = el("select");
  permissions.setAttribute("aria-label", "Permissions");
  const inherit = el("option", "", "Codex default");
  inherit.value = "";
  permissions.append(inherit);
  for (const p of opts.permissions) {
    const option = el("option", "", p.id);
    option.value = p.id;
    option.disabled = !p.allowed;
    permissions.append(option);
  }
  const submit = el("button", "primary", "Create session");
  submit.type = "submit";
  form.append(
    el("label", "", "Working directory"),
    cwd,
    el("label", "", "Model"),
    model,
    el("label", "", "Permissions"),
    permissions,
    submit,
  );
  form.onsubmit = async (e) => {
    e.preventDefault();
    submit.disabled = true;
    try {
      const result = await api("/threads", {
        cwd: cwd.value,
        model: model.value,
        permissions: permissions.value || null,
      });
      $("dialog").close();
      await openThread(result.thread.id);
      await loadSessions();
    } catch (error) {
      const p = el("p", "turn-error", error.message);
      form.append(p);
    } finally {
      submit.disabled = false;
    }
  };
  body.append(form);
}
async function returnToMessage() {
  if (isActive())
    throw new Error("Stop the active turn before returning to a message.");
  $("actionsMenu").hidden = true;
  const body = dialog("Return to a message");
  body.append(
    el(
      "p",
      "muted",
      "Select a prompt to edit. That turn and everything after it will be removed from chat history. Project files remain as they are.",
    ),
  );
  if (state.thread.historyMode !== "paginated") {
    body.append(
      el("p", "", "This older session does not support thread/revert."),
    );
    return;
  }
  function appendTurns(turns) {
    for (const turn of [...turns].reverse()) {
      const text = turn.items
        .filter((i) => i.type === "userMessage")
        .flatMap((i) => i.content)
        .filter((c) => c.type === "text")
        .map((c) => c.text)
        .join("\n");
      if (!text) continue;
      body.append(
        button(
          text,
          async () => {
            if (
              !confirm(
                "Remove this turn and all later chat history? Project files will stay as they are.",
              )
            )
              return;
            state.restoring = true;
            controls();
            try {
              const result = await api(`/threads/${state.thread.id}/restore`, {
                beforeTurnId: turn.id,
              });
              state.epoch++;
              ingest(result);
              $("message").value = result.text;
              saveDraft();
              notice("Returned to message. Edit it below and send when ready.");
              $("dialog").close();
              scrollBottom();
            } finally {
              state.restoring = false;
              controls();
            }
          },
          "wide",
        ),
      );
    }
  }
  appendTurns(state.turns);
  let cursor = state.historyCursor;
  if (cursor) {
    const more = button(
      "Load earlier prompts",
      async () => {
        const result = await api(
          `/threads/${state.thread.id}/history?cursor=${encodeURIComponent(cursor)}`,
        );
        more.remove();
        appendTurns(result.history.data.slice().reverse());
        cursor = result.history.nextCursor;
        if (cursor) body.append(more);
      },
      "wide",
    );
    body.append(more);
  }
}
function showHelp() {
  const body = dialog("Your Codex, on your phone");
  body.append(
    el(
      "p",
      "",
      "Turns keep running when you close this page. Reopening refreshes history and outstanding questions. Messages are never automatically resent.",
    ),
  );
  for (const [name, description] of Object.entries(commands))
    body.append(
      button(
        "/" + name + " — " + description,
        () => {
          $("dialog").close();
          return command(name);
        },
        "wide",
      ),
    );
  body.append(
    el("h3", "", "Add to Home Screen"),
    el(
      "p",
      "",
      "iPhone: open in Safari, tap Share, then Add to Home Screen. Android: use the browser menu and Add to Home screen. Installation features depend on browser support; full PWA installation generally needs HTTPS.",
    ),
    el(
      "p",
      "muted",
      "Text inputs use at least 16px. Pinch zoom is blocked where browsers permit it; iOS may override zoom restrictions. The layout follows the visual viewport as the keyboard opens.",
    ),
  );
}
async function command(name) {
  $("slashPicker").hidden = true;
  switch (name) {
    case "new":
      return newSession();
    case "resume":
      saveDraft();
      $("app").classList.remove("chat-open");
      return loadSessions();
    case "model":
      return chooseModel();
    case "permissions":
      return choosePermissions();
    case "plan":
      setMode(state.mode === "plan" ? "default" : "plan");
      return;
    case "status": {
      if (!state.thread) {
        notice("Choose a session first.");
        return;
      }
      await refresh();
      const body = dialog("Session status");
      body.append(
        el(
          "pre",
          "",
          JSON.stringify(
            {
              id: state.thread.id,
              cwd: state.thread.cwd,
              status: state.thread.status,
              model: state.model,
              mode: state.mode,
              permissions: state.permissions,
              historyMode: state.thread.historyMode,
              pending: state.pending.length,
              daemonConnected: state.connected,
            },
            null,
            2,
          ),
        ),
      );
      return;
    }
    case "compact":
      if (!state.thread) throw new Error("Choose a session first.");
      await api(`/threads/${state.thread.id}/compact`, {});
      notice("Compaction started.");
      scheduleRefresh();
      return;
    default:
      showHelp();
  }
}
function showSlash() {
  const term = $("message").value.startsWith("/")
    ? $("message").value.slice(1).toLowerCase()
    : "";
  $("slashPicker").replaceChildren();
  for (const [name, description] of Object.entries(commands)) {
    if (term && !name.startsWith(term)) continue;
    const b = button(
      "",
      () => {
        $("message").value = "";
        saveDraft();
        controls();
        return command(name);
      },
      "command-button",
    );
    b.append(el("strong", "", "/" + name), el("span", "", description));
    $("slashPicker").append(b);
  }
  $("slashPicker").hidden = false;
}
async function submit(e) {
  e.preventDefault();
  const text = $("message").value.trim();
  if (!text || state.sending) return;
  if (text.startsWith("/")) {
    $("message").value = "";
    saveDraft();
    controls();
    return command(text.slice(1).split(/\s/)[0]);
  }
  if (!state.thread) return;
  if (isActive())
    throw new Error("Wait for the current turn to finish or press Stop.");
  const id = state.thread.id;
  const pending = readLocal(`submission_${id}`);
  if (pending) {
    notice(
      "A previous submission has an uncertain result. Refresh history before sending again.",
    );
    await refresh();
    const body = dialog("Check the previous submission");
    body.append(
      el(
        "p",
        "",
        "This message may already have reached Codex. Check the conversation before sending another message.",
      ),
      el("pre", "", pending.text),
      button(
        "I checked history · allow a new message",
        () => {
          writeLocal(`submission_${id}`, null);
          $("dialog").close();
        },
        "primary",
      ),
    );
    return;
  }
  const submission = {
    submissionId: crypto.randomUUID
      ? crypto.randomUUID()
      : Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) =>
          v.toString(16).padStart(2, "0"),
        ).join(""),
    text,
    mode: state.mode,
    model: state.model,
    permissions: state.permissions,
    effort: state.effort,
  };
  writeLocal(`submission_${id}`, submission);
  state.sending = true;
  controls();
  notice();
  try {
    await api(`/threads/${id}/send`, submission);
    writeLocal(`submission_${id}`, null);
    if (state.thread?.id === id) {
      $("message").value = "";
      saveDraft();
      await refresh();
      scrollBottom();
    }
  } catch (error) {
    if (error.status && error.status < 500 && error.status !== 409)
      writeLocal(`submission_${id}`, null);
    throw error;
  } finally {
    state.sending = false;
    controls();
  }
}
function renderPrompts() {
  const old = new Map(
    [...$("pending").children].map((n) => [n.dataset.key, n]),
  );
  const fragment = document.createDocumentFragment();
  for (const prompt of state.pending) {
    const existing = old.get(prompt.key);
    if (existing) {
      fragment.append(existing);
      continue;
    }
    const p = prompt.params || {},
      form = el("form", "prompt");
    form.dataset.key = prompt.key;
    form.append(
      el(
        "h3",
        "",
        prompt.method === "item/tool/requestUserInput"
          ? "Codex has a question"
          : prompt.method.includes("elicitation")
            ? p.serverName + " needs your input"
            : "Approval needed",
      ),
    );
    const reply = async (result) => {
      for (const b of form.querySelectorAll("button")) b.disabled = true;
      try {
        await api("/prompts/" + encodeURIComponent(prompt.key), { result });
        state.pending = state.pending.filter((x) => x.key !== prompt.key);
        renderPrompts();
        controls();
      } catch (error) {
        for (const b of form.querySelectorAll("button")) b.disabled = false;
        fail(error);
      }
    };
    if (prompt.method === "item/tool/requestUserInput") {
      const fields = [];
      for (const q of p.questions) {
        const field = el("fieldset");
        field.append(el("legend", "", q.question));
        for (const [i, opt] of (q.options || []).entries()) {
          const label = el("label", "choice");
          const radio = el("input");
          radio.type = "radio";
          radio.name = q.id;
          radio.value = opt.label;
          const text = el("span", "", opt.label);
          if (opt.description) text.append(el("small", "", opt.description));
          label.append(radio, text);
          field.append(label);
        }
        const free = el(q.isSecret ? "input" : "textarea");
        if (q.isSecret) {
          free.type = "password";
          free.autocomplete = "off";
        }
        free.placeholder = "Or type your answer…";
        free.setAttribute("aria-label", q.question);
        field.append(free);
        fields.push({ q, field, free });
        form.append(field);
      }
      const send = el("button", "primary", "Send answers");
      send.type = "submit";
      form.append(send);
      form.onsubmit = (e) => {
        e.preventDefault();
        const answers = {};
        for (const { q, field, free } of fields) {
          const value =
            free.value.trim() || field.querySelector("input:checked")?.value;
          if (!value) {
            fail(
              new Error("Answer each question using a choice or free text."),
            );
            return;
          }
          answers[q.id] = { answers: [value] };
        }
        reply({ answers });
      };
    } else if (
      prompt.method === "mcpServer/elicitation/request" &&
      p.mode === "openai/userVerification"
    ) {
      form.append(
        el(
          "p",
          "",
          p.description ||
            p.title ||
            "Complete verification in the desktop Codex client.",
        ),
      );
      form.append(
        el(
          "p",
          "muted",
          "This verification requires the desktop Codex client.",
        ),
      );
    } else if (prompt.method === "mcpServer/elicitation/request") {
      form.append(el("p", "", p.message || p.description || p.title));
      const fields = [];
      if (p.url) {
        const a = el("a", "", "Open verification page ↗");
        try {
          const url = new URL(p.url);
          if (["https:", "http:"].includes(url.protocol)) {
            a.href = url.href;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            form.append(a);
          }
        } catch {}
      }
      const schema = p.requestedSchema || {};
      for (const [name, s] of Object.entries(schema.properties || {})) {
        let input;
        if (s.enum || s.oneOf) {
          input = el("select");
          for (const entry of s.oneOf || s.enum) {
            const value = typeof entry === "object" ? entry.const : entry;
            const option = el(
              "option",
              "",
              typeof entry === "object" ? entry.title || value : value,
            );
            option.value = value;
            input.append(option);
          }
        } else if (s.type === "array" && s.items?.enum) {
          input = el("select");
          input.multiple = true;
          for (const value of s.items.enum) {
            const o = el("option", "", value);
            o.value = value;
            input.append(o);
          }
        } else {
          input = el("input");
          input.type =
            s.type === "boolean"
              ? "checkbox"
              : ["integer", "number"].includes(s.type)
                ? "number"
                : s.format === "email"
                  ? "email"
                  : s.format === "uri"
                    ? "url"
                    : "text";
          if (s.type === "integer") input.step = "1";
          else if (s.type === "number") input.step = "any";
          if (s.minimum !== undefined) input.min = s.minimum;
          if (s.maximum !== undefined) input.max = s.maximum;
          if (s.minLength !== undefined) input.minLength = s.minLength;
          if (s.maxLength !== undefined) input.maxLength = s.maxLength;
        }
        input.name = name;
        input.setAttribute("aria-label", s.title || name);
        input.required =
          (schema.required || []).includes(name) && s.type !== "boolean";
        if (s.default !== undefined) {
          if (s.type === "boolean") input.checked = s.default;
          else input.value = s.default;
        }
        form.append(el("label", "", s.title || name), input);
        if (s.description) form.append(el("p", "muted", s.description));
        fields.push({ name, s, input });
      }
      const row = el("div", "button-row");
      const accept = el("button", "primary", "Submit");
      accept.type = "submit";
      row.append(
        accept,
        button("Decline", () =>
          reply({ action: "decline", content: null, _meta: null }),
        ),
        button("Cancel", () =>
          reply({ action: "cancel", content: null, _meta: null }),
        ),
      );
      form.append(row);
      form.onsubmit = (e) => {
        e.preventDefault();
        const content = {};
        for (const { name, s, input } of fields) {
          if (s.type === "boolean") content[name] = input.checked;
          else if (s.type === "array")
            content[name] = [...input.selectedOptions].map((o) => o.value);
          else if (input.value !== "")
            content[name] = ["integer", "number"].includes(s.type)
              ? Number(input.value)
              : input.value;
        }
        reply({
          action: "accept",
          content: fields.length ? content : null,
          _meta: null,
        });
      };
    } else if (prompt.method === "item/permissions/requestApproval") {
      form.append(
        el("p", "", p.reason || "Codex requests additional permissions."),
        el("pre", "", JSON.stringify(p.permissions, null, 2)),
      );
      const row = el("div", "button-row");
      row.append(
        button(
          "Allow this turn",
          () => reply({ permissions: p.permissions, scope: "turn" }),
          "primary",
        ),
        button("Deny", () => reply({ permissions: {}, scope: "turn" })),
      );
      form.append(row);
    } else if (
      [
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
      ].includes(prompt.method)
    ) {
      form.append(
        el("p", "", p.reason || "Review this action before continuing."),
        el("pre", "", p.command || JSON.stringify(p.changes || p, null, 2)),
      );
      const row = el("div", "button-row");
      row.append(
        button("Allow once", () => reply({ decision: "accept" }), "primary"),
        button("Allow for session", () =>
          reply({ decision: "acceptForSession" }),
        ),
        button("Deny", () => reply({ decision: "decline" })),
        button("Cancel turn", () => reply({ decision: "cancel" })),
      );
      form.append(row);
    } else {
      form.append(
        el("p", "", "This request requires the desktop Codex client."),
        el("pre", "", JSON.stringify(p, null, 2)),
      );
    }
    fragment.append(form);
  }
  $("pending").replaceChildren(fragment);
}
on("loginForm", "submit", async (e) => {
  e.preventDefault();
  try {
    await api("/login", { password: $("password").value });
    $("password").value = "";
    await start();
  } catch (error) {
    $("loginError").textContent = error.message;
  }
});
on("unarchive", "click", async () => {
  const snapshot = await api(`/threads/${state.thread.id}/unarchive`, {});
  ingest(snapshot);
  await loadOptions();
});
on("composer", "submit", submit);
on("new", "click", newSession);
on("back", "click", () => {
  saveDraft();
  $("app").classList.remove("chat-open");
  return loadSessions();
});
on("logout", "click", async () => {
  await api("/logout", {});
  state.socket?.close();
  location.reload();
});
on("homeHelp", "click", showHelp);
on("closeDialog", "click", () => $("dialog").close());
on("actions", "click", () => {
  $("actionsMenu").hidden = !$("actionsMenu").hidden;
  $("slashPicker").hidden = true;
  $("actions").setAttribute("aria-expanded", String(!$("actionsMenu").hidden));
});
on("codeMode", "click", () => setMode("default"));
on("planMode", "click", () => setMode("plan"));
on("modeLabel", "click", toggleMode);
on("returnMessage", "click", returnToMessage);
on("modelLabel", "click", chooseModel);
on("slash", "click", showSlash);
on("stop", "click", async () => {
  await api(`/threads/${state.thread.id}/stop`, {});
  scheduleRefresh();
});
on("moreSessions", "click", () => loadSessions(true));
on("recentTab", "click", () => { state.activeOnly = false; $("recentTab").setAttribute("aria-selected", "true"); $("runningTab").setAttribute("aria-selected", "false"); $("archived").disabled = false; loadSessions(); });
on("runningTab", "click", () => { state.activeOnly = true; $("recentTab").setAttribute("aria-selected", "false"); $("runningTab").setAttribute("aria-selected", "true"); $("archived").checked = false; $("archived").disabled = true; loadSessions(); });
on("archived", "change", () => loadSessions());
let searchTimer;
on("search", "input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadSessions().catch(fail), 250);
});
on("message", "input", () => {
  saveDraft();
  controls();
  if ($("message").value.startsWith("/")) showSlash();
  else $("slashPicker").hidden = true;
});
on("message", "keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});
on("older", "click", async () => {
  if (!state.historyCursor) return;
  const previous = $("conversation").scrollHeight;
  const snapshot = await api(
    `/threads/${state.thread.id}/history?cursor=${encodeURIComponent(state.historyCursor)}`,
  );
  ingest(snapshot, true);
  $("conversation").scrollTop = $("conversation").scrollHeight - previous;
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    if (state.socket?.readyState !== WebSocket.OPEN) connect();
    else scheduleRefresh();
  }
});
window.addEventListener("beforeunload", saveDraft);
document.addEventListener("gesturestart", (e) => e.preventDefault(), {
  passive: false,
});
document.addEventListener(
  "touchmove",
  (e) => {
    if (e.touches.length > 1) e.preventDefault();
  },
  { passive: false },
);
function viewport() {
  const view = window.visualViewport;
  document.documentElement.style.setProperty(
    "--app-height",
    (view?.height || innerHeight) + "px",
  );
  document.documentElement.style.setProperty(
    "--app-top",
    (view?.offsetTop || 0) + "px",
  );
}
window.visualViewport?.addEventListener("resize", viewport);
window.visualViewport?.addEventListener("scroll", viewport);
window.addEventListener("resize", viewport);
viewport();
async function start() {
  const session = await api("/session");
  state.csrf = session.csrf;
  state.installation = session.installation;
  connection(session.connected, session.error);
  $("login").hidden = true;
  $("app").hidden = false;
  try {
    await loadSessions();
  } catch (error) {
    $("browserConnection").hidden = false;
    $("browserConnection").textContent = error.message;
  }
  const id = readLocal("thread");
  if (id) {
    try {
      await openThread(id);
    } catch (error) {
      fail(error);
    }
  }
  connect();
}
start().catch(() => {
  $("login").hidden = false;
  $("app").hidden = true;
});

const byId = (id) => document.getElementById(id);

function number(value, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString(undefined, { maximumFractionDigits: digits })
    : "—";
}

function setText(id, value) {
  byId(id).textContent = value == null || value === "" ? "—" : String(value);
}

function renderTools(tools = []) {
  const list = byId("tools");
  list.replaceChildren();
  const rows = tools.slice(0, 5);
  setText("toolCount", `${tools.length} reading${tools.length === 1 ? "" : "s"}`);
  if (!rows.length) {
    const item = document.createElement("li");
    item.textContent = "No evidence snapshot yet.";
    list.append(item);
    return;
  }
  for (const tool of rows) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    const reading = document.createElement("b");
    label.textContent = String(tool.tool || tool.name || "Indicator");
    reading.textContent = String(tool.reading || tool.value || "active").slice(0, 32);
    item.append(label, reading);
    list.append(item);
  }
}

function render(state) {
  const snapshot = state?.snapshot;
  const online = Boolean(state?.ok && snapshot);
  byId("statusDot").classList.toggle("online", online);
  setText("source", online ? snapshot.source : state?.error || "SERVER OFFLINE");
  setText("age", state?.receivedAt ? `${Math.max(0, Math.round((Date.now() - state.receivedAt) / 1000))}s ago` : "—");
  if (!online) return;

  const decision = snapshot.decision || {};
  const market = snapshot.market || {};
  const direction = decision.direction || "Hold";
  setText("direction", direction.toUpperCase());
  byId("direction").className = String(direction).toLowerCase();
  setText("instrument", `${snapshot.instrument} · ${snapshot.timeframe}`);
  setText("conviction", `${number(decision.conviction, 0)}%`);
  setText("summary", decision.summary || "No summary supplied.");
  setText("last", number(market.last));
  setText("entry", number(decision.entry));
  setText("stop", number(decision.stop_loss));
  setText("target", number(decision.take_profit_1));
  renderTools(snapshot.evidence?.tools || []);
}

async function currentState() {
  const stored = await chrome.storage.local.get({
    serverBase: "http://127.0.0.1:8000",
    symbol: "BTC_USDT",
    timeframe: "1m",
    ravenState: null
  });
  byId("serverBase").value = stored.serverBase;
  byId("symbol").value = stored.symbol;
  byId("timeframe").value = stored.timeframe;
  byId("openDeck").href = stored.serverBase;
  render(stored.ravenState);
}

async function send(type, settings) {
  byId("save").disabled = true;
  byId("refresh").disabled = true;
  try {
    const state = await chrome.runtime.sendMessage({ type, settings });
    if (state?.settings?.serverBase) byId("openDeck").href = state.settings.serverBase;
    render(state);
  } finally {
    byId("save").disabled = false;
    byId("refresh").disabled = false;
  }
}

byId("refresh").addEventListener("click", () => send("RAVEN_REFRESH"));
byId("save").addEventListener("click", () =>
  send("RAVEN_SAVE_SETTINGS", {
    serverBase: byId("serverBase").value.trim(),
    symbol: byId("symbol").value,
    timeframe: byId("timeframe").value
  })
);

currentState();

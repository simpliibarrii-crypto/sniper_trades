const DEFAULTS = Object.freeze({
  serverBase: "http://127.0.0.1:8000",
  symbol: "BTC_USDT",
  timeframe: "1m"
});

function safeSettings(value = {}) {
  const serverBase = /^(http:\/\/(127\.0\.0\.1|localhost)(:\d{2,5})?)$/i.test(
    String(value.serverBase || "")
  )
    ? String(value.serverBase).replace(/\/$/, "")
    : DEFAULTS.serverBase;
  const symbol = /^[A-Z0-9]{2,12}_[A-Z0-9]{2,12}$/.test(String(value.symbol || ""))
    ? String(value.symbol)
    : DEFAULTS.symbol;
  const timeframe = /^(1m|5m|15m|1h|4h|6h|1D|1d)$/.test(String(value.timeframe || ""))
    ? String(value.timeframe)
    : DEFAULTS.timeframe;
  return { serverBase, symbol, timeframe };
}

async function loadSettings() {
  return safeSettings(await chrome.storage.local.get(DEFAULTS));
}

async function setBadge(direction, online) {
  const labels = { Long: "L", Short: "S", Exit: "X", Hold: "—" };
  await chrome.action.setBadgeText({ text: online ? labels[direction] || "—" : "!" });
  await chrome.action.setBadgeBackgroundColor({
    color: online
      ? direction === "Long"
        ? "#19d796"
        : direction === "Short" || direction === "Exit"
          ? "#ff5b7e"
          : "#7357ff"
      : "#596073"
  });
}

async function refreshSnapshot() {
  const settings = await loadSettings();
  const query = new URLSearchParams({
    symbol: settings.symbol,
    timeframe: settings.timeframe
  });
  try {
    const response = await fetch(`${settings.serverBase}/extension/snapshot?${query}`, {
      cache: "no-store",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    const snapshot = await response.json();
    const state = { ok: true, snapshot, settings, receivedAt: Date.now(), error: null };
    await chrome.storage.local.set({ ravenState: state });
    await setBadge(snapshot?.decision?.direction, true);
    return state;
  } catch (error) {
    const state = {
      ok: false,
      snapshot: null,
      settings,
      receivedAt: Date.now(),
      error: error instanceof Error ? error.message : "Local server unavailable"
    };
    await chrome.storage.local.set({ ravenState: state });
    await setBadge("Hold", false);
    return state;
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get(DEFAULTS);
  await chrome.storage.local.set(safeSettings(stored));
  await chrome.alarms.create("raven-refresh", { periodInMinutes: 1 });
  await refreshSnapshot();
});

chrome.runtime.onStartup.addListener(async () => {
  await chrome.alarms.create("raven-refresh", { periodInMinutes: 1 });
  await refreshSnapshot();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "raven-refresh") refreshSnapshot();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "RAVEN_REFRESH") {
    refreshSnapshot().then(sendResponse);
    return true;
  }
  if (message?.type === "RAVEN_SAVE_SETTINGS") {
    const settings = safeSettings(message.settings);
    chrome.storage.local
      .set(settings)
      .then(refreshSnapshot)
      .then(sendResponse);
    return true;
  }
  return false;
});

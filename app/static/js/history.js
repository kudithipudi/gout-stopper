/* GoutStopper — per-visitor history + personal triggers.
 *
 * Entirely client-side: nothing here is sent to the server. Backed by
 * localStorage, so it's private to this browser and may be empty or
 * unavailable (private windows, cleared storage) — every access is guarded
 * and fails to a silent no-op.
 */
(function () {
  "use strict";

  var HISTORY_KEY = "gs_history";
  var TRIGGERS_KEY = "gs_triggers";
  var MAX_ITEMS = 30;

  function read(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function write(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (e) {
      /* quota / disabled — ignore */
    }
  }

  function list() {
    var raw = read(HISTORY_KEY);
    if (!raw) return [];
    try {
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function record(entry) {
    if (!entry || typeof entry.id === "undefined") return;
    var id = Number(entry.id);
    if (!isFinite(id)) return;
    var items = list().filter(function (e) {
      return e && Number(e.id) !== id;
    });
    items.unshift({
      id: id,
      verdict: String(entry.verdict || ""),
      label: String(entry.label || "Food check"),
      ts: Date.now()
    });
    write(HISTORY_KEY, JSON.stringify(items.slice(0, MAX_ITEMS)));
  }

  function clear() {
    try {
      window.localStorage.removeItem(HISTORY_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  function triggers() {
    return String(read(TRIGGERS_KEY) || "")
      .toLowerCase()
      .split(",")
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);
  }

  function setTriggers(str) {
    write(TRIGGERS_KEY, String(str || "").slice(0, 500));
  }

  window.gsHistory = {
    list: list,
    record: record,
    clear: clear,
    triggers: triggers,
    setTriggers: setTriggers,
    triggersRaw: function () {
      return String(read(TRIGGERS_KEY) || "");
    }
  };
})();

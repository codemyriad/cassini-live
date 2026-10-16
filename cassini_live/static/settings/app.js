/* Cassini Live administrator settings page. Plain script, no build step, no external
 * resources. All server text is inserted with textContent (strict CSP, never parsed as HTML). */
(function () {
  "use strict";

  var BUSY = ["validating", "downloading", "verifying", "stopping", "loading", "rolling_back"];
  var CAPTIONS_OFF = ["stopping", "loading", "rolling_back"];
  var CANCELLABLE = ["downloading", "verifying", "stopping", "loading"];
  var FINISHED = ["succeeded", "failed", "cancelled"];
  var STEP_ORDER = ["validating", "downloading", "verifying", "stopping", "loading"];
  var STEP_NAMES = {
    validating: "checking requirements",
    downloading: "downloading",
    verifying: "verifying files",
    stopping: "stopping captions",
    loading: "loading the engine",
    rolling_back: "restoring the previous engine"
  };
  var PHASE_NAMES = {
    downloading: "Downloading",
    extracting: "Unpacking the download",
    verifying: "Verifying the download",
    installing: "Installing"
  };
  var LANGUAGE_NAMES = {
    en: "English", de: "German", fr: "French", es: "Spanish", it: "Italian", nl: "Dutch",
    pl: "Polish"
  };
  var REMOVABLE = ["ready", "partial", "corrupt"];
  var FAST_MS = 2000;
  var SLOW_MS = 10000;
  var BACKOFF_MS = [5000, 10000, 30000];
  var SESSION_MESSAGE = "Your Nextcloud session may have ended, or you are not an " +
    "administrator, or Cassini Live is disabled. Reload this page.";
  var OFFLINE_MESSAGE = "Cannot reach Cassini Live. Retrying…";

  var state = null;         // last GET api/state body
  var cards = {};           // engine id -> card elements
  var inFlight = false;     // one state request at a time
  var again = false;        // refresh requested while one was in flight
  var timer = null;
  var failures = 0;
  var connectionNotice = "";
  var actionNotice = null;  // {message, reasons}
  var lastLive = "";
  var cancelPending = false;
  var measurePending = false;
  var cancelPendingFor = null;

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        if (key === "className") { node.className = attrs[key]; }
        else { node.setAttribute(key, attrs[key]); }
      });
    }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function setText(node, text) {
    text = text === undefined || text === null ? "" : String(text);
    if (node.textContent !== text) { node.textContent = text; }
  }

  function show(node, visible) {
    if (node.hidden === !!visible) { node.hidden = !visible; }
  }

  function setDisabled(node, disabled) {
    if (node.disabled !== !!disabled) { node.disabled = !!disabled; }
  }

  // A pending control stays focusable (aria-disabled) so keyboard focus is not lost while
  // its request runs; other unavailable controls get the real disabled attribute.
  function setControl(node, disabled, pending) {
    setDisabled(node, disabled && !pending);
    setAttr(node, "aria-disabled", pending ? "true" : null);
  }

  function isInert(node) {
    return node.disabled || node.getAttribute("aria-disabled") === "true";
  }

  function setAttr(node, name, value) {
    if (value === null || value === undefined || value === false) {
      if (node.hasAttribute(name)) { node.removeAttribute(name); }
    } else if (node.getAttribute(name) !== String(value)) {
      node.setAttribute(name, String(value));
    }
  }

  function listItems(list, texts) {
    // Replace the items of a <ul> only when the text changed (never holds focusable nodes).
    var current = Array.prototype.map.call(list.children, function (li) { return li.textContent; });
    if (current.join("\n") === texts.join("\n")) { return; }
    while (list.firstChild) { list.removeChild(list.firstChild); }
    texts.forEach(function (text) { list.appendChild(el("li", null, text)); });
  }

  /* ---------- formatting ---------- */

  function trimNumber(value, digits) {
    return Number(value).toFixed(digits).replace(/\.?0+$/, "");
  }
  function gib(bytes, digits) {
    return trimNumber(bytes / 1073741824, digits === undefined ? 1 : digits) + " GiB";
  }
  function mib(bytes) { return Math.round(bytes / 1048576) + " MiB"; }
  function gibFromMib(value, digits) {
    return Number(value / 1024).toFixed(digits === undefined ? 2 : digits) + " GiB";
  }
  function localTime(value) {
    if (value === null || value === undefined || value === "") { return ""; }
    var date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    if (isNaN(date.getTime())) { return ""; }
    return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }
  function noPeriod(text) { return String(text || "").replace(/[.\s]+$/, ""); }
  function sentence(text) {
    text = String(text || "").trim();
    if (!text) { return ""; }
    return /[.!?…]$/.test(text) ? text : text + ".";
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  function engineName(id) {
    var engines = (state && state.panel && state.panel.engines) || [];
    for (var i = 0; i < engines.length; i++) {
      if (engines[i].id === id) { return engines[i].name; }
    }
    return id ? String(id) : "the previous engine";
  }

  function reasonText(reason) {
    var text = noPeriod(reason.message);
    if (reason.measured !== null && reason.measured !== undefined &&
        reason.required !== null && reason.required !== undefined) {
      text += " (measured " + reason.measured + ", required " + reason.required + ")";
    }
    return sentence(text);
  }

  function failureText(error) {
    if (!error) { return ""; }
    var message = noPeriod(error.message);
    switch (error.code) {
      case "timeout": return "It did not finish loading in time.";
      case "capacity_failed":  // saved by 0.7.x
        return "It did not load on this server (" + message + ").";
      case "load_failed": return "It could not be loaded (" + message + ").";
      case "hash_mismatch":
      case "size_mismatch":
      case "archive_layout":
        return "The downloaded files did not match the expected checksums. Try again later.";
      case "disk_space": return "Not enough free disk space.";
      case "network": return "The download failed (" + message + ").";
      case "cancelled": return "Cancelled.";
      case "disabled": return "Cassini Live was disabled during the change.";
      default: return sentence(message);
    }
  }

  function isBusy() {
    return !!(state && BUSY.indexOf(state.panel.switch.state) >= 0);
  }

  function downloadPercent(sw) {
    var p = sw.progress;
    if (sw.state !== "downloading" || !p || !p.bytesTotal) { return null; }
    return Math.max(0, Math.min(100, Math.floor(100 * p.bytesDone / p.bytesTotal)));
  }

  function stepText(sw) {
    if (sw.state === "rolling_back") { return "restoring " + engineName(sw.previous); }
    if (sw.state === "downloading" && sw.progress && PHASE_NAMES[sw.progress.phase]) {
      return PHASE_NAMES[sw.progress.phase].toLowerCase();
    }
    return STEP_NAMES[sw.state] || sw.state;
  }

  /* ---------- network ---------- */

  function request(method, path, body) {
    var headers = { "Accept": "application/json", "X-Cassini-Request": "1" };
    if (body) { headers["Content-Type"] = "application/json"; }
    var options = {
      method: method,
      credentials: "same-origin",
      cache: "no-store",
      referrerPolicy: "same-origin",
      redirect: "manual",
      headers: headers
    };
    if (body) { options.body = JSON.stringify(body); }
    if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) {
      options.signal = AbortSignal.timeout(15000);
    }
    return fetch(new URL(path, document.baseURI), options).then(function (response) {
      if (response.type === "opaqueredirect") {
        // Nextcloud sends logged-out visitors to its login page.
        throw { status: 401, code: "session", message: "", reasons: [] };
      }
      var type = response.headers.get("content-type") || "";
      var parse = type.indexOf("application/json") >= 0
        ? response.json().catch(function () { return null; })
        : Promise.resolve(null);
      return parse.then(function (data) {
        if (response.ok && data) { return data; }
        var error = (data && data.error) || {};
        if (data && typeof data.detail === "string" && !error.message) {
          error = { message: data.detail };
        }
        // A 2xx answer that is not our JSON is Nextcloud's login or error page.
        var status = response.ok ? 401 : response.status;
        throw {
          status: status,
          code: error.code || (status === 401 || status === 403 || status === 404
            ? "session" : "http_" + status),
          message: error.message || "",
          reasons: Array.isArray(error.reasons) ? error.reasons : []
        };
      });
    }, function (problem) {
      if (problem && problem.status) { throw problem; }
      throw {
        status: 0,
        code: problem && problem.name === "TimeoutError" ? "timeout" : "network",
        message: "",
        reasons: []
      };
    });
  }

  function isSessionError(error) {
    return (error.status === 401 || error.status === 403 || error.status === 404) &&
      (error.code === "session" || error.code === "forbidden" || !error.message);
  }

  /* ---------- polling ---------- */

  function nextDelay() {
    if (failures > 0) { return BACKOFF_MS[Math.min(failures, BACKOFF_MS.length) - 1]; }
    if (!state) { return SLOW_MS; }
    var safety = state.status && state.status.resourceSafety;
    var capacity = state.status && state.status.capacityAssessment;
    var calls = state.panel && state.panel.activeCalls;
    // Live numbers move during a load, a measurement or a call; idle pages poll slowly.
    if (isBusy() || (safety && (safety.phase === "loading" || safety.phase === "measuring")) ||
        (capacity && capacity.state === "checking") || calls > 0) {
      return FAST_MS;
    }
    return SLOW_MS;
  }

  function schedule() {
    clearTimeout(timer);
    timer = null;
    if (document.hidden) { return; }
    timer = setTimeout(refresh, nextDelay());
  }

  function refresh() {
    clearTimeout(timer);
    timer = null;
    if (inFlight) { again = true; return; }
    inFlight = true;
    again = false;
    request("GET", "api/state").then(function (data) {
      failures = 0;
      connectionNotice = "";
      state = data;
      render();
    }, function (error) {
      failures += 1;
      connectionNotice = isSessionError(error) ? SESSION_MESSAGE : OFFLINE_MESSAGE;
      renderNotice();
      if (!state) { setLive(isSessionError(error) ? "Cannot load the settings." : OFFLINE_MESSAGE); }
    }).then(function () {
      inFlight = false;
      if (again) { refresh(); } else { schedule(); }
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { clearTimeout(timer); timer = null; } else { refresh(); }
  });

  /* ---------- notices ---------- */

  function renderNotice() {
    var box = $("notice");
    var parts = [];
    if (connectionNotice) { parts.push({ message: connectionNotice, reasons: [] }); }
    if (actionNotice) { parts.push(actionNotice); }
    var key = JSON.stringify(parts);
    if (box.getAttribute("data-key") !== key) {
      box.setAttribute("data-key", key);
      while (box.firstChild) { box.removeChild(box.firstChild); }
      parts.forEach(function (part) {
        box.appendChild(el("p", null, part.message));
        if (part.reasons.length) {
          var list = el("ul");
          part.reasons.slice(0, 3).forEach(function (r) { list.appendChild(el("li", null, reasonText(r))); });
          box.appendChild(list);
        }
      });
    }
    show(box, parts.length > 0);
  }

  function actionFailed(what, error) {
    var message;
    if (isSessionError(error)) { message = SESSION_MESSAGE; }
    else if (error.status === 0) { message = what + " Cassini Live did not answer. Try again."; }
    else if (error.code === "csrf") {
      message = what + " The request was blocked as unsafe. Reload this page and try again.";
    } else { message = what + " " + sentence(error.message || "The server refused it."); }
    actionNotice = { message: message, reasons: error.reasons || [] };
    renderNotice();
  }

  function clearActionNotice() {
    if (actionNotice) { actionNotice = null; renderNotice(); }
    showActionResult("");
  }

  // Outcome of a page action. Kept apart from #live, which the next poll rewrites, so it
  // stays visible and is announced until the next action.
  function showActionResult(text) {
    var box = $("action-result");
    setText(box, text);
    show(box, !!text);
  }

  /* ---------- rendering ---------- */

  function setLive(text) {
    if (text !== lastLive) {
      lastLive = text;
      setText($("live"), text);
    }
  }

  function render() {
    renderNotice();
    renderLinks();
    renderOverview();
    renderChange();
    renderEngines();
    renderServer();
  }

  function renderLinks() {
    var links = state.links || {};
    if (links.adminSettings) { setAttr($("back"), "href", links.adminSettings); }
    if (links.docs) { setAttr($("docs"), "href", links.docs); }
    setText($("version"), state.version || (state.status && state.status.version) || "");
  }

  function captionsText(safety) {
    if (!safety) { return "Unknown"; }
    var reason = sentence(safety.reason || "");
    if (safety.phase === "measuring") { return "Paused: measuring capacity"; }
    if (safety.phase === "loading") { return "Loading the engine"; }
    switch (safety.state) {
      case "ready": return "Ready";
      case "recovering": return "Recovering automatically: " + (reason || "waiting for resources.");
      case "blocked":
        return "Paused: " + (reason || "not enough resources.") +
          (safety.adminActionRequired ? " Administrator action required." : "");
      case "disabled": return "Disabled";
      default: return sentence(safety.state);
    }
  }

  var HOLD_REASONS = {
    ceiling: "every seat is taken",
    memory: "memory is short",
    cpu: "the CPU is contended",
    lag: "captions are behind"
  };

  // Seats and live lag from the load governor: decided per speaker, never a fixed budget.
  function speakersText(safety) {
    var a = safety && safety.admission;
    if (!a || !safety || safety.state !== "ready") { return "–"; }
    var text = plural(a.speaking || 0, "speaking", "speaking");
    if (a.open) {
      text += " of up to " + a.ceiling + "; admitting more";
    } else {
      text += "; not admitting more: " + (HOLD_REASONS[a.reason] || a.reason);
    }
    if (a.captionAgeSeconds > 0.2) {
      text += "; captions trail speech by " + a.captionAgeSeconds.toFixed(1) + " s";
    }
    if (a.observeOnly) { text += " (observing; thresholds not yet measured)"; }
    return text;
  }

  function measuredLag(capacity) {
    var lag = null;
    (capacity.scenarios || []).forEach(function (s) {
      if (typeof s.maxLagSeconds === "number" && (lag === null || s.maxLagSeconds > lag)) {
        lag = s.maxLagSeconds;
      }
    });
    return lag;
  }

  function capacityText(capacity) {
    if (!capacity) { return "Not run yet"; }
    switch (capacity.state) {
      case "passed":
      case "limited":
        var when = localTime(capacity.checkedAt);
        var lag = measuredLag(capacity);
        return plural(capacity.testedSpeakers, "simultaneous speaker", "simultaneous speakers") +
          " passed" + (when ? " (" + when + ")" : "") +
          (lag === null ? "" : ", lag up to " + lag.toFixed(1) + " s");
      case "checking": return "Measuring… captions pause for up to two minutes";
      case "not_tested": return "Not run yet";
      case "insufficient": return "Did not pass for one speaker";
      case "interrupted": return "Interrupted";
      default: return "Did not complete";
    }
  }

  function renderOverview() {
    var panel = state.panel;
    var status = state.status || {};
    var safety = status.resourceSafety;
    var capacity = status.capacityAssessment;
    var sw = panel.switch;

    var active = engineName(panel.active);
    setText($("active-engine"), status.enabled === false ? active + " (Cassini Live is not enabled)" : active);
    setText($("captions"), captionsText(safety));
    show($("captions-spinner"), !!safety && safety.phase === "loading");
    setText($("speakers"), speakersText(safety));
    setText($("capacity"), capacityText(capacity));
    show($("capacity-spinner"), !!capacity && capacity.state === "checking");
    renderMeasure(panel, safety, capacity);
    setText($("calls"), String(panel.activeCalls || 0));

    var live;
    if (isBusy()) {
      var pct = downloadPercent(sw);
      live = "Changing to " + engineName(sw.target) + ": " + stepText(sw) +
        (pct === null ? "" : " " + Math.floor(pct / 10) * 10 + "%") + ".";
    } else {
      live = "Using " + active + ". Captions " +
        (safety && safety.state === "ready" ? "ready" : "paused") + ".";
    }
    setLive(live);

    var lastError = panel.lastError;
    var switchError = sw.error;
    var showError = !!lastError && !(switchError && switchError.message === lastError.message);
    if (showError) { setText($("last-error"), "At the last start: " + noPeriod(lastError.message) + "."); }
    show($("last-error"), showError);

    var refusal = panel.lastRefusal;
    if (refusal) {
      var when = localTime(refusal.at);
      setText($("last-refusal-text"), "A choice made in Administration settings" +
        (when ? " at " + when : "") + " was not applied: " +
        engineName(refusal.engine) + ". " + sentence(refusal.message));
      var reasons = (refusal.reasons || []).map(reasonText);
      listItems($("last-refusal-reasons"), reasons);
      show($("last-refusal-reasons"), reasons.length > 0);
    }
    show($("last-refusal"), !!refusal);
  }

  function renderChange() {
    var sw = state.panel.switch;
    var busy = isBusy();
    var finished = FINISHED.indexOf(sw.state) >= 0;
    show($("change"), busy || finished);
    if (!busy && !finished) { return; }
    setText($("change-title"), busy ? "Current change" : "Last change");

    // Steps
    var steps = $("steps");
    show(steps, busy);
    var currentIndex = STEP_ORDER.indexOf(sw.state);
    Array.prototype.forEach.call(steps.children, function (li) {
      var step = li.getAttribute("data-step");
      var rolling = sw.state === "rolling_back";
      var visible = rolling ? step === "rolling_back" : step !== "rolling_back";
      show(li, visible);
      var index = STEP_ORDER.indexOf(step);
      var current = step === sw.state;
      var done = !rolling && index >= 0 && index < currentIndex;
      setAttr(li, "aria-current", current ? "step" : null);
      li.classList.toggle("current", current);
      li.classList.toggle("done", done);
      var label = li.querySelector(".step-label");
      var text;
      if (step === "rolling_back") { text = "Restoring " + engineName(sw.previous); }
      else if (step === "downloading" && current && sw.progress && PHASE_NAMES[sw.progress.phase]) {
        text = PHASE_NAMES[sw.progress.phase];
      } else { text = STEP_NAMES[step].charAt(0).toUpperCase() + STEP_NAMES[step].slice(1); }
      if (done) { text += " (done)"; }
      setText(label, text);
    });

    // Progress
    var progress = $("progress");
    var box = $("progress-box");
    show(box, busy);
    if (busy) {
      var p = sw.progress;
      if (sw.state === "downloading" && p && p.bytesTotal) {
        var pct = downloadPercent(sw);
        setAttr(progress, "max", p.bytesTotal);
        if (progress.value !== p.bytesDone) { progress.value = p.bytesDone; }
        setText($("progress-label"), pct + "% · " + Math.round(p.bytesDone / 1048576) + " of " +
          Math.round(p.bytesTotal / 1048576) + " MiB");
      } else {
        setAttr(progress, "value", null);
        setAttr(progress, "max", null);
        setText($("progress-label"), "Progress: " + stepText(sw));
      }
    }

    // Explanation
    var text = "";
    if (busy) {
      if (CAPTIONS_OFF.indexOf(sw.state) >= 0) {
        text = "Captions are off in all calls until this finishes (seconds when the files are on disk).";
      } else {
        text = engineName(sw.previous) + " keeps serving calls meanwhile.";
      }
    }
    setText($("change-text"), text);
    show($("change-text"), busy);

    // Result
    var result = "";
    if (finished) {
      var target = engineName(sw.target);
      if (sw.state === "succeeded") { result = "Now using " + target + "."; }
      else if (sw.state === "cancelled") { result = "The change to " + target + " was cancelled."; }
      else {
        result = "Could not switch to " + target + ". " + failureText(sw.error) + " " +
          (sw.rolledBack ? "Rolled back to " + engineName(sw.previous) + "; captions work again."
            : "Captions will recover automatically.");
      }
    }
    setText($("result"), result);
    show($("result"), finished);
    $("result").className = sw.state === "failed" ? "message error" :
      sw.state === "succeeded" ? "message success" : "message";
    var when = finished ? localTime(sw.updatedAt) : "";
    setText($("result-time"), when ? "Finished " + when + "." : "");
    show($("result-time"), !!when);

    // Cancel button
    var cancel = $("cancel");
    var cancellable = busy && CANCELLABLE.indexOf(sw.state) >= 0;
    if (cancelPending && cancelPendingFor !== sw.state) { cancelPending = false; }
    var hadFocus = document.activeElement === cancel;
    show(cancel, cancellable);
    setControl(cancel, !cancellable || cancelPending, cancellable && cancelPending);
    setAttr(cancel, "aria-busy", cancelPending ? "true" : null);
    show(cancel.querySelector(".spinner"), cancelPending);
    if (hadFocus && (cancel.hidden || (cancel.disabled && !cancelPending))) { focusChangeTitle(); }
  }

  function focusChangeTitle() {
    var title = $("change-title");
    if (!$("change").hidden) { title.focus(); } else { $("overview-title").focus(); }
  }

  function measurementsText(m) {
    if (!m || typeof m !== "object" || !m.measuredOn) { return ""; }
    var date = new Date(String(m.measuredOn) + "T00:00:00Z");
    var day = isNaN(date.getTime()) ? String(m.measuredOn)
      : date.toLocaleDateString(undefined, { dateStyle: "medium", timeZone: "UTC" });
    var text = "Requirements measured on " + day;
    var estimates = (m.estimates || []).map(function (e) { return String(e).toUpperCase(); });
    if (estimates.length) {
      text += "; " + estimates.join(" and ") + " figures are estimates";
    }
    return text + ".";
  }

  function renderServer() {
    var host = state.panel.host || {};
    setText($("host-memory-limit"), host.memoryLimitBytes ? gib(host.memoryLimitBytes, 2) : "No limit set");
    setText($("host-memory"), host.hostMemoryBytes ? gib(host.hostMemoryBytes, 1) : "Unknown");
    var cpu = host.cpuQuota ? trimNumber(host.cpuQuota, 2) + " CPU" : "No limit set";
    if (host.affinityCpus) { cpu += " (" + plural(host.affinityCpus, "core", "cores") + " usable)"; }
    setText($("host-cpu"), cpu);
    setText($("host-disk"), host.freeDiskBytes || host.freeDiskBytes === 0 ? gib(host.freeDiskBytes, 1) : "Unknown");
    var flags = (host.cpuFlags || []).map(function (f) { return f.toUpperCase(); }).join(", ");
    setText($("host-flags"), (flags || "None of the optional ones") + (host.arch ? " · " + host.arch : ""));
    setText($("measurements"), measurementsText(state.panel.measurements));
  }

  /* ---------- engine cards ---------- */

  function factRow(dl, label) {
    var row = el("div");
    row.appendChild(el("dt", null, label));
    var dd = el("dd");
    row.appendChild(dd);
    dl.appendChild(row);
    return dd;
  }

  function button(className, text) {
    var b = el("button", { type: "button", className: className });
    var spin = el("span", { className: "spinner", "aria-hidden": "true" });
    spin.hidden = true;
    b.appendChild(spin);
    b.appendChild(el("span", { className: "label" }, text));
    return b;
  }

  function setButton(b, text, busy) {
    setText(b.querySelector(".label"), text);
    show(b.querySelector(".spinner"), !!busy);
    setAttr(b, "aria-busy", busy ? "true" : null);
  }

  function createCard(engine) {
    var id = engine.id;
    var c = { id: id };
    var li = el("li", { className: "card", id: "engine-" + id, "aria-labelledby": "engine-title-" + id });
    var head = el("div", { className: "card-head" });
    c.title = el("h3", { id: "engine-title-" + id, tabindex: "-1" }, engine.name);
    head.appendChild(c.title);
    c.badges = el("p", { className: "badges" });
    c.badge = {
      active: el("span", { className: "badge active" }, "In use"),
      selected: el("span", { className: "badge" }, "Selected"),
      ready: el("span", { className: "badge" }, "Downloaded"),
      ineligible: el("span", { className: "badge error" }, "Unavailable"),
      advisory: el("span", { className: "badge warning" }, "Warning"),
      changing: el("span", { className: "badge" }, "Changing")
    };
    Object.keys(c.badge).forEach(function (k) { c.badge[k].hidden = true; c.badges.appendChild(c.badge[k]); });
    head.appendChild(c.badges);
    li.appendChild(head);

    c.summary = el("p", { className: "summary" });
    li.appendChild(c.summary);

    c.reasons = el("ul", { id: "reasons-" + id, className: "reasons" });
    c.reasons.hidden = true;
    li.appendChild(c.reasons);

    var dl = el("dl", { className: "facts card-facts" });
    c.languages = factRow(dl, "Languages");
    c.accuracy = factRow(dl, "Accuracy");
    c.requirements = factRow(dl, "Requirements");
    c.modelRow = factRow(dl, "Model files");
    li.appendChild(dl);

    var lic = el("div", { className: "license" });
    var line = el("p");
    line.appendChild(document.createTextNode("License: "));
    c.licenseLink = el("a", { target: "_blank", rel: "noopener noreferrer" });
    line.appendChild(c.licenseLink);
    c.licenseVersion = document.createTextNode("");
    line.appendChild(c.licenseVersion);
    c.licenseTextSep = document.createTextNode(" · ");
    line.appendChild(c.licenseTextSep);
    c.licenseText = el("a", { target: "_blank", rel: "noopener" }, "full text");
    line.appendChild(c.licenseText);
    lic.appendChild(line);
    c.noticeBox = el("figure", { className: "notice-text" });
    c.noticeBox.appendChild(el("figcaption", null, "Required notice"));
    c.notice = el("blockquote");
    c.noticeBox.appendChild(c.notice);
    c.noticeBox.hidden = true;
    lic.appendChild(c.noticeBox);
    c.licenseSummary = el("ul", { className: "license-summary" });
    lic.appendChild(c.licenseSummary);
    lic.appendChild(el("p", { className: "muted" },
      "Shown for information. Using the engine means its license applies."));
    li.appendChild(lic);

    c.why = el("p", { id: "why-" + id, className: "muted why" });
    c.why.hidden = true;
    li.appendChild(c.why);

    var actions = el("div", { className: "actions" });
    c.use = button("use primary", "Use " + engine.name);
    c.del = button("delete destructive", "Remove downloaded files");
    c.del.hidden = true;
    actions.appendChild(c.use);
    actions.appendChild(c.del);
    li.appendChild(actions);

    c.confirm = el("div", { className: "confirm", role: "group", "aria-labelledby": "confirm-text-" + id });
    c.confirm.hidden = true;
    c.confirmText = el("p", { id: "confirm-text-" + id });
    c.confirm.appendChild(c.confirmText);
    var row = el("div", { className: "actions" });
    c.confirmYes = button("primary", "Change engine");
    c.confirmNo = button("secondary", "Keep current");
    row.appendChild(c.confirmYes);
    row.appendChild(c.confirmNo);
    c.confirm.appendChild(row);
    li.appendChild(c.confirm);

    c.use.addEventListener("click", function () { onUse(c); });
    c.del.addEventListener("click", function () { onDelete(c); });
    c.confirmYes.addEventListener("click", function () { onConfirm(c); });
    c.confirmNo.addEventListener("click", function () { closeConfirm(c, true); });
    c.confirm.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { event.preventDefault(); closeConfirm(c, true); }
    });
    c.pending = null;   // "use" | "delete" while a request is in flight
    c.confirmFor = null; // "use" | "delete" while the confirm is open
    c.li = li;
    return c;
  }

  function requirementsText(engine) {
    var r = engine.requirements || {};
    var parts = [];
    var text = gibFromMib(r.memoryLimitMiB, 2) + " container memory";
    if (r.recommendedMemoryLimitMiB) { text += " (" + gibFromMib(r.recommendedMemoryLimitMiB, 2) + " recommended)"; }
    parts.push(text);
    if (r.hostMemoryMiB) { parts.push(trimNumber(r.hostMemoryMiB / 1024, 2) + " GiB host memory"); }
    if (r.cpuQuota) { parts.push(trimNumber(r.cpuQuota, 2) + " CPU"); }
    var line = parts.join(", ");
    if (r.cpuFlags && r.cpuFlags.length) {
      line += ", " + r.cpuFlags.map(function (f) { return f.toUpperCase(); }).join(", ");
    }
    var model = engine.model || {};
    if (r.downloadBytes && model.state !== "ready" && model.state !== "bundled") {
      line += ". Download " + mib(r.downloadBytes) + ", needs " + gib(r.diskBytes, 1) + " free disk";
    }
    if (r.provisional) { line += " (estimate)"; }
    return sentence(line);
  }

  function modelText(model, engine) {
    switch (model.state) {
      case "bundled": return "Part of the Cassini Live image.";
      case "ready": return "Downloaded.";
      case "absent": return "Not downloaded.";
      case "partial":
        return "Partly downloaded (" + mib(model.bytesDone || 0) +
          (model.bytesTotal ? " of " + mib(model.bytesTotal) : "") + ").";
      case "downloading": return "Downloading.";
      case "verifying": return "Verifying.";
      case "installing": return "Installing.";
      case "corrupt": return "The downloaded files are damaged. Remove them or choose the engine again to download them again.";
      case "error": return "Could not be read.";
      default: return engine.active ? "In use." : sentence(model.state);
    }
  }

  function renderEngines() {
    var list = $("engine-list");
    var panel = state.panel;
    var status = state.status || {};
    var sw = panel.switch;
    var busy = isBusy();
    var links = (state.links && state.links.licenses) || {};
    (panel.engines || []).forEach(function (engine) {
      var c = cards[engine.id];
      if (!c) {
        c = cards[engine.id] = createCard(engine);
        list.appendChild(c.li);
      }
      var model = engine.model || {};
      var ineligible = !engine.eligible && !engine.advisory;
      var target = busy && sw.target === engine.id;
      // Another engine is selected but waits for a restart with suitable limits.
      var deferred = !busy && (panel.engines || []).some(function (e) { return e.deferred; });
      var keep = !!engine.active && deferred;

      setText(c.title, engine.name);
      show(c.badge.active, engine.active);
      show(c.badge.selected, !!engine.deferred);
      show(c.badge.ready, !engine.active && model.state === "ready");
      show(c.badge.ineligible, ineligible);
      show(c.badge.advisory, !!engine.advisory);
      show(c.badge.changing, target);
      c.li.classList.toggle("is-active", !!engine.active);
      c.li.classList.toggle("is-unavailable", ineligible);

      setText(c.summary, engine.summary);

      var langs = (engine.languages || []).map(function (code) { return LANGUAGE_NAMES[code] || code; }).join(", ");
      setText(c.languages, langs + (engine.automaticLanguage ? " + automatic detection" : ""));
      var acc = engine.accuracy || {};
      setText(c.accuracy, (typeof acc.meetingWer === "number"
        ? (acc.meetingWer * 100).toFixed(1) +
          "% word errors on our recorded English meetings (lower is better)."
        : "Not measured.") +
        (acc.note && acc.note.indexOf("recorded English meetings") < 0 ? " " + sentence(acc.note) : ""));
      setText(c.requirements, requirementsText(engine));
      setText(c.modelRow, modelText(model, engine) + (model.error ? " " + sentence(model.error) : ""));

      var lic = engine.license || {};
      setText(c.licenseLink, lic.name || lic.id || "Unknown");
      setAttr(c.licenseLink, "href", lic.url || null);
      setText(c.licenseVersion, lic.version ? " (version " + lic.version + ")" : "");
      var textUrl = links[lic.id];
      show(c.licenseText, !!textUrl);
      setText(c.licenseTextSep, textUrl ? " · " : "");
      if (textUrl) { setAttr(c.licenseText, "href", textUrl); }
      setAttr(c.licenseText, "aria-label", "Full text of the " + (lic.name || "license"));
      setText(c.notice, lic.notice || "");
      show(c.noticeBox, !!lic.notice);
      listItems(c.licenseSummary, lic.summary || []);
      show(c.licenseSummary, !!(lic.summary && lic.summary.length));

      var reasons = (engine.reasons || []).map(reasonText);
      listItems(c.reasons, reasons);
      show(c.reasons, reasons.length > 0);
      c.reasons.className = ineligible ? "reasons error" : "reasons warning";

      // Use button
      var why = "";
      if (engine.deferred) {
        why = "Selected, but this server does not meet the requirements listed above yet. " +
          "It returns by itself when Cassini Live restarts with suitable limits.";
      } else if (keep) {
        why = "Using " + engine.name + " meanwhile. Keep it to drop the choice of " +
          engineName(panel.selected) + ".";
      } else if (engine.active) { why = ""; }
      else if (status.enabled === false) { why = "Cassini Live is not enabled."; }
      else if (busy) { why = target ? "This change is in progress." : "Another change is in progress."; }
      else if (ineligible) { why = "This server does not meet the requirements listed above."; }
      var disabled = keep ? status.enabled === false || c.pending === "use"
        : !!engine.active || !!why || c.pending === "use";
      var needsDownload = model.state !== "ready" && model.state !== "bundled";
      var useText = keep ? "Keep using " + engine.name
        : engine.deferred ? engine.name + " is selected"
        : engine.active ? engine.name + " is in use"
        : (needsDownload ? "Download and use " : "Use ") + engine.name;
      var useFocused = document.activeElement === c.use;
      setButton(c.use, useText, c.pending === "use");
      setControl(c.use, disabled, c.pending === "use");
      setText(c.why, why);
      show(c.why, !!why);
      var described = [];
      if (reasons.length) { described.push(c.reasons.id); }
      if (why) { described.push(c.why.id); }
      setAttr(c.use, "aria-describedby", described.length ? described.join(" ") : null);
      if (disabled && c.confirmFor === "use") { closeConfirm(c, false); }

      // Delete button
      var removable = REMOVABLE.indexOf(model.state) >= 0 && !engine.active && !engine.deferred &&
        !target;
      var delFocused = document.activeElement === c.del;
      var size = model.state === "partial" && model.bytesDone ? " (" + mib(model.bytesDone) + ")" : "";
      setButton(c.del, "Remove downloaded files" + size, c.pending === "delete");
      setAttr(c.del, "aria-label", "Remove the downloaded " + engine.name + " files" + size);
      show(c.del, removable || c.pending === "delete");
      setControl(c.del, c.pending === "delete" || busy, c.pending === "delete");
      setAttr(c.del, "aria-describedby", busy && why ? c.why.id : null);
      if (!removable && c.confirmFor === "delete") { closeConfirm(c, false); }

      // Keep focus somewhere sensible when the focused control disappears or is disabled.
      if ((useFocused && c.use.disabled && c.pending !== "use") ||
          (delFocused && (c.del.hidden || c.del.disabled) && c.pending !== "delete")) {
        if (busy && !$("change").hidden) { focusChangeTitle(); } else { c.title.focus(); }
      }
    });
  }

  /* ---------- actions ---------- */

  function openConfirm(c, kind, text, yes) {
    c.confirmFor = kind;
    setText(c.confirmText, text);
    setButton(c.confirmYes, yes, false);
    setButton(c.confirmNo, kind === "use" ? "Keep current" : "Keep the files", false);
    c.confirmYes.className = kind === "delete" ? "destructive" : "primary";
    show(c.confirm, true);
    c.confirmYes.focus();
  }

  function closeConfirm(c, restoreFocus) {
    var kind = c.confirmFor;
    var hadFocus = c.confirm.contains(document.activeElement);
    c.confirmFor = null;
    show(c.confirm, false);
    if (restoreFocus || hadFocus) {
      var back = kind === "delete" ? c.del : c.use;
      if (!back.hidden && !back.disabled) { back.focus(); } else { c.title.focus(); }
    }
  }

  function onUse(c) {
    if (isInert(c.use)) { return; }
    clearActionNotice();
    var calls = (state && state.panel.activeCalls) || 0;
    var keep = !!state && state.panel.active === c.id;  // keeps captions running
    if (calls > 0 && !keep) {
      openConfirm(c, "use", (calls === 1 ? "1 call has" : calls + " calls have") +
        " captions on. They stop while the new engine loads.", "Change engine");
      return;
    }
    switchTo(c);
  }

  function onDelete(c) {
    if (isInert(c.del)) { return; }
    clearActionNotice();
    openConfirm(c, "delete", "Remove the " + engineName(c.id) +
      " model files? It downloads again if you choose it later.", "Remove files");
  }

  function onConfirm(c) {
    var kind = c.confirmFor;
    c.confirmFor = null;
    show(c.confirm, false);
    if (kind === "delete") { removeModel(c); } else { switchTo(c); }
  }

  function switchTo(c) {
    c.pending = "use";
    setButton(c.use, c.use.querySelector(".label").textContent, true);
    setControl(c.use, true, true);
    c.use.focus();
    var keep = !!state && state.panel.active === c.id;
    var dropped = keep ? engineName(state.panel.selected) : "";
    request("POST", "api/engine", { engine: c.id }).then(function (data) {
      c.pending = null;
      clearActionNotice();
      if (data && data.panel && state) { state.panel = data.panel; render(); }
      if (keep) {
        showActionResult("Keeping " + engineName(c.id) + ". " + dropped + " is no longer selected.");
        c.title.focus();
        return;
      }
      focusChangeTitle();
    }, function (error) {
      c.pending = null;
      actionFailed("Could not change to " + engineName(c.id) + ".", error);
      if (state) { render(); }
      if (!c.use.disabled) { c.use.focus(); } else { c.title.focus(); }
    }).then(refresh);
  }

  function removeModel(c) {
    c.pending = "delete";
    setButton(c.del, c.del.querySelector(".label").textContent, true);
    setControl(c.del, true, true);
    c.del.focus();
    request("DELETE", "api/models/" + encodeURIComponent(c.id)).then(function (data) {
      c.pending = null;
      clearActionNotice();
      if (data && data.panel && state) { state.panel = data.panel; render(); }
      showActionResult("Removed the " + engineName(c.id) + " model files.");
      if (c.del.hidden || c.del.disabled) { c.title.focus(); }
    }, function (error) {
      c.pending = null;
      actionFailed("Could not remove the " + engineName(c.id) + " files.", error);
      if (state) { render(); }
      if (!c.del.hidden && !c.del.disabled) { c.del.focus(); } else { c.title.focus(); }
    }).then(refresh);
  }

  function measureWhy(panel, safety, capacity) {
    if ((capacity && capacity.state === "checking") || (safety && safety.phase === "measuring")) {
      return "A measurement is running.";
    }
    if (isBusy()) { return "Wait for the engine change to finish."; }
    if (!safety || safety.state !== "ready") { return "Captions are not ready."; }
    if (panel.activeCalls) { return "End the calls with captions first; measuring pauses them."; }
    return "";
  }

  function renderMeasure(panel, safety, capacity) {
    var button = $("measure");
    var why = measureWhy(panel, safety, capacity);
    if (measurePending && why) { measurePending = false; }
    setControl(button, !!why || measurePending, measurePending);
    setAttr(button, "aria-busy", measurePending ? "true" : null);
    show(button.querySelector(".spinner"), measurePending);
    setText($("measure-why"), why);
    show($("measure-why"), !!why);
  }

  function onMeasure() {
    if (!state || isInert($("measure"))) { return; }
    clearActionNotice();
    measurePending = true;
    render();
    request("POST", "api/capacity", {}).then(function (data) {
      measurePending = false;
      showActionResult("Measuring capacity. Captions pause for up to two minutes.");
      if (data && data.panel && state) { state.panel = data.panel; render(); }
    }, function (error) {
      measurePending = false;
      actionFailed("Could not start the measurement.", error);
      if (state) { render(); }
    }).then(refresh);
  }

  function onCancel() {
    if (!state || isInert($("cancel"))) { return; }
    clearActionNotice();
    cancelPending = true;
    cancelPendingFor = state.panel.switch.state;
    var cancel = $("cancel");
    setControl(cancel, true, true);
    setAttr(cancel, "aria-busy", "true");
    show(cancel.querySelector(".spinner"), true);
    request("POST", "api/cancel", {}).then(function (data) {
      if (data && data.panel && state) { state.panel = data.panel; render(); }
    }, function (error) {
      cancelPending = false;
      actionFailed("Could not cancel the change.", error);
      if (state) { render(); }
      if (!cancel.hidden && !cancel.disabled) { cancel.focus(); }
    }).then(refresh);
  }

  function start() {
    $("cancel").addEventListener("click", onCancel);
    $("measure").addEventListener("click", onMeasure);
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
}());

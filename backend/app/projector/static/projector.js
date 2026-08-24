/* VerseSync projector client.
 *
 * Subscribes to the /ws/transcripts channel and renders whatever verse
 * should currently be on screen. Written as a plain script with no build
 * step and no dependencies: an OBS Browser Source has to be able to load
 * this from a LAN address on a machine with no internet access.
 *
 * Behaviour that exists specifically for OBS:
 *   - Reconnects forever with backoff. OBS frequently starts before the
 *     VerseSync server does, and "Shutdown source when not visible"
 *     tears the page down on every scene change.
 *   - Honours the server's retained state on reconnect, so switching
 *     scenes mid-verse brings the verse back rather than a blank frame.
 *   - Never throws on malformed payloads; a broken frame must not leave
 *     a dead overlay on a live stream.
 */
(function () {
  "use strict";

  var THEMES = ["lowerthird", "caption", "fullscreen"];
  var BACKGROUNDS = ["transparent", "dark", "light", "green"];

  var params = new URLSearchParams(window.location.search);

  function pick(name, allowed, fallback) {
    var raw = (params.get(name) || "").trim().toLowerCase();
    return allowed.indexOf(raw) !== -1 ? raw : fallback;
  }

  function num(name, fallback, min, max) {
    var raw = parseFloat(params.get(name));
    if (!isFinite(raw)) return fallback;
    return Math.min(max, Math.max(min, raw));
  }

  function flag(name, fallback) {
    var raw = params.get(name);
    if (raw === null) return fallback;
    raw = raw.trim().toLowerCase();
    if (raw === "" || raw === "1" || raw === "true" || raw === "yes") return true;
    if (raw === "0" || raw === "false" || raw === "no") return false;
    return fallback;
  }

  var config = {
    theme: pick("theme", THEMES, window.VERSESYNC_DEFAULTS.theme),
    bg: pick("bg", BACKGROUNDS, "transparent"),
    hold: num("hold", window.VERSESYNC_DEFAULTS.hold, 0, 3600),
    fontScale: num("fontScale", window.VERSESYNC_DEFAULTS.fontScale, 0.3, 4),
    showRef: flag("showRef", true),
    showTranslation: flag("showTranslation", true),
    maxVerses: num("maxVerses", 8, 1, 50),
    debug: flag("debug", false),
  };

  var body = document.body;
  var stage = document.getElementById("stage");
  var card = document.getElementById("card");
  var refEl = document.getElementById("reference");
  var refText = document.getElementById("reference-text");
  var translationEl = document.getElementById("translation");
  var versesEl = document.getElementById("verses");
  var statusEl = document.getElementById("status");

  body.setAttribute("data-theme", config.theme);
  body.setAttribute("data-bg", config.bg);
  body.style.setProperty("--font-scale", String(config.fontScale));
  if (config.debug) body.classList.add("debug");
  if (!config.showRef) refEl.style.display = "none";
  if (!config.showTranslation) translationEl.style.display = "none";

  var hideTimer = null;

  function setStatus(state, text) {
    statusEl.setAttribute("data-state", state);
    statusEl.textContent = text;
  }

  function clearHideTimer() {
    if (hideTimer !== null) {
      window.clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function hide() {
    clearHideTimer();
    body.classList.remove("is-visible");
  }

  function formatReference(ref) {
    if (!ref) return "";
    var out = String(ref.book_name || ref.book || "");
    if (ref.chapter != null) {
      out += " " + ref.chapter;
      if (ref.verse_start != null) {
        out += ":" + ref.verse_start;
        if (ref.verse_end != null && ref.verse_end !== ref.verse_start) {
          out += "-" + ref.verse_end;
        }
      }
    }
    return out.trim();
  }

  function render(payload, isReplay) {
    var verses = Array.isArray(payload.verses) ? payload.verses : [];
    if (verses.length === 0) {
      // A transcript with no scripture in it. Leave whatever is on
      // screen alone rather than blanking the overlay mid-sentence.
      return;
    }

    body.classList.toggle("is-replay", !!isReplay);

    refText.textContent = formatReference(payload.reference);
    translationEl.textContent = payload.translation || "";

    versesEl.textContent = "";
    verses.slice(0, config.maxVerses).forEach(function (verse) {
      var li = document.createElement("li");
      if (verses.length > 1 && verse.verse != null) {
        var num = document.createElement("span");
        num.className = "num";
        num.textContent = String(verse.verse);
        li.appendChild(num);
      }
      // textContent, never innerHTML: verse text is data that reaches us
      // over a socket, and an overlay is not a place to execute markup.
      li.appendChild(document.createTextNode(String(verse.text || "")));
      versesEl.appendChild(li);
    });

    clearHideTimer();
    body.classList.add("is-visible");

    if (config.hold > 0) {
      hideTimer = window.setTimeout(hide, config.hold * 1000);
    }

    if (isReplay) {
      // Drop the no-animation class once the frame has painted so the
      // NEXT verse animates in normally.
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(function () {
          body.classList.remove("is-replay");
        });
      });
    }
  }

  function handle(payload) {
    if (!payload || typeof payload !== "object") return;
    switch (payload.type) {
      case "detection":
        render(payload, payload.replayed === true);
        break;
      case "clear":
        hide();
        break;
      case "connected":
        setStatus("up", "connected");
        break;
      default:
        // heartbeat and anything added later: ignore silently.
        break;
    }
  }

  // ---------------- socket ----------------

  var socket = null;
  var retryMs = 500;
  var RETRY_MAX_MS = 10000;

  function socketUrl() {
    var proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + window.location.host + "/ws/transcripts";
  }

  function connect() {
    setStatus("connecting", "connecting...");
    try {
      socket = new WebSocket(socketUrl());
    } catch (err) {
      scheduleReconnect();
      return;
    }

    socket.onopen = function () {
      retryMs = 500;
      setStatus("up", "connected");
    };

    socket.onmessage = function (event) {
      var payload;
      try {
        payload = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      try {
        handle(payload);
      } catch (err) {
        // Rendering must never be able to kill the socket loop.
        if (config.debug && window.console) window.console.error(err);
      }
    };

    socket.onclose = function () {
      setStatus("down", "reconnecting...");
      scheduleReconnect();
    };

    socket.onerror = function () {
      try {
        socket.close();
      } catch (err) {
        /* onclose handles the retry */
      }
    };
  }

  function scheduleReconnect() {
    socket = null;
    window.setTimeout(connect, retryMs);
    retryMs = Math.min(RETRY_MAX_MS, Math.round(retryMs * 1.7));
  }

  // The server ignores inbound content; this keeps intermediate proxies
  // and the OBS CEF socket from idling the connection out during the
  // quiet stretches of a sermon.
  window.setInterval(function () {
    if (socket && socket.readyState === WebSocket.OPEN) {
      try {
        socket.send("ping");
      } catch (err) {
        /* the close handler will reconnect */
      }
    }
  }, 20000);

  connect();
})();

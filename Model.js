.pragma library
// Pure logic for the codex-voice-agent plugin, kept out of QML so it can be tested with node.

// Must equal "id" in manifest.json (a test checks). The one place the QML names it.
var PLUGIN_ID = "mlamoure.codex-voice-agent"

var GLYPH_MIC = "\u{F036C}"     // nf-md-microphone
var GLYPH_MIC_OFF = "\u{F036D}" // nf-md-microphone_off
var GLYPH_SEND = "\u{F048A}"    // nf-md-send
var GLYPH_NEW = "\u{F0415}"     // nf-md-plus
var GLYPH_TERMINAL = "\u{F018D}" // nf-md-console
var GLYPH_WARN = "\u{F0026}"    // nf-md-alert
var GLYPH_UPDATE = "\u{F06B0}"  // nf-md-update

var UPDATE_HELP_URL = "https://github.com/mlamoure/codex-voice-agent#updating"

// The one question the bar icon must always answer: is it listening?
// "listening": unmuted and the voice link is up (or coming up)
// "muted":     the agent cannot hear you
// "off":       the backend is not running / not reachable
function micState(connected, st) {
  if (!connected || !st || !st.threadId && st.muted === undefined) return "off"
  if (st.muted) return "muted"
  // Unmuted is not enough: something must actually be listening.
  if (st.voice === "on" || st.voice === "connecting") return "listening"
  return "muted"
}

function tooltip(connected, st) {
  var mic = micState(connected, st)
  if (mic === "off") return "Codex Voice Agent: not running"
  var parts = ["Codex Voice Agent: " + (mic === "listening" ? "listening" : "muted")]
  if (st.voice === "connecting") parts.push("connecting voice…")
  else if (st.voice === "error") parts.push("voice failed")
  if (st.working) parts.push("working")
  if (st.speaking) parts.push("speaking")
  return parts.join(" · ")
}

function statusLine(connected, st) {
  var mic = micState(connected, st)
  if (mic === "off") return "Not running — start it with: systemctl --user start codex-voice-agent"
  if (mic === "muted" && !st.muted) return "Voice disconnected — mute and unmute to reconnect"
  if (mic === "muted") return st.working ? "Muted · Codex is working" : "Muted — it can't hear you"
  if (st.voice === "connecting") return "Connecting voice…"
  if (st.voice === "error") return "Voice failed — see the history"
  return st.working ? "Listening · Codex is working" : "Listening"
}

function modelOptions(st) {
  if (!st || !st.models) return []
  return st.models.map(function(m) { return { value: m.id, label: m.name || m.id } })
}

var EFFORT_LABELS = { minimal: "Minimal", low: "Low", medium: "Medium", high: "High",
                      xhigh: "Extra high", max: "Max", ultra: "Ultra" }

function effortOptions(st) {
  if (!st || !st.models) return []
  var model = null
  for (var i = 0; i < st.models.length; i++) if (st.models[i].id === st.model) model = st.models[i]
  var efforts = model && model.efforts && model.efforts.length ? model.efforts : (st.effort ? [st.effort] : [])
  return efforts.map(function(e) { return { value: e, label: EFFORT_LABELS[e] || e } })
}

// How each history entry is presented.
function entryStyle(kind) {
  switch (kind) {
    case "you": return { who: "You (voice)", role: "user", mono: false }
    case "you_typed": return { who: "You", role: "user", mono: false }
    case "agent": return { who: "Agent (voice)", role: "agent", mono: false }
    case "codex": return { who: "Codex", role: "agent", mono: false, markdown: true }
    case "codex_note": return { who: "Codex", role: "note", mono: false, markdown: true }
    case "handoff": return { who: "Handed to Codex", role: "note", mono: false }
    case "command": return { who: "Ran", role: "tool", mono: true }
    case "files": return { who: "Edited", role: "tool", mono: true }
    case "review": return { who: "Auto-review", role: "review", mono: false }
    default: return { who: "", role: "notice", mono: false }
  }
}

function reviewLabel(entry) {
  var d = entry.detail || {}
  var head = (entry.status || "reviewed") + (d.risk ? " (risk " + d.risk + ")" : "")
  return head + (entry.text ? ": " + entry.text : "")
}

// How long the reply card stays after the agent stops speaking (or, for a typed
// request with no voice, after the answer arrives): long enough to read it.
function cardHoldMs(text, voice) {
  var words = String(text || "").split(/\s+/).filter(function(w) { return w.length }).length
  var base = voice ? 4000 : 6000
  return Math.min(15000, base + words * 120)
}

// Last few lines of a reply, for the small card.
function cardText(text, maxChars) {
  var t = String(text || "").trim()
  var limit = maxChars || 240
  if (t.length <= limit) return t
  return "…" + t.slice(t.length - limit).replace(/^\S*\s/, "")
}

function clampBands(bands, n) {
  var out = []
  for (var i = 0; i < n; i++) {
    var v = bands && i < bands.length ? Number(bands[i]) : 0
    out.push(isFinite(v) ? Math.max(0, Math.min(1, v)) : 0)
  }
  return out
}

// Microphone choices: the system default first (named after the current default device),
// then every input PipeWire reports. "" means "follow the system default".
function inputOptions(info) {
  var inputs = info && info.inputs ? info.inputs : []
  var def = null
  for (var i = 0; i < inputs.length; i++) if (inputs[i].id === (info && info.default)) def = inputs[i]
  var opts = [{ value: "", label: def ? "System default (" + def.name + ")" : "System default" }]
  for (var j = 0; j < inputs.length; j++) opts.push({ value: inputs[j].id, label: inputs[j].name })
  return opts
}

// What a history entry shows. Codex output can be steered by what it reads and Qt loads any
// image a rich text names, so rich text is only ever the backend's rendering (`html`, built by
// render.markdown_html with images removed) of a Codex entry; everything else is plain text.
// Only web links are ever opened.
function entryBody(entry) {
  if (entry.kind === "review") return { text: reviewLabel(entry), rich: false }
  if (entryStyle(entry.kind).markdown && typeof entry.html === "string") return { text: entry.html, rich: true }
  return { text: String(entry.text || ""), rich: false }
}

function isSafeLink(url) {
  return /^https?:\/\//i.test(String(url || ""))
}

// One argument for `bash -c`, safe whatever it contains.
function shellQuote(s) {
  return "'" + String(s).replace(/'/g, "'\\''") + "'"
}

// The panel's notice when the backend found a newer release ("" when there is none).
function updateText(st) {
  if (!st || !st.update || !st.update.version) return ""
  return "Codex Voice Agent " + st.update.version + " is available (you have " + (st.version || "?") + ")."
}

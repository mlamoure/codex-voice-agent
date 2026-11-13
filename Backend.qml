import QtQuick
import Quickshell
import Quickshell.Io

import "Model.js" as Model

// The link to the backend daemon: its control socket, the state and history it
// streams, and the commands the bar sends. Service.qml is one (plus the reply
// card). Widget.qml makes its own when the bar cannot hand it the service:
// Omarchy gives widgets on a replacement bar (a cloned bar such as OmacVM's
// notch bar) a shell without serviceFor(), so they must reach the daemon directly.
Item {
  id: backend

  readonly property string socketPath: Quickshell.env("XDG_RUNTIME_DIR") + "/codex-voice-agent.sock"
  readonly property string pluginId: Model.PLUGIN_ID

  property bool connected: false
  property var st: ({})               // latest state snapshot from the backend
  property var history: []            // history entries, oldest first
  property int historyRevision: 0     // bumps on every change, for views to follow
  property var bands: Model.clampBands([], 24)
  property var inputInfo: ({ default: null, inputs: [] })  // microphones, refreshed when the panel opens
  property string lastError: ""

  readonly property string micState: Model.micState(connected, st)
  readonly property bool working: connected && !!st.working
  readonly property bool speaking: connected && !!st.speaking

  // A reply event (start, delta, done, audio_done) for whoever shows replies.
  signal reply(var msg)

  // ------------------------------------------------------------ backend link
  Socket {
    id: sock
    path: backend.socketPath
    connected: false
    parser: SplitParser {
      onRead: function(line) {
        var msg
        try { msg = JSON.parse(line) } catch (e) { return }
        backend.handle(msg)
      }
    }
    onConnectedChanged: {
      backend.connected = sock.connected
      if (sock.connected) {
        backend.send({ cmd: "subscribe" })
        backend.send({ cmd: "history" })
      } else {
        reconnect.restart()
      }
    }
    onError: function(error) { backend.connected = false; reconnect.restart() }
  }

  Timer {
    id: reconnect
    interval: 2000
    repeat: false
    onTriggered: { sock.connected = false; sock.connected = true }
  }

  Component.onCompleted: sock.connected = true

  property int _nextId: 1
  function send(obj) {
    if (!sock.connected) return false
    obj.id = _nextId++
    sock.write(JSON.stringify(obj) + "\n")
    sock.flush()
    return true
  }

  function handle(msg) {
    if (msg.event === "state" || (msg.ok !== undefined && msg.threadId !== undefined && msg.muted !== undefined)) {
      backend.st = msg
    } else if (msg.inputs !== undefined) {
      backend.inputInfo = msg
    } else if (msg.items !== undefined) {
      backend.history = msg.items
      backend.historyRevision++
    } else if (msg.event === "history_reset") {
      backend.history = []
      backend.historyRevision++
    } else if (msg.event === "item") {
      var h = backend.history.slice()
      h.push(msg.item)
      if (h.length > 500) h.splice(0, h.length - 500)
      backend.history = h
      backend.historyRevision++
    } else if (msg.event === "spectrum") {
      backend.bands = Model.clampBands(msg.bands, 24)
    } else if (msg.event === "reply") {
      backend.reply(msg)
    } else if (msg.ok === false) {
      backend.lastError = msg.error || "request failed"
      errorClear.restart()
    }
  }

  Timer { id: errorClear; interval: 6000; onTriggered: backend.lastError = "" }

  onSpeakingChanged: if (!speaking) backend.bands = Model.clampBands([], 24)

  // ------------------------------------------------------------ actions
  function toggleMute() { send({ cmd: "mute", value: "toggle" }) }
  function setModel(m) { send({ cmd: "set_model", model: m }) }
  function setEffort(e) { send({ cmd: "set_effort", effort: e }) }
  function sendText(t) { return t && t.trim() ? send({ cmd: "send", text: t.trim() }) : false }
  function newSession() { send({ cmd: "new_session" }) }
  function approve(reviewId) { send({ cmd: "approve_denied", reviewId: reviewId }) }
  function setDictationAutoPause(v) { send({ cmd: "set_dictation_auto_pause", value: !!v }) }
  function setReplyCard(v) { send({ cmd: "set_reply_card", value: !!v }) }
  function refreshInputs() { send({ cmd: "list_inputs" }) }
  function setInput(id) { send({ cmd: "set_input", device: id ? id : null }) }
  function startBackend() { Quickshell.execDetached(["systemctl", "--user", "start", "codex-voice-agent.service"]) }
  function openTerminal() {
    if (!st.threadId) return
    Quickshell.execDetached(["uwsm-app", "--", "xdg-terminal-exec", "-e", "bash", "-lc",
                             "cd " + Model.shellQuote(st.workdir || Quickshell.env("HOME")) +
                             " && exec codex resume " + Model.shellQuote(st.threadId)])
  }
  function openPanel() {
    Quickshell.execDetached(["omarchy-shell", "shell", "summon", backend.pluginId, "{}"])
  }
}

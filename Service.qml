import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import qs.Commons

import "Model.js" as Model

// One instance per shell (bar widgets exist once per monitor): owns the backend
// connection, the shared state the widgets render, and the top-right reply card.
Item {
  id: service

  property var shell: null
  property var manifest: null

  readonly property string socketPath: Quickshell.env("XDG_RUNTIME_DIR") + "/codex-voice-agent.sock"
  readonly property string pluginId: Model.PLUGIN_ID

  property bool connected: false
  property var st: ({})               // latest state snapshot from the backend
  property var history: []            // history entries, oldest first
  property int historyRevision: 0     // bumps on every change, for views to follow
  property var bands: Model.clampBands([], 24)
  property var inputInfo: ({ default: null, inputs: [] })  // microphones, refreshed when the panel opens
  property string lastError: ""

  // reply card
  property bool cardOpen: false
  property string cardTextFull: ""
  property bool cardVoice: true

  readonly property string micState: Model.micState(connected, st)
  readonly property bool working: connected && !!st.working
  readonly property bool speaking: connected && !!st.speaking

  // ------------------------------------------------------------ backend link
  Socket {
    id: sock
    path: service.socketPath
    connected: false
    parser: SplitParser {
      onRead: function(line) {
        var msg
        try { msg = JSON.parse(line) } catch (e) { return }
        service.handle(msg)
      }
    }
    onConnectedChanged: {
      service.connected = sock.connected
      if (sock.connected) {
        service.send({ cmd: "subscribe" })
        service.send({ cmd: "history" })
      } else {
        reconnect.restart()
      }
    }
    onError: function(error) { service.connected = false; reconnect.restart() }
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
      service.st = msg
    } else if (msg.inputs !== undefined) {
      service.inputInfo = msg
    } else if (msg.items !== undefined) {
      service.history = msg.items
      service.historyRevision++
    } else if (msg.event === "history_reset") {
      service.history = []
      service.historyRevision++
    } else if (msg.event === "item") {
      var h = service.history.slice()
      h.push(msg.item)
      if (h.length > 500) h.splice(0, h.length - 500)
      service.history = h
      service.historyRevision++
    } else if (msg.event === "spectrum") {
      service.bands = Model.clampBands(msg.bands, 24)
    } else if (msg.event === "reply") {
      service.onReply(msg)
    } else if (msg.ok === false) {
      service.lastError = msg.error || "request failed"
      errorClear.restart()
    }
  }

  Timer { id: errorClear; interval: 6000; onTriggered: service.lastError = "" }

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
    Quickshell.execDetached(["omarchy-shell", "shell", "summon", service.pluginId, "{}"])
  }

  // ------------------------------------------------------------ reply card
  readonly property bool fullscreenFocused: !!(Hyprland.focusedWorkspace && Hyprland.focusedWorkspace.hasFullscreen)

  function onReply(msg) {
    if (msg.phase === "start" || msg.phase === "delta") {
      service.cardVoice = msg.voice !== false
      service.cardTextFull = msg.text || ""
      cardHide.stop()
      if (st.replyCard !== false && !service.fullscreenFocused) service.cardOpen = true
    } else if (msg.phase === "done" || msg.phase === "audio_done") {
      if (msg.text) service.cardTextFull = msg.text
      if (msg.phase === "done" && msg.voice === false) {
        service.cardVoice = false
        if (st.replyCard !== false && !service.fullscreenFocused) service.cardOpen = true
      }
      // keep it up while the agent is still audibly speaking
      if (!service.speaking || msg.voice === false) {
        cardHide.interval = Model.cardHoldMs(service.cardTextFull, service.cardVoice)
        cardHide.restart()
      }
    }
  }

  onSpeakingChanged: {
    if (speaking) { cardHide.stop(); return }
    service.bands = Model.clampBands([], 24)
    if (cardOpen) {
      cardHide.interval = Model.cardHoldMs(service.cardTextFull, service.cardVoice)
      cardHide.restart()
    }
  }

  Timer { id: cardHide; repeat: false; onTriggered: service.cardOpen = false }

  function dismissCard() { cardHide.stop(); service.cardOpen = false }

  ReplyCard {
    service: service
  }
}

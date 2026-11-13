import QtQuick
import Quickshell
import Quickshell.Hyprland

import "Model.js" as Model

// One instance per shell (bar widgets exist once per monitor): the backend link
// (Backend.qml), the shared state the widgets render, and the top-right reply card.
Backend {
  id: service

  property var shell: null
  property var manifest: null

  // reply card
  property bool cardOpen: false
  property string cardTextFull: ""
  property bool cardVoice: true

  onReply: function(msg) { service.onReplyEvent(msg) }

  // ------------------------------------------------------------ reply card
  readonly property bool fullscreenFocused: !!(Hyprland.focusedWorkspace && Hyprland.focusedWorkspace.hasFullscreen)

  function onReplyEvent(msg) {
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

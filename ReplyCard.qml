import QtQuick 6.11 // Qt 6.12's own Color would hide Omarchy's
import Quickshell
import Quickshell.Hyprland
import Quickshell.Wayland
import qs.Commons
import qs.Ui

import "Model.js" as Model

// The quick top-right card shown while the agent answers: agent icon, a live
// visualizer of its voice, and the reply text. Not a notification: our own
// layer surface, no keyboard focus, input only over the card itself.
PanelWindow {
  id: win

  required property var service

  readonly property var targetScreen: {
    var name = Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
    var screens = Quickshell.screens
    for (var i = 0; i < screens.length; i++) if (screens[i].name === name) return screens[i]
    return screens.length ? screens[0] : null
  }
  readonly property real topClearance: {
    var bar = service.shell ? service.shell.bar : null
    if (bar && !bar.barHidden && typeof bar.barSizeFor === "function" && targetScreen)
      return Math.max(0, Number(bar.barSizeFor(String(targetScreen.name)))) + Style.gapsOut
    return Style.space(40)
  }

  screen: targetScreen
  visible: service.cardOpen || card.opacity > 0
  color: "transparent"
  anchors { top: true; bottom: true; left: true; right: true }
  exclusionMode: ExclusionMode.Ignore
  WlrLayershell.namespace: "codex-voice-agent-reply"
  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
  mask: Region { item: card }

  readonly property color fg: Color.popups.text
  readonly property color accent: Color.accent

  BorderSurface {
    id: card
    width: Style.space(380)
    height: content.implicitHeight + Style.space(28)
    anchors.top: parent.top
    anchors.right: parent.right
    anchors.topMargin: win.topClearance
    anchors.rightMargin: Style.gapsOut
    color: Util.alpha(Color.popups.background, 0.97)
    borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
    radius: Style.cornerRadius
    opacity: win.service.cardOpen ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    transform: Translate {
      x: win.service.cardOpen ? 0 : Style.space(16)
      Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    }

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      cursorShape: Qt.PointingHandCursor
      onClicked: function(mouse) {
        win.service.dismissCard()
        if (mouse.button === Qt.LeftButton) win.service.openPanel()
      }
    }

    Row {
      id: content
      x: Style.space(14)
      y: Style.space(14)
      width: card.width - Style.space(28)
      spacing: Style.space(12)

      AgentIcon {
        id: icon
        size: Style.space(34)
        micState: win.service.micState
        working: win.service.working
        speaking: win.service.speaking
        foreground: win.fg
        accent: win.accent
        background: Color.popups.background
        bands: win.service.bands
      }

      Column {
        width: content.width - icon.width - content.spacing
        spacing: Style.space(8)

        Row {
          width: parent.width
          spacing: Style.space(8)
          Text {
            id: who
            text: win.service.cardVoice ? "Codex Voice Agent" : "Codex"
            color: win.fg
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            font.bold: true
            anchors.verticalCenter: parent.verticalCenter
          }
          Visualizer {
            width: parent.width - who.width - parent.spacing
            height: Style.space(18)
            anchors.verticalCenter: parent.verticalCenter
            bands: win.service.bands
            active: win.service.speaking
            color: win.accent
          }
        }

        Text {
          width: parent.width
          text: Model.cardText(win.service.cardTextFull, 260) || "…"
          // Codex output can be steered by what it reads: plain text, so an <img> in an answer
          // never makes the desktop fetch a URL when the card appears.
          textFormat: Text.PlainText
          color: win.fg
          wrapMode: Text.Wrap
          maximumLineCount: 5
          elide: Text.ElideLeft
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          lineHeight: 1.15
        }
      }
    }
  }
}

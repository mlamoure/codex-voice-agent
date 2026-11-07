import QtQuick 6.11 // Qt 6.12's own Color would hide Omarchy's
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui

import "Model.js" as Model

// Bar icon + popup. All state lives in Service.qml (one instance per shell);
// this widget exists once per monitor and only renders it.
Panel {
  id: root
  manageIpc: false

  readonly property var svc: bar && bar.shell && typeof bar.shell.serviceFor === "function"
                             ? bar.shell.serviceFor(Model.PLUGIN_ID) : null
  readonly property bool connected: !!(svc && svc.connected)
  readonly property var st: svc ? svc.st : ({})
  readonly property string micState: svc ? svc.micState : "off"
  readonly property color fg: Color.popups.text
  readonly property color dim: Qt.darker(fg, 1.55)
  readonly property color accent: Color.accent

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    if (svc) svc.refreshInputs()
    Qt.callLater(function() { history.positionViewAtEnd() })
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: Component { Item {} }
    tooltipText: Model.tooltip(root.connected, root.st)

    AgentIcon {
      x: (button.width - width) / 2
      y: (button.height - height) / 2
      size: Style.bar.iconCanvas + Style.space(2)
      micState: root.micState
      working: !!(root.svc && root.svc.working)
      speaking: !!(root.svc && root.svc.speaking)
      foreground: root.bar ? root.bar.barForeground : Color.foreground
      accent: root.accent
      background: root.bar && root.bar.background !== undefined ? root.bar.background : Color.bar.background
      bands: root.svc ? root.svc.bands : []
    }

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton || buttonCode === Qt.RightButton) {
        if (root.svc) root.svc.toggleMute()   // quick mute without opening the panel
      } else {
        root.toggle()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    // Open straight into the "Ask Codex" field; Escape there closes the panel.
    focusTarget: root.connected ? input : keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: modelDrop.popupOpen || effortDrop.popupOpen || micDrop.popupOpen || input.activeFocus
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var key = t.toLowerCase()
        if (key === "m" && root.svc) root.svc.toggleMute()
        else if (key === "/" || key === "i") input.forceActiveFocus()
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(10)

        // ---------------------------------------------------- header
        Row {
          width: parent.width
          spacing: Style.space(12)

          AgentIcon {
            id: heroIcon
            size: Style.space(40)
            micState: root.micState
            working: !!(root.svc && root.svc.working)
            speaking: !!(root.svc && root.svc.speaking)
            foreground: root.fg
            accent: root.accent
            background: Color.popups.background
            bands: root.svc ? root.svc.bands : []
          }

          Column {
            width: parent.width - heroIcon.width - muteButton.width - parent.spacing * 2
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)
            Text {
              text: "Codex Voice Agent"
              color: root.fg
              font.family: Style.font.family
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            Text {
              width: parent.width
              text: Model.statusLine(root.connected, root.st)
              color: root.micState === "listening" ? root.accent : root.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
            }
          }

          Button {
            id: muteButton
            anchors.verticalCenter: parent.verticalCenter
            visible: root.connected
            bordered: true
            selected: root.micState === "listening"
            iconText: root.micState === "listening" ? Model.GLYPH_MIC : Model.GLYPH_MIC_OFF
            text: root.micState === "listening" ? "Mute" : "Unmute"
            tooltipText: "Toggle listening (your mute key, a right-click on the bar icon, or M here)"
            onClicked: root.svc.toggleMute()
          }
          Button {
            anchors.verticalCenter: parent.verticalCenter
            visible: !root.connected
            bordered: true
            text: "Start"
            onClicked: root.svc && root.svc.startBackend()
          }
        }

        // ---------------------------------------------------- model + thinking
        Row {
          width: parent.width
          spacing: Style.space(10)
          visible: root.connected
          Dropdown {
            id: modelDrop
            width: (parent.width - parent.spacing) * 0.6
            label: "Model"
            options: Model.modelOptions(root.st)
            value: root.st.model || ""
            onChanged: function(v) { if (v !== root.st.model) root.svc.setModel(v) }
          }
          Dropdown {
            id: effortDrop
            width: (parent.width - parent.spacing) * 0.4
            label: "Thinking"
            options: Model.effortOptions(root.st)
            value: root.st.effort || ""
            onChanged: function(v) { if (v !== root.st.effort) root.svc.setEffort(v) }
          }
        }

        // ---------------------------------------------------- microphone
        Dropdown {
          id: micDrop
          width: parent.width
          visible: root.connected
          label: "Microphone"
          options: Model.inputOptions(root.svc ? root.svc.inputInfo : null)
          value: root.st.input || ""
          onChanged: function(v) { if (v !== (root.st.input || "")) root.svc.setInput(v) }
        }

        // ---------------------------------------------------- warnings / errors
        Repeater {
          model: (root.st.warnings || []).concat(root.svc && root.svc.lastError ? [root.svc.lastError] : [])
          Text {
            required property var modelData
            width: column.width
            text: Model.GLYPH_WARN + "  " + modelData
            textFormat: Text.PlainText  // errors can carry Codex text
            color: Color.urgent
            wrapMode: Text.Wrap
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
        }

        // ---------------------------------------------------- update available
        Column {
          id: updateNotice
          width: parent.width
          spacing: Style.space(4)
          visible: Model.updateText(root.st) !== ""
          Text {
            width: parent.width
            text: Model.GLYPH_UPDATE + "  " + Model.updateText(root.st)
            textFormat: Text.PlainText
            color: root.accent
            wrapMode: Text.Wrap
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          Row {
            spacing: Style.space(8)
            Button {
              bordered: true
              text: "Release notes"
              onClicked: {
                var url = root.st.update && root.st.update.url
                if (Model.isSafeLink(url)) Qt.openUrlExternally(url)
              }
            }
            Button {
              bordered: true
              text: "How to update"
              onClicked: Qt.openUrlExternally(Model.UPDATE_HELP_URL)
            }
          }
        }

        PanelSeparator { width: parent.width }

        // ---------------------------------------------------- history
        ListView {
          id: history
          width: parent.width
          // the panel has a maximum height: the update notice takes its room from the history
          height: Style.space(360) - (updateNotice.visible ? updateNotice.height + column.spacing : 0)
          clip: true
          spacing: Style.space(8)
          boundsBehavior: Flickable.StopAtBounds
          model: root.svc ? root.svc.history : []
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
          property bool pinnedToEnd: true
          onMovementEnded: pinnedToEnd = atYEnd
          onCountChanged: if (pinnedToEnd) Qt.callLater(positionViewAtEnd)

          Text {
            anchors.centerIn: parent
            visible: history.count === 0
            text: root.connected ? "Nothing yet. Unmute and talk, or type below." : "The backend is not running."
            color: root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }

          delegate: Column {
            id: entry
            required property var modelData
            readonly property var style: Model.entryStyle(modelData.kind)
            width: history.width - Style.space(10)
            spacing: Style.space(2)

            Text {
              visible: entry.style.who !== ""
              text: entry.style.who
              color: entry.style.role === "user" ? root.accent : root.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: entry.style.role === "user" || entry.style.role === "agent"
            }
            Text {
              width: parent.width
              readonly property var body: Model.entryBody(entry.modelData)
              text: body.text
              textFormat: body.rich ? Text.RichText : Text.PlainText
              wrapMode: Text.Wrap
              maximumLineCount: entry.style.role === "tool" ? 3 : 40
              elide: Text.ElideRight
              color: entry.style.role === "note" || entry.style.role === "tool" || entry.style.role === "notice"
                     ? root.dim : (entry.style.role === "review" && entry.modelData.status === "denied" ? Color.urgent : root.fg)
              font.family: entry.style.mono ? "monospace" : Style.font.family
              font.pixelSize: entry.style.role === "tool" || entry.style.role === "notice" ? Style.font.caption : Style.font.body
              font.italic: entry.style.role === "notice"
              onLinkActivated: function(link) { if (Model.isSafeLink(link)) Qt.openUrlExternally(link) }
            }
            Button {
              visible: entry.modelData.kind === "review" && entry.modelData.status === "denied"
              bordered: true
              text: "Approve anyway"
              tooltipText: entry.modelData.detail && entry.modelData.detail.command ? entry.modelData.detail.command : ""
              onClicked: root.svc.approve(entry.modelData.detail.reviewId)
            }
          }
        }

        // ---------------------------------------------------- type a request
        Row {
          width: parent.width
          spacing: Style.space(8)
          visible: root.connected
          TextField {
            id: input
            width: parent.width - sendButton.width - parent.spacing
            placeholderText: "Ask Codex…"
            maximumLength: 20000
            onAccepted: if (root.svc.sendText(text)) text = ""
            Keys.onEscapePressed: root.close()
          }
          Button {
            id: sendButton
            bordered: true
            iconText: Model.GLYPH_SEND
            tooltipText: "Send to Codex"
            onClicked: if (root.svc.sendText(input.text)) input.text = ""
          }
        }

        // ---------------------------------------------------- footer
        Row {
          width: parent.width
          spacing: Style.space(8)
          visible: root.connected
          Button {
            iconText: Model.GLYPH_NEW
            text: "New session"
            onClicked: root.svc.newSession()
          }
          Button {
            iconText: Model.GLYPH_TERMINAL
            text: "Open in terminal"
            tooltipText: "codex resume this session"
            onClicked: { root.svc.openTerminal(); root.close() }
          }
          Item { width: 1; height: 1 }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "Reply card"
            color: root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          ToggleSwitch {
            anchors.verticalCenter: parent.verticalCenter
            checked: root.st.replyCard !== false
            onToggled: root.svc.setReplyCard(!checked)
          }
        }
      }
    }
  }
}

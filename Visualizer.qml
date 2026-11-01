import QtQuick 6.11

// Bars driven by the backend's frequency bands of the agent's voice (24 bands,
// 0..1, ~30 fps). When idle the bars settle to a thin baseline.
Item {
  id: root
  property var bands: []
  property bool active: false
  property color color: "white"
  property int count: 24

  Row {
    anchors.fill: parent
    spacing: Math.max(1, Math.round(root.width / root.count * 0.25))

    Repeater {
      model: root.count
      Rectangle {
        required property int index
        readonly property real level: root.active && root.bands.length > index ? root.bands[index] : 0
        width: Math.max(1, (root.width - (root.count - 1) * parent.spacing) / root.count)
        height: Math.max(2, root.height * (0.08 + 0.92 * level))
        anchors.verticalCenter: parent.verticalCenter
        radius: width / 2
        color: root.color
        opacity: root.active ? 0.55 + 0.45 * level : 0.25
        Behavior on height { NumberAnimation { duration: 70 } }
      }
    }
  }
}

import QtQuick 6.11
import QtQuick.Shapes
import qs.Commons

// The agent's face: code braces for the sides of the head, two eyes inside.
// It must always answer one question at a glance: is it listening?
//   listening: accent colour, eyes open (with the odd blink)
//   muted:     dimmed, eyes closed
//   off:       grey, eyes closed
// Layered on top: working makes the eyes glance from side to side; speaking makes
// them bounce with the agent's real voice (the backend's frequency bands).
// Drawn on a 24x24 grid and scaled to `size`.
Item {
  id: root
  property real size: 20
  property string micState: "off"
  property bool working: false
  property bool speaking: false
  property var bands: []
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color background: Color.background  // kept for callers; unused
  property string fontFamily: Style.font.family  // kept for callers; unused

  implicitWidth: size
  implicitHeight: size
  width: size
  height: size

  readonly property bool awake: micState === "listening"
  readonly property color tint: awake ? accent
                                : micState === "muted" ? Qt.darker(foreground, 1.45)
                                : Qt.darker(foreground, 2.4)
  readonly property real k: size / 24

  // voice level for each eye: left follows the low voice bands, right the higher ones
  function eyeLevel(which) {
    if (!speaking || !bands || bands.length < 24) return 0
    var from = which === 0 ? 3 : 9, to = which === 0 ? 9 : 16, sum = 0
    for (var b = from; b < to; b++) sum += Number(bands[b]) || 0
    return Math.min(1, 1.2 * sum / (to - from))
  }

  Item {
    id: canvas
    width: 24
    height: 24
    scale: root.k
    transformOrigin: Item.TopLeft

    // braces: the sides of the face
    Shape {
      anchors.fill: parent
      preferredRendererType: Shape.CurveRenderer
      ShapePath {
        strokeColor: root.tint
        strokeWidth: 2
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        joinStyle: ShapePath.RoundJoin
        PathSvg {
          path: "M7.5 4.5c-1.9 0-2.6.9-2.6 2.6v2.4c0 1.4-.6 2.3-2 2.5 1.4.2 2 1.1 2 2.5v2.4c0 1.7.7 2.6 2.6 2.6"
              + "M16.5 4.5c1.9 0 2.6.9 2.6 2.6v2.4c0 1.4.6 2.3 2 2.5-1.4.2-2 1.1-2 2.5v2.4c0 1.7-.7 2.6-2.6 2.6"
        }
      }
    }

    // closed eyes (muted / off)
    Shape {
      anchors.fill: parent
      visible: !root.awake
      preferredRendererType: Shape.CurveRenderer
      ShapePath {
        strokeColor: root.tint
        strokeWidth: 1.8
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        PathSvg { path: "M8.55 12.1Q9.6 13.2 10.65 12.1M13.35 12.1Q14.4 13.2 15.45 12.1" }
      }
    }

    // open eyes
    Item {
      id: eyes
      anchors.fill: parent
      visible: root.awake

      // working: glance from side to side
      SequentialAnimation on x {
        running: root.awake && root.working
        loops: Animation.Infinite
        onRunningChanged: if (!running) eyes.x = 0
        NumberAnimation { to: -1.4; duration: 450; easing.type: Easing.InOutSine }
        PauseAnimation { duration: 250 }
        NumberAnimation { to: 1.4; duration: 600; easing.type: Easing.InOutSine }
        PauseAnimation { duration: 250 }
        NumberAnimation { to: 0; duration: 450; easing.type: Easing.InOutSine }
      }

      Repeater {
        model: 2
        Rectangle {
          id: eye
          required property int index
          readonly property real centreX: index === 0 ? 9.6 : 14.4
          // speaking: height follows the voice; otherwise a steady 5.2 with blinks
          readonly property real voiceHeight: 3.4 + 3.8 * root.eyeLevel(index)
          property real openness: 1
          width: 2.1
          height: Math.max(0.5, (root.speaking ? voiceHeight : 5.2) * openness)
          x: centreX - width / 2
          y: 12 - height / 2
          radius: width / 2
          color: root.tint
          Behavior on height { enabled: root.speaking; NumberAnimation { duration: 60 } }

          // listening: an occasional blink (both eyes together)
          SequentialAnimation on openness {
            running: root.awake && !root.speaking && !root.working
            loops: Animation.Infinite
            onRunningChanged: if (!running) eye.openness = 1
            PauseAnimation { duration: 3600 }
            NumberAnimation { to: 0.12; duration: 80; easing.type: Easing.InQuad }
            NumberAnimation { to: 1; duration: 120; easing.type: Easing.OutQuad }
          }
        }
      }
    }
  }
}

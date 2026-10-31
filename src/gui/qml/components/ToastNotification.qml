import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Bottom-right notifications. Reading time scales with the text; hovering pauses the countdown;
// warnings and errors stay longer and are never pushed away by a later, less important toast.
Item {
    id: root
    width: Math.min(root.wide ? 460 : 380, parent.width - 32)
    height: toastCard.height
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    anchors.margins: 20
    visible: opacity > 0
    opacity: 0

    property string message: ""
    property string toastType: "info" // "success", "error", "warning", "info"
    property var queue: []
    readonly property bool urgent: toastType === "error" || toastType === "warning"
    readonly property bool wide: message.length > 140

    // Milliseconds to read `text`: about 18 characters per second plus a base, longer for problems.
    function readingTime(text, type) {
        var urgentType = type === "error" || type === "warning";
        var base = urgentType ? 8000 : 4000;
        var perChar = urgentType ? 70 : 45;
        return Math.min(urgentType ? 30000 : 15000, base + text.length * perChar);
    }

    function priority(type) {
        return type === "error" ? 3 : (type === "warning" ? 2 : 1);
    }

    function show(msg, type) {
        var kind = type || "info";
        if (opacity > 0 && priority(kind) < priority(toastType) && dismissTimer.running) {
            // Do not bury a problem the user may still be reading; show this one right after it.
            var pending = queue.slice(-3);
            pending.push({ msg: msg, type: kind });
            queue = pending;
            return;
        }
        present(msg, kind);
    }

    function present(msg, kind) {
        message = msg;
        toastType = kind;
        dismissTimer.interval = readingTime(msg, kind);
        root.opacity = 1.0;
        toastCard.y = 0;
        dismissTimer.restart();
    }

    function dismiss() {
        dismissTimer.stop();
        root.opacity = 0.0;
        toastCard.y = 20;
        if (queue.length > 0) {
            var next = queue[0];
            queue = queue.slice(1);
            nextTimer.next = next;
            nextTimer.start();
        }
    }

    Timer {
        id: nextTimer
        property var next: null
        interval: 300
        onTriggered: if (next) root.present(next.msg, next.type)
    }

    Timer {
        id: dismissTimer
        interval: 4000
        onTriggered: root.dismiss()
    }

    Rectangle {
        id: toastCard
        width: parent.width
        implicitHeight: contentRow.implicitHeight + 28
        radius: Theme.radiusCard
        clip: true
        color: Theme.surfaceCard
        border.width: root.urgent ? 2 : 1
        border.color: {
            if (toastType === "success") return Theme.statusSuccess;
            if (toastType === "error") return Theme.statusDanger;
            if (toastType === "warning") return Theme.statusWarning;
            return Theme.accentSolid;
        }

        y: 20
        Behavior on y { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: 200 } }

        // Tinted wash and a bold edge make problems stand out from routine messages.
        Rectangle {
            anchors.fill: parent
            color: toastCard.border.color
            opacity: root.urgent ? 0.10 : 0.04
        }
        Rectangle {
            width: 5
            height: parent.height
            color: toastCard.border.color
        }

        HoverHandler {
            id: hover
            onHoveredChanged: {
                if (hovered) dismissTimer.stop();
                else if (root.opacity > 0) dismissTimer.restart();
            }
        }

        RowLayout {
            id: contentRow
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: 20
            anchors.rightMargin: 14
            anchors.topMargin: 14
            spacing: 12

            Text {
                Layout.alignment: Qt.AlignTop
                text: {
                    if (toastType === "success") return "✅";
                    if (toastType === "error") return "❌";
                    if (toastType === "warning") return "⚠️";
                    return "ℹ️";
                }
                font.pixelSize: root.urgent ? 22 : 18
            }

            Text {
                Layout.fillWidth: true
                text: root.message
                color: Theme.textPrimary
                font.pixelSize: root.urgent ? 14 : 13
                font.weight: root.urgent ? Font.Medium : Font.Normal
                wrapMode: Text.WordWrap
                lineHeight: 1.25
            }

            MouseArea {
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 24
                Layout.preferredHeight: 24
                cursorShape: Qt.PointingHandCursor
                onClicked: root.dismiss()
                Text {
                    anchors.centerIn: parent
                    text: "✕"
                    color: Theme.textMuted
                    font.pixelSize: 13
                }
            }
        }
    }
}

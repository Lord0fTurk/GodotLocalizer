import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: root

    property bool active: false
    property string iconGlyph: ""

    implicitHeight: Theme.heightBadge
    implicitWidth: tagContent.implicitWidth + leftPadding + rightPadding
    topInset: 0
    bottomInset: 0
    leftInset: 0
    rightInset: 0
    padding: 0
    leftPadding: 9
    rightPadding: 9
    scale: down ? 0.96 : 1.0

    background: Rectangle {
        radius: Theme.radiusBadge
        color: {
            if (root.active) return Theme.accentSubtle;
            return root.hovered ? Theme.surfaceHover : "transparent";
        }
        border.color: {
            if (root.active) return Theme.accentSolid;
            return root.hovered ? Theme.borderFocus : Theme.borderControl;
        }
        border.width: 1

        Behavior on color { ColorAnimation { duration: 120 } }
        Behavior on border.color { ColorAnimation { duration: 120 } }
    }

    contentItem: Item {
        implicitWidth: tagContent.implicitWidth
        implicitHeight: tagContent.implicitHeight

    RowLayout {
        id: tagContent
        anchors.centerIn: parent
        spacing: 4

        Icon {
            Layout.alignment: Qt.AlignVCenter
            visible: root.iconGlyph !== ""
            glyph: root.iconGlyph
            size: 11
            color: root.active 
                   ? (Theme.isDark ? Theme.accentHover : Theme.accentSolid) 
                   : (root.hovered ? Theme.textPrimary : Theme.textMuted)
        }

        Label {
            Layout.alignment: Qt.AlignVCenter
            text: root.text
            font.pixelSize: 11
            font.bold: root.active
            color: root.active 
                   ? (Theme.isDark ? Theme.accentHover : Theme.accentSolid) 
                   : (root.hovered ? Theme.textPrimary : Theme.textSecondary)
        }
    }
    }
}

import QtQuick 2.15
import ".."

Rectangle {
    id: badge

    property string text: ""
    property string variant: "neutral" // 'neutral' | 'success' | 'warning' | 'danger' | 'accent'

    implicitHeight: 24
    implicitWidth: badgeText.implicitWidth + 16
    radius: Theme.radiusBadge

    color: {
        if (variant === "success") return Theme.statusSuccessSubtle
        if (variant === "warning") return Theme.statusWarningSubtle
        if (variant === "danger") return Theme.statusDangerSubtle
        if (variant === "accent") return Theme.accentSubtle
        return Theme.surfaceControl
    }

    border.color: {
        if (variant === "success") return Theme.statusSuccessBorder
        if (variant === "warning") return Theme.statusWarningBorder
        if (variant === "danger") return Theme.statusDangerBorder
        if (variant === "accent") return Theme.accentBorder
        return Theme.borderControl
    }
    border.width: 1

    Text {
        id: badgeText
        anchors.centerIn: parent
        text: badge.text
        font.pixelSize: 11
        font.weight: Font.Medium
        color: {
            if (variant === "success") return Theme.statusSuccess
            if (variant === "warning") return Theme.statusWarning
            if (variant === "danger") return Theme.statusDanger
            if (variant === "accent") return Theme.accentHover
            return Theme.textSecondary
        }
    }
}

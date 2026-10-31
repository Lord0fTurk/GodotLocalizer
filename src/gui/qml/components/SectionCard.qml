import QtQuick 2.15
import ".."

Rectangle {
    id: card

    radius: Theme.radiusCard
    color: Theme.surfaceCard
    border.color: Theme.borderCard
    border.width: 1

    property alias headerTitle: titleText.text
    property alias headerSubtitle: subtitleText.text
    default property alias content: innerContainer.data

    Column {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        Column {
            width: parent.width
            spacing: 4
            visible: titleText.text.length > 0

            Text {
                id: titleText
                font.pixelSize: 16
                font.weight: Font.DemiBold
                color: Theme.textPrimary
            }

            Text {
                id: subtitleText
                font.pixelSize: 13
                color: Theme.textSecondary
                visible: text.length > 0
                wrapMode: Text.WordWrap
                width: parent.width
            }
        }

        Item {
            id: innerContainer
            width: parent.width
            height: parent.height - (titleText.text.length > 0 ? 44 : 0)
        }
    }
}

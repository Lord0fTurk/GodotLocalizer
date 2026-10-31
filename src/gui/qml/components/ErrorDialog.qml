import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import "../components"

// A problem the user has to read and act on (a game that cannot be opened): too important for a toast.
Popup {
    id: root
    objectName: "errorDialog"
    modal: true
    focus: true
    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    width: Math.min(Overlay.overlay ? Overlay.overlay.width - 48 : 540, 540)
    padding: 0
    closePolicy: Popup.CloseOnEscape

    property string title: ""
    property string message: ""

    function t(key) {
        return (localeManager && localeManager.currentLanguage, localeManager ? localeManager.t(key) : "");
    }

    Connections {
        target: appBackend
        function onBlockingError(heading, text) {
            root.title = heading;
            root.message = text;
            root.open();
        }
    }

    background: Rectangle {
        radius: Theme.radiusCard
        color: Theme.surfaceCard
        border.color: Theme.statusDanger
        border.width: 2
    }

    contentItem: ColumnLayout {
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.bottomMargin: 0
            spacing: 12

            Text {
                text: "⚠️"
                font.pixelSize: 26
            }
            Text {
                Layout.fillWidth: true
                text: root.title
                font.pixelSize: 18
                font.bold: true
                color: Theme.textPrimary
                wrapMode: Text.WordWrap
            }
        }

        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            text: root.message
            font.pixelSize: 13
            lineHeight: 1.3
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.topMargin: 6
            spacing: 10

            Item { Layout.fillWidth: true }

            PrimaryButton {
                text: root.t("common.ok")
                onClicked: root.close()
            }
        }
    }
}

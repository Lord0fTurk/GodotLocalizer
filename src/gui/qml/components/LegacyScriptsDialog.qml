import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import "../components"

// Shown at translation start when scripts use a bytecode format only GDRE Tools can read.
Popup {
    id: root
    modal: true
    focus: true
    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    width: Math.min(Overlay.overlay ? Overlay.overlay.width - 48 : 520, 520)
    padding: 0
    closePolicy: Popup.NoAutoClose

    property int count: 0

    function t(key) {
        return (localeManager && localeManager.currentLanguage, localeManager ? localeManager.t(key) : "");
    }

    Connections {
        target: appBackend
        function onLegacyScriptsFound(n) {
            root.count = n;
            root.open();
        }
    }

    background: Rectangle {
        radius: Theme.radiusCard
        color: Theme.surfaceCard
        border.color: Theme.borderCard
        border.width: 1
    }

    contentItem: ColumnLayout {
        spacing: 14

        Text {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.bottomMargin: 0
            text: root.t("settings.legacy_dialog_title")
            font.pixelSize: 17
            font.bold: true
            color: Theme.textPrimary
            wrapMode: Text.WordWrap
        }

        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            text: root.t("settings.legacy_dialog_body").replace("{count}", root.count)
            font.pixelSize: 12
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.topMargin: 6
            spacing: 10

            Item { Layout.fillWidth: true }

            SecondaryButton {
                text: root.t("settings.legacy_dialog_skip")
                onClicked: {
                    root.close();
                    if (appBackend) appBackend.answerLegacyScripts(false);
                }
            }
            PrimaryButton {
                text: root.t("settings.legacy_dialog_download")
                onClicked: {
                    root.close();
                    if (appBackend) appBackend.answerLegacyScripts(true);
                }
            }
        }
    }
}

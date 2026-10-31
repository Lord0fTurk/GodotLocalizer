import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import "../components"

// Asks for explicit approval before GDRE Tools is downloaded; shows exactly what will be fetched.
Popup {
    id: root
    modal: true
    focus: true
    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    width: Math.min(Overlay.overlay ? Overlay.overlay.width - 48 : 520, 560)
    padding: 0
    closePolicy: downloading ? Popup.NoAutoClose : (Popup.CloseOnEscape | Popup.CloseOnPressOutside)

    property var release: ({})
    property bool downloading: false
    property real progress: 0

    function t(key) {
        return (localeManager && localeManager.currentLanguage, localeManager ? localeManager.t(key) : "");
    }

    Connections {
        target: appBackend
        function onGdreReleaseFound(info) {
            root.release = info;
            root.progress = 0;
            root.downloading = false;
            root.open();
        }
        function onGdreDownloadProgress(value) { root.progress = value; }
        function onGdreDownloadFinished(ok) {
            root.downloading = false;
            root.close();
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
            text: root.t("settings.gdre_dialog_title") + "  (" + (root.release.tag || "") + ")"
            font.pixelSize: 17
            font.bold: true
            color: Theme.textPrimary
            wrapMode: Text.WordWrap
        }

        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            text: root.t("settings.gdre_dialog_hint")
            font.pixelSize: 12
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            columns: 2
            columnSpacing: 12
            rowSpacing: 6

            Repeater {
                model: [
                    { label: "settings.gdre_dialog_file", value: root.release.file || "" },
                    { label: "settings.gdre_dialog_size", value: (root.release.sizeMb || 0) + " MB" },
                    { label: "settings.gdre_dialog_source", value: root.release.source || "" },
                    { label: "settings.gdre_dialog_sha", value: root.release.sha256 || "" },
                    { label: "settings.gdre_dialog_license", value: root.release.license || "" }
                ]
                delegate: Item {
                    Layout.columnSpan: 2
                    Layout.fillWidth: true
                    implicitHeight: row.implicitHeight
                    RowLayout {
                        id: row
                        anchors.left: parent.left
                        anchors.right: parent.right
                        spacing: 12
                        Text {
                            Layout.preferredWidth: 80
                            Layout.alignment: Qt.AlignTop
                            text: root.t(modelData.label)
                            font.pixelSize: 12
                            font.bold: true
                            color: Theme.textSecondary
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.value
                            font.pixelSize: 12
                            color: Theme.textPrimary
                            wrapMode: Text.WrapAnywhere
                        }
                    }
                }
            }
        }

        ProgressBar {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            visible: root.downloading
            from: 0
            to: 1
            value: root.progress
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.topMargin: 6
            spacing: 10

            Item { Layout.fillWidth: true }

            SecondaryButton {
                text: root.t("settings.gdre_dialog_cancel")
                enabled: !root.downloading
                onClicked: root.close()
            }
            PrimaryButton {
                text: root.t("settings.gdre_dialog_confirm")
                busy: root.downloading
                enabled: !root.downloading
                onClicked: {
                    root.downloading = true;
                    if (appBackend) appBackend.downloadGdre();
                }
            }
        }
    }
}

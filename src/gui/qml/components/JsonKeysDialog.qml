import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import "../components"

// Shown at translation start when the package has JSON fields that look like player-facing text.
Popup {
    id: root
    modal: true
    focus: true
    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    width: Math.min(Overlay.overlay ? Overlay.overlay.width - 48 : 640, 640)
    padding: 0
    closePolicy: Popup.NoAutoClose

    property var candidates: []
    property var checked: ({})

    function t(key) {
        return (localeManager && localeManager.currentLanguage, localeManager ? localeManager.t(key) : "");
    }

    function selectedKeys() {
        var out = [];
        for (var i = 0; i < candidates.length; i++) {
            if (checked[candidates[i].key]) out.push(candidates[i].key);
        }
        return out;
    }

    Connections {
        target: appBackend
        function onJsonKeysSuggested(list) {
            var state = {};
            for (var i = 0; i < list.length; i++) state[list[i].key] = list[i].selected;
            root.checked = state;
            root.candidates = list;
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
        spacing: 12

        Text {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.bottomMargin: 0
            text: root.t("settings.json_dialog_title")
            font.pixelSize: 17
            font.bold: true
            color: Theme.textPrimary
            wrapMode: Text.WordWrap
        }

        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            text: root.t("settings.json_dialog_hint")
            font.pixelSize: 12
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 12
            Layout.preferredHeight: Math.min(contentHeight, 360)
            clip: true
            model: root.candidates
            delegate: ItemDelegate {
                width: list.width
                implicitHeight: 92
                onClicked: box.toggle()

                contentItem: RowLayout {
                    spacing: 10
                    CheckBox {
                        id: box
                        checked: root.checked[modelData.key] === true
                        onToggled: {
                            var state = root.checked;
                            state[modelData.key] = checked;
                            root.checked = state;
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Text {
                            Layout.fillWidth: true
                            text: root.t("settings.json_reason_" + modelData.reason)
                            font.pixelSize: 13
                            font.bold: true
                            color: Theme.textPrimary
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "\u201c" + modelData.sample + "\u201d"
                            font.pixelSize: 12
                            font.italic: true
                            color: Theme.textSecondary
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.key + "  \u00b7  " + root.t("settings.json_dialog_count").replace("{count}", modelData.count)
                            font.pixelSize: 11
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.topMargin: 6
            spacing: 10

            Item { Layout.fillWidth: true }

            SecondaryButton {
                text: root.t("settings.json_dialog_skip")
                onClicked: {
                    root.close();
                    if (appBackend) appBackend.skipJsonKeys();
                }
            }
            PrimaryButton {
                text: root.t("settings.json_dialog_use")
                enabled: root.selectedKeys().length > 0
                onClicked: {
                    var keys = root.selectedKeys();
                    root.close();
                    if (appBackend) appBackend.applyJsonKeys(keys);
                }
            }
        }
    }
}

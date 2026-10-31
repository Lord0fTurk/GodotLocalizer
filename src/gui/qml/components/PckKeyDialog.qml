import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "../components"

// Shown when a selected game turns out to be encrypted: asks for the key instead of failing.
Popup {
    id: root
    modal: true
    focus: true
    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    width: Math.min(Overlay.overlay ? Overlay.overlay.width - 48 : 520, 520)
    padding: 0
    closePolicy: Popup.CloseOnEscape

    property string reason: "missing"

    function t(key) {
        return (localeManager && localeManager.currentLanguage, localeManager ? localeManager.t(key) : "");
    }

    Connections {
        target: appBackend
        function onPckKeyNeeded(why) {
            root.reason = why;
            keyField.text = "";
            root.open();
            keyField.forceActiveFocus();
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
            text: root.t("settings.pck_dialog_title")
            font.pixelSize: 17
            font.bold: true
            color: Theme.textPrimary
        }

        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            text: root.t("settings.pck_dialog_" + root.reason)
            font.pixelSize: 12
            color: root.reason === "missing" ? Theme.textSecondary : Theme.statusDanger
            wrapMode: Text.WordWrap
        }

        Basic.TextField {
            id: keyField
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            Layout.preferredHeight: Theme.heightInput
            leftPadding: 12
            rightPadding: 12
            verticalAlignment: TextInput.AlignVCenter
            echoMode: TextInput.Password
            placeholderText: "64 hex"
            placeholderTextColor: Theme.textMuted
            color: Theme.textPrimary
            selectByMouse: true
            background: Rectangle {
                radius: Theme.radiusControl
                color: Theme.surfaceControl
                border.color: keyField.activeFocus ? Theme.borderFocus : Theme.borderControl
                border.width: 1
            }
            onAccepted: okButton.clicked()
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.topMargin: 6
            spacing: 10

            Item { Layout.fillWidth: true }

            SecondaryButton {
                text: root.t("settings.pck_dialog_cancel")
                onClicked: root.close()
            }
            PrimaryButton {
                id: okButton
                text: root.t("settings.pck_dialog_ok")
                enabled: keyField.text.trim().length > 0
                onClicked: {
                    var key = keyField.text.trim();
                    root.close();
                    if (appBackend) appBackend.submitPckKey(key);
                }
            }
        }
    }
}

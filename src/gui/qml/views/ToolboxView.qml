import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"

ScrollView {
    id: root
    contentWidth: availableWidth
    clip: true

    readonly property color clrCard:       Theme.surfaceCard
    readonly property color clrCardHover:  Theme.surfaceHover
    readonly property color clrCardBorder: Theme.borderCard
    readonly property color clrAccent:     Theme.accentSolid
    readonly property color clrAccentCyan: Theme.isDark ? Theme.accentHover : Theme.accentSolid
    readonly property color clrTxt:        Theme.textPrimary
    readonly property color clrTxt2:       Theme.textSecondary

    function t(key) {
        return (localeManager && localeManager.currentLanguage, localeManager ? localeManager.t(key) : "");
    }

    function tBtn(key) {
        return root.t(key).replace(/&/g, "&&");
    }

    Column {
        width: Math.min(parent.width - 48, 920)
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 20
        topPadding: 24
        bottomPadding: 40

        // Page Header
        Column {
            spacing: 6
            Text {
                text: "🛠️ " + root.t("toolbox.title")
                font.pixelSize: 22
                font.bold: true
                color: Theme.textPrimary
            }
            Text {
                text: root.t("toolbox.subtitle")
                font.pixelSize: 13
                color: Theme.textSecondary
            }
        }

        // =====================================================================
        // Card 1: Font & Tofu Doctor (DynamicFont / Theme Injector)
        // =====================================================================
        Rectangle {
            width: parent.width
            implicitHeight: fontCol.implicitHeight + 36
            radius: Theme.radiusCard
            color: Theme.surfaceCard
            border.color: Theme.borderCard
            border.width: 1

            property string activePreset: {
                if (appBackend && appBackend.settings && appBackend.settings["font_preset"])
                    return appBackend.settings["font_preset"];
                return "default_arial";
            }
            property string customFontPath: {
                if (appBackend && appBackend.settings && appBackend.settings["custom_font_path"])
                    return appBackend.settings["custom_font_path"];
                return "";
            }
            property var diagResult: null

            Column {
                id: fontCol
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 18
                spacing: 14

                RowLayout {
                    width: parent.width
                    spacing: 8
                    Text {
                        text: "🔡 " + root.t("toolbox.font_card_title")
                        font.pixelSize: 15
                        font.bold: true
                        color: Theme.textPrimary
                    }
                    Item { Layout.fillWidth: true }
                    Rectangle {
                        implicitHeight: Theme.heightBadge
                        implicitWidth: fontBadgeTxt.implicitWidth + 14
                        radius: Theme.radiusBadge
                        color: Theme.accentSubtle
                        border.color: Theme.accentBorder
                        border.width: 1
                        Text {
                            id: fontBadgeTxt
                            anchors.centerIn: parent
                            text: "Godot 3.x / 4.x"
                            font.pixelSize: 10
                            font.bold: true
                            color: Theme.isDark ? Theme.accentHover : Theme.accentSolid
                        }
                    }
                }

                Text {
                    text: root.t("toolbox.font_card_desc")
                    font.pixelSize: 12
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                    width: parent.width
                }

                // Preset selector buttons
                RowLayout {
                    spacing: 8
                    QuickTag {
                        text: "Arial / System"
                        active: fontCol.parent.activePreset === "default_arial"
                        onClicked: if (appBackend) appBackend.setSetting("font_preset", "default_arial")
                    }
                    QuickTag {
                        text: "Noto Sans / Fallback"
                        active: fontCol.parent.activePreset === "noto_sans"
                        onClicked: if (appBackend) appBackend.setSetting("font_preset", "noto_sans")
                    }
                    QuickTag {
                        text: "Custom Font (.ttf / .otf)"
                        active: fontCol.parent.activePreset === "custom"
                        onClicked: if (appBackend) appBackend.setSetting("font_preset", "custom")
                    }
                }

                // Custom font picker row
                RowLayout {
                    width: parent.width
                    spacing: 10
                    visible: fontCol.parent.activePreset === "custom"

                    TextField {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 38
                        text: fontCol.parent.customFontPath
                        placeholderText: "C:/Windows/Fonts/arial.ttf"
                        readOnly: true
                        color: Theme.textPrimary
                        font.pixelSize: 12
                        background: Rectangle {
                            radius: Theme.radiusControl
                            color: Theme.surfaceControl
                            border.color: Theme.borderControl
                            border.width: 1
                        }
                    }

                    SecondaryButton {
                        text: root.t("toolbox.browse")
                        iconGlyph: Icons.folder
                        onClicked: fontDialog.open()
                    }

                    PrimaryButton {
                        text: "Tofu Doctor"
                        iconGlyph: Icons.circleCheck
                        onClicked: {
                            if (appBackend && fontCol.parent.customFontPath) {
                                fontCol.parent.diagResult = appBackend.diagnoseTofu(fontCol.parent.customFontPath, appBackend.targetLanguage);
                            }
                        }
                    }
                }

                // Diagnosis Report Banner
                Rectangle {
                    width: parent.width
                    height: 48
                    radius: Theme.radiusControl
                    visible: fontCol.parent.diagResult !== null
                    color: fontCol.parent.diagResult && fontCol.parent.diagResult.is_ok ? Theme.statusSuccessSubtle : Theme.statusWarningSubtle
                    border.color: fontCol.parent.diagResult && fontCol.parent.diagResult.is_ok ? Theme.statusSuccess : Theme.statusWarning
                    border.width: 1

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10

                        Icon {
                            glyph: fontCol.parent.diagResult && fontCol.parent.diagResult.is_ok ? Icons.circleCheck : Icons.triangleAlert
                            color: fontCol.parent.diagResult && fontCol.parent.diagResult.is_ok ? Theme.statusSuccess : Theme.statusWarning
                            size: 18
                        }

                        Text {
                            Layout.fillWidth: true
                            text: {
                                var res = fontCol.parent.diagResult;
                                if (!res) return "";
                                if (res.is_ok) {
                                    return root.t("toolbox.font_perfect") + " (" + res.covered + "/" + res.total + " - %100)";
                                } else {
                                    return root.t("toolbox.font_missing") + " (%" + res.ratio + "): " + res.missing;
                                }
                            }
                            font.pixelSize: 12
                            color: Theme.textPrimary
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }

        // =====================================================================
        // Card 2: PCK Dissector / Unpacker
        // =====================================================================
        Rectangle {
            width: parent.width
            implicitHeight: unpackCol.implicitHeight + 36
            radius: Theme.radiusCard
            color: Theme.surfaceCard
            border.color: Theme.borderCard
            border.width: 1

            Column {
                id: unpackCol
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 18
                spacing: 14

                RowLayout {
                    width: parent.width
                    spacing: 8
                    Text {
                        text: "📦 " + root.t("toolbox.pck_unpacker_title")
                        font.pixelSize: 15
                        font.bold: true
                        color: Theme.textPrimary
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: (appBackend && appBackend.fileCount > 0) ? (appBackend.fileCount + " " + root.t("dashboard.metric_files")) : "—"
                        font.pixelSize: 12
                        color: Theme.accentSolid
                        font.bold: true
                    }
                }

                Text {
                    text: root.t("toolbox.pck_unpacker_desc")
                    font.pixelSize: 12
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                    width: parent.width
                }

                RowLayout {
                    spacing: 12
                    PrimaryButton {
                        text: root.t("toolbox.btn_unpack_pck")
                        iconGlyph: Icons.packageBox
                        enabled: appBackend && appBackend.gamePath !== "" && !appBackend.isBusy
                        onClicked: unpackFolderDialog.open()
                    }
                    SecondaryButton {
                        text: root.t("toolbox.open_game_folder")
                        iconGlyph: Icons.folder
                        enabled: appBackend && appBackend.gamePath !== ""
                        onClicked: if (appBackend) appBackend.openGameFolder()
                    }
                }
            }
        }

        // =====================================================================
        // Card 3: Revert Patch (Vanilla Restorer)
        // =====================================================================
        Rectangle {
            width: parent.width
            implicitHeight: revertCol.implicitHeight + 36
            radius: Theme.radiusCard
            color: Theme.surfaceCard
            border.color: Theme.borderCard
            border.width: 1

            Column {
                id: revertCol
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 18
                spacing: 14

                RowLayout {
                    width: parent.width
                    spacing: 8
                    Text {
                        text: "🔄 " + root.t("toolbox.revert_card_title")
                        font.pixelSize: 15
                        font.bold: true
                        color: Theme.textPrimary
                    }
                    Item { Layout.fillWidth: true }
                }

                Text {
                    text: root.t("toolbox.revert_card_desc")
                    font.pixelSize: 12
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                    width: parent.width
                }

                RowLayout {
                    spacing: 12
                    SecondaryButton {
                        text: root.t("toolbox.btn_revert_vanilla")
                        iconGlyph: Icons.refreshCw
                        enabled: appBackend && appBackend.gamePath !== "" && !appBackend.isBusy
                        onClicked: if (appBackend) appBackend.revertVanilla()
                    }
                }
            }
        }

        // =====================================================================
        // Card 4: Translation Memory & Cache Tools
        // =====================================================================
        Rectangle {
            width: parent.width
            implicitHeight: cacheCol.implicitHeight + 36
            radius: Theme.radiusCard
            color: Theme.surfaceCard
            border.color: Theme.borderCard
            border.width: 1

            Column {
                id: cacheCol
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 18
                spacing: 14

                RowLayout {
                    width: parent.width
                    spacing: 8
                    Text {
                        text: "💾 " + root.t("toolbox.cache_card_title")
                        font.pixelSize: 15
                        font.bold: true
                        color: Theme.textPrimary
                    }
                    Item { Layout.fillWidth: true }
                }

                Text {
                    text: root.t("toolbox.cache_card_desc")
                    font.pixelSize: 12
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                    width: parent.width
                }

                RowLayout {
                    spacing: 12
                    SecondaryButton {
                        text: root.t("toolbox.btn_clear_cache")
                        iconGlyph: Icons.trash2
                        onClicked: if (appBackend) appBackend.clearCache()
                    }
                    SecondaryButton {
                        text: root.t("toolbox.open_logs_folder")
                        iconGlyph: Icons.folderOpen
                        onClicked: if (appBackend) appBackend.openLogsFolder()
                    }
                }
            }
        }
    }

    // Dialogs
    FileDialog {
        id: fontDialog
        title: "Select Font File (.ttf, .otf)"
        nameFilters: ["Font files (*.ttf *.otf)"]
        onAccepted: {
            var path = selectedFile.toString();
            if (appBackend) {
                appBackend.setSetting("custom_font_path", path);
                appBackend.setSetting("font_preset", "custom");
            }
        }
    }

    FolderDialog {
        id: unpackFolderDialog
        title: "Select Output Folder for PCK Extraction"
        onAccepted: {
            if (appBackend) {
                appBackend.extractPck(selectedFolder.toString());
            }
        }
    }
}

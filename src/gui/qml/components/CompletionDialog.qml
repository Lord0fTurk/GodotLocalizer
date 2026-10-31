import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import "../components"

Popup {
    id: root
    modal: true
    focus: true
    anchors.centerIn: parent
    width: Math.min(parent.width - 48, 700)
    padding: 20
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    property var summary: null

    // True while everything inside the card stays within the card (checked by the QML smoke test).
    readonly property bool contentFits: contentItem !== null
        && contentItem.childrenRect.width <= contentItem.width + 1
        && contentItem.childrenRect.height <= contentItem.height + 1

    signal openEditorRequested()

    function openWithSummary(data) {
        summary = data;
        open();
    }

    function t(key) {
        return (localeManager && localeManager.currentLanguage, localeManager ? localeManager.t(key) : "");
    }

    background: Rectangle {
        radius: Theme.radiusCard
        color: Theme.surfaceCard
        border.color: Theme.borderCard
        border.width: 1

        Rectangle {
            z: -1
            anchors.fill: parent
            anchors.margins: -2
            radius: Theme.radiusCard + 2
            color: "transparent"
            border.color: Theme.isNeon ? Theme.accentAlpha(0.35) : "transparent"
            border.width: 1
        }
    }

    contentItem: ColumnLayout {
        spacing: 16

        // 1. Header with Celebration Badge & Close button
        RowLayout {
            Layout.fillWidth: true
            spacing: 14

            Rectangle {
                width: 44
                height: 44
                radius: Theme.radiusControl + 2
                color: Theme.statusSuccessSubtle
                border.color: Theme.statusSuccessBorder
                border.width: 1

                Icon {
                    anchors.centerIn: parent
                    glyph: Icons.partyPopper
                    size: 22
                    color: Theme.statusSuccess
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3

                Text {
                    text: root.t("dashboard.completion_title")
                    font.pixelSize: 17
                    font.bold: true
                    color: Theme.textPrimary
                }

                Text {
                    text: root.t("dashboard.completion_subtitle")
                    font.pixelSize: 12
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }

            // Close button
            Button {
                implicitWidth: 30
                implicitHeight: 30
                topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0; padding: 0
                background: Rectangle {
                    radius: Theme.radiusBadge
                    color: parent.hovered ? Theme.surfaceHover : "transparent"
                }
                contentItem: Icon {
                    anchors.centerIn: parent
                    glyph: Icons.x
                    size: 16
                    color: parent.parent.hovered ? Theme.textPrimary : Theme.textMuted
                }
                onClicked: root.close()
            }
        }

        // 2. Metrics 4-Box Grid
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            rowSpacing: 10
            columnSpacing: 10

            // Metric 1: Translated Lines
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 56
                radius: Theme.radiusControl
                color: Theme.surfaceControl
                border.color: Theme.borderControl
                border.width: 1

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 10

                    Icon { glyph: Icons.languages; size: 18; color: Theme.accentSolid }
                    ColumnLayout {
                        spacing: 2
                        Text { text: root.t("dashboard.completion_stat_translated"); font.pixelSize: 11; color: Theme.textSecondary }
                        Text { text: (root.summary ? root.summary.translated_count : 0).toLocaleString(); font.pixelSize: 15; font.bold: true; color: Theme.textPrimary }
                    }
                }
            }

            // Metric 2: Success Rate
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 56
                radius: Theme.radiusControl
                color: Theme.surfaceControl
                border.color: Theme.borderControl
                border.width: 1

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 10

                    Icon { glyph: Icons.circleCheck; size: 18; color: Theme.statusSuccess }
                    ColumnLayout {
                        spacing: 2
                        Text { text: root.t("dashboard.completion_stat_success"); font.pixelSize: 11; color: Theme.textSecondary }
                        Text { text: root.summary ? root.summary.success_rate : "100%"; font.pixelSize: 15; font.bold: true; color: Theme.statusSuccess }
                    }
                }
            }

            // Metric 3: Total Harvested
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 56
                radius: Theme.radiusControl
                color: Theme.surfaceControl
                border.color: Theme.borderControl
                border.width: 1

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 10

                    Icon { glyph: Icons.database; size: 18; color: Theme.textSecondary }
                    ColumnLayout {
                        spacing: 2
                        Text { text: root.t("dashboard.completion_stat_total"); font.pixelSize: 11; color: Theme.textSecondary }
                        Text { text: (root.summary ? root.summary.total_harvested : 0).toLocaleString(); font.pixelSize: 15; font.bold: true; color: Theme.textPrimary }
                    }
                }
            }

            // Metric 4: Target Language
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 56
                radius: Theme.radiusControl
                color: Theme.surfaceControl
                border.color: Theme.borderControl
                border.width: 1

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 10

                    Icon { glyph: Icons.globe; size: 18; color: Theme.textSecondary }
                    ColumnLayout {
                        spacing: 2
                        Text { text: root.t("dashboard.completion_stat_target"); font.pixelSize: 11; color: Theme.textSecondary }
                        Text { text: (root.summary ? root.summary.target_lang : "").toUpperCase(); font.pixelSize: 15; font.bold: true; color: Theme.isDark ? Theme.accentHover : Theme.accentSolid }
                    }
                }
            }
        }

        // 3. Destination Path Bar
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 36
            radius: Theme.radiusControl
            color: Theme.surfaceSubtle
            border.color: Theme.borderControl
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 8

                Icon { glyph: Icons.folder; size: 13; color: Theme.textSecondary }
                Text {
                    Layout.fillWidth: true
                    text: root.summary ? root.summary.game_path : ""
                    font.pixelSize: 11
                    color: Theme.textSecondary
                    elide: Text.ElideMiddle
                }
            }
        }

        // 3b. What happened to the rest (only when something is left)
        Text {
            Layout.fillWidth: true
            readonly property int rejected: root.summary ? root.summary.rejected_count : 0
            readonly property int failed: root.summary ? root.summary.failed_count : 0
            readonly property int unchanged: root.summary ? root.summary.unchanged_count : 0
            visible: rejected + failed + unchanged > 0
            text: root.t("dashboard.completion_unfinished")
                      .replace("{count}", rejected + failed + unchanged)
                      .replace("{rejected}", rejected)
                      .replace("{failed}", failed)
                      .replace("{unchanged}", unchanged)
            font.pixelSize: 12
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        // 4. Action Buttons (wrap onto a second line when the window is narrow)
        Flow {
            Layout.fillWidth: true
            spacing: 8

            // Oyunu Başlat
            Button {
                id: btnLaunch
                visible: Boolean(root.summary && root.summary.has_exe)
                implicitHeight: Theme.heightSecondary
                topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0; padding: 0
                scale: down ? 0.98 : 1.0

                background: Rectangle {
                    radius: Theme.radiusControl
                    color: btnLaunch.down ? Theme.statusSuccess : (btnLaunch.hovered ? Theme.accentHover : Theme.statusSuccess)
                    border.width: 0
                }

                contentItem: RowLayout {
                    spacing: 6
                    anchors.centerIn: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    Icon { glyph: Icons.play; size: 13; color: Theme.accentContrastText }
                    Text {
                        text: root.t("dashboard.completion_launch_game")
                        font.pixelSize: 12
                        font.bold: true
                        color: Theme.accentContrastText
                    }
                }

                onClicked: {
                    if (appBackend) appBackend.launchGame();
                }
            }

            // Editörde İncele
            Button {
                id: btnReview
                implicitHeight: Theme.heightSecondary
                topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0; padding: 0
                scale: down ? 0.98 : 1.0

                background: Rectangle {
                    radius: Theme.radiusControl
                    color: btnReview.down ? Theme.surfaceHover : (btnReview.hovered ? Theme.surfaceHover : Theme.surfaceControl)
                    border.color: btnReview.hovered ? Theme.borderFocus : Theme.borderControl
                    border.width: 1
                }

                contentItem: RowLayout {
                    spacing: 6
                    anchors.centerIn: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    Icon { glyph: Icons.squarePen; size: 13; color: btnReview.hovered ? Theme.textPrimary : Theme.textSecondary }
                    Text {
                        text: root.t("dashboard.completion_review_editor")
                        font.pixelSize: 12
                        color: btnReview.hovered ? Theme.textPrimary : Theme.textSecondary
                    }
                }

                onClicked: {
                    root.close();
                    root.openEditorRequested();
                }
            }

            // Yama Klasörünü Aç
            Button {
                id: btnFolder
                implicitHeight: Theme.heightSecondary
                topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0; padding: 0
                scale: down ? 0.98 : 1.0

                background: Rectangle {
                    radius: Theme.radiusControl
                    color: btnFolder.down ? Theme.surfaceHover : (btnFolder.hovered ? Theme.surfaceHover : Theme.surfaceControl)
                    border.color: btnFolder.hovered ? Theme.borderFocus : Theme.borderControl
                    border.width: 1
                }

                contentItem: RowLayout {
                    spacing: 6
                    anchors.centerIn: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    Icon { glyph: Icons.folderOpen; size: 13; color: btnFolder.hovered ? Theme.textPrimary : Theme.textSecondary }
                    Text {
                        text: root.t("dashboard.completion_open_folder")
                        font.pixelSize: 12
                        color: btnFolder.hovered ? Theme.textPrimary : Theme.textSecondary
                    }
                }

                onClicked: {
                    if (appBackend) appBackend.openGameFolder();
                }
            }

            // Kapat / Tamam
            Button {
                id: btnDone
                implicitHeight: Theme.heightSecondary
                topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0; padding: 0
                scale: down ? 0.98 : 1.0

                background: Rectangle {
                    radius: Theme.radiusControl
                    color: btnDone.down ? Theme.accentSolid : (btnDone.hovered ? Theme.accentHover : Theme.accentSolid)
                    border.width: 0
                }

                contentItem: RowLayout {
                    spacing: 6
                    anchors.centerIn: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 14
                    Icon { glyph: Icons.circleCheck; size: 13; color: Theme.accentContrastText }
                    Text {
                        text: root.t("dashboard.completion_close")
                        font.pixelSize: 12
                        font.bold: true
                        color: Theme.accentContrastText
                    }
                }

                onClicked: root.close()
            }
        }
    }
}

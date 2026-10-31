pragma Singleton
import QtQuick 2.15

QtObject {
    id: theme

    // Active theme mode: 'obsidian' | 'daylight' | 'cyber_neon'
    property string mode: "obsidian"

    // Preset B — Emerald / Mint (Veri / Sistem / Enjeksiyon per DESIGN.md)
    readonly property color accentSolid: "#10B981"
    readonly property color accentHover: "#34D399"
    readonly property color accentContrastText: "#050A08"
    readonly property string accentRgb: "16, 185, 129"

    // Semantic Status Tokens using Qt.rgba per DESIGN.md Rule 3
    readonly property color statusSuccess: "#10B981"
    readonly property color statusSuccessSubtle: Qt.rgba(16/255, 185/255, 129/255, 0.14)
    readonly property color statusSuccessBorder: Qt.rgba(16/255, 185/255, 129/255, 0.40)

    readonly property color statusWarning: "#F59E0B"
    readonly property color statusWarningSubtle: Qt.rgba(245/255, 158/255, 11/255, 0.14)
    readonly property color statusWarningBorder: Qt.rgba(245/255, 158/255, 11/255, 0.40)

    readonly property color statusDanger: "#F43F5E"
    readonly property color statusDangerSubtle: Qt.rgba(244/255, 63/255, 94/255, 0.14)
    readonly property color statusDangerBorder: Qt.rgba(244/255, 63/255, 94/255, 0.40)

    readonly property color statusInfo: "#38BDF8"
    readonly property color statusInfoSubtle: Qt.rgba(56/255, 189/255, 248/255, 0.12)
    readonly property color statusInfoBorder: Qt.rgba(56/255, 189/255, 248/255, 0.35)

    // 3-Theme Color Matrix
    readonly property color canvas: {
        if (mode === "daylight") return "#F1F5F9"
        if (mode === "cyber_neon") return "#060911"
        return "#090D14" // obsidian
    }

    readonly property color surfaceSidebar: {
        if (mode === "daylight") return "#E2E8F0"
        if (mode === "cyber_neon") return "#090E1A"
        return "#0C111A"
    }

    readonly property color surfaceCard: {
        if (mode === "daylight") return "#FFFFFF"
        if (mode === "cyber_neon") return "#0D1322"
        return "#111723"
    }

    readonly property color surfaceControl: {
        if (mode === "daylight") return "#F8FAFC"
        if (mode === "cyber_neon") return "#09111E"
        return "#171F2E"
    }

    readonly property color surfaceHover: {
        if (mode === "daylight") return "#E2E8F0"
        if (mode === "cyber_neon") return "#132038"
        return "#1E293B"
    }

    readonly property color surfaceSubtle: {
        if (mode === "daylight") return "#F8FAFC"
        if (mode === "cyber_neon") return "#0B1526"
        return "#141C2B"
    }

    readonly property color borderCard: {
        if (mode === "daylight") return "#CBD5E1"
        if (mode === "cyber_neon") return Qt.rgba(16/255, 185/255, 129/255, 0.25)
        return "#1E2636"
    }

    readonly property color borderControl: {
        if (mode === "daylight") return "#94A3B8"
        if (mode === "cyber_neon") return Qt.rgba(16/255, 185/255, 129/255, 0.55)
        return "#263042"
    }

    readonly property color borderFocus: {
        if (mode === "cyber_neon") return accentHover
        return accentSolid
    }

    readonly property color textPrimary: {
        if (mode === "daylight") return "#0F172A"
        if (mode === "cyber_neon") return "#F8FAFC"
        return "#F1F5F9"
    }

    readonly property color textSecondary: {
        if (mode === "daylight") return "#475569"
        return "#94A3B8"
    }

    readonly property color textMuted: {
        if (mode === "cyber_neon") return "#52637A"
        return "#64748B"
    }

    readonly property color accentSubtle: {
        if (mode === "daylight") return Qt.rgba(16/255, 185/255, 129/255, 0.15)
        if (mode === "cyber_neon") return Qt.rgba(16/255, 185/255, 129/255, 0.18)
        return Qt.rgba(16/255, 185/255, 129/255, 0.12)
    }

    readonly property color accentBorder: {
        if (mode === "daylight") return Qt.rgba(16/255, 185/255, 129/255, 0.55)
        if (mode === "cyber_neon") return Qt.rgba(16/255, 185/255, 129/255, 0.80)
        return Qt.rgba(16/255, 185/255, 129/255, 0.45)
    }

    // Geometry & Radius Scale Locks per DESIGN.md
    readonly property int radiusCard: 10
    readonly property int radiusControl: 6
    readonly property int radiusBadge: 4

    // Zero-Breakage Backward Aliases
    readonly property color bgDark: canvas
    readonly property color cardBg: surfaceCard
    readonly property color textColor: textPrimary
    readonly property color subTextColor: textSecondary
    readonly property color accentColor: accentSolid
    readonly property color hoverColor: surfaceHover
}

#!/bin/bash
# Script d'installation automatique des raccourcis .desktop pour XFCE / Linux
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "=========================================================="
echo "📦 Installation des raccourcis Détection d'Intrusion"
echo "=========================================================="
echo "Dossier du projet : $PROJECT_DIR"

# 1. Rendre les scripts et lanceurs exécutables
chmod +x intrusion_detection.sh intrusion_detection_test.sh intrusion-detection*.desktop

# 2. Copie dans le Menu des Applications de l'utilisateur (~/.local/share/applications)
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"
cp intrusion-detection.desktop "$APP_DIR/"
cp intrusion-detection-test.desktop "$APP_DIR/"
echo "✅ Ajouté au Menu des Applications : $APP_DIR"

# 3. Copie sur le Bureau
DESKTOP_DIR=""
if [ -d "$HOME/Bureau" ]; then
    DESKTOP_DIR="$HOME/Bureau"
elif [ -d "$HOME/Desktop" ]; then
    DESKTOP_DIR="$HOME/Desktop"
fi

if [ -n "$DESKTOP_DIR" ]; then
    cp intrusion-detection.desktop "$DESKTOP_DIR/"
    chmod +x "$DESKTOP_DIR/intrusion-detection.desktop"
    # Sous XFCE, marque le lanceur comme approuvé pour éviter le message de confirmation
    if command -v gio >/dev/null 2>&1; then
        gio set "$DESKTOP_DIR/intrusion-detection.desktop" metadata::trusted true 2>/dev/null || true
    fi
    echo "✅ Raccourci créé sur le Bureau : $DESKTOP_DIR/intrusion-detection.desktop"
fi

echo ""
echo "🎉 Terminé ! Les lanceurs sont agnostiques du chemin et s'adapteront même si vous déplacez le projet."

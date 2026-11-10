#!/bin/bash
# Se positionner dans le répertoire du projet
cd "$(dirname "$0")"

echo "=========================================================="
echo "🛡️  DÉMARRAGE DU SYSTÈME DE VIDÉOSURVEILLANCE INTRUSION"
echo "=========================================================="
echo "Dossier : $(pwd)"
echo ""

./venv/bin/python3 main.py --resolution 720p
EXIT_CODE=$?

echo ""
echo "=========================================================="
echo "Arrêt du système de surveillance (code $EXIT_CODE)."
echo "=========================================================="
read -n 1 -s -r -p "Appuyez sur une touche pour fermer cette fenêtre..."
echo ""

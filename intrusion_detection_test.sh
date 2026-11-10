#!/bin/bash
# Se positionner dans le répertoire du projet
cd "$(dirname "$0")"

echo "=========================================================="
echo "🎯  DÉMARRAGE DU MODE TEST / AIDE AU CADRAGE WEBCAM"
echo "=========================================================="
echo "Dossier : $(pwd)"
echo ""

./venv/bin/python3 main.py --test --resolution 720p
EXIT_CODE=$?

echo ""
echo "=========================================================="
echo "Fin du mode test (code $EXIT_CODE)."
echo "=========================================================="
read -n 1 -s -r -p "Appuyez sur une touche pour fermer cette fenêtre..."
echo ""

# 🛡️ Détection d'Intrusion & Vidéosurveillance par Caméra USB

Programme Python de vidéosurveillance et détection d'intrusion par webcam USB avec **contrôle et désactivation à distance via Telegram**, alertes e-mail avec **photos représentatives intégrées** et extraits vidéo MP4.

---

## 📋 Fonctionnalités & Spécifications

1. **Délai d'armement initial (1 minute par défaut)** :
   - En mode normal, la détection attend **60 secondes** avant de s'armer (vous permet de quitter la pièce et calibre la caméra).
   - **Mode test (`--test` ou `-t`)** : désactive le délai d'une minute et lance la détection immédiatement.
2. **Lorsqu'un mouvement est détecté** :
   - **Étape 1 (à T = 0.5s)** : Envoi d'une alerte avec photo instantanée (visage/corps engagé dans le champ) par e-mail et sur Telegram avec boutons d'action rapide `[🔴 Désarmer]` et `[⏸️ Pause 1 heure]`.
   - **Étape 2 (à T = 3.0s)** : Envoi de la vidéo des 3 premières secondes (`.mp4`) + photo clé.
   - **Étape 3 (toutes les 10 secondes tant que le mouvement persiste)** : Envoi de vidéos de 10 secondes et photos de l'activité continue.
   - **Fin de l'événement** : Retour automatique en veille active sans spammer si aucun mouvement n'est constaté durant 10s.
3. **Contrôle à distance via Telegram** :
   - **Désarmer à tout moment** depuis votre smartphone : éteint matériellement la caméra USB (LED éteinte, repos thermique complet et respect de votre intimité).
   - **Mettre en pause** (30 min, 1h, 2h) : éteint la caméra temporairement, avec réarmement et rallumage automatiques.
   - **Demander une photo en direct** (`📷 Prendre une photo`) : même si le système est désarmé et la caméra éteinte, le système l'allume furtivement pour prendre le cliché et la rééteint immédiatement.
   - **Consulter le statut** (`ℹ️ Statut`) : affiche si la caméra est matériellement allumée ou éteinte.

---

## 📁 Structure du Projet

```text
intrusion_detection/
├── config.py                  # Gestion des arguments CLI et du fichier .env
├── main.py                    # Contrôleur principal et machine à états
├── telegram_bot.py            # Service Telegram (commandes à distance et alertes)
├── motion_detector.py         # Détecteur de mouvement (MOG2 + filtrage des ombres)
├── notifier.py                # Envoi asynchrone des e-mails avec photos inline et vidéos MP4
├── recorder.py                # Enregistreur vidéo MP4 et extracteur de photos clés
├── test_email.py              # Script de test rapide d'envoi d'e-mail avec photo
├── requirements.txt           # Dépendances Python
├── .env.example               # Modèle de configuration
├── .env                       # Fichier de configuration locale
├── test_suite.py              # Tests unitaires
├── test_telegram.py           # Tests unitaires du service Telegram
└── test_workflow_simulation.py # Test d'intégration complet
```

---

## 🤖 Configuration du Bot Telegram (Désactivation à distance)

La mise en place prend **moins de 2 minutes** :

1. Ouvrez l'application **Telegram** sur votre smartphone ou PC.
2. Cherchez l'utilisateur officiel **`@BotFather`** et ouvrez la conversation.
3. Envoyez la commande `/newbot`.
4. Donnez un nom à votre bot (ex: `Ma Camera Surveillance`) puis un identifiant se terminant par `bot` (ex: `ma_camera_maison_bot`).
5. **@BotFather** vous donne un **Token API** (ressemble à `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
6. Ajoutez ce token dans votre fichier [`.env`](file:///home/tangi/projects/intrusion_detection/.env) :
   ```dotenv
   TELEGRAM_BOT_TOKEN=votre_token_recu_ici
   ```
7. Lancez le programme (`./venv/bin/python3 main.py --test`).
8. Ouvrez la conversation avec votre nouveau bot sur Telegram et cliquez sur **Démarrer** (ou envoyez `/start`).
   - Le système lie automatiquement votre compte de manière sécurisée et vous affiche le clavier de contrôle !

---

## 📱 Commandes disponibles sur Telegram

Des boutons interactifs apparaissent sous votre zone de texte :
- `🔴 Désarmer` : coupe instantanément la surveillance.
- `🟢 Armer` : réactive la surveillance.
- `⏸️ Pause 30 min` / `⏸️ Pause 1h` / `⏸️ Pause 2h` : désactive temporairement la détection, puis **se réarme automatiquement** en vous envoyant une notification de reprise.
- `📷 Prendre une photo` : prend une photo instantanée en direct depuis la caméra et vous l'envoie.
- `ℹ️ Statut` : affiche l'état actuel (armé, en pause, alertes déclenchées).

> [!TIP]
> Lorsque la caméra détecte un mouvement, l'alerte reçue sur Telegram contient deux boutons sous la photo :  
> `[🔴 Désarmer]` et `[⏸️ Pause 1 heure]` pour réagir d'un simple clic en cas de fausse alerte !

---

## ⚙️ Configuration E-mail (.env)

Dans le fichier [`.env`](file:///home/tangi/projects/intrusion_detection/.env) :

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=votre_adresse@gmail.com
SMTP_PASSWORD=votre_mot_de_passe_application_16_caracteres
SMTP_USE_SSL=false

EMAIL_FROM=votre_adresse@gmail.com
EMAIL_TO=votre_adresse@gmail.com
```

---

## 💻 Utilisation

### Mode Test (recommandé pour essayer)
```bash
./venv/bin/python3 main.py --test
```

### Mode Normal (avec délai d'armement d'une minute)
```bash
./venv/bin/python3 main.py
```

### Options de la ligne de commande

| Option | Raccourci | Description | Défaut |
|---|---|---|---|
| `--test` | `-t` | Démarrage immédiat (mode test) | `False` |
| `--delay <sec>` | `-d` | Délai avant armement en secondes | `60` (`0` en mode test) |
| `--photo-delay <sec>` | | Retard de la prise de photo d'alerte initiale | `0.5` |
| `--resolution <res>` | `-r` | Résolution : `480p`, `720p`, `1080p` ou `1920x1080` | `480p` |
| `--camera <idx>` | `-c` | Index de la webcam USB | `0` |
| `--telegram-token <tok>`| | Token du Bot Telegram | Variable d'environnement |
| `--min-area <px>`| `-a` | Surface minimale en pixels pour détecter un mouvement | Auto (selon résolution) |
| `--output-dir <dir>` | `-o` | Dossier de sauvegarde des vidéos et photos | `./recordings` |

import os
import argparse
from dataclasses import dataclass
from typing import Tuple
from dotenv import load_dotenv

RESOLUTION_PRESETS = {
    "480p": (640, 480),
    "vga": (640, 480),
    "720p": (1280, 720),
    "hd": (1280, 720),
    "1080p": (1920, 1080),
    "fhd": (1920, 1080),
    "fullhd": (1920, 1080),
}


def parse_resolution_string(res_str: str) -> Tuple[int, int]:
    """Convertit une chaîne (ex: '720p', '1080p', '1280x720') en tuple (width, height)."""
    clean_str = res_str.strip().lower()
    if clean_str in RESOLUTION_PRESETS:
        return RESOLUTION_PRESETS[clean_str]

    if "x" in clean_str:
        try:
            parts = clean_str.split("x")
            w, h = int(parts[0]), int(parts[1])
            if w > 0 and h > 0:
                return (w, h)
        except ValueError:
            pass

    return (640, 480)


@dataclass
class AppConfig:
    # Mode & Délais
    is_test_mode: bool
    startup_delay: float
    initial_photo_delay: float

    # Caméra & Détection
    camera_index: int
    resolution: Tuple[int, int]
    min_area: int
    consecutive_frames: int
    show_display: bool

    # Stockage
    output_dir: str
    keep_videos: bool

    # E-mail / SMTP
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_use_tls: bool
    smtp_use_ssl: bool
    email_from: str
    email_to: str
    mock_email: bool

    # Telegram
    telegram_token: str
    telegram_chat_id: str
    telegram_alerts: bool


def parse_args_and_config() -> AppConfig:
    # Charge le fichier .env s'il existe
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Surveillance vidéo et détection d'intrusion par webcam USB avec alertes e-mail."
    )

    parser.add_argument(
        "-t",
        "--test",
        action="store_true",
        default=os.getenv("TEST_MODE", "false").lower() in ("true", "1", "yes"),
        help="Mode test : désactive le délai d'une minute au démarrage (démarrage immédiat).",
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=None,
        help="Délai en secondes avant le démarrage de la détection (par défaut : 60s en mode normal, 0s en mode test).",
    )
    parser.add_argument(
        "--photo-delay",
        type=float,
        default=float(os.getenv("PHOTO_DELAY", "0.5")),
        help="Délai en secondes avant la prise de photo de l'alerte immédiate (défaut: 0.5s).",
    )
    parser.add_argument(
        "-c",
        "--camera",
        type=int,
        default=int(os.getenv("CAMERA_INDEX", "0")),
        help="Index de la caméra USB (défaut: 0).",
    )
    parser.add_argument(
        "-r",
        "--resolution",
        type=str,
        default=os.getenv("CAMERA_RESOLUTION", "480p"),
        help="Résolution de la caméra : '480p', '720p', '1080p' ou '1920x1080' (défaut: 480p).",
    )
    parser.add_argument(
        "-a",
        "--min-area",
        type=int,
        default=None,
        help="Surface minimale en pixels pour détecter un mouvement (défaut: calibré automatiquement selon la résolution).",
    )
    parser.add_argument(
        "--consecutive-frames",
        type=int,
        default=int(os.getenv("CONSECUTIVE_FRAMES", "2")),
        help="Nombre de frames consécutives nécessaires pour confirmer le mouvement (défaut: 2).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default=os.getenv("OUTPUT_DIR", "./recordings"),
        help="Dossier d'enregistrement des vidéos (défaut: ./recordings).",
    )
    parser.add_argument(
        "--keep-videos",
        action="store_true",
        default=os.getenv("KEEP_VIDEOS", "true").lower() in ("true", "1", "yes"),
        help="Conserver les copies des vidéos sur disque après envoi (défaut: True).",
    )
    parser.add_argument(
        "--display",
        action="store_true",
        default=None,
        help="Afficher la fenêtre graphique d'aide au cadrage (activé par défaut en mode --test).",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="Désactiver l'affichage de la fenêtre graphique même en mode --test.",
    )
    parser.add_argument(
        "--mock-email",
        action="store_true",
        default=os.getenv("MOCK_EMAIL", "false").lower() in ("true", "1", "yes"),
        help="Simuler l'envoi des e-mails dans la console sans contacter de serveur SMTP réel.",
    )
    parser.add_argument(
        "--smtp-host",
        type=str,
        default=os.getenv("SMTP_HOST", ""),
        help="Hôte SMTP (ex: smtp.gmail.com).",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=int(os.getenv("SMTP_PORT", "587")),
        help="Port SMTP (587 pour STARTTLS, 465 pour SSL, défaut: 587).",
    )
    parser.add_argument(
        "--smtp-user",
        type=str,
        default=os.getenv("SMTP_USER", ""),
        help="Utilisateur SMTP / adresse e-mail d'authentification.",
    )
    parser.add_argument(
        "--smtp-password",
        type=str,
        default=os.getenv("SMTP_PASSWORD", ""),
        help="Mot de passe SMTP ou mot de passe d'application.",
    )
    parser.add_argument(
        "--smtp-ssl",
        action="store_true",
        default=os.getenv("SMTP_USE_SSL", "false").lower() in ("true", "1", "yes"),
        help="Utiliser SSL direct (port 465).",
    )
    parser.add_argument(
        "--no-tls",
        action="store_true",
        help="Désactiver STARTTLS (port 587).",
    )
    parser.add_argument(
        "--email-from",
        type=str,
        default=os.getenv("EMAIL_FROM", ""),
        help="Adresse e-mail expéditeur (défaut: identique à smtp-user).",
    )
    parser.add_argument(
        "--email-to",
        type=str,
        default=os.getenv("EMAIL_TO", ""),
        help="Adresse e-mail destinataire pour les alertes.",
    )
    parser.add_argument(
        "--telegram-token",
        type=str,
        default=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        help="Token du Bot Telegram (@BotFather).",
    )
    parser.add_argument(
        "--telegram-chat-id",
        type=str,
        default=os.getenv("TELEGRAM_CHAT_ID", ""),
        help="Chat ID Telegram du propriétaire.",
    )
    parser.add_argument(
        "--no-telegram-alerts",
        action="store_true",
        help="Ne pas envoyer les alertes sur Telegram (utiliser Telegram uniquement pour les commandes).",
    )

    args = parser.parse_args()

    # Détermination du délai d'attente initial
    if args.delay is not None:
        startup_delay = max(0.0, float(args.delay))
    else:
        startup_delay = 0.0 if args.test else 60.0

    use_tls = not args.no_tls and not args.smtp_ssl
    target_res = parse_resolution_string(args.resolution)

    # Calcul automatique de la surface minimale (sensibilité) selon la résolution
    # Base : ~1200 px pour 640x480 (soit ~0.39% de la surface de l'image)
    if args.min_area is not None:
        min_area = args.min_area
    elif os.getenv("MIN_AREA"):
        min_area = int(os.getenv("MIN_AREA"))
    else:
        scale = (target_res[0] * target_res[1]) / (640 * 480)
        min_area = int(1200 * scale)

    telegram_alerts = not args.no_telegram_alerts and bool(args.telegram_token)

    # Détermination de l'affichage graphique pour l'aide au cadrage
    has_display = bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))
    if args.no_display:
        show_display = False
    elif args.display:
        show_display = True
    elif args.test:
        show_display = has_display
    else:
        show_display = False

    return AppConfig(
        is_test_mode=args.test,
        startup_delay=startup_delay,
        initial_photo_delay=max(0.0, float(args.photo_delay)),
        camera_index=args.camera,
        resolution=target_res,
        min_area=min_area,
        consecutive_frames=args.consecutive_frames,
        show_display=show_display,
        output_dir=args.output_dir,
        keep_videos=args.keep_videos,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_user=args.smtp_user,
        smtp_password=args.smtp_password,
        smtp_use_tls=use_tls,
        smtp_use_ssl=args.smtp_ssl,
        email_from=args.email_from,
        email_to=args.email_to,
        mock_email=args.mock_email,
        telegram_token=args.telegram_token,
        telegram_chat_id=args.telegram_chat_id,
        telegram_alerts=telegram_alerts,
    )

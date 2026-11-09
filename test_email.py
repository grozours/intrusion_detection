#!/usr/bin/env python3
import os
import cv2
import numpy as np
from datetime import datetime

from config import parse_args_and_config
from notifier import EmailNotifier


def test_gmail():
    cfg = parse_args_and_config()

    print("==================================================")
    print("🧪 Test de transmission d'e-mail avec photo vers Gmail")
    print("==================================================")
    print(f"Hôte SMTP   : {cfg.smtp_host}:{cfg.smtp_port}")
    print(f"Utilisateur : {cfg.smtp_user}")
    print(f"Expéditeur  : {cfg.email_from}")
    print(f"Destinataire: {cfg.email_to}")
    print("==================================================")

    if not cfg.smtp_host or not cfg.smtp_user or not cfg.email_to:
        print("⚠️ Attention : les variables SMTP_HOST, SMTP_USER ou EMAIL_TO sont vides dans le fichier .env.")
        print("Éditez le fichier .env avant de lancer ce test.")
        return

    # Capture d'une image test depuis la caméra (ou génération d'une mire si caméra non dispo)
    os.makedirs("./recordings", exist_ok=True)
    test_img_path = "./recordings/test_photo_gmail.jpg"

    cap = cv2.VideoCapture(cfg.camera_index)
    ret, frame = cap.read() if cap.isOpened() else (False, None)
    cap.release()

    if not ret or frame is None:
        print("Note : Caméra indisponible pour ce test, génération d'une mire de test...")
        frame = np.full((480, 640, 3), 40, dtype=np.uint8)
        cv2.putText(
            frame,
            "TEST PHOTO GMAIL",
            (100, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (0, 255, 0),
            2,
        )

    # Ajout de l'horodatage
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(
        frame,
        f"Test de surveillance - {ts}",
        (15, frame.shape[0] - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.imwrite(test_img_path, frame)

    notifier = EmailNotifier(
        smtp_host=cfg.smtp_host,
        smtp_port=cfg.smtp_port,
        smtp_user=cfg.smtp_user,
        smtp_password=cfg.smtp_password,
        use_tls=cfg.smtp_use_tls,
        use_ssl=cfg.smtp_use_ssl,
        email_from=cfg.email_from,
        email_to=cfg.email_to,
        mock=False,
    )

    print("Connexion SMTP et envoi de l'e-mail avec photo intégrée...")
    notifier.send_alert_async(
        subject="📷 [TEST] Photo de surveillance envoyée avec succès !",
        body_text=(
            "Félicitations !\n\n"
            "Votre configuration Gmail fonctionne correctement.\n"
            "La photo ci-dessous est intégrée directement dans le corps de l'e-mail."
        ),
        image_path=test_img_path,
    )

    notifier.shutdown()
    print("✅ Opération terminée. Ouvrez votre boîte Gmail pour admirer le résultat !")


if __name__ == "__main__":
    test_gmail()

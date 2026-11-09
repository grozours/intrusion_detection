import os
import mimetypes
import logging
import smtplib
from email.message import EmailMessage
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

logger = logging.getLogger("IntrusionDetection.Notifier")


class EmailNotifier:
    """
    Gestionnaire d'envoi d'e-mails d'alerte, asynchrone (non-bloquant).
    Gère le texte, l'affichage de photo intégrée (inline CID) et la pièce jointe vidéo MP4.
    """

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        use_tls: bool = True,
        use_ssl: bool = False,
        email_from: str = "",
        email_to: str = "",
        mock: bool = False,
    ):
        self.smtp_host = smtp_host.strip()
        self.smtp_port = int(smtp_port)
        self.smtp_user = smtp_user.strip()
        self.smtp_password = smtp_password.strip()
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.email_from = email_from.strip() or self.smtp_user
        self.email_to = email_to.strip()
        self.mock = mock

        # Si les identifiants SMTP ne sont pas renseignés, on passe en mode simulation
        if not self.mock and (not self.smtp_host or not self.email_to):
            logger.warning(
                "Configuration SMTP incomplète (hôte ou destinataire manquant). "
                "Mode SIMULATION activé pour les e-mails."
            )
            self.mock = True

        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="EmailSender")

    def send_alert_async(
        self,
        subject: str,
        body_text: str,
        video_path: str = None,
        image_path: str = None,
    ):
        """Lance l'envoi d'un e-mail en arrière-plan sans bloquer la boucle vidéo."""
        self.executor.submit(
            self._send_email_worker, subject, body_text, video_path, image_path
        )

    def _generate_html(
        self, subject: str, body_text: str, has_image: bool, has_video: bool, video_name: str = ""
    ) -> str:
        paragraphs = "".join(f"<p>{line}</p>" for line in body_text.strip().split("\n\n"))
        paragraphs = paragraphs.replace("\n", "<br>")

        img_html = ""
        if has_image:
            img_html = """
            <div style="margin: 20px 0; text-align: center;">
                <p style="font-weight: bold; color: #4a5568; margin-bottom: 8px;">📷 Capture instantanée :</p>
                <img src="cid:snapshot" alt="Photo de l'alerte" 
                     style="max-width: 100%; height: auto; border-radius: 8px; border: 2px solid #e53e3e; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
            </div>
            """

        video_notice = ""
        if has_video:
            video_notice = f"""
            <div style="background-color: #edf2f7; border-left: 4px solid #3182ce; padding: 12px; margin-top: 15px; border-radius: 4px;">
                <strong>📹 Vidéo attachée :</strong> <code>{video_name}</code> (consultez les pièces jointes).
            </div>
            """

        return f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f7fafc; margin: 0; padding: 20px; color: #2d3748; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 10px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); }}
  .header {{ background: linear-gradient(135deg, #e53e3e, #c53030); color: white; padding: 20px; text-align: center; }}
  .header h2 {{ margin: 0; font-size: 20px; }}
  .content {{ padding: 24px; line-height: 1.6; font-size: 15px; }}
  .footer {{ background: #edf2f7; padding: 12px 24px; font-size: 12px; color: #718096; text-align: center; }}
</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h2>🛡️ ALERTE VIDÉOSURVEILLANCE</h2>
    </div>
    <div class="content">
      {paragraphs}
      {img_html}
      {video_notice}
    </div>
    <div class="footer">
      Notification générée automatiquement par le système d'intrusion USB Camera.
    </div>
  </div>
</body>
</html>
"""

    def _send_email_worker(
        self,
        subject: str,
        body_text: str,
        video_path: str = None,
        image_path: str = None,
    ):
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        has_image = bool(image_path and os.path.exists(image_path))
        has_video = bool(video_path and os.path.exists(video_path))
        video_name = os.path.basename(video_path) if has_video else ""

        if self.mock:
            self._log_mock_email(
                subject, body_text, video_path, image_path, timestamp_str
            )
            return

        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.email_from
            msg["To"] = self.email_to
            msg["Date"] = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")

            # Contenu texte brut (fallback)
            plain_content = body_text
            if has_image:
                plain_content += f"\n\n[Photo capturée : {os.path.basename(image_path)}]"
            if has_video:
                plain_content += f"\n[Vidéo jointe : {video_name}]"
            msg.set_content(plain_content)

            # Contenu HTML enrichi avec la photo intégrée inline
            html_content = self._generate_html(
                subject, body_text, has_image, has_video, video_name
            )
            msg.add_alternative(html_content, subtype="html")

            # Intégration de la photo inline avec CID:snapshot
            if has_image:
                with open(image_path, "rb") as img_file:
                    img_data = img_file.read()
                img_size_kb = len(img_data) / 1024.0
                msg.get_payload()[1].add_related(
                    img_data,
                    maintype="image",
                    subtype="jpeg",
                    cid="<snapshot>",
                    filename=os.path.basename(image_path),
                )
                logger.info(
                    f"[EMAIL] Photo incluse dans le mail : {os.path.basename(image_path)} ({img_size_kb:.1f} Ko)"
                )

            # Ajout de la vidéo MP4 en pièce jointe
            if has_video:
                with open(video_path, "rb") as vid_file:
                    vid_data = vid_file.read()
                vid_size_kb = len(vid_data) / 1024.0
                msg.add_attachment(
                    vid_data,
                    maintype="video",
                    subtype="mp4",
                    filename=video_name,
                )
                logger.info(
                    f"[EMAIL] Vidéo attachée : {video_name} ({vid_size_kb:.1f} Ko)"
                )

            logger.info(f"[EMAIL] Connexion au serveur SMTP {self.smtp_host}:{self.smtp_port}...")

            if self.use_ssl:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15) as server:
                    if self.smtp_user and self.smtp_password:
                        server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                    if self.use_tls:
                        server.starttls()
                    if self.smtp_user and self.smtp_password:
                        server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)

            logger.info(f"✅ [EMAIL ENVOYÉ] Sujet : \"{subject}\" -> {self.email_to}")

        except Exception as e:
            logger.error(f"❌ [ERREUR EMAIL] Échec de l'envoi de l'e-mail \"{subject}\" : {e}")

    def _log_mock_email(
        self,
        subject: str,
        body: str,
        video: str,
        image: str,
        timestamp: str,
    ):
        sep = "=" * 60
        img_info = "Aucune"
        if image:
            if os.path.exists(image):
                sz = os.path.getsize(image) / 1024.0
                img_info = f"{os.path.basename(image)} ({sz:.1f} Ko)"
            else:
                img_info = f"{image} (fichier non trouvé)"

        vid_info = "Aucune"
        if video:
            if os.path.exists(video):
                sz = os.path.getsize(video) / 1024.0
                vid_info = f"{os.path.basename(video)} ({sz:.1f} Ko)"
            else:
                vid_info = f"{video} (fichier non trouvé)"

        lines = [
            "",
            sep,
            f"📧 [SIMULATION E-MAIL] {timestamp}",
            f"De : {self.email_from or 'alerte@local'}",
            f"À : {self.email_to or 'destinataire@exemple.com'}",
            f"Sujet : {subject}",
            f"📷 Photo incluse : {img_info}",
            f"📹 Vidéo jointe  : {vid_info}",
            "-" * 60,
            body.strip(),
            sep,
            "",
        ]
        logger.info("\n".join(lines))

    def shutdown(self):
        """Arrête le pool d'envoi et attend la fin des transferts en cours."""
        logger.info("Fermeture du service d'envoi d'e-mails...")
        self.executor.shutdown(wait=True)

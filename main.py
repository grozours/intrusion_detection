#!/usr/bin/env python3
import os
import sys
import time
import signal
import logging
import threading
from typing import Optional
from datetime import datetime
import cv2

from config import parse_args_and_config
from motion_detector import MotionDetector
from recorder import VideoSegment
from notifier import EmailNotifier
from telegram_bot import TelegramService

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("IntrusionDetection.Main")


class IntrusionDetectionApp:
    def __init__(self):
        self.config = parse_args_and_config()
        self.running = True

        # État de la surveillance (contrôlable à distance via Telegram)
        self.is_armed = True
        self.paused_until = 0.0
        self.alerts_count = 0

        # Mémorisation et verrous
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.camera_lock = threading.Lock()

        # Initialisation du notificateur d'e-mails
        self.notifier = EmailNotifier(
            smtp_host=self.config.smtp_host,
            smtp_port=self.config.smtp_port,
            smtp_user=self.config.smtp_user,
            smtp_password=self.config.smtp_password,
            use_tls=self.config.smtp_use_tls,
            use_ssl=self.config.smtp_use_ssl,
            email_from=self.config.email_from,
            email_to=self.config.email_to,
            mock=self.config.mock_email,
        )

        # Initialisation du service Telegram
        self.telegram = TelegramService(
            token=self.config.telegram_token,
            chat_id=self.config.telegram_chat_id,
            on_arm=self.arm,
            on_disarm=self.disarm,
            on_pause=self.pause,
            on_status=self.get_status_str,
            on_photo_request=self.capture_live_photo,
        )

        # Initialisation du détecteur de mouvement
        self.detector = MotionDetector(
            min_area=self.config.min_area,
            consecutive_frames_required=self.config.consecutive_frames,
        )

        # Caméra et caractéristiques du flux
        self.cap = None
        self.fps = 30.0
        self.frame_size = self.config.resolution
        self.fps_calibrated = False

        # Enregistreur vidéo actif
        self.current_segment = None

        # Gestion des signaux d'interruption
        signal.signal(signal.SIGINT, self._handle_exit_signal)
        signal.signal(signal.SIGTERM, self._handle_exit_signal)

    def _handle_exit_signal(self, signum, frame):
        logger.info("\nSignal d'arrêt reçu. Arrêt propre du programme...")
        self.running = False

    # --- Gestion matérielle de la Caméra USB (Allumage / Extinction) ---

    def open_camera(self) -> bool:
        """Allume matériellement la caméra USB si elle était éteinte."""
        with self.camera_lock:
            if self.cap is not None and self.cap.isOpened():
                return True

            logger.info(f"⚡ Allumage matériel de la caméra (index={self.config.camera_index})...")
            self.cap = cv2.VideoCapture(self.config.camera_index)

            if not self.cap.isOpened():
                logger.error(
                    f"Impossible d'ouvrir la caméra à l'index {self.config.camera_index}."
                )
                self.cap = None
                return False

            target_w, target_h = self.config.resolution
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, target_w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, target_h)

            ret, frame = self.cap.read()
            if not ret or frame is None:
                logger.error("Impossible de lire une frame après allumage.")
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
                return False

            h, w = frame.shape[:2]
            self.frame_size = (w, h)

            # Étalonnage du FPS uniquement au tout premier démarrage
            if not self.fps_calibrated:
                t0 = time.time()
                test_frames = 20
                for _ in range(test_frames):
                    ret, _ = self.cap.read()
                    if not ret:
                        break
                t_elapsed = time.time() - t0
                measured = test_frames / t_elapsed if t_elapsed > 0 else 30.0
                prop_fps = self.cap.get(cv2.CAP_PROP_FPS)
                if 5.0 <= prop_fps <= 120.0:
                    self.fps = prop_fps
                else:
                    self.fps = measured
                self.fps_calibrated = True

            logger.info(
                f"✅ Caméra allumée : {w}x{h} à {self.fps:.1f} FPS (format MJPG)."
            )
            return True

    def release_camera(self):
        """Éteint matériellement la caméra USB (LED coupée, repos thermique)."""
        with self.camera_lock:
            if self.cap is not None:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
                logger.info("🔌 Caméra éteinte matériellement (veille).")

    # --- Actions de contrôle à distance (Telegram) ---

    def arm(self) -> str:
        """Réarme le système de surveillance et rallume la caméra."""
        self.is_armed = True
        self.paused_until = 0.0
        logger.info("🟢 Demande de réarmement reçue via Telegram.")

        if not self.open_camera():
            logger.error("Échec de rallumage de la caméra.")
            return "❌ *Erreur* : Impossible de rallumer la caméra USB."

        # Stabilisation de l'exposition et modèle de fond
        for _ in range(15):
            ret, frame = self.cap.read()
            if ret:
                self.detector.warmup(frame)
                with self.frame_lock:
                    self.latest_frame = frame

        self.detector.reset()
        logger.info("🟢 Caméra rallumée et surveillance RÉARMÉE avec succès.")
        return "🟢 *Système RÉARMÉ avec succès.*\nLa caméra a été rallumée et surveille !"

    def disarm(self) -> str:
        """Désarme le système et éteint matériellement la caméra."""
        self.is_armed = False
        self.paused_until = 0.0

        if self.current_segment:
            try:
                self.current_segment.discard()
            except Exception:
                pass
            self.current_segment = None

        self.detector.reset()
        self.release_camera()

        logger.info("🔴 Système DÉSARMÉ et caméra éteinte via Telegram.")
        return (
            "🔴 *Système DÉSARMÉ.*\n"
            "La caméra a été éteinte matériellement (LED éteinte, repos thermique complet)."
        )

    def pause(self, minutes: int) -> str:
        """Met la surveillance en pause et éteint la caméra."""
        self.is_armed = True
        self.paused_until = time.time() + (minutes * 60)
        end_time_str = datetime.fromtimestamp(self.paused_until).strftime("%H:%M:%S")

        if self.current_segment:
            try:
                self.current_segment.discard()
            except Exception:
                pass
            self.current_segment = None

        self.detector.reset()
        self.release_camera()

        logger.info(
            f"⏸️ Surveillance en PAUSE pour {minutes} min (jusqu'à {end_time_str}). Caméra éteinte."
        )
        return (
            f"⏸️ *Surveillance en pause pendant {minutes} minutes.*\n"
            f"La caméra est éteinte. Rallumage et reprise automatique à *{end_time_str}*."
        )

    def get_status_str(self) -> str:
        """Retourne un résumé complet de l'état actuel pour Telegram."""
        now_str = datetime.now().strftime("%H:%M:%S")
        if not self.is_armed:
            state_str = "🔴 *Désarmé* (surveillance coupée)"
        elif time.time() < self.paused_until:
            rem_min = max(1, int((self.paused_until - time.time()) / 60))
            state_str = f"⏸️ *En pause* (~{rem_min} min restantes)"
        else:
            state_str = "🟢 *Armé & En veille active*"

        cam_state = "Éteinte 🔌 (repos)" if (self.cap is None) else "Allumée ⚡"
        res_str = f"{self.frame_size[0]}x{self.frame_size[1]}"

        return (
            f"🛡️ *État du système de vidéosurveillance :*\n\n"
            f"• État : {state_str}\n"
            f"• Matériel caméra : *{cam_state}* (#{self.config.camera_index}, {res_str} @ {self.fps:.0f} FPS)\n"
            f"• Alertes déclenchées : {self.alerts_count}\n"
            f"• Heure locale : {now_str}\n"
        )

    def capture_live_photo(self) -> Optional[str]:
        """Capture une image instantanée en direct pour Telegram (même si la caméra est éteinte)."""
        was_off = False
        frame = None

        with self.camera_lock:
            if self.cap is None or not self.cap.isOpened():
                was_off = True
                logger.info("Allumage temporaire de la caméra pour la capture demandée...")
                target_w, target_h = self.config.resolution
                temp_cap = cv2.VideoCapture(self.config.camera_index)
                if not temp_cap.isOpened():
                    return None
                temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, target_w)
                temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, target_h)

                # Stabilisation de l'exposition automatique (~0.3s)
                for _ in range(10):
                    temp_cap.read()
                ret, frame = temp_cap.read()
                temp_cap.release()
                logger.info("Caméra rééteinte après la capture de photo.")
            else:
                ret, frame = self.cap.read()

        if frame is None:
            return None

        os.makedirs(self.config.output_dir, exist_ok=True)
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_path = os.path.join(self.config.output_dir, f"live_{now_str}.jpg")

        stamped = frame.copy()
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(
            stamped,
            ts_str,
            (10, self.frame_size[1] - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            stamped,
            ts_str,
            (10, self.frame_size[1] - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

        cv2.imwrite(img_path, stamped)
        return img_path

    def _save_instant_snapshot(self, frame, boxes) -> str:
        """Sauvegarde une photo instantanée au moment de l'alerte."""
        os.makedirs(self.config.output_dir, exist_ok=True)
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_name = f"alerte_immediate_{now_str}.jpg"
        img_path = os.path.join(self.config.output_dir, img_name)

        stamped = frame.copy()
        for (x, y, w, h) in boxes:
            cv2.rectangle(stamped, (x, y), (x + w, y + h), (0, 0, 255), 2)

        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(
            stamped,
            ts_str,
            (10, self.frame_size[1] - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            stamped,
            ts_str,
            (10, self.frame_size[1] - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

        cv2.imwrite(img_path, stamped)
        return img_path

    def _render_framing_display(self, frame, motion_result=None, state_str="IDLE"):
        """Affiche une fenêtre graphique d'aide au cadrage avec repères et détection en direct."""
        try:
            preview = frame.copy()
            h, w = preview.shape[:2]

            # 1. Repères de cadrage (Règle des tiers - lignes discrètes)
            grid_color = (70, 70, 70)
            cv2.line(preview, (w // 3, 0), (w // 3, h), grid_color, 1, cv2.LINE_AA)
            cv2.line(preview, (2 * w // 3, 0), (2 * w // 3, h), grid_color, 1, cv2.LINE_AA)
            cv2.line(preview, (0, h // 3), (w, h // 3), grid_color, 1, cv2.LINE_AA)
            cv2.line(preview, (0, 2 * h // 3), (w, 2 * h // 3), grid_color, 1, cv2.LINE_AA)

            # Réticule central (+)
            cx, cy = w // 2, h // 2
            cv2.line(preview, (cx - 15, cy), (cx + 15, cy), (0, 255, 255), 1, cv2.LINE_AA)
            cv2.line(preview, (cx, cy - 15), (cx, cy + 15), (0, 255, 255), 1, cv2.LINE_AA)

            # 2. Dessin des zones de mouvement en direct
            if motion_result and motion_result.bounding_boxes:
                box_color = (0, 0, 255) if motion_result.detected else (0, 255, 0)
                for (bx, by, bw, bh) in motion_result.bounding_boxes:
                    cv2.rectangle(preview, (bx, by), (bx + bw, by + bh), box_color, 2)
                    cv2.putText(
                        preview,
                        f"{bw * bh} px",
                        (bx, max(15, by - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        box_color,
                        1,
                        cv2.LINE_AA,
                    )

            # 3. Bandeaux d'information (haut et bas)
            overlay = preview.copy()
            cv2.rectangle(overlay, (0, 0), (w, 36), (15, 15, 15), -1)
            cv2.rectangle(overlay, (0, h - 30), (w, h), (15, 15, 15), -1)
            cv2.addWeighted(overlay, 0.75, preview, 0.25, 0, preview)

            # Texte supérieur
            cv2.putText(
                preview,
                "MODE TEST - AIDE AU CADRAGE  |  Fermer : Touche 'q' ou 'Echap'",
                (15, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )

            # Texte inférieur : indicateurs techniques
            motion_px = motion_result.moving_area if motion_result else 0
            status_txt = (
                f"Res: {w}x{h} | FPS: {self.fps:.0f} | Mouvement: {motion_px:.0f} px "
                f"(Seuil: {self.config.min_area} px) | État: {state_str}"
            )
            cv2.putText(
                preview,
                status_txt,
                (15, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

            window_name = "Aide au Cadrage Webcam (Mode Test) - Touche 'q' pour quitter"
            cv2.imshow(window_name, preview)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):  # 'q' ou ESC
                logger.info("Touche 'q' ou Echap pressée. Arrêt du programme...")
                self.running = False

        except cv2.error as e:
            logger.warning(
                f"Affichage graphique indisponible (OpenCV GUI: {e}). L'aide au cadrage est désactivée."
            )
            self.config.show_display = False
        except Exception as e:
            logger.warning(f"Erreur d'affichage : {e}. L'aide au cadrage est désactivée.")
            self.config.show_display = False

    def run_startup_delay(self):
        """Délai d'attente avant armement (1 minute par défaut, 0s si mode test)."""
        delay = self.config.startup_delay
        if delay <= 0:
            logger.info("⚡ [MODE TEST ACTIF] Démarrage immédiat sans délai d'attente.")
            for _ in range(30):
                ret, frame = self.cap.read()
                if ret:
                    self.detector.warmup(frame)
                    with self.frame_lock:
                        self.latest_frame = frame
                    if self.config.show_display:
                        self._render_framing_display(frame, None, "CALIBRATION")
            return

        logger.info(
            f"⏳ Période d'armement initial : surveillance active dans {delay:.0f} secondes."
        )
        logger.info("   (Ce délai vous permet de quitter la pièce et calibre la caméra)")

        t_start = time.time()
        last_log_second = int(delay)

        while self.running and (time.time() - t_start) < delay:
            ret, frame = self.cap.read()
            if ret:
                self.detector.warmup(frame)
                with self.frame_lock:
                    self.latest_frame = frame
                if self.config.show_display:
                    self._render_framing_display(frame, None, "ARMEMENT")

            remaining = int(delay - (time.time() - t_start))
            if remaining != last_log_second and (
                remaining % 10 == 0 or remaining <= 5
            ):
                logger.info(f"⏳ Armement dans {remaining} seconde(s)...")
                last_log_second = remaining

            time.sleep(0.01)

        if self.running:
            logger.info("🔔 SYSTÈME ARMÉ ET PRÊT. Surveillance des mouvements en cours.")

    def run(self):
        """Boucle principale du programme avec machine à états."""
        if not self.open_camera():
            return

        # Démarrage de l'écoute des commandes Telegram
        self.telegram.start()

        self.run_startup_delay()

        # États : "IDLE", "RECORD_INITIAL_3S", "RECORD_CYCLE_10S"
        state = "IDLE"
        cycle_number = 0
        cycle_had_motion = False
        initial_alert_sent = False

        try:
            while self.running:
                # 1. Vérification si le système est DÉSARMÉ à distance
                if not self.is_armed:
                    if self.cap is not None:
                        self.release_camera()
                    time.sleep(0.2)
                    continue

                # 2. Vérification si le système est EN PAUSE temporaire
                if time.time() < self.paused_until:
                    if self.cap is not None:
                        self.release_camera()
                    time.sleep(0.2)
                    continue
                elif self.paused_until > 0.0:
                    # La pause vient de se terminer ! Réarmement et allumage automatique
                    self.paused_until = 0.0
                    logger.info("🟢 Fin de la période de pause. Rallumage de la caméra...")
                    self.open_camera()
                    for _ in range(20):
                        ret, frame = self.cap.read()
                        if ret:
                            self.detector.warmup(frame)
                    self.detector.reset()
                    self.telegram.send_message_async(
                        "🟢 *La pause est terminée.* La caméra a été rallumée et surveille !"
                    )

                # Si la caméra n'est pas ouverte alors qu'on est armé
                if self.cap is None or not self.cap.isOpened():
                    if not self.open_camera():
                        time.sleep(1.0)
                        continue

                with self.camera_lock:
                    ret, frame = self.cap.read() if self.cap else (False, None)

                if not ret or frame is None:
                    time.sleep(0.05)
                    continue

                with self.frame_lock:
                    self.latest_frame = frame

                now = datetime.now()
                now_time_str = now.strftime("%H:%M:%S")
                now_date_str = now.strftime("%d/%m/%Y")

                if state == "IDLE":
                    # Détection passive de mouvement
                    motion = self.detector.process_frame(frame)
                    if self.config.show_display:
                        self._render_framing_display(frame, motion, "VEILLE ACTIVE")

                    if motion.detected:
                        self.alerts_count += 1
                        logger.warning(
                            f"🚨 MOUVEMENT DÉTECTÉ à {now_time_str} ! (surface: {motion.moving_area:.0f} px)"
                        )

                        # Démarrage immédiat de l'enregistrement de 3 secondes à T=0
                        self.current_segment = VideoSegment(
                            output_dir=self.config.output_dir,
                            prefix="alerte_initial_3s",
                            fps=self.fps,
                            frame_size=self.frame_size,
                        )
                        self.current_segment.write(
                            frame,
                            motion_area=motion.moving_area,
                            boxes=motion.bounding_boxes,
                        )
                        initial_alert_sent = False
                        state = "RECORD_INITIAL_3S"

                elif state == "RECORD_INITIAL_3S":
                    motion = self.detector.process_frame(frame)
                    if self.config.show_display:
                        self._render_framing_display(frame, motion, "ENREGISTREMENT 3S")

                    self.current_segment.write(
                        frame,
                        motion_area=motion.moving_area,
                        boxes=motion.bounding_boxes,
                    )

                    # Étape 1 : Prise de photo après 0.5s pour que l'intrus soit bien entré
                    if not initial_alert_sent and (
                        self.current_segment.elapsed() >= self.config.initial_photo_delay
                    ):
                        snapshot_instant_path = self._save_instant_snapshot(
                            frame, motion.bounding_boxes
                        )

                        # Envoi E-mail
                        subject = f"🚨 [ALERTE INTRUSION] Mouvement détecté à {now_time_str}"
                        body = (
                            f"ALERTE DE SÉCURITÉ\n\n"
                            f"Un mouvement a été détecté par votre caméra USB.\n"
                            f"Date : {now_date_str}\n"
                            f"Heure : {now_time_str}\n"
                            f"Caméra : #{self.config.camera_index}\n\n"
                            f"Une photo prise après confirmation du mouvement est affichée ci-dessous.\n"
                            f"Un enregistrement vidéo des 3 premières secondes est en cours "
                            f"et vous sera transmis dans un instant.\n"
                        )
                        self.notifier.send_alert_async(
                            subject=subject,
                            body_text=body,
                            image_path=snapshot_instant_path,
                        )

                        # Envoi Telegram avec boutons d'action rapide (Désarmer / Pause 1h) en tâche asynchrone
                        if self.config.telegram_alerts:
                            self.telegram.send_photo_async(
                                snapshot_instant_path,
                                caption=(
                                    f"🚨 *ALERTE INTRUSION* - Mouvement détecté à {now_time_str} !\n"
                                    f"Une vidéo de 3 secondes arrive..."
                                ),
                                reply_markup=self.telegram.alert_inline_keyboard,
                            )

                        initial_alert_sent = True

                    if self.current_segment.elapsed() >= 3.0:
                        # Sécurité : envoyer la photo initiale si non encore fait
                        if not initial_alert_sent:
                            snapshot_instant_path = self._save_instant_snapshot(
                                frame, motion.bounding_boxes
                            )
                            self.notifier.send_alert_async(
                                subject=f"🚨 [ALERTE INTRUSION] Mouvement détecté à {now_time_str}",
                                body_text="Alerte sécurité intrusion.",
                                image_path=snapshot_instant_path,
                            )
                            if self.config.telegram_alerts:
                                self.telegram.send_photo_async(
                                    snapshot_instant_path,
                                    caption=f"🚨 *ALERTE INTRUSION* - Mouvement détecté à {now_time_str} !",
                                    reply_markup=self.telegram.alert_inline_keyboard,
                                )
                            initial_alert_sent = True

                        # Étape 2 terminée : envoi vidéo 3s
                        result = self.current_segment.finish()

                        # Envoi E-mail
                        subject = f"📹 [ALERTE VIDÉO 3s] Extrait initial - {now_time_str}"
                        body = (
                            f"ALERTE DE SÉCURITÉ - ENREGISTREMENT INITIAL\n\n"
                            f"Veuillez trouver ci-joint l'enregistrement vidéo des 3 premières "
                            f"secondes suivant la détection de mouvement ainsi qu'une photo de la séquence.\n\n"
                            f"Date : {now_date_str}\n"
                            f"Heure : {now_time_str}\n"
                            f"Fichier : {os.path.basename(result.video_path)}\n\n"
                            f"La surveillance continue activement. Si le mouvement persiste, "
                            f"vous recevrez une vidéo et photo toutes les 10 secondes.\n"
                        )
                        self.notifier.send_alert_async(
                            subject=subject,
                            body_text=body,
                            video_path=result.video_path,
                            image_path=result.image_path,
                        )

                        # Envoi Telegram asynchrone
                        if self.config.telegram_alerts:
                            self.telegram.send_video_async(
                                result.video_path,
                                caption=f"📹 *Vidéo initiale (3s)* - {now_time_str}",
                                reply_markup=self.telegram.alert_inline_keyboard,
                            )

                        # Étape 3 : Transition vers les cycles de 10 secondes
                        cycle_number = 1
                        cycle_had_motion = False
                        logger.info(
                            f"Démarrage du cycle 10s #{cycle_number} de surveillance continue..."
                        )
                        self.current_segment = VideoSegment(
                            output_dir=self.config.output_dir,
                            prefix=f"alerte_continue_10s_cycle{cycle_number}",
                            fps=self.fps,
                            frame_size=self.frame_size,
                        )
                        state = "RECORD_CYCLE_10S"

                elif state == "RECORD_CYCLE_10S":
                    motion = self.detector.process_frame(frame)
                    if motion.detected:
                        cycle_had_motion = True

                    self.current_segment.write(
                        frame,
                        motion_area=motion.moving_area,
                        boxes=motion.bounding_boxes,
                    )

                    if self.current_segment.elapsed() >= 10.0:
                        if cycle_had_motion:
                            logger.info(
                                f"Mouvement toujours actif durant le cycle #{cycle_number}."
                            )
                            result = self.current_segment.finish()

                            # Envoi E-mail
                            subject = (
                                f"📹 [ALERTE VIDÉO 10s] Activité continue "
                                f"(Cycle #{cycle_number}) - {now_time_str}"
                            )
                            body = (
                                f"ALERTE DE SÉCURITÉ - ACTIVITÉ CONTINUE\n\n"
                                f"Un mouvement persiste devant la caméra.\n"
                                f"Date : {now_date_str}\n"
                                f"Heure : {now_time_str}\n"
                                f"Cycle : #{cycle_number} (durée : 10 secondes)\n"
                                f"Fichier vidéo ci-joint : {os.path.basename(result.video_path)}\n\n"
                                f"Une photo de l'activité la plus marquée est intégrée ci-dessous.\n"
                            )
                            self.notifier.send_alert_async(
                                subject=subject,
                                body_text=body,
                                video_path=result.video_path,
                                image_path=result.image_path,
                            )

                            # Envoi Telegram asynchrone
                            if self.config.telegram_alerts:
                                self.telegram.send_video_async(
                                    result.video_path,
                                    caption=(
                                        f"📹 *Vidéo continue (10s)* - Cycle #{cycle_number} à {now_time_str}"
                                    ),
                                    reply_markup=self.telegram.alert_inline_keyboard,
                                )

                            # Début du cycle suivant
                            cycle_number += 1
                            cycle_had_motion = False
                            logger.info(
                                f"Démarrage du cycle 10s #{cycle_number} de surveillance continue..."
                            )
                            self.current_segment = VideoSegment(
                                output_dir=self.config.output_dir,
                                prefix=f"alerte_continue_10s_cycle{cycle_number}",
                                fps=self.fps,
                                frame_size=self.frame_size,
                            )
                        else:
                            logger.info(
                                "Aucun mouvement détecté durant les 10 dernières secondes. "
                                "Fin de l'événement d'alerte."
                            )
                            if self.config.keep_videos:
                                self.current_segment.finish()
                            else:
                                self.current_segment.discard()

                            self.detector.reset()
                            self.current_segment = None
                            state = "IDLE"
                            logger.info("Retour en veille active. En attente de mouvement...")

        finally:
            self._cleanup()

    def _cleanup(self):
        """Nettoyage des ressources lors de la fermeture."""
        logger.info("Libération des ressources...")
        self.telegram.stop()

        if self.current_segment:
            try:
                self.current_segment.finish()
            except Exception:
                pass
            self.current_segment = None

        self.release_camera()
        self.notifier.shutdown()
        logger.info("Programme terminé avec succès.")


if __name__ == "__main__":
    app = IntrusionDetectionApp()
    app.run()

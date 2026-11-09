import os
import time
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable, Dict, Tuple
import requests

logger = logging.getLogger("IntrusionDetection.Telegram")


class TelegramService:
    """
    Service d'interaction Telegram pour le système de détection d'intrusion.
    Gère la réception des commandes à distance (Désarmer, Armer, Pause, Statut, Photo)
    et la transmission des alertes (photos + vidéos) de manière asynchrone et résiliente
    avec reconnexion automatique et nouvelle tentative en cas de timeout.
    """

    def __init__(
        self,
        token: str,
        chat_id: Optional[str] = None,
        on_arm: Optional[Callable[[], str]] = None,
        on_disarm: Optional[Callable[[], str]] = None,
        on_pause: Optional[Callable[[int], str]] = None,
        on_status: Optional[Callable[[], str]] = None,
        on_photo_request: Optional[Callable[[], Optional[str]]] = None,
    ):
        self.token = token.strip() if token else ""
        self.chat_id = str(chat_id).strip() if chat_id else ""
        self.enabled = bool(self.token)

        self.on_arm = on_arm
        self.on_disarm = on_disarm
        self.on_pause = on_pause
        self.on_status = on_status
        self.on_photo_request = on_photo_request

        self.running = False
        self.polling_thread: Optional[threading.Thread] = None
        self.last_update_id = 0
        self.base_url = f"https://api.telegram.org/bot{self.token}"

        # Session HTTP persistante pour réutiliser les connexions SSL
        self.session = requests.Session()

        # Pool de threads pour les envois d'alertes en arrière-plan (non-bloquant pour la caméra)
        self.executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="TelegramSender"
        )

        # Clavier persistant interactif sous la zone de saisie
        self.main_keyboard = {
            "keyboard": [
                [{"text": "🔴 Désarmer"}, {"text": "🟢 Armer"}],
                [
                    {"text": "⏸️ Pause 30 min"},
                    {"text": "⏸️ Pause 1h"},
                    {"text": "⏸️ Pause 2h"},
                ],
                [{"text": "📷 Prendre une photo"}, {"text": "ℹ️ Statut"}],
            ],
            "resize_keyboard": True,
            "persistent": True,
        }

        # Boutons d'action rapide sous les alertes
        self.alert_inline_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🔴 Désarmer", "callback_data": "cmd_disarm"},
                    {"text": "⏸️ Pause 1 heure", "callback_data": "cmd_pause_60"},
                ]
            ]
        }

    def start(self):
        """Démarre le thread d'écoute des messages Telegram."""
        if not self.enabled:
            logger.info("Service Telegram non activé (aucun token fourni).")
            return

        self.running = True
        self.polling_thread = threading.Thread(
            target=self._polling_worker, name="TelegramPolling", daemon=True
        )
        self.polling_thread.start()
        logger.info("🤖 Service Telegram démarré (écoute des commandes à distance).")

        if not self.chat_id:
            logger.warning(
                "⚠️ TELEGRAM_CHAT_ID non configuré. "
                "Envoyez /start à votre bot sur Telegram pour lier automatiquement votre compte !"
            )
        else:
            self.send_message_async(
                "🛡️ *Système de vidéosurveillance connecté !*\n"
                "Vous pouvez contrôler et désarmer le système à distance depuis ce chat.",
                reply_markup=self.main_keyboard,
            )

    def stop(self):
        """Arrête proprement le thread d'écoute et le pool d'envois."""
        self.running = False
        if self.polling_thread and self.polling_thread.is_alive():
            self.polling_thread.join(timeout=2.0)
        self.executor.shutdown(wait=False)
        self.session.close()
        logger.info("Service Telegram arrêté.")

    def _post_with_retry(
        self,
        endpoint: str,
        data: Optional[dict] = None,
        files_dict: Optional[dict] = None,
        json_data: Optional[dict] = None,
        timeout: Tuple[float, float] = (5.0, 30.0),
        max_retries: int = 3,
    ) -> bool:
        """
        Envoie une requête POST vers l'API Telegram avec gestion des timeouts,
        reconnexion automatique et tentatives répétées (retries).
        """
        url = f"{self.base_url}/{endpoint}"

        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.post(
                    url,
                    data=data,
                    files=files_dict,
                    json=json_data,
                    timeout=timeout,
                )

                if response.status_code == 200:
                    return True

                if response.status_code == 429:
                    # Limite de débit Telegram (Rate limit)
                    retry_after = 2
                    try:
                        retry_after = int(
                            response.json().get("parameters", {}).get("retry_after", 2)
                        )
                    except Exception:
                        pass
                    logger.warning(
                        f"[Telegram] Limite d'appels atteinte (429). Pause de {retry_after}s avant nouvel essai..."
                    )
                    time.sleep(retry_after)
                    continue

                logger.warning(
                    f"[Telegram] Réponse HTTP {response.status_code} ({response.text[:120]}) "
                    f"sur {endpoint} (tentative {attempt}/{max_retries})"
                )
                if attempt < max_retries:
                    time.sleep(attempt * 1.5)

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                err_type = "Timeout réseau" if isinstance(e, requests.exceptions.Timeout) else "Erreur de connexion"
                if attempt < max_retries:
                    logger.warning(
                        f"⚠️ [Telegram] {err_type} sur {endpoint} (tentative {attempt}/{max_retries}). "
                        f"Nouvel essai dans {attempt * 2}s..."
                    )
                    time.sleep(attempt * 2.0)
                else:
                    logger.error(
                        f"❌ [Telegram] Échec définitif sur {endpoint} après {max_retries} tentatives : {e}"
                    )
            except Exception as e:
                logger.error(f"❌ [Telegram] Erreur inattendue sur {endpoint} : {e}")
                break

        return False

    def _polling_worker(self):
        """Boucle de long-polling pour intercepter les commandes Telegram."""
        # Purge des anciens messages pour ne pas rejouer d'anciennes commandes
        try:
            r = self.session.get(
                f"{self.base_url}/getUpdates",
                params={"offset": -1, "timeout": 1},
                timeout=(5.0, 5.0),
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("result"):
                    self.last_update_id = data["result"][-1]["update_id"] + 1
        except Exception:
            pass

        while self.running:
            try:
                url = f"{self.base_url}/getUpdates"
                params = {"offset": self.last_update_id, "timeout": 20}
                res = self.session.get(url, params=params, timeout=(5.0, 25.0))

                if res.status_code == 200:
                    data = res.json()
                    for update in data.get("result", []):
                        self.last_update_id = update["update_id"] + 1
                        self._process_update(update)
                elif res.status_code == 401:
                    logger.error("❌ Token Telegram invalide (erreur 401).")
                    time.sleep(10)
                else:
                    time.sleep(1)

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                # Normal lors du long-polling ou perte temporaire de signal, on boucle
                time.sleep(1)
            except Exception as e:
                logger.error(f"Erreur inattendue Telegram polling: {e}")
                time.sleep(2)

    def _process_update(self, update: dict):
        """Traite une mise à jour reçue (message texte ou bouton callback)."""
        if "callback_query" in update:
            cb = update["callback_query"]
            cb_id = cb["id"]
            user_chat_id = str(cb["message"]["chat"]["id"])
            data = cb.get("data", "")

            # Répondre à Telegram pour effacer le sablier du bouton
            try:
                self.session.post(
                    f"{self.base_url}/answerCallbackQuery",
                    json={"callback_query_id": cb_id},
                    timeout=(3.0, 5.0),
                )
            except Exception:
                pass

            if not self._check_auth(user_chat_id):
                return

            if data == "cmd_disarm":
                self._handle_disarm()
            elif data == "cmd_pause_60":
                self._handle_pause(60)
            return

        if "message" in update and "text" in update["message"]:
            msg = update["message"]
            user_chat_id = str(msg["chat"]["id"])
            text = msg["text"].strip()

            if not self.chat_id:
                self.chat_id = user_chat_id
                logger.info(f"✅ Compte Telegram associé automatiquement avec le Chat ID : {user_chat_id}")
                self.send_message_async(
                    f"🎉 *Compte lié avec succès !*\n\n"
                    f"Votre Chat ID est : `{user_chat_id}`\n"
                    f"Vous pouvez ajouter `TELEGRAM_CHAT_ID={user_chat_id}` dans votre fichier `.env`.\n\n"
                    f"Vous contrôlez désormais votre système de vidéosurveillance !",
                    reply_markup=self.main_keyboard,
                )
                return

            if not self._check_auth(user_chat_id):
                return

            self._route_command(text)

    def _check_auth(self, user_chat_id: str) -> bool:
        """Vérifie que l'expéditeur est bien le propriétaire autorisé."""
        if str(user_chat_id) != str(self.chat_id):
            logger.warning(
                f"Tentative d'accès non autorisée rejetée depuis le Chat ID : {user_chat_id}"
            )
            return False
        return True

    def _route_command(self, text: str):
        """Achemine la commande saisie vers la bonne action."""
        cmd = text.lower()

        if cmd in ("/start", "/help", "/aide"):
            self.send_message_async(
                "🛡️ *Menu de Contrôle à Distance*\n\n"
                "Utilisez les boutons ci-dessous pour contrôler le système :\n"
                "• *🔴 Désarmer* : stoppe la détection\n"
                "• *🟢 Armer* : réactive la surveillance\n"
                "• *⏸️ Pause* : met en pause temporaire (30m, 1h, 2h)\n"
                "• *📷 Photo* : prend une photo instantanée en direct\n"
                "• *ℹ️ Statut* : état actuel du système",
                reply_markup=self.main_keyboard,
            )

        elif "désarmer" in cmd or "desarmer" in cmd or cmd == "/disarm":
            self._handle_disarm()

        elif "armer" in cmd or cmd == "/arm":
            self._handle_arm()

        elif "30 min" in cmd or cmd == "/pause 30":
            self._handle_pause(30)

        elif "pause 1h" in cmd or cmd == "/pause 60" or cmd == "/pause":
            self._handle_pause(60)

        elif "pause 2h" in cmd or cmd == "/pause 120":
            self._handle_pause(120)

        elif "statut" in cmd or cmd == "/status":
            self._handle_status()

        elif "photo" in cmd or cmd == "/photo":
            self._handle_photo_request()

        else:
            self.send_message_async(
                "Commande non reconnue. Utilisez les boutons du clavier.",
                reply_markup=self.main_keyboard,
            )

    def _handle_disarm(self):
        msg = "🔴 Le système a été *DÉSARMÉ*. La détection de mouvement est coupée."
        if self.on_disarm:
            res = self.on_disarm()
            if res:
                msg = res
        self.send_message_async(msg, reply_markup=self.main_keyboard)

    def _handle_arm(self):
        msg = "🟢 Le système a été *RÉARMÉ*. Surveillance active !"
        if self.on_arm:
            res = self.on_arm()
            if res:
                msg = res
        self.send_message_async(msg, reply_markup=self.main_keyboard)

    def _handle_pause(self, minutes: int):
        msg = f"⏸️ Surveillance mise en pause pour *{minutes} minutes*."
        if self.on_pause:
            res = self.on_pause(minutes)
            if res:
                msg = res
        self.send_message_async(msg, reply_markup=self.main_keyboard)

    def _handle_status(self):
        msg = "ℹ️ Statut du système inconnu."
        if self.on_status:
            msg = self.on_status()
        self.send_message_async(msg, reply_markup=self.main_keyboard)

    def _handle_photo_request(self):
        self.send_message_async("📸 Capture de la caméra en cours...")
        photo_path = None
        if self.on_photo_request:
            photo_path = self.on_photo_request()

        if photo_path and os.path.exists(photo_path):
            now_str = time.strftime("%H:%M:%S")
            self.send_photo_async(
                photo_path,
                caption=f"📷 Vue en direct de la caméra à {now_str}",
                reply_markup=self.main_keyboard,
            )
        else:
            self.send_message_async(
                "❌ Impossible de capturer la photo (caméra occupée ou indisponible)."
            )

    # --- Méthodes d'envoi réseau résilientes et asynchrones ---

    def send_message(self, text: str, reply_markup=None) -> bool:
        """Envoie un message texte sur Telegram (synchrone avec retries)."""
        if not self.enabled or not self.chat_id:
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        return self._post_with_retry("sendMessage", json_data=payload, timeout=(5.0, 15.0))

    def send_message_async(self, text: str, reply_markup=None):
        """Envoie un message texte en arrière-plan sans bloquer la boucle principale."""
        if not self.enabled or not self.chat_id:
            return
        self.executor.submit(self.send_message, text, reply_markup)

    def send_photo(self, photo_path: str, caption: str = "", reply_markup=None) -> bool:
        """Envoie une photo sur Telegram (avec retries et timeout étendu)."""
        if not self.enabled or not self.chat_id or not os.path.exists(photo_path):
            return False

        try:
            with open(photo_path, "rb") as f:
                photo_bytes = f.read()

            filename = os.path.basename(photo_path)
            data = {"chat_id": self.chat_id, "parse_mode": "Markdown"}
            if caption:
                data["caption"] = caption
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)

            files_dict = {"photo": (filename, photo_bytes)}
            # Timeout : 5s pour se connecter, 35s pour transférer la photo
            return self._post_with_retry(
                "sendPhoto",
                data=data,
                files_dict=files_dict,
                timeout=(5.0, 35.0),
                max_retries=3,
            )
        except Exception as e:
            logger.error(f"Échec lecture photo {photo_path} : {e}")
            return False

    def send_photo_async(self, photo_path: str, caption: str = "", reply_markup=None):
        """Envoie une photo en arrière-plan (non-bloquant pour la caméra)."""
        if not self.enabled or not self.chat_id or not os.path.exists(photo_path):
            return
        self.executor.submit(self.send_photo, photo_path, caption, reply_markup)

    def send_video(self, video_path: str, caption: str = "", reply_markup=None) -> bool:
        """Envoie une vidéo sur Telegram (avec retries et timeout de 60s)."""
        if not self.enabled or not self.chat_id or not os.path.exists(video_path):
            return False

        try:
            with open(video_path, "rb") as f:
                video_bytes = f.read()

            filename = os.path.basename(video_path)
            data = {"chat_id": self.chat_id, "parse_mode": "Markdown"}
            if caption:
                data["caption"] = caption
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)

            files_dict = {"video": (filename, video_bytes)}
            # Timeout : 5s pour se connecter, 60s pour transférer la vidéo
            return self._post_with_retry(
                "sendVideo",
                data=data,
                files_dict=files_dict,
                timeout=(5.0, 60.0),
                max_retries=3,
            )
        except Exception as e:
            logger.error(f"Échec lecture vidéo {video_path} : {e}")
            return False

    def send_video_async(self, video_path: str, caption: str = "", reply_markup=None):
        """Envoie une vidéo en arrière-plan (non-bloquant pour la caméra)."""
        if not self.enabled or not self.chat_id or not os.path.exists(video_path):
            return
        self.executor.submit(self.send_video, video_path, caption, reply_markup)

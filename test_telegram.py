import unittest
from unittest.mock import MagicMock, patch
import requests
from telegram_bot import TelegramService


class TestTelegramService(unittest.TestCase):
    def test_disabled_when_no_token(self):
        service = TelegramService(token="")
        self.assertFalse(service.enabled)

    def test_security_rejection_unauthorized_user(self):
        service = TelegramService(token="fake_token", chat_id="12345")
        self.assertFalse(service._check_auth("99999"))
        self.assertTrue(service._check_auth("12345"))

    def test_command_routing(self):
        mock_arm = MagicMock(return_value="ARMED")
        mock_disarm = MagicMock(return_value="DISARMED")
        mock_pause = MagicMock(return_value="PAUSED")
        mock_status = MagicMock(return_value="STATUS")

        service = TelegramService(
            token="fake_token",
            chat_id="12345",
            on_arm=mock_arm,
            on_disarm=mock_disarm,
            on_pause=mock_pause,
            on_status=mock_status,
        )
        service.send_message_async = MagicMock()

        # Test Arm
        service._route_command("🟢 Armer")
        mock_arm.assert_called_once()

        # Test Disarm
        service._route_command("🔴 Désarmer")
        mock_disarm.assert_called_once()

        # Test Pause
        service._route_command("⏸️ Pause 1h")
        mock_pause.assert_called_once_with(60)

        # Test Status
        service._route_command("ℹ️ Statut")
        mock_status.assert_called_once()

    def test_callback_button_handling(self):
        mock_disarm = MagicMock(return_value="DISARMED")
        service = TelegramService(
            token="fake_token",
            chat_id="12345",
            on_disarm=mock_disarm,
        )
        service.send_message_async = MagicMock()

        update = {
            "callback_query": {
                "id": "cb1",
                "message": {"chat": {"id": 12345}},
                "data": "cmd_disarm",
            }
        }
        service._process_update(update)
        mock_disarm.assert_called_once()

    def test_retry_on_timeout(self):
        service = TelegramService(token="fake_token", chat_id="12345")
        # Simuler un premier appel qui timeout, puis un second qui réussit
        mock_resp_success = MagicMock()
        mock_resp_success.status_code = 200

        with patch.object(service.session, "post") as mock_post:
            mock_post.side_effect = [
                requests.exceptions.ReadTimeout("Read timed out"),
                mock_resp_success,
            ]
            success = service._post_with_retry("testEndpoint", max_retries=2)
            self.assertTrue(success)
            self.assertEqual(mock_post.call_count, 2)

    def test_send_message_fallback_on_parse_error(self):
        """Vérifie que send_message bascule automatiquement en texte brut si Telegram renvoie une erreur 400 (Bad Request)."""
        service = TelegramService(token="fake_token", chat_id="12345")
        mock_resp_400 = MagicMock()
        mock_resp_400.status_code = 400
        mock_resp_400.text = '{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities"}'

        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200

        with patch.object(service.session, "post") as mock_post:
            # 1er appel avec parse_mode=Markdown échoue (400), 2e appel sans parse_mode réussit (200)
            mock_post.side_effect = [mock_resp_400, mock_resp_200]
            success = service.send_message("Message avec caractère problématique (ex: un_seul_underscore)")
            self.assertTrue(success)
            self.assertEqual(mock_post.call_count, 2)
            # Vérifier que le second appel n'a pas de parse_mode
            second_call_json = mock_post.call_args_list[1][1]["json"]
            self.assertNotIn("parse_mode", second_call_json)

    def test_handler_exception_safety(self):
        """Vérifie que les exceptions levées par les callbacks ne font pas planter le service."""
        mock_failing_status = MagicMock(side_effect=RuntimeError("Erreur interne simulée"))
        mock_failing_disarm = MagicMock(side_effect=ValueError("Erreur disarm simulée"))

        service = TelegramService(
            token="fake_token",
            chat_id="12345",
            on_status=mock_failing_status,
            on_disarm=mock_failing_disarm,
        )
        service.send_message_async = MagicMock()

        # Tester statut avec exception
        service._handle_status()
        service.send_message_async.assert_called_once()
        self.assertIn("Erreur statut", service.send_message_async.call_args[0][0])

        # Tester disarm avec exception
        service.send_message_async.reset_mock()
        service._handle_disarm()
        service.send_message_async.assert_called_once()
        self.assertIn("Erreur lors du désarmement", service.send_message_async.call_args[0][0])


if __name__ == "__main__":
    unittest.main()

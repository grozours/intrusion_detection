import os
import time
import unittest
import numpy as np
import cv2
import tempfile
import shutil

from notifier import EmailNotifier
from motion_detector import MotionDetector
from recorder import VideoSegment
from config import parse_exclude_zones


class TestIntrusionDetection(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_email_notifier_mock_with_image_and_video(self):
        """Vérifie que le notificateur gère la photo inline et la vidéo sans erreur."""
        notifier = EmailNotifier(
            smtp_host="",
            email_to="destinataire@test.com",
            mock=True,
        )

        test_img = os.path.join(self.test_dir, "test.jpg")
        test_vid = os.path.join(self.test_dir, "test.mp4")

        # Création d'une fausse image et vidéo
        cv2.imwrite(test_img, np.zeros((100, 100, 3), dtype=np.uint8))
        with open(test_vid, "wb") as f:
            f.write(b"fake video")

        notifier.send_alert_async(
            subject="Alerte Test Complète",
            body_text="Corps du message",
            video_path=test_vid,
            image_path=test_img,
        )
        notifier.shutdown()
        self.assertTrue(True)

    def test_email_notifier_disabled_when_user_empty(self):
        """Vérifie que le notificateur e-mail est désactivé si smtp_user est vide."""
        notifier = EmailNotifier(
            smtp_host="smtp.gmail.com",
            smtp_user="",
            smtp_password="",
            email_to="test@test.com",
            mock=False,
        )
        self.assertFalse(notifier.enabled)
        # L'appel à send_alert_async ne doit rien soumettre ni lever d'exception
        notifier.send_alert_async("Sujet", "Corps")
        notifier.shutdown()

    def test_motion_detector_static_and_motion(self):
        """Vérifie que le détecteur ignore les images fixes et réagit au mouvement."""
        detector = MotionDetector(min_area=500, consecutive_frames_required=2)

        base_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        base_frame[:] = (100, 100, 100)

        for _ in range(15):
            detector.warmup(base_frame)

        for _ in range(5):
            res = detector.process_frame(base_frame)
            self.assertFalse(res.detected)

        motion_frame = base_frame.copy()
        cv2.rectangle(motion_frame, (200, 200), (300, 300), (255, 255, 255), -1)

        res1 = detector.process_frame(motion_frame)
        res2 = detector.process_frame(motion_frame)
        self.assertTrue(res2.detected)
        self.assertGreater(res2.moving_area, 500)

    def test_video_segment_recording_and_snapshot(self):
        """Vérifie la création conjointe du MP4 et de la photo JPEG."""
        segment = VideoSegment(
            output_dir=self.test_dir,
            prefix="test_clip",
            fps=20.0,
            frame_size=(320, 240),
            add_timestamp=True,
        )

        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        for i in range(10):
            segment.write(frame, motion_area=float(i * 100))

        result = segment.finish()
        self.assertTrue(os.path.exists(result.video_path))
        self.assertTrue(os.path.exists(result.image_path))
        self.assertGreater(os.path.getsize(result.video_path), 0)
        self.assertGreater(os.path.getsize(result.image_path), 0)

    def test_motion_detector_with_exclusion_zone(self):
        """Vérifie que le mouvement situé dans la zone d'exclusion est totalement ignoré."""
        # Zone d'exclusion dans le coin inférieur gauche : x de 0 à 30%, y de 70 à 100%
        # Sur une image 640x480 : x in [0, 192], y in [336, 480]
        detector = MotionDetector(
            min_area=400,
            consecutive_frames_required=1,
            exclude_zones=[(0.0, 70.0, 30.0, 100.0)],
        )

        base_frame = np.ones((480, 640, 3), dtype=np.uint8) * 100
        for _ in range(15):
            detector.warmup(base_frame)

        # 1. Simuler un faisceau lumineux sous la porte (DANS la zone exclue)
        hallway_light_frame = base_frame.copy()
        cv2.rectangle(hallway_light_frame, (20, 380), (160, 460), (255, 255, 255), -1)
        res_ignored = detector.process_frame(hallway_light_frame)
        self.assertFalse(
            res_ignored.detected,
            "Le mouvement sous la porte aurait dû être masqué et ignoré !",
        )
        self.assertEqual(res_ignored.contours_count, 0)

        # 2. Simuler un intrus au milieu de la pièce (HORS de la zone exclue)
        intruder_frame = base_frame.copy()
        cv2.rectangle(intruder_frame, (250, 150), (350, 300), (255, 255, 255), -1)
        res_detected = detector.process_frame(intruder_frame)
        self.assertTrue(
            res_detected.detected,
            "L'intrus au centre de la pièce doit être détecté !",
        )
        self.assertGreaterEqual(res_detected.contours_count, 1)

    def test_door_detection_and_bottom_20_percent(self):
        """Vérifie la détection géométrique d'une porte et le calcul des 20% inférieurs."""
        # Créer une image synthétique avec une porte intérieure
        # Ratio hauteur / largeur ~ 2.45
        frame = np.ones((720, 1280, 3), dtype=np.uint8) * 200
        # Dessiner le cadre de la porte
        dx, dy, dw, dh = 200, 100, 240, 580
        cv2.rectangle(frame, (dx, dy), (dx + dw, dy + dh), (110, 80, 60), -1)
        cv2.rectangle(frame, (dx - 4, dy - 4), (dx + dw + 4, dy + dh + 4), (50, 30, 20), 4)

        candidates = MotionDetector.detect_door_candidates(frame)
        self.assertGreater(len(candidates), 0, "Une porte devrait être détectée !")

        # Vérifier le calcul des 20% inférieurs
        best_door = candidates[0]
        bx, by, bw, bh = best_door
        zx1, zy1, zx2, zy2 = MotionDetector.calculate_bottom_zone(best_door, bottom_ratio=0.20)

        # Les 20% inférieurs doivent correspondre au bas de la porte
        expected_cut_y = by + int(bh * 0.80)
        self.assertEqual(zx1, bx)
        self.assertEqual(zx2, bx + bw)
        self.assertEqual(zy1, expected_cut_y)
        self.assertEqual(zy2, by + bh)

    def test_parse_exclude_zones(self):
        """Vérifie le parsing des formats de zones d'exclusion."""
        # Pourcentage simple
        z1 = parse_exclude_zones("0,75,35,100")
        self.assertEqual(z1, [(0.0, 75.0, 35.0, 100.0)])

        # Multiples zones avec espaces et symboles %
        z2 = parse_exclude_zones("0%, 75%, 35%, 100% ; 70, 80, 100, 100")
        self.assertEqual(len(z2), 2)
        self.assertEqual(z2[0], (0.0, 75.0, 35.0, 100.0))
        self.assertEqual(z2[1], (70.0, 80.0, 100.0, 100.0))

        # Chaîne vide ou invalide
        self.assertEqual(parse_exclude_zones(""), [])
        self.assertEqual(parse_exclude_zones("invalide, format"), [])


if __name__ == "__main__":
    unittest.main()

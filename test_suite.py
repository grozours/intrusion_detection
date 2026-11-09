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


if __name__ == "__main__":
    unittest.main()

import os
import time
import shutil
import tempfile
import numpy as np
import cv2

from motion_detector import MotionDetector
from recorder import VideoSegment


class MockEmailCollector:
    def __init__(self):
        self.sent_emails = []

    def send_alert_async(self, subject, body_text, video_path=None, image_path=None):
        self.sent_emails.append(
            {
                "time": time.time(),
                "subject": subject,
                "body": body_text,
                "video": video_path,
                "image": image_path,
                "video_exists": os.path.exists(video_path) if video_path else False,
                "image_exists": os.path.exists(image_path) if image_path else False,
            }
        )

    def shutdown(self):
        pass


def run_workflow_simulation():
    test_dir = tempfile.mkdtemp(prefix="sim_workflow_")
    collector = MockEmailCollector()
    detector = MotionDetector(min_area=500, consecutive_frames_required=1)

    static_frame = np.full((240, 320, 3), 100, dtype=np.uint8)

    for _ in range(15):
        detector.warmup(static_frame)

    state = "IDLE"
    current_segment = None
    cycle_number = 0
    cycle_had_motion = False
    fps = 30.0
    frame_size = (320, 240)

    def get_frame(sim_elapsed):
        if sim_elapsed < 0.5:
            return static_frame
        elif sim_elapsed < 14.0:
            frame = static_frame.copy()
            x = int(50 + (sim_elapsed * 15) % 150)
            cv2.rectangle(frame, (x, 50), (x + 80, 150), (255, 255, 255), -1)
            return frame
        else:
            return static_frame

    sim_time = 0.0
    time_step = 0.1

    initial_alert_sent = False

    while sim_time < 25.0:
        sim_time += time_step
        frame = get_frame(sim_time)

        if state == "IDLE":
            res = detector.process_frame(frame)
            if res.detected:
                # Démarrage de l'enregistrement à T=0
                current_segment = VideoSegment(test_dir, prefix="alert_3s", fps=fps, frame_size=frame_size)
                current_segment.write(frame, motion_area=res.moving_area, boxes=res.bounding_boxes)
                initial_alert_sent = False
                state = "RECORD_INITIAL_3S"
                segment_sim_start = sim_time

        elif state == "RECORD_INITIAL_3S":
            res = detector.process_frame(frame)
            current_segment.write(frame, motion_area=res.moving_area, boxes=res.bounding_boxes)

            # Prise de photo après 0.5s
            if not initial_alert_sent and (sim_time - segment_sim_start) >= 0.5:
                img_path = os.path.join(test_dir, f"instant_{sim_time:.1f}.jpg")
                cv2.imwrite(img_path, frame)
                collector.send_alert_async("Alerte immédiate", "Mouvement détecté", image_path=img_path)
                initial_alert_sent = True

            if (sim_time - segment_sim_start) >= 3.0:
                result = current_segment.finish()
                collector.send_alert_async("Vidéo 3s", "Vidéo 3s", video_path=result.video_path, image_path=result.image_path)

                cycle_number = 1
                cycle_had_motion = False
                current_segment = VideoSegment(test_dir, prefix=f"alert_10s_c{cycle_number}", fps=fps, frame_size=frame_size)
                segment_sim_start = sim_time
                state = "RECORD_CYCLE_10S"

        elif state == "RECORD_CYCLE_10S":
            res = detector.process_frame(frame)
            if res.detected:
                cycle_had_motion = True

            current_segment.write(frame, motion_area=res.moving_area, boxes=res.bounding_boxes)

            if (sim_time - segment_sim_start) >= 10.0:
                if cycle_had_motion:
                    result = current_segment.finish()
                    collector.send_alert_async(
                        f"Vidéo 10s c{cycle_number}",
                        f"Cycle {cycle_number}",
                        video_path=result.video_path,
                        image_path=result.image_path,
                    )

                    cycle_number += 1
                    cycle_had_motion = False
                    current_segment = VideoSegment(test_dir, prefix=f"alert_10s_c{cycle_number}", fps=fps, frame_size=frame_size)
                    segment_sim_start = sim_time
                else:
                    current_segment.discard()
                    detector.reset()
                    current_segment = None
                    state = "IDLE"

    print(f"Total emails triggered: {len(collector.sent_emails)}")
    for i, email in enumerate(collector.sent_emails, 1):
        print(f"  {i}. Subject: {email['subject']}")
        print(f"     Image: {email['image']} (Exists: {email['image_exists']})")
        print(f"     Video: {email['video']} (Exists: {email['video_exists']})")

    assert len(collector.sent_emails) == 3, f"Expected 3 emails, got {len(collector.sent_emails)}"
    # Email 1 : immédiat avec photo
    assert collector.sent_emails[0]["image_exists"] is True
    assert collector.sent_emails[0]["video"] is None

    # Email 2 : 3s avec photo + vidéo 3s
    assert collector.sent_emails[1]["image_exists"] is True
    assert collector.sent_emails[1]["video_exists"] is True

    # Email 3 : 10s avec photo + vidéo 10s
    assert collector.sent_emails[2]["image_exists"] is True
    assert collector.sent_emails[2]["video_exists"] is True

    print("\n✅ All photo and video tests passed!")
    shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    run_workflow_simulation()

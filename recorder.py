import os
import cv2
import time
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Tuple, Optional, List

logger = logging.getLogger("IntrusionDetection.Recorder")


@dataclass
class SegmentResult:
    video_path: str
    image_path: Optional[str]
    duration: float
    frames_count: int


class VideoSegment:
    """
    Enregistreur d'un segment vidéo individuel au format MP4 (codec mp4v)
    avec extraction automatique d'une photo représentative (snapshot JPEG).
    """

    def __init__(
        self,
        output_dir: str,
        prefix: str = "alert",
        fps: float = 30.0,
        frame_size: tuple = (640, 480),
        add_timestamp: bool = True,
    ):
        self.output_dir = output_dir
        self.prefix = prefix
        self.fps = max(1.0, float(fps))
        self.frame_size = frame_size
        self.add_timestamp = add_timestamp

        os.makedirs(self.output_dir, exist_ok=True)

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.base_name = f"{self.prefix}_{now_str}"
        self.filename_mp4 = f"{self.base_name}.mp4"
        self.filepath_mp4 = os.path.join(self.output_dir, self.filename_mp4)
        self.filepath_jpg = os.path.join(self.output_dir, f"{self.base_name}.jpg")

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(self.filepath_mp4, fourcc, self.fps, self.frame_size)
        if not self.writer.isOpened():
            logger.error(f"Impossible d'ouvrir le VideoWriter pour {self.filepath_mp4}")

        self.start_time = time.time()
        self.frames_count = 0
        self.is_closed = False

        # Suivi de la meilleure image (celle avec le plus fort mouvement) pour la photo
        self.best_snapshot_frame = None
        self.max_motion_area = -1.0
        self.best_boxes = []

    def _stamp_frame(self, frame, boxes: List[Tuple[int, int, int, int]] = None):
        """Ajoute l'horodatage et les boîtes de détection sur l'image."""
        stamped = frame.copy()
        if boxes:
            for (x, y, w, h) in boxes:
                cv2.rectangle(stamped, (x, y), (x + w, y + h), (0, 0, 255), 2)

        if self.add_timestamp:
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Fond noir puis texte blanc pour une lisibilité optimale
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
        return stamped

    def write(self, frame, motion_area: float = 0.0, boxes: List[Tuple[int, int, int, int]] = None):
        """Écrit une frame dans le fichier vidéo et met à jour le cliché représentatif."""
        if self.is_closed or not self.writer or not self.writer.isOpened():
            return

        out_frame = self._stamp_frame(frame)
        self.writer.write(out_frame)
        self.frames_count += 1

        # Mémorise l'image ayant le mouvement le plus significatif
        if motion_area > self.max_motion_area or self.best_snapshot_frame is None:
            self.max_motion_area = motion_area
            self.best_snapshot_frame = frame.copy()
            self.best_boxes = boxes or []

    def elapsed(self) -> float:
        """Retourne le temps écoulé en secondes depuis le début de ce segment."""
        return time.time() - self.start_time

    def finish(self) -> SegmentResult:
        """Finalise le fichier vidéo et génère la photo JPEG représentative."""
        if not self.is_closed:
            self.is_closed = True
            if self.writer:
                self.writer.release()
                self.writer = None

            # Sauvegarde de la photo représentative
            image_saved_path = None
            if self.best_snapshot_frame is not None:
                snapshot_to_save = self._stamp_frame(
                    self.best_snapshot_frame, self.best_boxes
                )
                if cv2.imwrite(self.filepath_jpg, snapshot_to_save):
                    image_saved_path = self.filepath_jpg

            actual_duration = self.elapsed()
            vid_kb = (
                os.path.getsize(self.filepath_mp4) / 1024.0
                if os.path.exists(self.filepath_mp4)
                else 0
            )
            img_kb = (
                os.path.getsize(image_saved_path) / 1024.0
                if image_saved_path and os.path.exists(image_saved_path)
                else 0
            )

            logger.info(
                f"Vidéo et photo générées : {self.filename_mp4} ({actual_duration:.1f}s, {vid_kb:.1f} Ko) "
                f"+ {os.path.basename(self.filepath_jpg)} ({img_kb:.1f} Ko)"
            )

            return SegmentResult(
                video_path=self.filepath_mp4,
                image_path=image_saved_path,
                duration=actual_duration,
                frames_count=self.frames_count,
            )

        return SegmentResult(
            video_path=self.filepath_mp4,
            image_path=self.filepath_jpg if os.path.exists(self.filepath_jpg) else None,
            duration=self.elapsed(),
            frames_count=self.frames_count,
        )

    def discard(self):
        """Ferme et supprime les fichiers temporaires (si aucun mouvement n'a été détecté)."""
        if not self.is_closed:
            self.is_closed = True
            if self.writer:
                self.writer.release()
                self.writer = None

        for path in (self.filepath_mp4, self.filepath_jpg):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as e:
                    logger.warning(f"Impossible de supprimer {path} : {e}")

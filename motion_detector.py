import cv2
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class MotionResult:
    detected: bool
    moving_area: float
    contours_count: int
    bounding_boxes: List[Tuple[int, int, int, int]]


class MotionDetector:
    """
    Détecteur de mouvement par soustraction d'arrière-plan (MOG2).
    Intègre un filtrage des ombres, du bruit et un seuil de surface minimum.
    """

    def __init__(
        self,
        min_area: int = 1200,
        var_threshold: int = 35,
        history: int = 500,
        detect_shadows: bool = True,
        consecutive_frames_required: int = 2,
    ):
        self.min_area = min_area
        self.var_threshold = var_threshold
        self.history = history
        self.detect_shadows = detect_shadows
        self.consecutive_frames_required = max(1, consecutive_frames_required)
        self.consecutive_count = 0

        # Substracteur d'arrière-plan MOG2 de OpenCV
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=self.history,
            varThreshold=self.var_threshold,
            detectShadows=self.detect_shadows,
        )

        self.kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        self.kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    def warmup(self, frame: np.ndarray):
        """Alimente le modèle d'arrière-plan sans déclencher d'alerte."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (21, 21), 0)
        self.fgbg.apply(blurred)

    def process_frame(self, frame: np.ndarray) -> MotionResult:
        """
        Analyse une image et retourne le résultat de détection de mouvement.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (21, 21), 0)

        # Calcul du masque de premier plan
        fg_mask = self.fgbg.apply(blurred)

        # Les ombres ont la valeur 127 dans MOG2 : on ne garde que le vrai premier plan (255)
        _, thresh = cv2.threshold(fg_mask, 250, 255, cv2.THRESH_BINARY)

        # Élimination du bruit (ouverture morphologique)
        cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, self.kernel_open)
        # Dilatation pour regrouper les zones en mouvement
        dilated = cv2.dilate(cleaned, self.kernel_dilate, iterations=2)

        contours, _ = cv2.findContours(
            dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        significant_boxes = []
        total_moving_area = 0.0

        for contour in contours:
            area = cv2.contourArea(contour)
            if area >= self.min_area:
                total_moving_area += area
                x, y, w, h = cv2.boundingRect(contour)
                significant_boxes.append((x, y, w, h))

        frame_has_motion = len(significant_boxes) > 0

        if frame_has_motion:
            self.consecutive_count += 1
        else:
            self.consecutive_count = 0

        confirmed_motion = self.consecutive_count >= self.consecutive_frames_required

        return MotionResult(
            detected=confirmed_motion,
            moving_area=total_moving_area,
            contours_count=len(significant_boxes),
            bounding_boxes=significant_boxes,
        )

    def reset(self):
        """Réinitialise les compteurs et recrée un modèle de fond vierge."""
        self.consecutive_count = 0
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=self.history,
            varThreshold=self.var_threshold,
            detectShadows=self.detect_shadows,
        )

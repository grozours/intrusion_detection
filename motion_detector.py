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
    Intègre un filtrage des ombres, du bruit, un seuil de surface minimum,
    et le masquage de zones d'exclusion (pour ignorer l'éclairage sous une porte, etc.).
    """

    def __init__(
        self,
        min_area: int = 1200,
        var_threshold: int = 35,
        history: int = 500,
        detect_shadows: bool = True,
        consecutive_frames_required: int = 2,
        exclude_zones: List[Tuple[float, float, float, float]] = None,
    ):
        self.min_area = min_area
        self.var_threshold = var_threshold
        self.history = history
        self.detect_shadows = detect_shadows
        self.consecutive_frames_required = max(1, consecutive_frames_required)
        self.consecutive_count = 0
        self.exclude_zones = list(exclude_zones) if exclude_zones else []

        # Substracteur d'arrière-plan MOG2 de OpenCV
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=self.history,
            varThreshold=self.var_threshold,
            detectShadows=self.detect_shadows,
        )

        self.kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        self.kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    @staticmethod
    def resolve_zone_pixels(
        zone: Tuple[float, float, float, float], width: int, height: int
    ) -> Tuple[int, int, int, int]:
        """
        Convertit une zone (pourcentage 0-100, normalisé 0-1 ou pixels absolus)
        en coordonnées pixels entières (x1, y1, x2, y2).
        """
        x1, y1, x2, y2 = zone
        max_val = max(abs(x1), abs(y1), abs(x2), abs(y2))

        if max_val <= 1.0:
            # Valeurs normalisées entre 0.0 et 1.0
            px1 = int(x1 * width)
            py1 = int(y1 * height)
            px2 = int(x2 * width)
            py2 = int(y2 * height)
        elif max_val <= 100.0:
            # Valeurs en pourcentages (0% à 100%)
            px1 = int((x1 / 100.0) * width)
            py1 = int((y1 / 100.0) * height)
            px2 = int((x2 / 100.0) * width)
            py2 = int((y2 / 100.0) * height)
        else:
            # Valeurs absolues en pixels
            px1, py1, px2, py2 = int(x1), int(y1), int(x2), int(y2)

        # Assurer que x1 <= x2 et y1 <= y2 et restreindre aux dimensions de l'image
        rx1, rx2 = min(px1, px2), max(px1, px2)
        ry1, ry2 = min(py1, py2), max(py1, py2)
        rx1 = max(0, min(width, rx1))
        rx2 = max(0, min(width, rx2))
        ry1 = max(0, min(height, ry1))
        ry2 = max(0, min(height, ry2))

        return (rx1, ry1, rx2, ry2)

    @staticmethod
    def detect_door_candidates(frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        Détecte automatiquement les cadres de portes intérieures dans une image
        par analyse géométrique des arêtes verticales et des proportions standards.
        Retourne la liste des boîtes (x, y, w, h) triées par probabilité / surface.
        """
        h_img, w_img = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Filtre bilatéral pour préserver les arêtes nettes du cadre
        filtered = cv2.bilateralFilter(gray, 7, 50, 50)
        edges = cv2.Canny(filtered, 30, 100)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        dilated = cv2.dilate(edges, kernel, iterations=1)

        contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w < 30 or h < 80:
                continue
            ratio = h / float(w)
            # Critères d'une porte intérieure standard :
            # 1. Ratio hauteur/largeur entre 1.6 et 3.5 (standard 204cm x 83cm = ~2.45)
            # 2. Hauteur de porte occupant au moins 30% de la hauteur de l'image
            # 3. Base de la porte située dans la moitié inférieure de l'image (près du sol)
            if 1.6 <= ratio <= 3.5 and h >= (h_img * 0.30) and (y + h) >= (h_img * 0.50):
                candidates.append((x, y, w, h, w * h))

        # Trier par surface décroissante
        candidates.sort(key=lambda b: b[4], reverse=True)
        return [(c[0], c[1], c[2], c[3]) for c in candidates]

    @staticmethod
    def calculate_bottom_zone(
        door_box: Tuple[int, int, int, int], bottom_ratio: float = 0.20
    ) -> Tuple[int, int, int, int]:
        """
        Calcule les coordonnées pixels (x1, y1, x2, y2) de la zone inférieure
        (par exemple les 20% du bas) d'une porte.
        door_box: (x, y, w, h)
        """
        x, y, w, h = door_box
        cut_y = y + int(h * (1.0 - bottom_ratio))
        return (x, cut_y, x + w, y + h)

    def warmup(self, frame: np.ndarray):
        """Alimente le modèle d'arrière-plan sans déclencher d'alerte."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (21, 21), 0)
        self.fgbg.apply(blurred)

    def process_frame(self, frame: np.ndarray) -> MotionResult:
        """
        Analyse une image et retourne le résultat de détection de mouvement.
        Les mouvements situés dans les zones d'exclusion sont totalement ignorés.
        """
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (21, 21), 0)

        # Calcul du masque de premier plan
        fg_mask = self.fgbg.apply(blurred)

        # Les ombres ont la valeur 127 dans MOG2 : on ne garde que le vrai premier plan (255)
        _, thresh = cv2.threshold(fg_mask, 250, 255, cv2.THRESH_BINARY)

        # Masquage direct des zones d'exclusion (ex: fente sous la porte)
        if self.exclude_zones:
            for zone in self.exclude_zones:
                zx1, zy1, zx2, zy2 = self.resolve_zone_pixels(zone, w, h)
                if zx2 > zx1 and zy2 > zy1:
                    thresh[zy1:zy2, zx1:zx2] = 0

        # Élimination du bruit (ouverture morphologique)
        cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, self.kernel_open)
        # Dilatation pour regrouper les zones en mouvement
        dilated = cv2.dilate(cleaned, self.kernel_dilate, iterations=2)

        # Deuxième passe de masquage pour empêcher toute bavure morphologique
        if self.exclude_zones:
            for zone in self.exclude_zones:
                zx1, zy1, zx2, zy2 = self.resolve_zone_pixels(zone, w, h)
                if zx2 > zx1 and zy2 > zy1:
                    dilated[zy1:zy2, zx1:zx2] = 0

        contours, _ = cv2.findContours(
            dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        significant_boxes = []
        total_moving_area = 0.0

        for contour in contours:
            area = cv2.contourArea(contour)
            if area >= self.min_area:
                total_moving_area += area
                x, y, bw, bh = cv2.boundingRect(contour)
                significant_boxes.append((x, y, bw, bh))

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

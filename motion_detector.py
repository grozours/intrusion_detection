import os
import time
import logging
import urllib.request
import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

logger = logging.getLogger("IntrusionDetection.Detector")

DEFAULT_YOLO_MODEL_URL = "https://huggingface.co/Kalray/yolov8/resolve/main/yolov8n.onnx"
DEFAULT_YOLO_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "yolov8n.onnx"
)


@dataclass
class MotionResult:
    detected: bool
    moving_area: float
    contours_count: int
    bounding_boxes: List[Tuple[int, int, int, int]]
    confidences: List[float] = field(default_factory=list)


class MotionDetector:
    """
    Détecteur d'intrusion supportant :
    1. Mode IA Humain uniquement (YOLOv8n ONNX via OpenCV DNN) : ignore 100% des
       variations de lumière, ombres, reflets et objets non humains.
    2. Mode Classique (soustraction d'arrière-plan MOG2) : détection de mouvement pixel.
    Intègre également le masquage de zones d'exclusion.
    """

    def __init__(
        self,
        min_area: int = 1200,
        var_threshold: int = 35,
        history: int = 500,
        detect_shadows: bool = True,
        consecutive_frames_required: int = 2,
        exclude_zones: List[Tuple[float, float, float, float]] = None,
        human_only: bool = False,
        human_confidence: float = 0.50,
        model_path: Optional[str] = None,
        inference_interval: float = 0.12,
    ):
        self.min_area = min_area
        self.var_threshold = var_threshold
        self.history = history
        self.detect_shadows = detect_shadows
        self.consecutive_frames_required = max(1, consecutive_frames_required)
        self.consecutive_count = 0
        self.exclude_zones = list(exclude_zones) if exclude_zones else []

        self.human_only = human_only
        self.human_confidence = float(human_confidence)
        self.model_path = model_path or DEFAULT_YOLO_MODEL_PATH
        self.inference_interval = float(inference_interval)
        self.net = None

        # État interne pour le throttling intelligent de l'inférence IA
        self.prev_tiny: Optional[np.ndarray] = None
        self.last_inference_time: float = 0.0
        self.last_boxes: List[Tuple[int, int, int, int]] = []
        self.last_scores: List[float] = []

        if self.human_only:
            self._init_yolo_model()

        # Substracteur d'arrière-plan MOG2 de OpenCV
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=self.history,
            varThreshold=self.var_threshold,
            detectShadows=self.detect_shadows,
        )

        self.kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        self.kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    def _init_yolo_model(self):
        """Charge le modèle YOLOv8n ONNX (et le télécharge automatiquement s'il est absent)."""
        try:
            if not os.path.exists(self.model_path):
                os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
                logger.info(
                    f"📥 Téléchargement du modèle IA de détection humaine (YOLOv8n ONNX) vers {self.model_path}..."
                )
                urllib.request.urlretrieve(DEFAULT_YOLO_MODEL_URL, self.model_path)
                logger.info("✅ Modèle YOLOv8n téléchargé avec succès.")

            self.net = cv2.dnn.readNetFromONNX(self.model_path)
            logger.info(
                f"🧠 Détection IA Humaine activée (modèle: {os.path.basename(self.model_path)}, "
                f"seuil de confiance: {self.human_confidence * 100:.0f}%)."
            )
        except Exception as e:
            logger.error(
                f"⚠️ Impossible de charger le modèle IA YOLOv8n ({e}). Bascule sur le détecteur MOG2 classique."
            )
            self.net = None
            self.human_only = False

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

    def _is_point_excluded(self, cx: float, cy: float, w: int, h: int) -> bool:
        """Vérifie si le point central (cx, cy) se trouve dans une zone d'exclusion."""
        if not self.exclude_zones:
            return False
        for zone in self.exclude_zones:
            zx1, zy1, zx2, zy2 = self.resolve_zone_pixels(zone, w, h)
            if zx1 <= cx <= zx2 and zy1 <= cy <= zy2:
                return True
        return False

    def _detect_humans_yolo(
        self, frame: np.ndarray
    ) -> Tuple[List[Tuple[int, int, int, int]], List[float]]:
        """
        Exécute l'inférence YOLOv8n sur l'image et retourne uniquement les boîtes
        englobantes (x, y, w, h) et scores de confiance de la classe 0 ('person').
        """
        h, w = frame.shape[:2]
        scale = min(640.0 / w, 640.0 / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        pad_w, pad_h = (640 - nw) // 2, (640 - nh) // 2

        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
        canvas[pad_h : pad_h + nh, pad_w : pad_w + nw] = resized

        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 255.0, (640, 640), swapRB=True, crop=False
        )
        self.net.setInput(blob)
        outputs = self.net.forward()  # (1, 84, 8400)

        preds = outputs[0]  # (84, 8400)
        # Dans COCO (80 classes), la classe 0 ('person') est à l'indice 4 (après cx, cy, w, h)
        person_scores = preds[4, :]
        mask = person_scores >= self.human_confidence
        if not np.any(mask):
            return [], []

        filtered_boxes = preds[:4, mask].T  # (N, 4)
        filtered_scores = person_scores[mask]

        raw_boxes = []
        raw_scores = []
        for (cx, cy, bw, bh), score in zip(filtered_boxes, filtered_scores):
            x1 = (cx - bw / 2.0 - pad_w) / scale
            y1 = (cy - bh / 2.0 - pad_h) / scale
            box_w = bw / scale
            box_h = bh / scale

            ix = int(max(0, min(w - 1, round(x1))))
            iy = int(max(0, min(h - 1, round(y1))))
            iw = int(max(1, min(w - ix, round(box_w))))
            ih = int(max(1, min(h - iy, round(box_h))))

            if iw * ih < self.min_area:
                continue

            center_x = ix + iw / 2.0
            center_y = iy + ih / 2.0
            if self._is_point_excluded(center_x, center_y, w, h):
                continue

            raw_boxes.append([ix, iy, iw, ih])
            raw_scores.append(float(score))

        if not raw_boxes:
            return [], []

        indices = cv2.dnn.NMSBoxes(
            raw_boxes, raw_scores, self.human_confidence, 0.45
        )
        final_boxes: List[Tuple[int, int, int, int]] = []
        final_scores: List[float] = []
        if len(indices) > 0:
            for idx in np.array(indices).flatten():
                bx, by, bw, bh = raw_boxes[idx]
                final_boxes.append((bx, by, bw, bh))
                final_scores.append(raw_scores[idx])

        return final_boxes, final_scores

    def warmup(self, frame: np.ndarray):
        """Alimente le modèle d'arrière-plan sans déclencher d'alerte."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.prev_tiny = cv2.resize(gray, (64, 48), interpolation=cv2.INTER_AREA)
        if not self.human_only:
            blurred = cv2.GaussianBlur(gray, (21, 21), 0)
            self.fgbg.apply(blurred)

    def _process_frame_human_only(self, frame: np.ndarray) -> MotionResult:
        """
        Analyse la présence d'un humain par IA (YOLOv8n).
        Optimise l'usage CPU en évitant de ré-exécuter le réseau si l'image est
        strictement statique ou si une inférence vient tout juste d'être effectuée.
        """
        now = time.time()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        tiny = cv2.resize(gray, (64, 48), interpolation=cv2.INTER_AREA)

        scene_changed = True
        if self.prev_tiny is not None:
            mean_diff = float(np.mean(cv2.absdiff(tiny, self.prev_tiny)))
            scene_changed = mean_diff > 1.2

        # On lance l'inférence YOLO si :
        # - un humain est en cours de confirmation (0 < consecutive_count < required), OU
        # - l'intervalle d'inférence est écoulé ET la scène a évolué (ou un humain était déjà présent), OU
        # - la scène vient de changer significativement
        need_confirmation = 0 < self.consecutive_count < self.consecutive_frames_required
        interval_elapsed = (now - self.last_inference_time) >= self.inference_interval
        heartbeat_elapsed = (now - self.last_inference_time) >= 0.40

        if need_confirmation or (interval_elapsed and (scene_changed or self.consecutive_count > 0)) or heartbeat_elapsed or (scene_changed and self.last_inference_time == 0.0):
            self.prev_tiny = tiny
            self.last_inference_time = now
            boxes, scores = self._detect_humans_yolo(frame)
            self.last_boxes = boxes
            self.last_scores = scores

            if boxes:
                self.consecutive_count += 1
            else:
                self.consecutive_count = 0
        elif scene_changed:
            # Si la scène change brusquement (ex: dans un test unitaire rapide sans délai)
            self.prev_tiny = tiny
            self.last_inference_time = now
            boxes, scores = self._detect_humans_yolo(frame)
            self.last_boxes = boxes
            self.last_scores = scores

            if boxes:
                self.consecutive_count += 1
            else:
                self.consecutive_count = 0

        confirmed = self.consecutive_count >= self.consecutive_frames_required
        total_area = float(sum(w * h for (_, _, w, h) in self.last_boxes))

        return MotionResult(
            detected=confirmed,
            moving_area=total_area,
            contours_count=len(self.last_boxes),
            bounding_boxes=list(self.last_boxes),
            confidences=list(self.last_scores),
        )

    def process_frame(self, frame: np.ndarray) -> MotionResult:
        """
        Analyse une image et retourne le résultat de détection.
        En mode human_only, seule la présence d'un humain déclenche une alerte.
        """
        if self.human_only and self.net is not None:
            return self._process_frame_human_only(frame)

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
            confidences=[],
        )

    def reset(self):
        """Réinitialise les compteurs et recrée un modèle de fond vierge."""
        self.consecutive_count = 0
        self.prev_tiny = None
        self.last_inference_time = 0.0
        self.last_boxes = []
        self.last_scores = []
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=self.history,
            varThreshold=self.var_threshold,
            detectShadows=self.detect_shadows,
        )

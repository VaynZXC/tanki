from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image
import imagehash

from loguru import logger


State = str  # e.g., "main_menu", "login_menu", "account_is_login", "game_loading", etc.


@dataclass
class StateMatch:
    state: State
    distance: int
    template_path: Path


class PHashStateClassifier:
    """Very light classifier based on perceptual hashing over template images.

    Usage:
      - Put several representative PNG/JPG templates per state under dataset/<state>/
      - The classifier computes pHash for each template at load and matches a new image
        to the closest template by Hamming distance.
    """

    def __init__(self, dataset_root: Path, hash_size: int = 16):
        self.dataset_root = dataset_root
        self.hash_size = hash_size
        self.state_to_hashes: Dict[State, List[Tuple[imagehash.ImageHash, Path]]] = {}

    def load(self) -> int:
        total = 0
        for state_dir in sorted(self.dataset_root.iterdir()):
            if not state_dir.is_dir():
                continue
            state = state_dir.name
            # Skip utility/template directories
            if state.lower() == "templates":
                continue
            hashes: List[Tuple[imagehash.ImageHash, Path]] = []
            for img_path in sorted(state_dir.glob("*.*")):
                if img_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
                    continue
                try:
                    with Image.open(img_path) as im:
                        im = im.convert("RGB")
                        h = imagehash.phash(im, hash_size=self.hash_size)
                        hashes.append((h, img_path))
                        total += 1
                except Exception as exc:
                    logger.debug(f"Skip {img_path}: {exc}")
            if hashes:
                self.state_to_hashes[state] = hashes
                logger.info(f"Loaded {len(hashes)} templates for state '{state}'")
        return total

    def classify(self, img: Image.Image) -> StateMatch | None:
        if not self.state_to_hashes:
            logger.warning("No templates loaded")
            return None
        img = img.convert("RGB")
        h = imagehash.phash(img, hash_size=self.hash_size)
        best: StateMatch | None = None
        for state, hashes in self.state_to_hashes.items():
            for th, path in hashes:
                d = h - th  # Hamming distance
                if best is None or d < best.distance:
                    best = StateMatch(state=state, distance=d, template_path=path)
        return best

    @staticmethod
    def open_image(path: Path) -> Image.Image:
        im = Image.open(path)
        return im

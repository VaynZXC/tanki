from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger


def bulk_rename(src_dir: Path, class_name: str, start_index: int = 1, dest_dir: Path | None = None) -> None:
    if not src_dir.exists():
        logger.error(f"Директория не найдена: {src_dir}")
        return

    # По умолчанию перемещаем в dataset/<class_name>
    if dest_dir is None:
        dest_dir = Path("dataset") / class_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    exts = {".png", ".jpg", ".jpeg", ".bmp"}
    images = sorted([p for p in src_dir.iterdir() if p.suffix.lower() in exts])
    idx = start_index

    for p in images:
        new_name = f"{class_name}_{idx:05d}{p.suffix.lower()}"
        target_path = dest_dir / new_name
        if target_path.exists():
            logger.warning(f"Пропуск — файл уже существует: {target_path}")
            idx += 1
            continue
        p.rename(target_path)
        logger.info(f"{p.name} -> {target_path}")
        idx += 1


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Массовое переименование скринов под YOLO класс с перемещением в dataset/<class>")
    ap.add_argument("class_name", help="Имя класса (префикс файла)")
    ap.add_argument("--src", type=str, default="dataset/raw", help="Папка со скринами-источником")
    ap.add_argument("--start", type=int, default=1, help="Начальный индекс нумерации")
    ap.add_argument("--dest", type=str, default=None, help="Папка назначения (по умолчанию dataset/<class>)")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    dest = Path(args.dest) if args.dest else None
    bulk_rename(Path(args.src), args.class_name, args.start, dest)


if __name__ == "__main__":
    main()

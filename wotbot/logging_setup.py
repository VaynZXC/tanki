from __future__ import annotations

import os
from pathlib import Path
from loguru import logger


def setup_logging(logs_dir: str | os.PathLike[str] = "logs") -> None:
    """Configure loguru logging sinks.

    Creates a rotating log file under `logs/wotbot.log` and sets a readable console format.
    """
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)
    log_file = logs_path / "wotbot.log"

    logger.remove()
    logger.add(
        log_file,
        rotation="5 MB",
        retention=10,
        compression="zip",
        enqueue=True,
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}",
    )
    logger.add(
        lambda msg: print(msg, end=""),
        level="INFO",
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{message}</cyan>",
    )

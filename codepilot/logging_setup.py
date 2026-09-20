"""日志落盘初始化：为 codepilot.* 日志器挂一个固定的 FileHandler。

设计：发射器（agent 等）只通过 logging.getLogger("codepilot.xxx").info(...)
记录事件，不关心写到哪里；sink（文件路径/级别）在启动时于此处统一配置。
这样新增日志点无需改动配置，符合「模块低耦合、可维护」原则。
"""

import logging
from pathlib import Path


def setup_logging(file: str, level: str = "INFO") -> None:
    """配置 codepilot 根日志器。file 为空则仅允许内部日志、不落盘。"""
    logger = logging.getLogger("codepilot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not file:
        return
    path = Path(file)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(handler)
    logger.info("=== CodePilot session start ===")
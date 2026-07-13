"""统一日志：带阶段前缀，输出到 stderr，避免与正常输出混淆。"""

from __future__ import annotations

import logging
import sys
from typing import Optional

_CONFIGURED = False


def setup_logger(verbose: bool = False) -> logging.Logger:
    """配置并返回全局 logger。

    Args:
        verbose: True 时输出 DEBUG 级别，否则 INFO。

    Returns:
        配置好的 logging.Logger 实例。
    """
    global _CONFIGURED
    logger = logging.getLogger("auto_vmware")
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)-5s %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        _CONFIGURED = True
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取子 logger。"""
    base = logging.getLogger("auto_vmware")
    return base.getChild(name) if name else base

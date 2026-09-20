"""测试日志落盘 — setup_logging 会写出文件、内容含记录。"""

import logging

from codepilot.logging_setup import setup_logging


def test_setup_creates_log_file(tmp_path):
    log_file = tmp_path / "session.log"
    setup_logging(str(log_file))
    logger = logging.getLogger("codepilot.test")
    logger.info("hello_log_line")
    # FileHandler 需 flush 落盘
    for h in logger.handlers:
        h.flush()
    assert log_file.exists()
    text = log_file.read_text(encoding="utf-8")
    assert "hello_log_line" in text


def test_setup_create_nested_dirs(tmp_path):
    log_file = tmp_path / "a" / "b" / "s.log"
    setup_logging(str(log_file))
    assert log_file.parent.exists()


def test_setup_empty_file_no_write(tmp_path):
    setup_logging("")  # 不应抛异常，也不创建文件
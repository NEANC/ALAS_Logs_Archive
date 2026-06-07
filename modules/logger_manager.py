#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
from datetime import datetime
from typing import Optional

# 日志格式常量
LOG_FORMAT = "%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
LOG_FILE_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s"


def cleanup_old_logs(log_folder: str, max_files: int, logger: Optional[logging.Logger] = None) -> None:
    """清理旧的日志文件，保留最新的 max_files 个文件

    Args:
        log_folder: 日志文件夹路径
        max_files: 保留的最大日志文件数量
        logger: 日志记录器（可选）
    """
    if not os.path.exists(log_folder):
        return

    log_files = []
    for filename in os.listdir(log_folder):
        if not filename.endswith(".log"):
            continue

        file_path = os.path.join(log_folder, filename)
        if not os.path.isfile(file_path):
            continue

        log_files.append((file_path, os.path.getmtime(file_path)))

    if len(log_files) <= max_files:
        if logger:
            logger.debug(f"日志文件数量 {len(log_files)} 未超过限制 {max_files}，无需清理")
        return

    log_files.sort(key=lambda x: x[1], reverse=True)
    files_to_delete = log_files[max_files:]

    deleted_count = 0
    for file_path, _ in files_to_delete:
        try:
            os.remove(file_path)
            if logger:
                logger.debug(f"删除旧日志文件: {os.path.basename(file_path)}")
            deleted_count += 1
        except Exception as e:
            if logger:
                logger.error(f"删除日志文件 {os.path.basename(file_path)} 失败: {e}")

    if logger and deleted_count > 0:
        logger.info(f"已清理 {deleted_count} 个日志文件")


def setup_logger(log_folder: str = "logs", max_log_files: int = 15, log_level: int = logging.INFO, save_logs: bool = True) -> logging.Logger:
    """设置日志记录器

    Args:
        log_folder: 日志文件夹路径
        max_log_files: 保留的最大日志文件数量
        log_level: 日志等级
        save_logs: 是否保存日志文件到本地

    Returns:
        logging.Logger: 配置好的日志记录器
    """
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    if save_logs:
        if not os.path.exists(log_folder):
            os.makedirs(log_folder)

        cleanup_old_logs(log_folder, max_log_files, logger)

        log_filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        log_file_path = os.path.join(log_folder, log_filename)

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter(LOG_FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger

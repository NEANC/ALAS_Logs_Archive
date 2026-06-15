#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import re
import shutil


def delete_gui_files(target_folder: str, current_date: str, logger: logging.Logger) -> None:
    """删除目标文件夹中所有格式为 年-月-日_gui.txt 的文件（排除当日文件）

    Args:
        target_folder: 目标文件夹路径
        current_date: 当前日期（格式：年-月-日）
        logger: 日志记录器
    """
    if not os.path.exists(target_folder):
        logger.warning(f"目标文件夹不存在: {target_folder}")
        return

    gui_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}_gui\b")
    current_date_pattern = re.compile(rf"^{re.escape(current_date)}_gui\b")
    deleted_count = 0

    for filename in os.listdir(target_folder):
        if not gui_pattern.match(filename):
            continue

        if current_date_pattern.match(filename):
            logger.debug(f"跳过当日文件: {filename}")
            continue

        file_path = os.path.join(target_folder, filename)
        try:
            os.remove(file_path)
            logger.debug(f"已删除文件: {filename}")
            deleted_count += 1
        except Exception as e:
            logger.error(f"删除文件 {filename} 失败: {e}")

    logger.info(f"共删除 {deleted_count} 个 gui 文件")


def delete_error_folder(target_folder: str, logger: logging.Logger) -> None:
    """删除目标文件夹中的 error 文件夹

    Args:
        target_folder: 目标文件夹路径
        logger: 日志记录器
    """
    if not os.path.exists(target_folder):
        logger.warning(f"目标文件夹不存在: {target_folder}")
        return

    error_folder_path = os.path.join(target_folder, "error")

    if not os.path.exists(error_folder_path):
        logger.info("error 文件夹不存在，跳过删除")
        return

    try:
        shutil.rmtree(error_folder_path)
        logger.info(f"已删除 error 文件夹: {error_folder_path}")
    except Exception as e:
        logger.error(f"删除 error 文件夹失败: {e}")

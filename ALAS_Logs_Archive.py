#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from typing import List

from modules.alas_logger_processor import delete_error_folder, delete_gui_files
from modules.config_manager import CONFIG_FILE, ConfigManager
from modules.logger_manager import setup_logger
from modules.version import VERSION, print_info
from modules.zip_compress import create_archive
from modules.zip_decompress import decompress_archive


def get_files_to_archive(target_folder: str, current_date: str, logger: logging.Logger) -> List[str]:
    """获取需要打包归档的文件列表

    Args:
        target_folder: 目标文件夹路径
        current_date: 当前日期（格式：年-月-日）
        logger: 日志记录器

    Returns:
        List[str]: 需要归档的文件路径列表
    """
    if not os.path.exists(target_folder):
        logger.warning(f"目标文件夹不存在: {target_folder}")
        return []

    gui_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}_gui\.txt$")
    current_date_pattern = re.compile(f"^{re.escape(current_date)}_")
    files_to_archive = []

    for item in os.listdir(target_folder):
        item_path = os.path.join(target_folder, item)

        if os.path.isdir(item_path):
            continue

        if gui_pattern.match(item):
            continue

        if current_date_pattern.match(item):
            continue

        files_to_archive.append(item_path)

    logger.info(f"找到 {len(files_to_archive)} 个需要归档的文件")
    return files_to_archive


def validate_compression_level(level: int) -> bool:
    """验证压缩等级是否有效

    Args:
        level: 压缩等级

    Returns:
        bool: 是否有效
    """
    return 1 <= level <= 9


def validate_compression_algorithm(algorithm: str) -> bool:
    """验证压缩算法是否有效

    Args:
        algorithm: 压缩算法

    Returns:
        bool: 是否有效
    """
    return algorithm.lower() in ["lzma", "bzip2"]


def validate_archive_mode(mode: str) -> bool:
    """验证归档模式是否有效

    Args:
        mode: 归档模式

    Returns:
        bool: 是否有效
    """
    return mode.lower() in ["scroll", "incremental"]


def parse_command_line_args() -> argparse.Namespace:
    """解析命令行参数

    Returns:
        argparse.Namespace: 解析后的命令行参数
    """
    parser = argparse.ArgumentParser(description="ALAS 日志归档工具")
    parser.add_argument("-n", "--name", help="归档文件名")
    parser.add_argument("-t", "--target", help="目标文件夹路径")
    parser.add_argument("-a", "--archive", help="归档文件夹路径")
    parser.add_argument("-m", "--mode", help="归档模式", choices=["scroll", "incremental"])
    parser.add_argument("-c", "--compression", help="压缩算法", choices=["lzma", "bzip2"])
    parser.add_argument("-l", "--level", help="压缩等级", type=int, choices=range(1, 10), metavar="1-9")
    parser.add_argument("-w", "--workers", help="多线程设置", type=int)
    parser.add_argument("-L", "--save-logs", help="日志文件输出控制", choices=["true", "false"])
    parser.add_argument("-d", "--decompress", help="解压归档文件（指定ZIP文件路径）")
    parser.add_argument("-o", "--output", help="解压输出目录（与 -d 配合使用，默认为ZIP同目录下同名文件夹）")
    return parser.parse_args()


def _handle_decompress(args: argparse.Namespace) -> None:
    """处理解压操作

    Args:
        args: 命令行参数
    """
    archive_path = args.decompress
    output_dir = args.output if args.output else os.path.splitext(archive_path)[0]

    logger = setup_logger("logs", 15, logging.INFO, False)
    logger.info(f"解压模式：归档文件 {archive_path}")
    logger.info(f"输出目录: {output_dir}")

    try:
        decompress_archive(archive_path, output_dir, logger)
    except KeyboardInterrupt:
        print("\r" + " " * 80 + "\r", end="", flush=True)
        logger.warning("捕获到Ctrl+C，终止运行")
        sys.exit(0)
    except Exception as e:
        logger.error(f"解压失败: {e}")
        raise


def main():
    """主函数"""
    args = parse_command_line_args()

    print_info()

    # 解压模式：仅通过命令行控制，不依赖配置文件
    if args.decompress:
        _handle_decompress(args)
        return

    config_mgr = ConfigManager(CONFIG_FILE)
    config_mgr.load()
    log_folder = config_mgr.log_folder
    max_log_files = config_mgr.max_log_files
    log_level = config_mgr.log_level
    save_logs = config_mgr.save_logs

    # 命令行参数覆盖配置文件
    if args.save_logs:
        save_logs = args.save_logs.lower() == "true"

    logger = setup_logger(log_folder, max_log_files, log_level, save_logs)

    # 日志系统就绪，注入 ConfigManager
    config_mgr.set_logger(logger)

    try:
        target_folder = args.target if args.target else config_mgr.target_folder
        archive_folder = args.archive if args.archive else config_mgr.archive_folder
        archive_name_format = args.name if args.name else config_mgr.archive_name_format
        compression_algorithm = args.compression if args.compression else config_mgr.compression_algorithm
        compression_level = args.level if args.level else config_mgr.compression_level
        archive_mode = args.mode if args.mode else config_mgr.archive_mode
        max_workers = args.workers if args.workers else config_mgr.max_workers
        chunk_size = config_mgr.chunk_size
        current_date = datetime.now().strftime("%Y-%m-%d")

        if not validate_compression_algorithm(compression_algorithm):
            logger.error(f"不支持的压缩算法: {compression_algorithm}")
            sys.exit(1)

        if not validate_compression_level(compression_level):
            logger.error(f"无效的压缩等级: {compression_level}")
            sys.exit(1)

        if not validate_archive_mode(archive_mode):
            logger.error(f"无效的归档模式: {archive_mode}")
            sys.exit(1)

        if max_workers < 1:
            logger.error(f"无效的工作线程数: {max_workers}")
            sys.exit(1)

        if not save_logs:
            logger.warning("日志仅控制台输出")

        logger.info(f"版本号: {VERSION}")
        logger.info(f"目标文件夹: {target_folder}")
        logger.info(f"归档文件夹: {archive_folder}")

        mode_display = "增量" if archive_mode == "incremental" else "滚动"
        logger.info(f"归档模式: {mode_display}")

        delete_gui_files(target_folder, current_date, logger)
        delete_error_folder(target_folder, logger)

        files_to_archive = get_files_to_archive(target_folder, current_date, logger)
        create_archive(files_to_archive, archive_folder, archive_name_format, compression_algorithm, compression_level, archive_mode, max_workers, chunk_size, logger)

    except KeyboardInterrupt:
        print("\r" + " " * 80 + "\r", end="", flush=True)
        logger.warning("捕获到Ctrl+C，终止运行")
        sys.exit(0)
    except Exception as e:
        logger.error(f"程序执行出错: {e}")
        raise


if __name__ == "__main__":
    main()

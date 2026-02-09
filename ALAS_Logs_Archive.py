#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import argparse
import bz2
import configparser
import concurrent.futures
import logging
import lzma
import os
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

# 硬编码参数
CONFIG_FILE = "config.ini"
LOG_FORMAT = "%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
LOG_FILE_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s"
DEFAULT_COMPRESSION_ALGORITHM = "bzip2"
DEFAULT_ARCHIVE_MODE = "overwrite"
LZMA_PRESET = 9
LZMA_DICT_SIZE = 32 * 1024 * 1024
BZIP2_COMPRESSLEVEL = 9
CHUNK_SIZE = 8192
MAX_WORKERS = 1
PROGRESS_UPDATE_INTERVAL = 1
VERSION = "v2.0.0"

def print_info():
    """打印程序的版本和版权信息，发版前手动修改。"""
    print("\n")
    print("+ " + " ALAS Logs Archive ".center(60, "="), "+")
    print("||" + "".center(60, " ") + "||")
    print("||" + "ALAS 日志归档工具".center(54, " ") + "||")
    print("||" + "本项目使用 AI 进行生成".center(51, " ") + "||")
    print("||" + "".center(60, " ") + "||")
    print("|| " + "".center(58, "-") + " ||")
    print("||" + "".center(60, " ") + "||")
    print("||" + f"Version: {VERSION}    License: WTFPL".center(60, " ") + "||")
    print("||" + "".center(60, " ") + "||")
    print("+ " + "".center(60, "=") + " +")
    print("\n")

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


def create_default_config(config_path: str) -> None:
    """创建默认配置文件

    Args:
        config_path: 配置文件路径
    """
    config = configparser.ConfigParser()
    config["settings"] = {
        "target_folder": r"X:\AzurLaneAutoScript\log",
        "archive_folder": r"X:\ALAS_Logs",
        "archive_name_format": "存档",
        "compression_algorithm": "bzip2",
        "compression_level": "9",
        "archive_mode": "scroll",
        "log_folder": "logs",
        "max_log_files": "15",
        "log_level": "INFO",
        "save_logs": "true",
        "max_workers": "1",
        "chunk_size": "8192"
    }
    
    with open(config_path, "w", encoding="utf-8") as f:
        config.write(f)


def load_config(config_path: str) -> dict:
    """加载配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        dict: 配置字典
    """
    if not os.path.exists(config_path):
        print(f"配置文件不存在: {config_path}")
        print("正在生成默认配置文件...")
        create_default_config(config_path)
        print(f"已生成默认配置文件: {config_path}")
        print("请修改配置文件中的 target_folder 和 archive_folder 后重新运行程序")
        sys.exit(1)
    
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf-8")
    
    if "settings" not in config:
        print("配置文件中缺少 [settings] 节")
        print("正在重新生成配置文件...")
        create_default_config(config_path)
        print(f"已重新生成配置文件: {config_path}")
        print("请修改配置文件中的 target_folder 和 archive_folder 后重新运行程序")
        sys.exit(1)
    
    if not config.has_option("settings", "target_folder") or not config.has_option("settings", "archive_folder"):
        print("配置文件中缺少必需的配置项: target_folder 或 archive_folder")
        print("正在重新生成配置文件...")
        create_default_config(config_path)
        print(f"已重新生成配置文件: {config_path}")
        print("请修改配置文件中的 target_folder 和 archive_folder 后重新运行程序")
        sys.exit(1)
    
    log_level_str = config.get("settings", "log_level", fallback="INFO").upper()
    log_level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    log_level = log_level_map.get(log_level_str, logging.INFO)
    
    save_logs_str = config.get("settings", "save_logs", fallback="true").lower()
    save_logs = save_logs_str in ["true", "yes", "1"]
    
    config_dict = {
        "target_folder": config.get("settings", "target_folder"),
        "archive_folder": config.get("settings", "archive_folder"),
        "archive_name_format": config.get("settings", "archive_name_format", fallback="存档"),
        "compression_algorithm": config.get("settings", "compression_algorithm", fallback=DEFAULT_COMPRESSION_ALGORITHM).lower(),
        "compression_level": config.getint("settings", "compression_level", fallback=9),
        "archive_mode": config.get("settings", "archive_mode", fallback="scroll").lower(),
        "log_folder": config.get("settings", "log_folder", fallback="logs"),
        "max_log_files": config.getint("settings", "max_log_files", fallback=15),
        "log_level": log_level,
        "save_logs": save_logs,
        "max_workers": config.getint("settings", "max_workers", fallback=MAX_WORKERS),
        "chunk_size": config.getint("settings", "chunk_size", fallback=CHUNK_SIZE)
    }
    
    return config_dict


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
    
    gui_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}_gui\.txt$")
    current_date_pattern = re.compile(rf"^{re.escape(current_date)}_gui\.txt$")
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
    
    logger.info(f"共删除 {deleted_count} 个 gui.txt 文件")


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


def format_size(size_bytes: int) -> str:
    """格式化文件大小

    Args:
        size_bytes: 文件大小（字节）

    Returns:
        str: 格式化后的文件大小
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def read_file_chunked(file_path: str, chunk_size: int) -> bytes:
    """分块读取文件内容

    Args:
        file_path: 文件路径
        chunk_size: 块大小

    Returns:
        bytes: 文件内容
    """
    chunks = []
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def compress_file(file_path: str, compression_algorithm: str, compression_level: int, chunk_size: int) -> Tuple[str, bytes, int]:
    """压缩单个文件

    Args:
        file_path: 文件路径
        compression_algorithm: 压缩算法
        compression_level: 压缩等级
        chunk_size: 块大小

    Returns:
        Tuple[str, bytes, int]: (文件名, 压缩后的数据, 原始大小)
    """
    data = read_file_chunked(file_path, chunk_size)
    original_size = len(data)
    
    if compression_algorithm.lower() == "lzma":
        lzma_filters = [
            {"id": lzma.FILTER_LZMA2, "preset": compression_level, "dict_size": LZMA_DICT_SIZE}
        ]
        compressed_data = lzma.compress(data, filters=lzma_filters)
    elif compression_algorithm.lower() == "bzip2":
        compressed_data = bz2.compress(data, compresslevel=compression_level)
    else:
        raise ValueError(f"不支持的压缩算法: {compression_algorithm}")
    
    return (os.path.basename(file_path), compressed_data, original_size)


def create_archive_generic(files: List[str], archive_path: str, compression_algorithm: str, compression_level: int, max_workers: int, chunk_size: int, incremental_mode: bool, logger: logging.Logger) -> None:
    """使用指定压缩算法创建归档文件

    Args:
        files: 需要归档的文件路径列表
        archive_path: 归档文件路径
        compression_algorithm: 压缩算法（lzma 或 bzip2）
        compression_level: 压缩等级（1-9）
        max_workers: 最大工作线程数
        chunk_size: 读取块大小
        incremental_mode: 是否为增量模式
        logger: 日志记录器
    """
    total_files = len(files)
    logger.info(f"开始压缩 {total_files} 个文件，使用 {max_workers} 个线程")
    
    start_time = time.time()
    compressed_results = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(compress_file, file_path, compression_algorithm, compression_level, chunk_size): file_path
            for file_path in files
        }
        
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            file_path = futures[future]
            
            try:
                result = future.result()
                compressed_results.append(result)
                logger.debug(f"已压缩文件: {result[0]}")
            except Exception as e:
                logger.error(f"压缩文件 {file_path} 失败: {e}")
            
            completed += 1
            
            progress = (completed / total_files) * 100
            progress_line = f"\r压缩进度: {progress:.1f}% ({completed}/{total_files})"
            print(progress_line, end="", flush=True)
    
    print("\r" + " " * 80 + "\r", end="", flush=True)
    
    original_size = sum(result[2] for result in compressed_results)
    
    zip_mode = "a" if incremental_mode and os.path.exists(archive_path) else "w"
    existing_size = 0
    files_to_add = []
    
    if incremental_mode and os.path.exists(archive_path):
        existing_size = os.path.getsize(archive_path)
        logger.info(f"现有归档大小: {format_size(existing_size)}")
        
        # 检查ZIP文件中已存在的文件
        existing_files = set()
        try:
            with zipfile.ZipFile(archive_path, "r") as zipf:
                for info in zipf.infolist():
                    # 只处理文件，跳过目录
                    if not info.filename.endswith('/'):
                        existing_files.add(info.filename)
        except Exception as e:
            logger.error(f"读取现有归档文件失败: {e}")
        
        # 分离重复文件和新文件
        new_files = []  # 新文件，使用增量模式
        duplicate_files = []  # 重复文件，使用滚动模式
        
        for arcname, compressed_data, original_size in compressed_results:
            if arcname in existing_files:
                # 重复文件，使用滚动模式处理
                logger.debug(f"重复文件: {arcname}")
                duplicate_files.append((arcname, compressed_data, original_size))
            else:
                # 新文件，使用增量模式添加
                new_files.append((arcname, compressed_data, original_size))
        
        # 处理新文件（增量模式）
        files_to_add = new_files
        
        # 处理重复文件（滚动模式）
        if duplicate_files:
            # 创建新的归档文件名（带日期时间戳）
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            base_name = os.path.basename(archive_path)
            if base_name.endswith('.zip'):
                base_name = base_name[:-4]
            append_archive_path = os.path.join(os.path.dirname(archive_path), f"重复文件_{base_name}_{timestamp}.zip")
            
            # 使用滚动模式创建新归档
            with zipfile.ZipFile(append_archive_path, "w", zipfile.ZIP_STORED) as zipf:
                for arcname, compressed_data, original_size in duplicate_files:
                    # 创建ZipInfo对象，设置正确的文件大小
                    zinfo = zipfile.ZipInfo(arcname, time.localtime()[:6])
                    zinfo.file_size = original_size
                    zinfo.compress_size = len(compressed_data)
                    zinfo.compress_type = zipfile.ZIP_STORED
                    zipf.writestr(zinfo, compressed_data)
            
            logger.info(f"{len(duplicate_files)} 个重复文件已保存到新归档文件: {append_archive_path}")
    else:
        # 非增量模式或文件不存在，添加所有文件
        files_to_add = compressed_results
    
    # 处理需要添加的文件
    if files_to_add:
        # 使用存储模式，因为文件已经被压缩过了
        with zipfile.ZipFile(archive_path, zip_mode, zipfile.ZIP_STORED) as zipf:
            # 直接添加新文件（增量模式）
            for arcname, compressed_data, original_size in files_to_add:
                # 创建ZipInfo对象，设置正确的文件大小
                zinfo = zipfile.ZipInfo(arcname, time.localtime()[:6])
                zinfo.file_size = original_size
                zinfo.compress_size = len(compressed_data)
                zinfo.compress_type = zipfile.ZIP_STORED
                zipf.writestr(zinfo, compressed_data)
    
    elapsed_time = time.time() - start_time
    final_size = os.path.getsize(archive_path)
    
    if incremental_mode:
        # 增量模式：只计算新添加文件的压缩率
        added_original_size = sum(result[2] for result in files_to_add)
        added_compressed_size = sum(len(result[1]) for result in files_to_add)
        compression_ratio = (1 - added_compressed_size / added_original_size) * 100 if added_original_size > 0 else 0
        
        if files_to_add:
            logger.info(f"新增了 {len(files_to_add)} 个文件到增量归档")
        logger.info(f"已完成归档，压缩耗时: {elapsed_time:.2f}秒，已保存到: {archive_path}")
        logger.info(f"新增原始大小: {format_size(added_original_size)}，新增压缩大小: {format_size(added_compressed_size)}，压缩率: {compression_ratio:.2f}%")
        logger.info(f"归档总大小: {format_size(final_size)}（增加了 {format_size(final_size - existing_size)}）")
    else:
        # 滚动模式：计算整体压缩率
        compression_ratio = (1 - final_size / original_size) * 100 if original_size > 0 else 0
        logger.info(f"已完成归档，压缩耗时: {elapsed_time:.2f}秒，已保存到: {archive_path}")
        logger.info(f"原始大小: {format_size(original_size)}，压缩后大小: {format_size(final_size)}，压缩率: {compression_ratio:.2f}%")
    
    deleted_count = 0
    for file_path in files:
        if not os.path.exists(file_path):
            continue
        
        try:
            os.remove(file_path)
            logger.debug(f"已删除原始文件: {os.path.basename(file_path)}")
            deleted_count += 1
        except Exception as e:
            logger.error(f"删除文件 {file_path} 失败: {e}")
    
    logger.info(f"共删除 {deleted_count} 个原始文件")


def create_archive(files: List[str], archive_folder: str, archive_name_format: str, compression_algorithm: str, compression_level: int, archive_mode: str, max_workers: int, chunk_size: int, logger: logging.Logger) -> None:
    """创建归档文件

    Args:
        files: 需要归档的文件路径列表
        archive_folder: 归档文件夹路径
        archive_name_format: 归档文件名格式（可选包含 {date} 占位符，可选包含 .zip 扩展名）
        compression_algorithm: 压缩算法（lzma 或 bzip2）
        compression_level: 压缩等级（1-9）
        archive_mode: 归档模式（scroll 或 incremental）
        max_workers: 最大工作线程数
        chunk_size: 读取块大小
        logger: 日志记录器
    """
    if not files:
        logger.info("没有文件需要归档")
        return
    
    if not os.path.exists(archive_folder):
        os.makedirs(archive_folder)
        logger.info(f"创建归档文件夹: {archive_folder}")
    
    archive_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    incremental_mode = archive_mode == "incremental"
    
    # 处理文件名
    if incremental_mode:
        # 增量模式：直接使用提供的文件名，不添加日期前缀
        archive_filename = archive_name_format
        # 自动添加 .zip 扩展名（如果缺少）
        if not archive_filename.endswith(".zip"):
            archive_filename += ".zip"
        archive_path = os.path.join(archive_folder, archive_filename)
        
        if os.path.exists(archive_path):
            logger.info(f"增量模式：追加到现有归档文件: {archive_filename}")
        else:
            logger.info(f"增量模式：创建新归档文件: {archive_filename}")
    else:  # scroll 模式
        # 滚动模式：使用年-月-日作为前缀
        base_name = archive_name_format
        # 处理扩展名
        if not base_name.endswith(".zip"):
            base_name += ".zip"
        
        # 检查是否包含 {date} 占位符
        if "{date}" in base_name:
            archive_filename = base_name.replace("{date}", archive_date)
        else:
            # 如果不包含 {date} 占位符，在文件名前添加日期前缀
            name_without_ext = base_name.rsplit(".", 1)[0]
            ext = base_name.rsplit(".", 1)[1]
            archive_filename = f"{archive_date}_{name_without_ext}.{ext}"
        
        archive_path = os.path.join(archive_folder, archive_filename)
        
        # 处理文件已存在的情况
        counter = 0
        original_filename = archive_filename
        while os.path.exists(archive_path):
            counter += 1
            name_without_ext = original_filename.rsplit(".", 1)[0]
            ext = original_filename.rsplit(".", 1)[1]
            archive_filename = f"{name_without_ext}_{counter}.{ext}"
            archive_path = os.path.join(archive_folder, archive_filename)
        
        if counter > 1:
            logger.info(f"检测到已有归档文件，将创建: {archive_filename}")
        else:
            logger.info(f"创建新归档文件: {archive_filename}")
    
    logger.info(f"使用压缩算法: {compression_algorithm.upper()}，压缩等级: {compression_level}")
    
    try:
        create_archive_generic(files, archive_path, compression_algorithm, compression_level, max_workers, chunk_size, incremental_mode, logger)
    except Exception as e:
        logger.error(f"创建归档文件失败: {e}")
        raise


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
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_command_line_args()
    
    print_info()
    
    config = load_config(CONFIG_FILE)
    log_folder = config.get("log_folder", "logs")
    max_log_files = config.get("max_log_files", 15)
    log_level = config.get("log_level", logging.INFO)
    save_logs = config.get("save_logs", True)
    
    # 命令行参数覆盖配置文件
    if args.save_logs:
        save_logs = args.save_logs.lower() == "true"
    
    logger = setup_logger(log_folder, max_log_files, log_level, save_logs)
    
    try:
        target_folder = args.target if args.target else config.get("target_folder", "target")
        archive_folder = args.archive if args.archive else config.get("archive_folder", "archive")
        archive_name_format = args.name if args.name else config.get("archive_name_format", "{date}_归档.zip")
        compression_algorithm = args.compression if args.compression else config.get("compression_algorithm", DEFAULT_COMPRESSION_ALGORITHM)
        compression_level = args.level if args.level else config.get("compression_level", 9)
        archive_mode = args.mode if args.mode else config.get("archive_mode", "overwrite")
        max_workers = args.workers if args.workers else config.get("max_workers", MAX_WORKERS)
        chunk_size = config.get("chunk_size", CHUNK_SIZE)
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

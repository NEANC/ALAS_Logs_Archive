#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import bz2
import concurrent.futures
import logging
import lzma
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import List, Tuple

# 压缩相关硬编码参数
LZMA_DICT_SIZE = 32 * 1024 * 1024


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

        for arcname, compressed_data, orig_size in compressed_results:
            if arcname in existing_files:
                # 重复文件，使用滚动模式处理
                logger.debug(f"重复文件: {arcname}")
                duplicate_files.append((arcname, compressed_data, orig_size))
            else:
                # 新文件，使用增量模式添加
                new_files.append((arcname, compressed_data, orig_size))

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
                for arcname, compressed_data, orig_size in duplicate_files:
                    # 创建ZipInfo对象，设置正确的文件大小
                    zinfo = zipfile.ZipInfo(arcname, time.localtime()[:6])
                    zinfo.file_size = orig_size
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
            for arcname, compressed_data, orig_size in files_to_add:
                # 创建ZipInfo对象，设置正确的文件大小
                zinfo = zipfile.ZipInfo(arcname, time.localtime()[:6])
                zinfo.file_size = orig_size
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

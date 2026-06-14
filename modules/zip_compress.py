#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import bz2
import concurrent.futures
import logging
import lzma
import os
import shutil
import tempfile
import time
import zipfile
import zstandard as zstd
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import List, Tuple

# 压缩相关硬编码参数
# 各算法支持的压缩等级范围
LEVEL_RANGES = {
    'zstd': (1, 22),
    'bzip2': (1, 9),
    'lzma': (0, 19),  # 0-9 常规模式, 10-19 是添加了 PRESET_EXTREME 的 0-9
}
# 流式压缩阈值 1GB
STREAMING_THRESHOLD = 1 * 1024 * 1024 * 1024


def _normalize_level(compression_algorithm: str, compression_level: int) -> int:
    """将用户传入的压缩等级钳位到算法支持的范围内

    Args:
        compression_algorithm: 压缩算法
        compression_level: 用户输入的压缩等级

    Returns:
        int: 钳位后的压缩等级
    """
    algo = compression_algorithm.lower()
    lo, hi = LEVEL_RANGES.get(algo, (1, 9))
    return max(lo, min(hi, compression_level))


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

    algo = compression_algorithm.lower()
    level = _normalize_level(algo, compression_level)

    if algo == "lzma":
        preset = level % 10
        kwargs = {'format': lzma.FORMAT_XZ, 'preset': preset}
        if level > 9:
            kwargs['preset'] = preset | lzma.PRESET_EXTREME
        compressed_data = lzma.compress(data, **kwargs)
    elif algo == "bzip2":
        compressed_data = bz2.compress(data, compresslevel=level)
    elif algo == "zstd":
        compressed_data = zstd.ZstdCompressor(level=level, write_checksum=True).compress(data)
    else:
        raise ValueError(f"不支持的压缩算法: {compression_algorithm}")

    return (os.path.basename(file_path), compressed_data, original_size)


def _get_streaming_compressor(compression_algorithm: str, compression_level: int):
    """获取流式压缩器对象

    Args:
        compression_algorithm: 压缩算法
        compression_level: 压缩等级

    Returns:
        压缩器对象（有 compress/flush 方法）
    """
    algo = compression_algorithm.lower()
    level = _normalize_level(algo, compression_level)

    if algo == "lzma":
        preset = level % 10
        if level > 9:
            preset = preset | lzma.PRESET_EXTREME
        return lzma.LZMACompressor(format=lzma.FORMAT_XZ, preset=preset)
    elif algo == "bzip2":
        return bz2.BZ2Compressor(compresslevel=level)
    elif algo == "zstd":
        return zstd.ZstdCompressor(level=level, write_checksum=True)
    else:
        raise ValueError(f"不支持的压缩算法: {compression_algorithm}")


def _append_compressed_to_zip(zipf: zipfile.ZipFile, arcname: str,
                               temp_path: str, original_size: int,
                               logger: logging.Logger) -> None:
    """将已压缩的临时文件以流式方式追加到 ZIP（ZIP_STORED 模式）

    Args:
        zipf: 已打开的 ZipFile 对象（写模式）
        arcname: ZIP 中的文件名
        temp_path: 已压缩的临时文件路径
        original_size: 原始文件大小
        logger: 日志记录器
    """
    compressed_size = os.path.getsize(temp_path)
    zinfo = zipfile.ZipInfo(arcname, time.localtime()[:6])
    zinfo.file_size = original_size
    zinfo.compress_size = compressed_size
    zinfo.compress_type = zipfile.ZIP_STORED

    with zipf.open(zinfo, 'w') as zip_entry:
        with open(temp_path, 'rb') as src:
            while True:
                chunk = src.read(8192)
                if not chunk:
                    break
                zip_entry.write(chunk)

    logger.debug(f"已归档文件: {arcname} ({format_size(original_size)} → {format_size(compressed_size)})")


def create_archive_generic(files: List[str], archive_path: str, compression_algorithm: str, compression_level: int, max_workers: int, chunk_size: int, incremental_mode: bool, logger: logging.Logger) -> None:
    """使用指定压缩算法创建归档文件

    Args:
        files: 需要归档的文件路径列表
        archive_path: 归档文件路径
        compression_algorithm: 压缩算法（lzma / bzip2 / zstd）
        compression_level: 压缩等级（1-9）
        max_workers: 最大工作线程数
        chunk_size: 读取块大小
        incremental_mode: 是否为增量模式
        logger: 日志记录器
    """
    # 预先收集增量模式下 ZIP 中已存在的文件（用于过滤重复）
    existing_files = set()
    existing_size = 0
    if incremental_mode and os.path.exists(archive_path):
        existing_size = os.path.getsize(archive_path)
        logger.info(f"现有归档大小: {format_size(existing_size)}")
        try:
            with zipfile.ZipFile(archive_path, "r") as zipf:
                for info in zipf.infolist():
                    if not info.filename.endswith('/'):
                        existing_files.add(info.filename)
        except Exception as e:
            logger.error(f"读取现有归档文件失败: {e}")

    # 增量模式：过滤掉已在 ZIP 中的重复文件，只压缩新文件
    if incremental_mode and existing_files:
        dup_count = sum(1 for f in files if os.path.basename(f) in existing_files)
        if dup_count > 0:
            logger.info(f"增量模式：跳过 {dup_count} 个已存在于归档中的文件")
        files = [f for f in files if os.path.basename(f) not in existing_files]

    if not files:
        logger.info("没有需要归档的新文件")
        return

    total_files = len(files)

    # 预扫描文件总大小，决定是否启用流式压缩
    total_raw_size = sum(os.path.getsize(fp) for fp in files if os.path.exists(fp))
    use_streaming = total_raw_size > STREAMING_THRESHOLD
    if use_streaming:
        logger.info(f"检测到文件总量 {format_size(total_raw_size)} 超过 {format_size(STREAMING_THRESHOLD)}，启用流式压缩")
    logger.info(f"开始压缩 {total_files} 个文件，使用 {max_workers} 个线程")

    start_time = time.time()
    zip_mode = "a" if incremental_mode and os.path.exists(archive_path) else "w"

    if use_streaming:
        _create_archive_streaming(files, archive_path, compression_algorithm, compression_level,
                                   chunk_size, zip_mode=zip_mode, incremental_mode=incremental_mode,
                                   existing_files=existing_files, existing_size=existing_size,
                                   logger=logger)
    else:
        _create_archive_memory(files, archive_path, compression_algorithm, compression_level,
                                max_workers, chunk_size, zip_mode, incremental_mode,
                                existing_files, existing_size, logger)

    elapsed_time = time.time() - start_time
    final_size = os.path.getsize(archive_path)
    logger.info(f"压缩总耗时: {elapsed_time:.2f}秒，归档总大小: {format_size(final_size)}")

    # 完整性校验：验证归档可打开且条目数一致
    expected_count = len(files)
    try:
        with zipfile.ZipFile(archive_path, "r") as verify_zip:
            verify_entries = [i for i in verify_zip.infolist() if not i.filename.endswith("/")]
            if len(verify_entries) < expected_count:
                logger.error(f"完整性校验失败：归档中仅有 {len(verify_entries)} 个条目，预期至少 {expected_count} 个，将保留原始文件")
                return
    except Exception as e:
        logger.error(f"完整性校验失败：无法打开归档进行验证: {e}，将保留原始文件")
        return

    logger.info("完整性校验通过，开始删除原始文件")
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


def _create_archive_streaming(files: List[str], archive_path: str,
                               compression_algorithm: str, compression_level: int,
                               chunk_size: int,
                               zip_mode: str, incremental_mode: bool,
                               existing_files: set, existing_size: int,
                               logger: logging.Logger) -> None:
    """流式压缩路径：每个文件独立创建压缩器，逐一流式压缩到临时文件后写入 ZIP

    Args:
        files: 文件路径列表
        archive_path: 目标 ZIP 路径
        compression_algorithm: 压缩算法
        compression_level: 压缩等级
        chunk_size: 读取块大小
        zip_mode: ZIP 打开模式（"a" 或 "w"）
        incremental_mode: 是否为增量模式
        existing_files: ZIP 中已存在的文件名集合
        existing_size: 现有 ZIP 大小（字节）
        logger: 日志记录器
    """
    total = len(files)
    temp_dir = tempfile.mkdtemp(prefix="alas_archive_")
    compressed_tempfiles = []  # [(arcname, original_size, compressed_size, temp_path)]

    try:
        # 第一步：每个文件独立创建压缩器，流式压缩到临时文件
        for idx, file_path in enumerate(files, 1):
            if not os.path.exists(file_path):
                continue

            compressor = _get_streaming_compressor(compression_algorithm, compression_level)
            original_size = os.path.getsize(file_path)
            arcname = os.path.basename(file_path)
            temp_path = os.path.join(temp_dir, f"{idx:05d}_{arcname}.tmp")

            with open(file_path, 'rb') as src, open(temp_path, 'wb') as dst:
                while True:
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break
                    dst.write(compressor.compress(chunk))
                dst.write(compressor.flush())

            compressed_size = os.path.getsize(temp_path)
            compressed_tempfiles.append((arcname, original_size, compressed_size, temp_path))
            logger.debug(f"已流式压缩文件: {arcname} ({format_size(original_size)} → {format_size(compressed_size)})")

            progress = (idx / total) * 100
            print(f"\r压缩进度: {progress:.1f}% ({idx}/{total})", end="", flush=True)

        print("\r" + " " * 80 + "\r", end="", flush=True)

        # 第二步：写入 ZIP（调用方已过滤重复文件，所有文件都是新的）
        if compressed_tempfiles:
            total_original = sum(item[1] for item in compressed_tempfiles)
            with zipfile.ZipFile(archive_path, zip_mode, zipfile.ZIP_STORED) as zipf:
                for arcname, orig_size, comp_size, temp_path in compressed_tempfiles:
                    _append_compressed_to_zip(zipf, arcname, temp_path, orig_size, logger)

            total_final = os.path.getsize(archive_path)
            compression_ratio = (1 - total_final / total_original) * 100 if total_original > 0 else 0
            if incremental_mode:
                logger.info(f"新增了 {len(compressed_tempfiles)} 个文件到增量归档，已保存到: {archive_path}")
            else:
                logger.info(f"已完成归档，已保存到: {archive_path}")
            logger.info(f"原始大小: {format_size(total_original)}，压缩后大小: {format_size(total_final)}，压缩率: {compression_ratio:.2f}%")
        else:
            if incremental_mode:
                logger.info(f"无新增文件到增量归档，归档文件未变更: {archive_path}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _create_archive_memory(files: List[str], archive_path: str,
                            compression_algorithm: str, compression_level: int,
                            max_workers: int, chunk_size: int,
                            zip_mode: str, incremental_mode: bool,
                            existing_files: set, existing_size: int,
                            logger: logging.Logger) -> None:
    """内存压缩路径：ThreadPoolExecutor 并行压缩到内存，再 writestr 写入 ZIP

    Args:
        files: 文件路径列表
        archive_path: 目标 ZIP 路径
        compression_algorithm: 压缩算法
        compression_level: 压缩等级
        max_workers: 线程数
        chunk_size: 读取块大小
        zip_mode: ZIP 打开模式
        incremental_mode: 是否为增量模式
        existing_files: ZIP 中已存在的文件名集合
        existing_size: 现有 ZIP 大小（字节）
        logger: 日志记录器
    """
    total_files = len(files)
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
            print(f"\r压缩进度: {progress:.1f}% ({completed}/{total_files})", end="", flush=True)

    print("\r" + " " * 80 + "\r", end="", flush=True)

    if not compressed_results:
        if incremental_mode:
            logger.info(f"无新增文件到增量归档，归档文件未变更: {archive_path}")
        return

    original_size = sum(result[2] for result in compressed_results)

    # 写入主归档
    with zipfile.ZipFile(archive_path, zip_mode, zipfile.ZIP_STORED) as zipf:
        for arcname, compressed_data, orig_size in compressed_results:
            zinfo = zipfile.ZipInfo(arcname, time.localtime()[:6])
            zinfo.file_size = orig_size
            zinfo.compress_size = len(compressed_data)
            zinfo.compress_type = zipfile.ZIP_STORED
            zipf.writestr(zinfo, compressed_data)

    final_size = os.path.getsize(archive_path)
    compression_ratio = (1 - final_size / original_size) * 100 if original_size > 0 else 0
    if incremental_mode:
        logger.info(f"新增了 {len(compressed_results)} 个文件到增量归档，已保存到: {archive_path}")
    else:
        logger.info(f"已完成归档，已保存到: {archive_path}")
    logger.info(f"原始大小: {format_size(original_size)}，压缩后大小: {format_size(final_size)}，压缩率: {compression_ratio:.2f}%")


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

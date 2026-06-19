#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import bz2
import concurrent.futures
import io
import logging
import lzma
import os
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import zstandard as zstd
from typing import List, Set, Tuple

from modules.progress_bar import make_byte_bar

# 压缩相关硬编码参数
# 各算法支持的压缩等级范围
LEVEL_RANGES = {
    'zstd': (1, 22),
    'bzip2': (1, 9),
    'lzma': (0, 19),  # 0-9 常规模式, 10-19 是添加了 PRESET_EXTREME 的 0-9
}
# 单个文件超过此阈值时使用流式压缩直接写入 ZIP
LARGE_FILE_THRESHOLD = 256 * 1024 * 1024  # 256MB


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


def _ratio(compressed: int, original: int) -> float:
    """压缩率 = 压缩后 / 原始 × 100"""
    return compressed / original * 100 if original > 0 else 0.0


def _space_saving(compressed: int, original: int) -> float:
    """空间节省率 = (1 - 压缩后/原始) × 100"""
    return (1 - compressed / original) * 100 if original > 0 else 0.0


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


def compress_file(file_path: str, compression_algorithm: str, compression_level: int, chunk_size: int) -> Tuple[str, bytes, int, float]:
    """压缩单个文件

    Args:
        file_path: 文件路径
        compression_algorithm: 压缩算法
        compression_level: 压缩等级
        chunk_size: 块大小

    Returns:
        Tuple[str, bytes, int, float]: (文件名, 压缩后的数据, 原始大小, 耗时秒)
    """
    t0 = time.time()
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
        compressed_data = bz2.compress(data, level)
    elif algo == "zstd":
        compressed_data = zstd.ZstdCompressor(level=level, write_checksum=True).compress(data)
    else:
        raise ValueError(f"不支持的压缩算法: {compression_algorithm}")

    return (os.path.basename(file_path), compressed_data, original_size, time.time() - t0)


def _stream_compress_to_zip(file_path: str, compression_algorithm: str,
                             compression_level: int, chunk_size: int,
                             zipf: zipfile.ZipFile, logger: logging.Logger) -> Tuple[str, int, int]:
    """流式压缩单个大文件（>256MB）写入 ZIP 条目

    流程：源文件 → 流式压缩到临时文件 → 读取临时文件分块写入 ZIP entry。
    不在内存中累积完整压缩结果。

    Args:
        file_path: 源文件路径
        compression_algorithm: 压缩算法
        compression_level: 压缩等级
        chunk_size: 读取块大小
        zipf: 已打开的 ZipFile 对象（写模式）
        logger: 日志记录器

    Returns:
        (arcname, original_size, compressed_size)
    """
    t0 = time.time()
    algo = compression_algorithm.lower()
    level = _normalize_level(algo, compression_level)
    arcname = os.path.basename(file_path)
    original_size = os.path.getsize(file_path)

    # 先用临时文件接收压缩数据以获取压缩后大小（ZipInfo 需要 compress_size）
    fd, temp_path = tempfile.mkstemp(prefix="alas_large_")
    os.close(fd)

    try:
        with open(file_path, 'rb') as src, open(temp_path, 'wb') as dst:
            if algo == "zstd":
                cctx = zstd.ZstdCompressor(level=level, write_checksum=True)
                with cctx.stream_writer(dst) as writer:
                    while True:
                        chunk = src.read(chunk_size)
                        if not chunk:
                            break
                        writer.write(chunk)
            elif algo == "bzip2":
                cctx = bz2.BZ2Compressor(level)
                while True:
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break
                    dst.write(cctx.compress(chunk))
                dst.write(cctx.flush())
            elif algo == "lzma":
                preset = level % 10
                if level > 9:
                    preset = preset | lzma.PRESET_EXTREME
                cctx = lzma.LZMACompressor(format=lzma.FORMAT_XZ, preset=preset)
                while True:
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break
                    dst.write(cctx.compress(chunk))
                dst.write(cctx.flush())
            else:
                raise ValueError(f"不支持的压缩算法: {compression_algorithm}")


        compressed_size = os.path.getsize(temp_path)
        zinfo = zipfile.ZipInfo(arcname, time.localtime()[:6])
        zinfo.file_size = original_size
        zinfo.compress_size = compressed_size
        zinfo.compress_type = zipfile.ZIP_STORED

        with zipf.open(zinfo, 'w') as zip_entry:
            with open(temp_path, 'rb') as src:
                while True:
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break
                    zip_entry.write(chunk)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    elapsed = time.time() - t0
    logger.debug(f"已流式归档文件: {arcname} ({format_size(original_size)} → {format_size(compressed_size)}, 压缩率: {_ratio(compressed_size, original_size):.1f}% (节省率: {_space_saving(compressed_size, original_size):.1f}%), 压缩用时: {elapsed:.2f}s)")
    return (arcname, original_size, compressed_size)


def _verify_archive_entries(archive_path: str, all_files: List[str],
                             logger: logging.Logger) -> Set[str]:
    """逐条验证 ZIP 中每个条目可读、可解压

    对每个文件打开 ZIP entry，用 magic bytes 检测算法并试解压头部。

    Args:
        archive_path: ZIP 文件路径
        all_files: 本次应归档的源文件路径列表
        logger: 日志记录器

    Returns:
        验证失败的文件路径集合（空集表示全部通过）
    """
    expected_names = {os.path.basename(f) for f in all_files}
    failed = set()

    try:
        with zipfile.ZipFile(archive_path, "r") as vf:
            actual_names = {i.filename for i in vf.infolist() if not i.filename.endswith("/")}
            missing = expected_names - actual_names
            if missing:
                logger.error(f"完整性校验失败：{len(missing)} 个文件未写入归档: {missing}")
                return missing

            for arcname in expected_names:
                try:
                    with vf.open(arcname) as entry:
                        head = entry.read(65536)
                except Exception as e:
                    logger.error(f"无法读取 ZIP 条目 {arcname}: {e}")
                    failed.add(arcname)
                    continue

                # 用 magic bytes 检测算法并试解压（只验证头部可解压出至少 1 字节）
                if len(head) >= 4 and head[:4] == b'\x28\xB5\x2F\xFD':
                    try:
                        reader = zstd.ZstdDecompressor().stream_reader(io.BytesIO(head))
                        reader.read(1)
                        reader.close()
                    except Exception as e:
                        logger.error(f"ZSTD 解压验证失败 {arcname}: {e}")
                        failed.add(arcname)
                elif len(head) >= 3 and head[:3] == b'BZh':
                    try:
                        bz2.BZ2Decompressor().decompress(head, max_length=1)
                    except Exception as e:
                        logger.error(f"BZIP2 解压验证失败 {arcname}: {e}")
                        failed.add(arcname)
                elif len(head) >= 6 and head[:6] == b'\xFD\x37\x7A\x58\x5A\x00':
                    try:
                        lzma.LZMADecompressor().decompress(head, max_length=1)
                    except Exception as e:
                        logger.error(f"LZMA 解压验证失败 {arcname}: {e}")
                        failed.add(arcname)
    except Exception as e:
        logger.critical(f"无法打开归档进行完整性校验: {e}")
        return expected_names  # 全部视为失败

    return failed


def create_archive_generic(files: List[str], archive_path: str, compression_algorithm: str, compression_level: int, max_workers: int, chunk_size: int, incremental_mode: bool, logger: logging.Logger) -> None:
    """使用指定压缩算法创建归档文件（统一流水线）

    小文件（≤256MB）：ThreadPoolExecutor 并发压缩到内存 → zipf.writestr()
    大文件（>256MB）：单线程流式压缩到临时文件 → zipf.open() 写入 ZIP entry

    All files are compressed, ZIP is closed, integrity is verified,
    then source files are deleted atomically.

    Args:
        files: 需要归档的文件路径列表
        archive_path: 归档文件路径
        compression_algorithm: 压缩算法（lzma / bzip2 / zstd）
        compression_level: 压缩等级
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

    # 增量模式：过滤掉已在 ZIP 中的重复文件
    all_files = list(files)  # 保存原始列表，用于校验和删除
    if incremental_mode and existing_files:
        dup_count = sum(1 for f in files if os.path.basename(f) in existing_files)
        if dup_count > 0:
            logger.info(f"增量模式：跳过 {dup_count} 个已存在于归档中的文件")
        files = [f for f in files if os.path.basename(f) not in existing_files]

    if not files:
        # 所有文件均已存在于 ZIP 中，校验后删除原始文件
        logger.info("没有需要归档的新文件")
        verify_failed = _verify_archive_entries(archive_path, all_files, logger)
        if verify_failed:
            logger.error(f"完整性校验失败：{len(verify_failed)} 个文件验证未通过，将保留原始文件")
            return
        logger.info("所有文件已存在于归档中，清理原始文件")
        deleted_count = 0
        for file_path in all_files:
            if not os.path.exists(file_path):
                continue
            try:
                os.remove(file_path)
                logger.debug(f"已删除原始文件: {os.path.basename(file_path)}")
                deleted_count += 1
            except Exception as e:
                logger.error(f"删除文件 {file_path} 失败: {e}")
        logger.info(f"共删除 {deleted_count} 个原始文件")
        return

    # 按文件大小分流
    small_files = []
    large_files = []
    for fp in files:
        if not os.path.exists(fp):
            continue
        if os.path.getsize(fp) > LARGE_FILE_THRESHOLD:
            large_files.append(fp)
        else:
            small_files.append(fp)

    if large_files:
        logger.info(f"检测到 {len(large_files)} 个大文件（>256MB），将对其使用流式压缩")
    total_files = len(small_files) + len(large_files)
    total_bytes = sum(os.path.getsize(fp) for fp in small_files + large_files)
    logger.info(f"开始压缩 {total_files} 个文件（{format_size(total_bytes)}），使用 {max_workers} 个线程")

    start_time = time.time()
    zip_mode = "a" if incremental_mode and os.path.exists(archive_path) else "w"
    total_original = 0
    processed_count = 0
    failed_files = []  # 记录压缩失败的源文件路径

    with zipfile.ZipFile(archive_path, zip_mode, zipfile.ZIP_STORED) as zipf:

        pbar = make_byte_bar(total_bytes, desc="正在压缩: ")

        # 并发压缩小文件
        if small_files:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(compress_file, fp, compression_algorithm, compression_level, chunk_size): fp
                    for fp in small_files
                }
                for future in concurrent.futures.as_completed(futures):
                    file_path = futures[future]
                    try:
                        arcname, compressed_data, orig_size, elapsed = future.result()
                        zinfo = zipfile.ZipInfo(arcname, time.localtime()[:6])
                        zinfo.file_size = orig_size
                        zinfo.compress_size = len(compressed_data)
                        zinfo.compress_type = zipfile.ZIP_STORED
                        zipf.writestr(zinfo, compressed_data)
                        total_original += orig_size
                        logger.debug(f"已归档文件: {arcname} ({format_size(orig_size)} → {format_size(len(compressed_data))}, 压缩率: {_ratio(len(compressed_data), orig_size):.1f}% (节省率: {_space_saving(len(compressed_data), orig_size):.1f}%), 压缩用时: {elapsed:.2f}s)")
                        del compressed_data  # 立即释放内存
                    except Exception as e:
                        logger.error(f"压缩文件 {file_path} 失败: {e}")
                        failed_files.append(file_path)
                        orig_size = os.path.getsize(file_path)
                    processed_count += 1
                    pbar.update(orig_size)
                    pbar.set_description(f"正在压缩 ({os.path.basename(file_path)})")

        # 大文件流式压缩（单线程顺序）
        for file_path in large_files:
            try:
                arcname, orig_size, comp_size = _stream_compress_to_zip(
                    file_path, compression_algorithm, compression_level, chunk_size, zipf, logger)
                total_original += orig_size
            except Exception as e:
                logger.error(f"流式压缩文件 {file_path} 失败: {e}")
                failed_files.append(file_path)
                orig_size = os.path.getsize(file_path)
            processed_count += 1
            pbar.update(orig_size)
            pbar.set_description(f"正在压缩 ({os.path.basename(file_path)})")

        pbar.close()

    elapsed_time = time.time() - start_time
    final_size = os.path.getsize(archive_path)
    logger.info(f"压缩总耗时: {elapsed_time:.2f}秒，归档总大小: {format_size(final_size)}")

    # 如果压缩过程中有文件失败，阻断删除
    if failed_files:
        logger.error(f"归档部分失败：{len(failed_files)} 个文件未能写入归档，将保留所有原始文件")
        logger.error(f"失败文件: {failed_files}")
        return

    # 完整性校验：逐条验证 ZIP 中每个条目可读、可解压
    verify_failed = _verify_archive_entries(archive_path, all_files, logger)
    if verify_failed:
        logger.critical(f"完整性校验失败：{len(verify_failed)} 个文件验证未通过，将保留原始文件")
        return

    # 统计信息
    if incremental_mode:
        added_compressed = final_size - existing_size
        logger.info(f"新增了 {len(files)} 个文件到增量归档，已保存到: {archive_path}")
        logger.info(f"新增原始大小: {format_size(total_original)}，新增压缩大小: {format_size(added_compressed)}，压缩率: {_ratio(added_compressed, total_original):.1f}% (节省率: {_space_saving(added_compressed, total_original):.1f}%)")
        logger.info(f"归档总大小: {format_size(final_size)}")
    else:
        logger.info(f"已完成归档，已保存到: {archive_path}")
        logger.info(f"原始大小: {format_size(total_original)}，压缩后大小: {format_size(final_size)}，压缩率: {_ratio(final_size, total_original):.1f}% (节省率: {_space_saving(final_size, total_original):.1f}%)")

    logger.info("完整性校验通过，开始删除原始文件")
    deleted_count = 0
    for file_path in all_files:
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
            logger.info(f"未找到现有归档文件，将创建新归档文件: {archive_filename}")
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

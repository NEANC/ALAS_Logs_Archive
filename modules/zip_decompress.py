#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import bz2
import logging
import lzma
import os
import sys
import zipfile
import zstandard as zstd

# 流式解压阈值 1GB
STREAMING_THRESHOLD = 1 * 1024 * 1024 * 1024


def decompress_archive(archive_path: str, output_dir: str, logger: logging.Logger) -> None:
    """解压由本工具创建的归档文件

    自动检测压缩算法（zstd/bzip2/lzma）并还原原始文件。
    由于本工具在归档时先压缩再存入ZIP（ZIP_STORED模式），
    直接用常规解压工具提取得到的将是压缩后的乱码数据，
    必须使用本函数才能正确还原。
    单个文件超过 1GB 时自动启用流式解压。

    Args:
        archive_path: 归档文件路径（ZIP文件）
        output_dir: 解压输出目录
        logger: 日志记录器
    """
    if not os.path.exists(archive_path):
        logger.error(f"归档文件不存在: {archive_path}")
        sys.exit(1)

    if not zipfile.is_zipfile(archive_path):
        logger.error(f"文件不是有效的ZIP归档: {archive_path}")
        sys.exit(1)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    with zipfile.ZipFile(archive_path, "r") as zipf:
        entries = [info for info in zipf.infolist() if not info.filename.endswith("/")]
        total_entries = len(entries)
        logger.info(f"归档中共有 {total_entries} 个文件，开始解压")

        extracted_count = 0
        for info in entries:
            output_path = os.path.join(output_dir, info.filename)
            parent_dir = os.path.dirname(output_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)

            # 单个文件超过阈值时使用流式解压
            if info.compress_size > STREAMING_THRESHOLD:
                logger.info(f"检测到大文件 {info.filename} ({info.compress_size / (1024**3):.2f} GB)，启用流式解压")
                _decompress_entry_streaming(zipf, info, output_path, logger)
            else:
                _decompress_entry_memory(zipf, info, output_path, logger)

            extracted_count += 1
            progress = (extracted_count / total_entries) * 100
            progress_line = f"\r解压进度: {progress:.1f}% ({extracted_count}/{total_entries})"
            print(progress_line, end="", flush=True)

    print("\r" + " " * 80 + "\r", end="", flush=True)
    logger.info(f"解压完成，共 {extracted_count} 个文件，输出到: {output_dir}")


def _decompress_entry_memory(zipf: zipfile.ZipFile, info: zipfile.ZipInfo,
                              output_path: str, logger: logging.Logger) -> None:
    """内存解压：将整个条目读入内存后解压（适用于小文件）"""
    compressed_data = zipf.read(info.filename)

    # 自动检测压缩算法并解压：zstd → bzip2 → lzma，都不匹配则视为未压缩
    data = None
    try:
        data = zstd.decompress(compressed_data)
        logger.debug(f"使用 zstd 解压: {info.filename}")
    except Exception:
        try:
            data = bz2.decompress(compressed_data)
            logger.debug(f"使用 bzip2 解压: {info.filename}")
        except Exception:
            try:
                data = lzma.decompress(compressed_data)
                logger.debug(f"使用 lzma 解压: {info.filename}")
            except Exception:
                data = compressed_data
                logger.debug(f"未检测到压缩，直接提取: {info.filename}")

    with open(output_path, "wb") as f:
        f.write(data)


def _decompress_entry_streaming(zipf: zipfile.ZipFile, info: zipfile.ZipInfo,
                                 output_path: str, logger: logging.Logger) -> None:
    """流式解压：分块读取 ZIP 条目并流式解压写入（适用于 >1GB 的文件）

    Args:
        zipf: 已打开的 ZipFile 对象
        info: ZIP 条目信息
        output_path: 输出文件路径
        logger: 日志记录器
    """
    # 读取头部 chunk 用于检测压缩算法
    with zipf.open(info, 'r') as src:
        head_chunk = src.read(65536)  # 64KB 头部

    # 算法检测
    algo_name = "none"
    decompressor = None
    try:
        zstd.decompress(head_chunk)
        decompressor = zstd.ZstdDecompressor()
        algo_name = "zstd"
    except Exception:
        try:
            bz2.decompress(head_chunk)
            decompressor = bz2.BZ2Decompressor()
            algo_name = "bzip2"
        except Exception:
            try:
                lzma.decompress(head_chunk)
                decompressor = lzma.LZMADecompressor()
                algo_name = "lzma"
            except Exception:
                algo_name = "none"

    logger.debug(f"使用流式 {algo_name} 解压: {info.filename}")

    with zipf.open(info, 'r') as src, open(output_path, 'wb') as dst:
        if decompressor:
            # 流式解压：分块读取、解压、写入
            while True:
                chunk = src.read(128 * 1024)  # 128KB 块
                if not chunk:
                    break
                try:
                    data = decompressor.decompress(chunk)
                    if data:
                        dst.write(data)
                except EOFError:
                    # bzip2/lzma 流结束
                    break
                except Exception as e:
                    logger.debug(f"流式解压异常: {e}")
                    break
            # flush 剩余数据（bzip2/lzma/zstd 都有 flush 方法）
            try:
                tail = decompressor.flush()
                if tail:
                    dst.write(tail)
            except Exception:
                pass
        else:
            # 未压缩：直接流式复制
            while True:
                chunk = src.read(128 * 1024)
                if not chunk:
                    break
                dst.write(chunk)

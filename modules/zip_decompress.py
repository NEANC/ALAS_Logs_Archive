#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import bz2
import logging
import lzma
import os
import shutil
import sys
import zipfile
import zstandard as zstd

# 压缩算法 magic bytes（6 字节足够区分全部格式）
ZSTD_MAGIC = b"\x28\xB5\x2F\xFD"
BZIP2_MAGIC = b"BZh"
XZ_MAGIC = b"\xFD\x37\x7A\x58\x5A\x00"

CHUNK_SIZE = 131072  # 128KB


def _detect_compression_algorithm(magic: bytes) -> str:
    """通过 magic bytes 检测压缩算法"""
    if len(magic) >= 4 and magic[:4] == ZSTD_MAGIC:
        return "zstd"
    if len(magic) >= 3 and magic[:3] == BZIP2_MAGIC:
        return "bzip2"
    if len(magic) >= 6 and magic[:6] == XZ_MAGIC:
        return "lzma"
    return "none"


def decompress_archive(archive_path: str, output_dir: str, logger: logging.Logger) -> None:
    """解压由本工具创建的归档文件

    自动检测压缩算法（zstd/bzip2/lzma）并还原原始文件。
    由于本工具在归档时先压缩再存入ZIP（ZIP_STORED模式），
    直接用常规解压工具提取得到的将是压缩后的乱码数据，
    必须使用本函数才能正确还原。
    单个文件超过 1GB 时自动启用流式解压。
    包含 Zip Slip 路径穿越防护。

    Args:
        archive_path: 归档文件路径（ZIP文件）
        output_dir: 解压输出目录
        logger: 日志记录器
    """
    if not os.path.exists(archive_path):
        logger.critical(f"归档文件不存在: {archive_path}")
        sys.exit(1)

    if not zipfile.is_zipfile(archive_path):
        logger.critical(f"文件不是有效的ZIP归档: {archive_path}")
        sys.exit(1)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    real_output_dir = os.path.realpath(output_dir)

    with zipfile.ZipFile(archive_path, "r") as zipf:
        entries = [info for info in zipf.infolist() if not info.filename.endswith("/")]
        total_entries = len(entries)
        logger.info(f"归档中共有 {total_entries} 个文件，开始解压")

        extracted_count = 0
        for info in entries:
            output_path = os.path.join(output_dir, info.filename)

            # Zip Slip 路径穿越防护
            real_output_path = os.path.realpath(output_path)
            if os.path.commonpath([real_output_dir, real_output_path]) != real_output_dir:
                logger.warning(f"{os.path.basename(info.filename)} 文件解压路径异常: {output_path}")
                logger.warning(f"应解压到: {real_output_path}")
                logger.warning("已跳过该文件，请检查归档文件是否被篡改")
                continue

            parent_dir = os.path.dirname(output_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)

            # 使用流式解压
            _decompress_entry_streaming(zipf, info, output_path, logger)

            extracted_count += 1
            progress = (extracted_count / total_entries) * 100
            print(f"\r解压进度: {progress:.1f}% ({extracted_count}/{total_entries})", end="", flush=True)

    print("\r" + " " * 80 + "\r", end="", flush=True)
    logger.info(f"解压完成，共 {extracted_count} 个文件，输出到: {output_dir}")


def _decompress_entry_streaming(zipf: zipfile.ZipFile, info: zipfile.ZipInfo,
                                 output_path: str, logger: logging.Logger) -> None:
    """流式解压：分块读取 ZIP 条目并流式解压写入

    通过 magic bytes 检测压缩算法。
    ZSTD:  \x28\xB5\x2F\xFD   BZIP2: BZh   XZ: \xFD\x37\x7A\x58\x5A\x00

    Args:
        zipf: 已打开的 ZipFile 对象
        info: ZIP 条目信息
        output_path: 输出文件路径
        logger: 日志记录器
    """
    # 读 magic bytes 检测算法（6 字节）
    with zipf.open(info, 'r') as src:
        magic = src.read(6)
    algo = _detect_compression_algorithm(magic)
    logger.debug(f"确认文件: {os.path.basename(info.filename)} 的压缩算法为: {algo}，将文件解压到: {output_path}")

    # 重新打开，流式解压写入
    with zipf.open(info, 'r') as src, open(output_path, 'wb') as dst:
        if algo == "zstd":
            zstd.ZstdDecompressor().copy_stream(src, dst, read_size=CHUNK_SIZE, write_size=CHUNK_SIZE)
        elif algo in ("bzip2", "lzma"):
            decompressor = (bz2.BZ2Decompressor() if algo == "bzip2"
                            else lzma.LZMADecompressor())
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                try:
                    data = decompressor.decompress(chunk)
                    if data:
                        dst.write(data)
                except EOFError:
                    break
            # flush 剩余尾部数据
            try:
                tail = decompressor.flush()
                if tail:
                    dst.write(tail)
            except Exception:
                pass
        else:
            # 未压缩：直接流式复制
            shutil.copyfileobj(src, dst, length=CHUNK_SIZE)

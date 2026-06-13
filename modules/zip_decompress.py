#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import bz2
import logging
import lzma
import os
import sys
import zipfile
import zstandard as zstd


def decompress_archive(archive_path: str, output_dir: str, logger: logging.Logger) -> None:
    """解压由本工具创建的归档文件

    自动检测压缩算法（bzip2/lzma）并还原原始文件。
    由于本工具在归档时先压缩再存入ZIP（ZIP_STORED模式），
    直接用常规解压工具提取得到的将是压缩后的乱码数据，
    必须使用本函数才能正确还原。

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

            output_path = os.path.join(output_dir, info.filename)
            parent_dir = os.path.dirname(output_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)

            with open(output_path, "wb") as f:
                f.write(data)

            extracted_count += 1
            progress = (extracted_count / total_entries) * 100
            progress_line = f"\r解压进度: {progress:.1f}% ({extracted_count}/{total_entries})"
            print(progress_line, end="", flush=True)

    print("\r" + " " * 80 + "\r", end="", flush=True)
    logger.info(f"解压完成，共 {extracted_count} 个文件，输出到: {output_dir}")

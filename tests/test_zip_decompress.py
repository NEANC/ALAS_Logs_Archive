#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 modules.zip_decompress 模块"""

import bz2
import logging
import os
import tempfile
import zipfile

from modules.zip_decompress import decompress_archive
from modules.zip_compress import compress_file


def _create_test_zip(archive_path: str, files: dict) -> None:
    """创建测试用ZIP归档（模拟本工具的归档方式：先压缩再ZIP_STORED）

    Args:
        archive_path: ZIP文件路径
        files: {文件名: 原始字节数据} 字典
    """
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as zipf:
        for filename, data in files.items():
            compressed = bz2.compress(data, compresslevel=9)
            zinfo = zipfile.ZipInfo(filename)
            zinfo.file_size = len(data)
            zinfo.compress_size = len(compressed)
            zinfo.compress_type = zipfile.ZIP_STORED
            zipf.writestr(zinfo, compressed)


class TestDecompressArchive:
    """decompress_archive 函数测试"""

    def test_decompress_bzip2(self):
        """测试解压含bzip2压缩文件的ZIP"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.WARNING)

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, "test.zip")
            output_dir = os.path.join(tmpdir, "output")

            original_content = b"Hello World! Decompress test.\n" * 100
            _create_test_zip(archive_path, {"test_file.txt": original_content})

            decompress_archive(archive_path, output_dir, logger)

            output_file = os.path.join(output_dir, "test_file.txt")
            assert os.path.exists(output_file)
            with open(output_file, "rb") as f:
                result = f.read()
            assert result == original_content

    def test_decompress_multiple_files(self):
        """测试解压多文件ZIP"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.WARNING)

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, "test.zip")
            output_dir = os.path.join(tmpdir, "output")

            original_files = {
                "file1.txt": b"Content 1\n" * 50,
                "file2.txt": b"Content 2\n" * 50,
                "file3.txt": b"Content 3\n" * 50,
            }
            _create_test_zip(archive_path, original_files)

            decompress_archive(archive_path, output_dir, logger)

            for filename, expected in original_files.items():
                output_file = os.path.join(output_dir, filename)
                assert os.path.exists(output_file)
                with open(output_file, "rb") as f:
                    assert f.read() == expected

    def test_decompress_creates_output_dir(self):
        """解压时自动创建输出目录"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.WARNING)

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, "test.zip")
            output_dir = os.path.join(tmpdir, "nested", "output")

            _create_test_zip(archive_path, {"hello.txt": b"world"})

            assert not os.path.exists(output_dir)
            decompress_archive(archive_path, output_dir, logger)
            assert os.path.exists(output_dir)

    def test_decompress_lzma(self):
        """测试解压含lzma压缩文件的ZIP"""
        import lzma
        logger = logging.getLogger("test")
        logger.setLevel(logging.WARNING)

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, "test.zip")
            output_dir = os.path.join(tmpdir, "output")

            original_content = b"LZMA compressed data\n" * 100
            compressed = lzma.compress(original_content)

            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as zipf:
                zinfo = zipfile.ZipInfo("lzma_file.txt")
                zinfo.file_size = len(original_content)
                zinfo.compress_size = len(compressed)
                zinfo.compress_type = zipfile.ZIP_STORED
                zipf.writestr(zinfo, compressed)

            decompress_archive(archive_path, output_dir, logger)
            output_file = os.path.join(output_dir, "lzma_file.txt")
            with open(output_file, "rb") as f:
                assert f.read() == original_content

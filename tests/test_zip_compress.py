#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 modules.zip_compress 模块"""

import logging
import os
import tempfile

import pytest

from modules.zip_compress import format_size, compress_file, create_archive, read_file_fully


class TestFormatSize:
    """format_size 函数测试"""

    def test_bytes(self):
        assert format_size(500) == "500.00 B"

    def test_kb(self):
        assert format_size(2048) == "2.00 KB"

    def test_mb(self):
        assert format_size(3 * 1024 * 1024) == "3.00 MB"

    def test_gb(self):
        assert format_size(2 * 1024 * 1024 * 1024) == "2.00 GB"

    def test_zero(self):
        assert format_size(0).startswith("0.00")


class TestReadFileChunked:
    """read_file_fully 函数测试"""

    def test_small_file(self):
        """读取小于块大小的文件"""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(b"Hello World!")
            tmp_path = f.name

        try:
            result = read_file_fully(tmp_path, 8192)
            assert result == b"Hello World!"
        finally:
            os.unlink(tmp_path)

    def test_chunked_read(self):
        """分块读取大于块大小的文件"""
        data = b"A" * 5000
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(data)
            tmp_path = f.name

        try:
            result = read_file_fully(tmp_path, 1024)
            assert result == data
            assert len(result) == 5000
        finally:
            os.unlink(tmp_path)


class TestCompressFile:
    """compress_file 函数测试"""

    def test_compress_bzip2(self):
        """测试 bzip2 压缩"""
        data = b"Hello World! " * 100
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(data)
            tmp_path = f.name

        try:
            name, compressed, orig_size, _ = compress_file(tmp_path, "bzip2", 9, 8192)
            assert name.endswith(".txt")
            assert orig_size == len(data)
            assert len(compressed) < len(data)  # 压缩后应更小
            # 验证可以正确解压
            import bz2
            decompressed = bz2.decompress(compressed)
            assert decompressed == data
        finally:
            os.unlink(tmp_path)

    def test_compress_lzma(self):
        """测试 lzma 压缩"""
        data = b"Hello World! " * 100
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(data)
            tmp_path = f.name

        try:
            name, compressed, orig_size, _ = compress_file(tmp_path, "lzma", 9, 8192)
            assert name.endswith(".txt")
            assert orig_size == len(data)
            # 验证可以正确解压
            import lzma
            decompressed = lzma.decompress(compressed)
            assert decompressed == data
        finally:
            os.unlink(tmp_path)

    @pytest.mark.parametrize("level", [10, 15, 19])
    def test_compress_lzma_extreme(self, level):
        """测试 lzma PRESET_EXTREME (10-19) 压缩并验证可解压"""
        data = b"PRESET_EXTREME test data\n" * 200
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(data)
            tmp_path = f.name

        try:
            name, compressed, orig_size, _ = compress_file(tmp_path, "lzma", level, 8192)
            assert name.endswith(".txt")
            assert orig_size == len(data)
            assert len(compressed) < len(data)
            # 验证可以正确解压
            import lzma
            decompressed = lzma.decompress(compressed)
            assert decompressed == data
        finally:
            os.unlink(tmp_path)

    def test_compress_zstd(self):
        """测试 zstd 压缩"""
        data = b"Hello World! " * 100
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(data)
            tmp_path = f.name

        try:
            name, compressed, orig_size, _ = compress_file(tmp_path, "zstd", 9, 8192)
            assert name.endswith(".txt")
            assert orig_size == len(data)
            assert len(compressed) < len(data)
            # 验证可以正确解压
            import zstandard as zstd
            decompressed = zstd.decompress(compressed)
            assert decompressed == data
        finally:
            os.unlink(tmp_path)

    def test_compress_invalid_algorithm(self):
        """测试不支持的压缩算法抛出异常"""
        data = b"test"
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(data)
            tmp_path = f.name

        try:
            with pytest.raises(ValueError, match="不支持的压缩算法"):
                compress_file(tmp_path, "gzip", 1, 8192)
        finally:
            os.unlink(tmp_path)

    def test_compress_returns_filename(self):
        """验证返回的文件名是 basename"""
        data = b"test"
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(data)
            tmp_path = f.name

        try:
            name, compressed, orig_size, _ = compress_file(tmp_path, "bzip2", 9, 8192)
            assert name == os.path.basename(tmp_path)
        finally:
            os.unlink(tmp_path)


class TestCreateArchive:
    """create_archive 端到端测试"""

    def test_scroll_mode_creates_archive(self):
        """测试滚动模式创建归档并删除源文件"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.WARNING)

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            src_dir = os.path.join(tmpdir, "src")
            archive_dir = os.path.join(tmpdir, "archive")
            os.makedirs(src_dir)

            file1 = os.path.join(src_dir, "2025-01-01.log")
            file2 = os.path.join(src_dir, "2025-01-02.log")
            with open(file1, "w", encoding="utf-8") as f:
                f.write("log content 1\n" * 50)
            with open(file2, "w", encoding="utf-8") as f:
                f.write("log content 2\n" * 50)

            files = [file1, file2]
            create_archive(files, archive_dir, "测试归档.zip", "bzip2", 9, "scroll", 1, 8192, logger)

            # 验证归档文件存在
            archives = os.listdir(archive_dir)
            assert len(archives) == 1
            assert archives[0].startswith("20")  # 日期前缀
            assert archives[0].endswith(".zip")

            # 验证源文件已被删除
            assert not os.path.exists(file1)
            assert not os.path.exists(file2)

    def test_empty_files_no_archive(self):
        """空文件列表不创建归档"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.WARNING)

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_dir = os.path.join(tmpdir, "archive")
            create_archive([], archive_dir, "empty.zip", "bzip2", 9, "scroll", 1, 8192, logger)
            assert not os.path.exists(os.path.join(archive_dir, "empty.zip"))

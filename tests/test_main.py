#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 ALAS_Logs_Archive 主入口文件的函数"""

import logging
import os
import sys
import tempfile

import pytest

from ALAS_Logs_Archive import (
    get_files_to_archive,
    validate_compression_algorithm,
    validate_compression_level,
    validate_archive_mode,
    parse_command_line_args,
)


class TestValidateFunctions:
    """验证函数测试"""

    @pytest.mark.parametrize("algo,expected", [
        ("bzip2", True),
        ("lzma", True),
        ("BZIP2", True),
        ("LZMA", True),
        ("gzip", False),
        ("zip", False),
        ("", False),
    ])
    def test_validate_compression_algorithm(self, algo, expected):
        assert validate_compression_algorithm(algo) == expected

    @pytest.mark.parametrize("level,expected", [
        (1, True),
        (5, True),
        (9, True),
        (0, False),
        (10, False),
        (-1, False),
    ])
    def test_validate_compression_level(self, level, expected):
        assert validate_compression_level(level) == expected

    @pytest.mark.parametrize("mode,expected", [
        ("scroll", True),
        ("incremental", True),
        ("SCROLL", True),
        ("INCREMENTAL", True),
        ("overwrite", False),
        ("append", False),
        ("", False),
    ])
    def test_validate_archive_mode(self, mode, expected):
        assert validate_archive_mode(mode) == expected


class TestGetFilesToArchive:
    """get_files_to_archive 测试"""

    def test_returns_non_today_files(self):
        """返回非当日的非gui文件"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.DEBUG)

        with tempfile.TemporaryDirectory() as tmpdir:
            # 旧日志文件（应被归档）
            old_log = os.path.join(tmpdir, "2025-01-01_Anything.log")
            with open(old_log, "w") as f:
                f.write("old")

            # 当日文件（不应被归档）
            today_log = os.path.join(tmpdir, "2025-06-07_Anything.log")
            with open(today_log, "w") as f:
                f.write("today")

            # gui文件（不应被归档）
            gui_file = os.path.join(tmpdir, "2025-01-01_gui.txt")
            with open(gui_file, "w") as f:
                f.write("gui")

            # 非日期前缀的文件（应被归档）
            misc_file = os.path.join(tmpdir, "other_file.txt")
            with open(misc_file, "w") as f:
                f.write("other")

            files = get_files_to_archive(tmpdir, "2025-06-07", logger)

            file_names = [os.path.basename(f) for f in files]
            assert "2025-01-01_Anything.log" in file_names
            assert "other_file.txt" in file_names
            assert "2025-06-07_Anything.log" not in file_names
            assert "2025-01-01_gui.txt" not in file_names

    def test_target_not_exists(self):
        """目标文件夹不存在时返回空列表"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.DEBUG)
        files = get_files_to_archive("/nonexistent/path", "2025-06-07", logger)
        assert files == []


class TestParseCommandLineArgs:
    """parse_command_line_args 测试"""

    def test_no_args(self):
        """无参数时有默认值"""
        old_argv = sys.argv[:]
        sys.argv = ["ALAS_Logs_Archive.py"]
        try:
            args = parse_command_line_args()
            assert args.name is None
            assert args.target is None
            assert args.decompress is None
            assert args.mode is None
        finally:
            sys.argv = old_argv

    def test_decompress_arg(self):
        """-d 参数正确解析"""
        old_argv = sys.argv[:]
        sys.argv = ["ALAS_Logs_Archive.py", "-d", "test.zip", "-o", "output_dir"]
        try:
            args = parse_command_line_args()
            assert args.decompress == "test.zip"
            assert args.output == "output_dir"
        finally:
            sys.argv = old_argv

    def test_compression_args(self):
        """压缩相关参数正确解析"""
        old_argv = sys.argv[:]
        sys.argv = ["ALAS_Logs_Archive.py", "-c", "lzma", "-l", "5", "-w", "2"]
        try:
            args = parse_command_line_args()
            assert args.compression == "lzma"
            assert args.level == 5
            assert args.workers == 2
        finally:
            sys.argv = old_argv

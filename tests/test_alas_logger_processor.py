#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 modules.alas_logger_processor 模块"""

import logging
import os
import tempfile

import pytest

from modules.alas_logger_processor import delete_error_folder, delete_gui_files


class TestDeleteGuiFiles:
    """delete_gui_files 测试"""

    def test_deletes_old_gui_files(self):
        """删除旧gui.txt文件，保留当日文件"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.DEBUG)

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建旧 gui 文件
            old_file = os.path.join(tmpdir, "2025-01-01_gui.txt")
            current_file = os.path.join(tmpdir, "2025-06-07_gui.txt")
            with open(old_file, "w") as f:
                f.write("old")
            with open(current_file, "w") as f:
                f.write("current")

            delete_gui_files(tmpdir, "2025-06-07", logger)

            assert not os.path.exists(old_file)
            assert os.path.exists(current_file)

    def test_non_gui_files_untouched(self):
        """非gui.txt格式的文件不应被删除"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.DEBUG)

        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "2025-01-01.log")
            with open(log_file, "w") as f:
                f.write("log")

            delete_gui_files(tmpdir, "2025-06-07", logger)

            assert os.path.exists(log_file)

    def test_target_not_exists(self):
        """目标文件夹不存在时不报错"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.DEBUG)
        delete_gui_files("/nonexistent/path", "2025-06-07", logger)


class TestDeleteErrorFolder:
    """delete_error_folder 测试"""

    def test_deletes_error_folder(self):
        """删除error文件夹"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.DEBUG)

        with tempfile.TemporaryDirectory() as tmpdir:
            error_dir = os.path.join(tmpdir, "error")
            os.makedirs(error_dir)
            with open(os.path.join(error_dir, "error.log"), "w") as f:
                f.write("error")

            delete_error_folder(tmpdir, logger)

            assert not os.path.exists(error_dir)

    def test_no_error_folder(self):
        """error文件夹不存在时无操作"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.DEBUG)

        with tempfile.TemporaryDirectory() as tmpdir:
            delete_error_folder(tmpdir, logger)

    def test_target_not_exists(self):
        """目标文件夹不存在时不报错"""
        logger = logging.getLogger("test")
        logger.setLevel(logging.DEBUG)
        delete_error_folder("/nonexistent/path", logger)

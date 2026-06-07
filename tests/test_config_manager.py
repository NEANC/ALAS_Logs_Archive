#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 modules.config_manager 的 ConfigManager.validate() 方法"""

import logging

import pytest

from modules.config_manager import ConfigManager

# 测试用配置文件模板
_CONFIG_HEAD = """[settings]
target_folder = C:\\test
archive_folder = C:\\archive
[zip]
"""
_LOG_TAIL = """
[log]
save_logs = true
log_folder = logs
max_log_files = 15
log_level = INFO
"""


class TestConfigManagerValidate:
    """ConfigManager.validate() 测试（替代已移除的三个独立 validate 函数）"""

    def test_validate_all_valid(self, tmp_path):
        """所有配置合法时返回True"""
        config_path = tmp_path / "test.ini"
        config_path.write_text(_CONFIG_HEAD + """compression_algorithm = bzip2
compression_level = 9
archive_mode = scroll
max_workers = 1
""" + _LOG_TAIL, encoding="utf-8")

        mgr = ConfigManager(str(config_path))
        mgr.load()
        assert mgr.validate() is True

    @pytest.mark.parametrize("algo,valid", [
        ("bzip2", True),
        ("lzma", True),
        ("BZIP2", True),
        ("LZMA", True),
        ("gzip", False),
        ("zip", False),
        ("", False),
    ])
    def test_validate_compression_algorithm(self, algo, valid, tmp_path):
        """校验压缩算法"""
        config_path = tmp_path / "test.ini"
        config_path.write_text(_CONFIG_HEAD + f"""compression_algorithm = {algo}
compression_level = 9
archive_mode = scroll
max_workers = 1
""" + _LOG_TAIL, encoding="utf-8")

        mgr = ConfigManager(str(config_path))
        mgr.load()
        assert mgr.validate() is valid

    @pytest.mark.parametrize("level,valid", [
        (1, True),
        (5, True),
        (9, True),
        (0, False),
        (10, False),
        (-1, False),
    ])
    def test_validate_compression_level(self, level, valid, tmp_path):
        """校验压缩等级"""
        config_path = tmp_path / "test.ini"
        config_path.write_text(_CONFIG_HEAD + f"""compression_algorithm = bzip2
compression_level = {level}
archive_mode = scroll
max_workers = 1
""" + _LOG_TAIL, encoding="utf-8")

        mgr = ConfigManager(str(config_path))
        mgr.load()
        assert mgr.validate() is valid

    @pytest.mark.parametrize("mode,valid", [
        ("scroll", True),
        ("incremental", True),
        ("SCROLL", True),
        ("INCREMENTAL", True),
        ("overwrite", False),
        ("append", False),
        ("", False),
    ])
    def test_validate_archive_mode(self, mode, valid, tmp_path):
        """校验归档模式"""
        config_path = tmp_path / "test.ini"
        config_path.write_text(_CONFIG_HEAD + f"""compression_algorithm = bzip2
compression_level = 9
archive_mode = {mode}
max_workers = 1
""" + _LOG_TAIL, encoding="utf-8")

        mgr = ConfigManager(str(config_path))
        mgr.load()
        assert mgr.validate() is valid

    def test_validate_empty_target_folder(self, tmp_path):
        """target_folder 为显式空值时返回False（load自动补默认值，需手动覆盖）"""
        config_path = tmp_path / "test.ini"
        config_path.write_text(_CONFIG_HEAD + """compression_algorithm = bzip2
compression_level = 9
archive_mode = scroll
max_workers = 1
""" + _LOG_TAIL, encoding="utf-8")

        mgr = ConfigManager(str(config_path))
        mgr.load()
        mgr.target_folder = ""  # 手动清空以模拟异常状态
        assert mgr.validate() is False

    def test_validate_invalid_max_workers(self, tmp_path):
        """max_workers 为0时返回False"""
        config_path = tmp_path / "test.ini"
        config_path.write_text(_CONFIG_HEAD + """compression_algorithm = bzip2
compression_level = 9
archive_mode = scroll
max_workers = 0
""" + _LOG_TAIL, encoding="utf-8")

        mgr = ConfigManager(str(config_path))
        mgr.load()
        assert mgr.validate() is False

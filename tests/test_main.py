#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 ALAS_Logs_Archive 主入口文件的函数"""

import io
import logging
import os
import sys
import tempfile
from unittest import mock

import pytest

from ALAS_Logs_Archive import (
    get_files_to_archive,
    parse_command_line_args,
    _resolve_config_path,
    _cleanup_update_residue,
    _handle_update_state,
    get_temp_folder,
)


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


class TestSelfUpdateInternalArgs:
    """自更新内部参数解析测试"""

    def test_internal_args_all_present(self):
        """五个内部参数全部正确解析"""
        old_argv = sys.argv[:]
        sys.argv = [
            "ALAS_Logs_Archive.py",
            "--self-update-verify",
            "--expected-sha256", "abc123",
            "--expected-version", "v4.0.0",
            "--retry-update",
            "--update-failed",
        ]
        try:
            args = parse_command_line_args()
            assert args.self_update_verify is True
            assert args.expected_sha256 == "abc123"
            assert args.expected_version == "v4.0.0"
            assert args.retry_update is True
            assert args.update_failed is True
        finally:
            sys.argv = old_argv

    def test_internal_args_defaults(self):
        """未传内部参数时恢复默认值"""
        old_argv = sys.argv[:]
        sys.argv = ["ALAS_Logs_Archive.py"]
        try:
            args = parse_command_line_args()
            assert args.self_update_verify is False
            assert args.expected_sha256 == ""
            assert args.expected_version == ""
            assert args.retry_update is False
            assert args.update_failed is False
        finally:
            sys.argv = old_argv

    def test_internal_args_hidden_from_help(self):
        """--help 不暴露内部参数"""
        old_argv = sys.argv[:]
        sys.argv = ["ALAS_Logs_Archive.py", "--help"]

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            try:
                parse_command_line_args()
            except SystemExit:
                pass
        help_text = buf.getvalue()

        # 确认 --help 确实打印了内容
        assert "usage" in help_text.lower() or "ALAS" in help_text

        internal_names = [
            "self-update-verify", "expected-sha256", "expected-version",
            "retry-update", "update-failed",
        ]
        for name in internal_names:
            assert name not in help_text, f"'{name}' should be hidden from --help"

        sys.argv = old_argv

    def test_update_and_update_force_args(self):
        """--update 和 --update-force 正确解析"""
        old_argv = sys.argv[:]
        sys.argv = ["ALAS_Logs_Archive.py", "--update", "--Update-force"]
        try:
            args = parse_command_line_args()
            assert args.update is True
            assert args.update_force is True
        finally:
            sys.argv = old_argv


class TestSelfUpdateE2E:
    """自更新端到端测试（通过 subprocess 调用）"""

    @pytest.fixture(autouse=True)
    def _save_restore_argv(self):
        old = sys.argv[:]
        yield
        sys.argv = old

    @staticmethod
    def _run_subprocess(*args: str, timeout: int = 30, input_str: str = None):
        """带 UTF-8 编码的 subprocess.run 封装"""
        import subprocess
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "ALAS_Logs_Archive.py"] + list(args),
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            env=env, input=input_str,
        )

    def test_verify_mode_sha256_mismatch(self):
        """--self-update-verify SHA256 不匹配时退出码为 2"""
        result = self._run_subprocess(
            "--self-update-verify",
            "--expected-sha256", "00" * 64,
        )
        assert result.returncode == 2
        assert "SHA256" in (result.stdout + result.stderr)

    def test_verify_mode_sha256_match(self):
        """--self-update-verify 匹配当前文件时退出码为 0"""
        import hashlib
        content = open("ALAS_Logs_Archive.py", "rb").read()
        actual = hashlib.sha256(content).hexdigest()
        result = self._run_subprocess(
            "--self-update-verify",
            "--expected-sha256", actual,
            "--expected-version", "x.y.z",
        )
        assert result.returncode == 0

    def test_update_failed_without_state(self):
        """--update-failed 无状态文件时退出码为 1 并提示无法读取"""
        ini_path = os.path.join(os.path.dirname(__file__), "..", "update_state.ini")
        if os.path.exists(ini_path):
            backup = ini_path + ".bak"
            os.rename(ini_path, backup)
            try:
                result = self._run_subprocess("--update-failed", input_str="\n")
                assert result.returncode == 1
                assert "无法读取状态" in (result.stdout + result.stderr)
            finally:
                os.rename(backup, ini_path)
        else:
            result = self._run_subprocess("--update-failed", input_str="\n")
            assert result.returncode == 1
            assert "无法读取状态" in (result.stdout + result.stderr)

    def test_retry_update_is_source_mode(self):
        """--retry-update 在源码模式下跳过更新并正常退出"""
        result = self._run_subprocess("--retry-update")
        # 源码模式下 SelfUpdater 打印警告后退出 1
        assert result.returncode in (0, 1)


class TestHelpers:
    """辅助函数测试"""

    def test_resolve_config_path_exists(self):
        """_resolve_config_path 在当前目录存在 config.ini 时返回正确路径"""
        path = _resolve_config_path()
        assert path.endswith("config.ini")
        assert os.path.isabs(path) or os.path.exists(path) or "config.ini" in path

    def test_get_temp_folder_returns_abs(self):
        """get_temp_folder 返回绝对路径且包含 ALAS_Logs_Archive"""
        folder = get_temp_folder()
        assert "ALAS_Logs_Archive" in folder
        assert "Cache" in folder
        assert os.path.isabs(folder)

    def test_get_temp_folder_windows_path(self):
        """get_temp_folder 在 Windows 下使用 LOCALAPPDATA"""
        if sys.platform == "win32":
            folder = get_temp_folder()
            localappdata = os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", ""))
            assert folder.startswith(localappdata) or "ALAS_Logs_Archive" in folder

    @mock.patch("modules.self_updater.SelfUpdater.clean_update_cache")
    def test_cleanup_update_residue(self, mock_clean):
        """_cleanup_update_residue 调用 clean_update_cache"""
        logger = logging.getLogger("test_cleanup")
        logger.setLevel(logging.DEBUG)
        _cleanup_update_residue(logger)
        mock_clean.assert_called_once()

    @mock.patch("modules.self_updater.SelfUpdater.rollback")
    @mock.patch("modules.self_updater.SelfUpdater.clean_update_cache")
    def test_handle_update_state_pending_rollback(self, mock_clean, mock_rollback):
        """_handle_update_state 在 pending 状态时触发回滚"""
        from modules.config_self_updater import UpdateState
        import tempfile, shutil

        logger = logging.getLogger("test_hup")
        logger.setLevel(logging.DEBUG)

        # 创建假状态文件
        import configparser
        state = UpdateState()
        state["state"] = "downloaded_verified"
        state["target"] = "fake.exe"
        state["backup_file"] = "fake.backup.exe"
        state["old_version"] = "v1.0.0"
        state["new_version"] = "v2.0.0"
        state["old_sha256"] = "00" * 32
        state["new_sha256"] = "00" * 32
        state.save()

        try:
            _handle_update_state(logger)
            mock_rollback.assert_called_once()
            mock_clean.assert_called_once()
        finally:
            state.delete()

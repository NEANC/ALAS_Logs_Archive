#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 ALAS_Logs_Archive 主入口文件的函数"""

import io
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from ALAS_Logs_Archive import (
    get_files_to_archive,
    main,
    parse_command_line_args,
    wait_for_process_exit,
    _resolve_config_path,
    _run_startup_cleanup,
    _handle_update_state,
    _is_process_running,
    _build_updater,
    get_self_update_root,
)
from modules.version import VERSION


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
            assert args.self_update_cleanup is False
            assert args.self_update_parent_pid == 0
        finally:
            sys.argv = old_argv

    def test_cleanup_internal_args(self):
        """cleanup 内部参数应正确解析。"""
        with mock.patch.object(
                sys,
                "argv",
                ["ALAS_Logs_Archive.py", "--self-update-cleanup",
                 "--self-update-parent-pid", "321"],
        ):
            args = parse_command_line_args()

        assert args.self_update_cleanup is True
        assert args.self_update_parent_pid == 321

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
            "--expected-version", VERSION,
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
            "--expected-version", VERSION,
        )
        assert result.returncode == 0

    def test_verify_mode_version_mismatch(self):
        """--self-update-verify 版本不匹配时退出码为 3"""
        import hashlib
        content = open("ALAS_Logs_Archive.py", "rb").read()
        actual = hashlib.sha256(content).hexdigest()
        result = self._run_subprocess(
            "--self-update-verify",
            "--expected-sha256", actual,
            "--expected-version", "0.0.0-mismatch",
        )
        assert result.returncode == 3

    def test_verify_mode_missing_version_fails(self):
        """--self-update-verify 缺少期望版本时退出码为 4（版本为必填）"""
        import hashlib
        content = open("ALAS_Logs_Archive.py", "rb").read()
        actual = hashlib.sha256(content).hexdigest()
        result = self._run_subprocess(
            "--self-update-verify",
            "--expected-sha256", actual,
        )
        assert result.returncode == 4

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


@mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
@mock.patch("ALAS_Logs_Archive.wait_for_process_exit")
@mock.patch("ALAS_Logs_Archive.setup_logger")
@mock.patch("ALAS_Logs_Archive.print_info")
def test_cleanup_mode_waits_then_cleans_and_exits(
        mock_info, mock_logger, mock_wait, mock_cleanup, monkeypatch):
    """cleanup 模式应显示界面、等待 Helper、清理并直接退出。"""
    monkeypatch.setattr(
        sys,
        "argv",
        ["ALAS_Logs_Archive.py", "--self-update-cleanup",
         "--self-update-parent-pid", "321"],
    )
    mock_cleanup.return_value.success = True

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    mock_info.assert_called_once()
    mock_wait.assert_called_once_with(321, mock_logger.return_value)
    mock_cleanup.assert_called_once()


@mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
@mock.patch("ALAS_Logs_Archive.wait_for_process_exit")
@mock.patch("ALAS_Logs_Archive.setup_logger")
@mock.patch("ALAS_Logs_Archive.print_info")
def test_cleanup_mode_reports_partial_failure(
        mock_info, mock_logger, mock_wait, mock_cleanup, monkeypatch, capsys):
    """cleanup 部分失败时仍以 0 退出并提示下次启动继续清理。"""
    monkeypatch.setattr(
        sys,
        "argv",
        ["ALAS_Logs_Archive.py", "--self-update-cleanup",
         "--self-update-parent-pid", "321"],
    )
    mock_cleanup.return_value.success = False
    mock_cleanup.return_value.failed_paths = ["C:/x/helper.ps1"]

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    assert "部分临时文件将在下次启动时继续清理" in capsys.readouterr().out


@mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
@mock.patch("ALAS_Logs_Archive.wait_for_process_exit")
@mock.patch("ALAS_Logs_Archive.setup_logger")
@mock.patch("ALAS_Logs_Archive.print_info")
def test_cleanup_mode_proceeds_after_wait_timeout(
        mock_info, mock_logger, mock_wait, mock_cleanup, monkeypatch):
    """等待 Helper 超时后仍执行清理并以 0 退出。"""
    monkeypatch.setattr(
        sys,
        "argv",
        ["ALAS_Logs_Archive.py", "--self-update-cleanup",
         "--self-update-parent-pid", "321"],
    )
    mock_wait.return_value = False
    mock_cleanup.return_value.success = True

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    mock_cleanup.assert_called_once()


@mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
@mock.patch("ALAS_Logs_Archive.wait_for_process_exit")
@mock.patch("ALAS_Logs_Archive.setup_logger")
@mock.patch("ALAS_Logs_Archive.print_info")
def test_cleanup_mode_cleans_after_exception(
        mock_info, mock_logger, mock_wait, mock_cleanup, monkeypatch, capsys):
    """清理抛异常时仍以 0 退出并提示下次启动继续清理。"""
    monkeypatch.setattr(
        sys,
        "argv",
        ["ALAS_Logs_Archive.py", "--self-update-cleanup",
         "--self-update-parent-pid", "321"],
    )
    mock_cleanup.side_effect = RuntimeError("boom")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    assert "部分临时文件将在下次启动时继续清理" in capsys.readouterr().out


@mock.patch("ALAS_Logs_Archive._handle_decompress")
@mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
@mock.patch("ALAS_Logs_Archive.setup_logger")
@mock.patch("ALAS_Logs_Archive.print_info")
def test_decompress_entry_runs_startup_cleanup(
        mock_info, mock_logger, mock_cleanup, mock_decompress, monkeypatch, tmp_path):
    """解压入口应先执行启动兜底清理，再执行解压。"""
    zip_file = tmp_path / "test.zip"
    zip_file.write_bytes(b"zip")
    monkeypatch.setattr(sys, "argv", [
        str(tmp_path / "ALAS_Logs_Archive.py"), "-d", str(zip_file),
    ])

    call_order = []
    mock_cleanup.side_effect = lambda *a, **k: call_order.append("cleanup")
    mock_decompress.side_effect = lambda *a, **k: call_order.append("decompress")

    main()

    assert call_order == ["cleanup", "decompress"]
    mock_cleanup.assert_called_once()
    mock_decompress.assert_called_once()


@mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
@mock.patch("ALAS_Logs_Archive.setup_logger")
@mock.patch("ALAS_Logs_Archive.print_info")
def test_cli_entry_runs_startup_cleanup(
        mock_info, mock_logger, mock_cleanup, monkeypatch, tmp_path):
    """CLI 入口（-t 与 -a）应先执行启动兜底清理。"""
    monkeypatch.setattr(sys, "argv", [
        str(tmp_path / "ALAS_Logs_Archive.py"),
        "-t", str(tmp_path / "target"),
        "-a", str(tmp_path / "archive"),
    ])
    mock_cleanup.return_value.success = True

    main()

    mock_cleanup.assert_called_once()


class TestHelpers:
    """辅助函数测试"""

    def test_resolve_config_path_exists(self):
        """_resolve_config_path 在当前目录存在 config.ini 时返回正确路径"""
        path = _resolve_config_path()
        assert path.endswith("config.ini")
        assert os.path.isabs(path) or os.path.exists(path) or "config.ini" in path

    def test_get_self_update_root_returns_abs(self, monkeypatch, tmp_path):
        """get_self_update_root 在 Windows 且 LOCALAPPDATA 存在时返回计划目录"""
        if sys.platform != "win32":
            folder = get_self_update_root()
            assert "ALAS_Logs_Archive" in folder
            assert "SelfUpdate" in folder
            assert os.path.isabs(folder)
            return

        local_appdata = tmp_path / "LocalAppData"
        monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

        assert Path(get_self_update_root()) == local_appdata / "ALAS_Logs_Archive" / "SelfUpdate"

    def test_get_self_update_root_windows_returns_empty_without_localappdata(self, monkeypatch):
        """Windows 且 LOCALAPPDATA 缺失时返回空字符串"""
        if sys.platform != "win32":
            pytest.skip("仅 Windows 环境验证 LOCALAPPDATA 缺失行为")

        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setenv("TEMP", "C:\\Temp")

        assert get_self_update_root() == ""

    def test_get_self_update_root_windows_path(self):
        """get_self_update_root 在 Windows 下使用 LOCALAPPDATA"""
        if sys.platform == "win32":
            folder = get_self_update_root()
            localappdata = os.environ.get("LOCALAPPDATA", "")
            assert folder == "" or folder.startswith(localappdata)

    def test_build_updater_falls_back_to_program_dir_without_localappdata(self, monkeypatch, tmp_path):
        """_build_updater 在 LOCALAPPDATA 缺失时让 runtime_dir 回退到程序目录"""
        if sys.platform != "win32":
            pytest.skip("仅 Windows 环境验证 LOCALAPPDATA 缺失行为")

        exe = tmp_path / "program" / "ALAS_Logs_Archive.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"exe")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setenv("TEMP", str(tmp_path / "Temp"))

        updater = _build_updater(
            logging.getLogger("test_build_updater_runtime_dir_fallback"),
            mock.Mock(github_proxy="", self_update_channel="stable"),
            True,
            "Nuitka",
        )
        paths = updater._build_update_runtime_paths(exe, "v2.0.0")

        assert updater.temp_folder == ""
        assert paths["runtime_dir"] == exe.parent / "SelfUpdate" / "v2.0.0"

    @mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
    def test_run_startup_cleanup_calls_public_entry(self, mock_cleanup, monkeypatch, tmp_path):
        """_run_startup_cleanup 只调用公开协调入口 cleanup_self_update"""
        exe = tmp_path / "ALAS_Logs_Archive.exe"
        exe.write_bytes(b"exe")
        monkeypatch.setattr(sys, "argv", [str(exe)])
        logger = logging.getLogger("test_cleanup")
        logger.setLevel(logging.DEBUG)
        _run_startup_cleanup(logger)
        mock_cleanup.assert_called_once()

    @mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
    def test_run_startup_cleanup_handles_verified_state(self, mock_cleanup, monkeypatch, tmp_path):
        """_run_startup_cleanup 对 verified 终态调用统一清理入口。"""
        from modules.config_self_updater import UpdateState

        exe = tmp_path / "ALAS_Logs_Archive.exe"
        exe.write_bytes(b"exe")
        monkeypatch.setattr(sys, "argv", [str(exe)])

        state = UpdateState()
        state["state"] = "verified"
        state.save()

        _run_startup_cleanup(logging.getLogger("test_cleanup_verified_chain"))

        mock_cleanup.assert_called_once()

        state.delete()

    @mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
    @mock.patch("ALAS_Logs_Archive.SelfUpdater.rollback")
    def test_run_startup_cleanup_skips_cleanup_when_rollback_fails(
            self, mock_rollback, mock_cleanup, monkeypatch, tmp_path):
        """回滚失败时保留更新现场，跳过统一清理。"""
        from modules.config_self_updater import UpdateState

        exe = tmp_path / "ALAS_Logs_Archive.exe"
        exe.write_bytes(b"exe")
        monkeypatch.setattr(sys, "argv", [str(exe)])

        state = UpdateState()
        state["state"] = "replacing"
        state.save()
        mock_rollback.return_value = False

        _run_startup_cleanup(logging.getLogger("test_cleanup_rollback_failed"))

        mock_rollback.assert_called_once()
        mock_cleanup.assert_not_called()

        state.delete()

    @mock.patch("ALAS_Logs_Archive.SelfUpdater.cleanup_self_update")
    @mock.patch("ALAS_Logs_Archive.SelfUpdater.rollback")
    def test_run_startup_cleanup_cleans_after_successful_rollback(
            self, mock_rollback, mock_cleanup, monkeypatch, tmp_path):
        """回滚成功（返回 True）后继续调用统一清理入口。"""
        from modules.config_self_updater import UpdateState

        exe = tmp_path / "ALAS_Logs_Archive.exe"
        exe.write_bytes(b"exe")
        monkeypatch.setattr(sys, "argv", [str(exe)])

        state = UpdateState()
        state["state"] = "replacing"
        state.save()
        mock_rollback.return_value = True

        _run_startup_cleanup(logging.getLogger("test_cleanup_rollback_ok"))

        mock_rollback.assert_called_once()
        mock_cleanup.assert_called_once()

        state.delete()

    @mock.patch("modules.self_updater.SelfUpdater.rollback")
    @mock.patch("modules.self_updater.SelfUpdater.clean_update_cache")
    def test_handle_update_state_pending_rollback(self, mock_clean, mock_rollback):
        """_handle_update_state 在 pending 状态时触发回滚并允许后续清理"""
        from modules.config_self_updater import UpdateState

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
            # mock_rollback 默认返回 Mock（真值），表示回滚成功
            result = _handle_update_state(logger)
            assert result is True
            mock_rollback.assert_called_once()
            # 清理统一由 _run_startup_cleanup 包装函数处理，此处不直接调用
            mock_clean.assert_not_called()
        finally:
            state.delete()


class TestProcessExit:
    """进程状态判断与等待函数测试"""

    def test_is_process_running_non_positive(self):
        """进程号非正数时直接判定为不在运行。"""
        assert _is_process_running(0) is False
        assert _is_process_running(-1) is False

    def test_is_process_running_non_windows(self, monkeypatch):
        """非 Windows 平台下直接返回 False 且不触发 ctypes。"""
        monkeypatch.setattr(sys, "platform", "linux")
        assert _is_process_running(1234) is False

    def test_wait_for_process_exit_non_positive(self, caplog):
        """非正进程号直接返回 True 且不等待。"""
        assert wait_for_process_exit(0, logging.getLogger("test")) is True

    def test_wait_for_process_exit_timeout(self, monkeypatch):
        """Helper 一直未退出时等待超时返回 False。"""
        monkeypatch.setattr("ALAS_Logs_Archive._is_process_running", lambda pid: True)
        assert wait_for_process_exit(
            1234, logging.getLogger("test"), timeout_seconds=0.05, poll_interval=0.01) is False

#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import argparse
import ctypes
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from functools import partial
from typing import List

import colorama

from modules.alas_logger_processor import delete_error_folder, delete_gui_files
from modules.config_manager import CONFIG_FILE, ConfigManager
from modules.download_manager import download_with_progress
from modules.logger_manager import setup_logger
from modules.version import VERSION, print_info
from modules.zip_compress import create_archive
from modules.zip_decompress import decompress_archive
from modules.self_updater import SelfUpdater
from modules.config_self_updater import UpdateState
from modules.self_utils import detect_package_type

# 1MB，文件读写块大小
CHUNK_SIZE = 1048576


def setup_utf8_console() -> None:
    """强制 stdout/stderr 使用 UTF-8 编码"""
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if sys.stdin and hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def get_files_to_archive(target_folder: str, current_date: str, logger: logging.Logger) -> List[str]:
    """获取需要打包归档的文件列表

    Args:
        target_folder: 目标文件夹路径
        current_date: 当前日期（格式：年-月-日）
        logger: 日志记录器

    Returns:
        List[str]: 需要归档的文件路径列表
    """
    if not os.path.exists(target_folder):
        logger.warning(f"目标文件夹不存在: {target_folder}")
        return []

    gui_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}_gui\.txt$")
    current_date_pattern = re.compile(f"^{re.escape(current_date)}_")
    files_to_archive = []

    for item in os.listdir(target_folder):
        item_path = os.path.join(target_folder, item)

        if os.path.isdir(item_path):
            continue

        if gui_pattern.match(item):
            continue

        if current_date_pattern.match(item):
            continue

        files_to_archive.append(item_path)

    logger.info(f"找到 {len(files_to_archive)} 个需要归档的文件")
    return files_to_archive


def parse_command_line_args() -> argparse.Namespace:
    """解析命令行参数

    Returns:
        argparse.Namespace: 解析后的命令行参数
    """
    parser = argparse.ArgumentParser(description="ALAS 日志归档工具")
    parser.add_argument("-n", "--name", help="归档文件名")
    parser.add_argument("-t", "--target", help="目标文件夹路径")
    parser.add_argument("-a", "--archive", help="归档文件夹路径")
    parser.add_argument("-m", "--mode", help="归档模式", choices=["scroll", "incremental"])
    parser.add_argument("-c", "--compression", help="压缩算法", choices=["zstd", "lzma", "bzip2"])
    parser.add_argument("-l", "--level", help="压缩等级", type=int, choices=range(0, 23), metavar="0-22")
    parser.add_argument("-w", "--workers", help="多线程设置", type=int)
    parser.add_argument("-L", "--save-logs", help="日志文件输出控制", choices=["true", "false"])
    parser.add_argument("-C", "--console-level", help="控制台日志等级",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    parser.add_argument("-d", "--decompress", help="解压归档文件（指定ZIP文件路径）")
    parser.add_argument("-o", "--output", help="解压输出目录（与 -d 配合使用，默认为ZIP同目录下同名文件夹）")
    parser.add_argument("--update", "--Update", action="store_true",
                        dest="update", default=False,
                        help="仅检查自身更新")
    parser.add_argument("--update-force", "--Update-force", "--Update-Force",
                        action="store_true", dest="update_force", default=False,
                        help="强制更新自身到最新版本")
    parser.add_argument("--self-update-verify", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--expected-sha256", type=str, default="", help=argparse.SUPPRESS)
    parser.add_argument("--expected-version", type=str, default="", help=argparse.SUPPRESS)
    parser.add_argument("--retry-update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-failed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-update-cleanup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-update-parent-pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("zipfile", nargs="?", default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def _handle_decompress(archive_path: str, output_dir: str, save_logs: bool = False,
                       console_level: int = logging.INFO) -> None:
    """处理解压操作

    Args:
        archive_path: 归档文件路径（ZIP文件）
        output_dir: 解压输出目录
        save_logs: 是否保存日志文件
        console_level: 控制台日志等级
    """
    logger = setup_logger("logs", 15, console_level, save_logs=save_logs)
    logger.info(f"解压归档文件： {archive_path}")
    logger.info(f"解压到目录: {output_dir}")

    try:
        decompress_archive(archive_path, output_dir, logger)
    except KeyboardInterrupt:
        logger.warning("捕获到Ctrl+C，终止运行")
        sys.exit(0)
    except Exception as e:
        logger.error(f"解压失败: {e}")
        raise


def _handle_update_state(logger: logging.Logger) -> None:
    """检查并处理中断的更新状态（非终态先回滚，终态清理由协调入口统一处理）"""
    state = UpdateState.load()
    if not state:
        return

    current_state = state.get("State", "state", fallback="idle")
    if current_state == "failed_disabled":
        failed_ver = state["new_version"]
        logger.warning(f"版本 {failed_ver} 此前更新失败，已禁用自动更新。")
    elif current_state in ("downloaded_verified", "helper_started", "replacing",
                            "pending_new_verify", "rollback"):
        logger.warning(f"检测到未完成的更新（状态: {current_state}），尝试回滚...")
        SelfUpdater.rollback(logger)


def _is_process_running(process_id: int) -> bool:
    """使用 Windows 进程句柄判断指定进程是否仍在运行。

    Args:
        process_id: 目标进程 PID

    Returns:
        进程仍在运行返回 True；进程不存在或已退出返回 False
    """
    if process_id <= 0:
        return False
    if sys.platform != "win32":
        # 自更新仅支持 Windows，非 Windows 平台一律视为进程不存在
        return False

    synchronize = 0x00100000
    wait_timeout = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 显式声明签名，避免 64 位 HANDLE 被默认 c_int 截断导致误判进程不存在
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.CloseHandle(handle)


def wait_for_process_exit(
        parent_pid: int,
        logger: logging.Logger,
        timeout_seconds: float = 30.0,
        poll_interval: float = 0.1) -> bool:
    """等待 Helper 进程退出，超时后记录警告并返回 False。

    Args:
        parent_pid: Helper 进程 PID
        logger: 日志记录器
        timeout_seconds: 最长等待秒数
        poll_interval: 轮询间隔秒数

    Returns:
        进程已退出或无需等待返回 True；超时返回 False
    """
    if parent_pid <= 0:
        return True

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _is_process_running(parent_pid):
            return True
        time.sleep(poll_interval)

    logger.warning(f"等待更新 Helper 退出超时，PID: {parent_pid}")
    return False


def get_self_update_root() -> str:
    """获取自更新运行时与缓存根目录路径。"""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA')
        if not base:
            return ''
    else:
        base = os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))
    return str(Path(base) / 'ALAS_Logs_Archive' / 'SelfUpdate')


def _build_updater(logger: logging.Logger, config_mgr, is_bundled: bool,
                   package_type: str) -> SelfUpdater:
    """构建 SelfUpdater 实例（避免四处重复拼参数）"""
    download_func = partial(
        download_with_progress,
        proxy=config_mgr.github_proxy,
        logger=logger,
    )
    return SelfUpdater(
        github_repo="NEANC/ALAS_Logs_Archive",
        asset_pattern=r"^ALAS_Logs_Archive-(Nuitka|PyInstaller)-v[\d.]+.*\.exe$",
        app_name="ALAS_Logs_Archive",
        current_version=VERSION,
        proxy=config_mgr.github_proxy,
        temp_folder=get_self_update_root(),
        logger=logger,
        download_func=download_func,
        self_update_channel=config_mgr.self_update_channel,
        is_bundled=is_bundled,
        package_type=package_type,
    )


def _run_startup_cleanup(logger: logging.Logger) -> None:
    """处理更新状态（含失败状态提示与未完成更新回滚），随后执行完整自更新清理。"""
    _handle_update_state(logger)
    SelfUpdater.cleanup_self_update(get_self_update_root(), logger)


def _resolve_config_path() -> str:
    """解析 config.ini 路径（打包 exe 可能指向临时目录，回落 CWD）"""
    config_path = str(Path(sys.argv[0]).resolve().parent / CONFIG_FILE)
    if not os.path.exists(config_path) and os.path.exists(os.path.join(os.getcwd(), CONFIG_FILE)):
        config_path = os.path.join(os.getcwd(), CONFIG_FILE)
    return config_path


def main():
    """主函数"""
    args = parse_command_line_args()

    # ── 自更新验证模式（由 PS1 Helper 在替换后调用） ──
    if args.self_update_verify:
        exit_code = SelfUpdater.self_update_verify(
            expected_sha256=args.expected_sha256,
            expected_version=args.expected_version,
            version_func=lambda: VERSION,
        )
        sys.exit(exit_code)

    # ── 自更新清理模式（PS1 Helper 替换完成后调用） ──
    if args.self_update_cleanup:
        print_info()
        logger = setup_logger("logs", 15, logging.INFO, save_logs=False)
        logger.info("正在清理上次自更新残留...")
        # 等待 Helper 退出；超时仍继续尽力清理，失败路径由状态文件保留供下次启动兜底
        wait_for_process_exit(args.self_update_parent_pid, logger)
        try:
            result = SelfUpdater.cleanup_self_update(get_self_update_root(), logger)
        except Exception as e:
            logger.error(f"自更新清理异常: {e}")
            result = None
        if result is not None and result.success:
            print("更新已完成，临时文件已清理。")
        else:
            print("更新已完成，部分临时文件将在下次启动时继续清理。")
        sys.exit(0)

    # ── 重试更新模式（PS1 回滚后 retry_count < max） ──
    if args.retry_update:
        # 此时还未初始化 logger/config，使用最小化 logger
        logger = setup_logger("logs", 15, logging.INFO, save_logs=False)
        logger.info("正在重试自更新...")
        is_bundled, package_type = detect_package_type()
        try:
            config_path = _resolve_config_path()
            config_mgr = ConfigManager(config_path)
            config_mgr.load()
            config_mgr.set_logger(logger)
            updater = _build_updater(logger, config_mgr, is_bundled, package_type)
            need_exit = updater.check_self_update()
            if need_exit:
                sys.exit(0)
        except Exception as e:
            logger.error(f"重试更新失败: {e}")
        logger.error("重试更新失败，无法获取新版本")
        sys.exit(1)

    # ── 更新失败模式（PS1 回滚耗尽 retry_count） ──
    if args.update_failed:
        logger = setup_logger("logs", 15, logging.INFO, save_logs=False)
        state = UpdateState.load()
        if state:
            failed_ver = state["new_version"]
            logger.critical(f"自更新失败：版本 {failed_ver} 多次验证不通过")
            print(f"\n软件自动更新失败，版本 {failed_ver} 已被标记为不可用。")
            print("已回退到旧版本，后续将跳过该版本的自动更新。")
        else:
            logger.critical("自更新失败，但无法读取状态信息")
        input("\n按任意键退出...")
        sys.exit(1)

    print_info()

    # 解压模式：支持三种入口
    #   1. 命令行 -d 显式指定  →  args.decompress + args.output
    #   2. 文件关联/拖放 ZIP   →  args.zipfile (位置参数)
    if args.decompress or args.zipfile:
        save_logs_arg = bool(args.save_logs and args.save_logs.lower() == "true")
        console_lvl = getattr(logging, args.console_level) if args.console_level else logging.INFO

        archive = args.decompress if args.decompress else args.zipfile
        output = args.output if args.output else os.path.splitext(archive)[0]
        _handle_decompress(archive, output, save_logs=save_logs_arg, console_level=console_lvl)
        return

    # CLI 模式：-t 和 -a 均提供时，直接使用 CLI 参数，不依赖配置文件
    cli_only = bool(args.target and args.archive)
    if cli_only:
        save_logs = args.save_logs.lower() == "true" if args.save_logs else False
        log_folder = "logs"
        max_log_files = 15
        log_level = getattr(logging, args.console_level) if args.console_level else logging.INFO

        logger = setup_logger(log_folder, max_log_files, log_level, save_logs)
        logger.debug(f"版本号: {VERSION}")

        target_folder = args.target
        archive_folder = args.archive
        archive_name_format = args.name if args.name else "存档"
        compression_algorithm = args.compression if args.compression else "zstd"
        compression_level = args.level if args.level is not None else 9
        archive_mode = args.mode if args.mode else "scroll"
        max_workers = args.workers if args.workers is not None else 1
    else:
        config_path = _resolve_config_path()
        config_mgr = ConfigManager(config_path)
        config_mgr.load()
        log_folder = config_mgr.log_folder
        max_log_files = config_mgr.max_log_files
        log_level = config_mgr.log_level
        save_logs = config_mgr.save_logs

        # 命令行参数覆盖配置文件
        if args.console_level:
            log_level = getattr(logging, args.console_level)
        if args.save_logs:
            save_logs = args.save_logs.lower() == "true"

        logger = setup_logger(log_folder, max_log_files, log_level, save_logs)
        # 日志系统就绪，注入 ConfigManager
        config_mgr.set_logger(logger)

        # 检测运行环境
        is_bundled, package_type = detect_package_type()
        logger.debug(f"版本号: {VERSION}")

        # ── 仅检查自身更新 / 强制更新模式 ──
        if args.update or args.update_force:
            if is_bundled:
                try:
                    updater = _build_updater(logger, config_mgr, is_bundled, package_type)
                    if updater.check_self_update(force=args.update_force):
                        logger.info("已将新版本下载到临时文件夹，即将退出以完成更新...")
                        sys.exit(0)
                except Exception as e:
                    logger.error(f"自更新检查失败: {e}")
            input("\n按任意键退出...")
            sys.exit(0)

        # ── 正常启动：清理上次更新残留 ──
        _run_startup_cleanup(logger)

        target_folder = args.target if args.target else config_mgr.target_folder
        archive_folder = args.archive if args.archive else config_mgr.archive_folder
        archive_name_format = args.name if args.name else config_mgr.archive_name_format
        compression_algorithm = args.compression if args.compression else config_mgr.compression_algorithm
        compression_level = args.level if args.level is not None else config_mgr.compression_level
        archive_mode = args.mode if args.mode else config_mgr.archive_mode
        max_workers = args.workers if args.workers is not None else config_mgr.max_workers

        # 将 CLI 覆盖值写入 ConfigManager，供 validate() 统一校验
        config_mgr.target_folder = target_folder
        config_mgr.archive_folder = archive_folder
        config_mgr.compression_algorithm = compression_algorithm
        config_mgr.compression_level = compression_level
        config_mgr.archive_mode = archive_mode
        config_mgr.max_workers = max_workers

        if not config_mgr.validate():
            sys.exit(1)

    chunk_size = CHUNK_SIZE
    current_date = datetime.now().strftime("%Y-%m-%d")

    try:
        if not save_logs:
            logger.warning("日志仅控制台输出")

        logger.info(f"目标文件夹: {target_folder}")
        logger.info(f"归档文件夹: {archive_folder}")

        # 目标文件夹检查（避免下游函数各自重复打印）
        if not os.path.exists(target_folder):
            logger.warning(f"目标文件夹不存在: {target_folder}")
            logger.info("没有文件需要归档")
            return

        mode_display = "增量" if archive_mode == "incremental" else "滚动"
        logger.info(f"归档模式: {mode_display}")

        delete_gui_files(target_folder, current_date, logger)
        delete_error_folder(target_folder, logger)

        files_to_archive = get_files_to_archive(target_folder, current_date, logger)
        create_archive(files_to_archive, archive_folder, archive_name_format, compression_algorithm, compression_level, archive_mode, max_workers, chunk_size, logger)

    except KeyboardInterrupt:
        logger.warning("捕获到Ctrl+C，终止运行")
        sys.exit(0)
    except Exception as e:
        logger.error(f"程序执行出错: {e}")
        raise

    # ── 主流程完成后：自动检查更新 ──
    if not cli_only and is_bundled and config_mgr.self_update_enabled:
        try:
            updater = _build_updater(logger, config_mgr, is_bundled, package_type)
            need_exit = updater.check_self_update()
            if need_exit:
                return
        except Exception as e:
            logger.warning(f"自更新检查跳过: {e}")


if __name__ == "__main__":
    setup_utf8_console()
    colorama.init(autoreset=True)
    main()

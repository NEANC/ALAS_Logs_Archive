#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""
自更新器核心模块。

负责：
  - 从 GitHub Release 获取最新版本信息
  - 版本比较（支持 pre-release 语义）
  - 下载新版本 exe 并校验 SHA256
  - 生成 PowerShell 脚本完成热替换
  - 新版健康检查验证
  - 失败回滚

可移植到其他项目，通过构造函数注入参数即可。
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import requests

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from modules.config_self_updater import UpdateState
from modules.ps1_fragments import (
    generate_common_base_functions_ps1,
    generate_common_state_functions_ps1,
    generate_helper_argument_functions_ps1,
    generate_helper_file_cleanup_functions_ps1,
    generate_helper_lifecycle_functions_ps1,
    generate_helper_retry_functions_ps1,
    generate_move_with_retry_ps1,
    generate_sha256_function_ps1,
)
from modules.self_utils import (
    calculate_sha256,
    detect_package_type,
    get_exe_path,
    is_build_tag,
    version_newer_than,
    version_to_tuple,
)


def _get_existing_retry_count() -> str:
    """读取已存在的 update_state.ini 中的 retry_count，若无则返回 '0'"""
    existing = UpdateState.load()
    if existing:
        return existing.get("Retry", "retry_count", fallback="0")
    return "0"


@dataclass
class CleanupResult:
    """记录一次自更新清理的完整结果。"""

    cleaned_paths: List[str] = field(default_factory=list)
    failed_paths: List[str] = field(default_factory=list)
    state_deleted: bool = False
    cache_deleted: bool = False
    root_deleted: bool = False

    @property
    def success(self) -> bool:
        """返回本次清理是否不存在失败对象。"""
        return not self.failed_paths

    def merge(self, other: "CleanupResult") -> None:
        """合并另一个清理步骤的结果。"""
        self.cleaned_paths.extend(other.cleaned_paths)
        self.failed_paths.extend(other.failed_paths)
        self.state_deleted = self.state_deleted or other.state_deleted
        self.cache_deleted = self.cache_deleted or other.cache_deleted
        self.root_deleted = self.root_deleted or other.root_deleted


class SelfUpdater:
    """自更新器，负责自我更新检查、下载、替换、回滚"""

    def __init__(self, github_repo: str, asset_pattern: str, app_name: str,
                 current_version: str, proxy: str, temp_folder: str,
                 logger: logging.Logger,
                 download_func: Optional[Callable[[str, str], bool]] = None,
                 self_update_channel: str = 'preview',
                 is_bundled: Optional[bool] = None,
                 package_type: Optional[str] = None):
        """
        初始化自更新器

        Args:
            github_repo: GitHub 仓库（格式 "owner/repo"）
            asset_pattern: exe asset 文件名正则模式
            app_name: 应用名称（用于 PS1 脚本和缓存目录命名）
            current_version: 当前版本号（如 v1.0.0）
            proxy: 代理地址（空字符串表示无代理）
            temp_folder: 自更新根目录；为空时回退到系统或程序目录 SelfUpdate
            logger: 日志记录器
            download_func: 下载回调 (url, save_path) -> bool，不传则使用内置 requests 下载
            self_update_channel: 更新通道 ('preview', 'stable')
            is_bundled: 外部预检测的是否为打包程序（可选）
            package_type: 外部预检测的打包方式（可选）
        """
        self.github_repo = github_repo
        self.asset_regex = re.compile(asset_pattern)
        self.app_name = app_name
        self.current_version = current_version
        self.proxy = proxy
        self.temp_folder = temp_folder
        self.logger = logger
        self._download_func = download_func or self._default_download
        self.self_update_channel = self_update_channel
        self._is_bundled = is_bundled
        self._package_type = package_type

    def _default_download(self, url: str, save_path: str) -> bool:
        """内置下载实现（无进度条），外部可注入带进度的下载函数覆盖"""
        try:
            headers = {'User-Agent': 'SelfUpdater'}
            proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None
            response = requests.get(url, headers=headers, proxies=proxies,
                                    timeout=120, stream=True)
            response.raise_for_status()
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1048576):
                    if chunk:
                        f.write(chunk)
            return True
        except requests.RequestException as e:
            self.logger.error(f"下载失败: {e}")
            return False

    def _resolve_channel(self) -> str:
        """解析通道配置，兼容旧值"""
        if self.self_update_channel in ('preview', 'release'):
            return 'preview'
        if self.self_update_channel in ('stable', 'latest'):
            return 'stable'
        return 'preview'

    def _check_system_requirements(self) -> bool:
        """检查系统环境是否满足自更新条件：Windows + PowerShell 5.1+"""
        if sys.platform != 'win32':
            self.logger.critical("自我更新仅支持 Windows 操作系统")
            return False

        try:
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-Command',
                 '$PSVersionTable.PSVersion.Major'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                self.logger.critical("无法获取 PowerShell 版本信息")
                return False
            major = int(result.stdout.strip())
            if major < 5:
                self.logger.critical(f"PowerShell 版本过低: {major}，需要 5.1 或更高版本")
                return False
            self.logger.debug(f"PowerShell 版本：{major} 环境检查通过")
        except (subprocess.TimeoutExpired, ValueError, OSError) as e:
            self.logger.critical(f"检测 PowerShell 版本失败: {e}")
            return False

        return True

    def _make_headers(self) -> Dict[str, str]:
        """构建请求头"""
        return {'User-Agent': self.app_name}

    def _make_proxies(self) -> Optional[Dict[str, str]]:
        """构建代理配置"""
        return {'http': self.proxy, 'https': self.proxy} if self.proxy else None

    def _fetch_latest_release(self) -> Optional[Dict]:
        """从 GitHub API 获取最新 release 信息"""
        try:
            headers = self._make_headers()
            proxies = self._make_proxies()
            channel = self._resolve_channel()

            if channel == 'preview':
                api_url = f"https://api.github.com/repos/{self.github_repo}/releases"
                response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                response.raise_for_status()
                releases = response.json()
                releases = [r for r in releases if not r.get('draft')]
                if not releases:
                    self.logger.error("未找到任何有效的 release")
                    return None
                release_info = releases[0]
            else:
                api_url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
                response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                response.raise_for_status()
                release_info = response.json()

            self.logger.debug(f"GitHub 版本: {release_info.get('tag_name', 'Unknown')}")
            return release_info
        except requests.RequestException as e:
            self.logger.critical(f"获取 GitHub release 信息失败: {e}")
            return None
        except Exception as e:
            self.logger.critical(f"获取 GitHub release 时出错: {e}")
            return None

    def _get_asset_sha256(self, release_info: Dict, asset_name: str) -> str:
        """从 release 的 assets 和 body 中提取指定文件的 SHA256"""
        assets = release_info.get('assets', [])
        for asset in assets:
            if asset.get('name') == asset_name:
                digest = asset.get('digest', '')
                if digest.startswith('sha256:'):
                    return digest[7:]
        body = release_info.get('body', '')
        for line in body.split('\n'):
            if asset_name in line and 'sha256' in line.lower():
                match = re.search(r'[0-9a-f]{64}', line.lower())
                if match:
                    return match.group(0)
        return ""

    def _match_asset(self, release_info: Dict,
                     package_type: str) -> Tuple[str, str]:
        """从 release assets 中匹配对应打包方式的 exe 文件"""
        primary_keyword = package_type
        secondary_keyword = "PyInstaller" if package_type == "Nuitka" else "Nuitka"
        assets = release_info.get('assets', [])

        for asset in assets:
            asset_name = asset.get('name', '')
            if self.asset_regex.match(asset_name) and primary_keyword in asset_name:
                self.logger.info(f"找到 {primary_keyword} 版本: {asset_name}")
                return asset.get('browser_download_url', ''), asset_name

        for asset in assets:
            asset_name = asset.get('name', '')
            if self.asset_regex.match(asset_name) and secondary_keyword in asset_name:
                self.logger.info(
                    f"未找到 {primary_keyword} 版本，降级使用 {secondary_keyword} 版本: {asset_name}"
                )
                return asset.get('browser_download_url', ''), asset_name

        self.logger.critical("未找到符合命名规范的 exe 文件")
        return '', ''

    def _fetch_current_release_sha256(self, package_type: str) -> str:
        """从 GitHub API 获取当前版本的 exe asset 的 SHA256"""
        try:
            api_url = (
                f"https://api.github.com/repos/{self.github_repo}"
                f"/releases/tags/{self.current_version}"
            )
            headers = self._make_headers()
            proxies = self._make_proxies()

            response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
            if response.status_code == 404:
                self.logger.debug("tag 名称精确匹配未找到，遍历查找...")
                release_info = self._match_release_by_tag(headers, proxies)
            else:
                response.raise_for_status()
                release_info = response.json()

            if not release_info:
                self.logger.warning(
                    f"GitHub 上未找到当前版本 {self.current_version} 的 release"
                )
                return ""

            _, exe_name = self._match_asset(release_info, package_type)
            if not exe_name:
                return ""

            return self._get_asset_sha256(release_info, exe_name)
        except requests.RequestException as e:
            self.logger.warning(f"获取当前版本 SHA256 失败: {e}")
            return ""

    def _match_release_by_tag(self, headers: dict,
                               proxies: dict) -> Optional[Dict]:
        """遍历 releases 列表，按 tag_name 大小写不敏感匹配当前版本"""
        try:
            api_url = f"https://api.github.com/repos/{self.github_repo}/releases"
            params = {'per_page': 50}
            response = requests.get(api_url, headers=headers, proxies=proxies,
                                    params=params, timeout=30)
            response.raise_for_status()
            for release in response.json():
                if release.get('draft'):
                    continue
                if release.get('tag_name', '').lower() == self.current_version.lower():
                    return release
        except requests.RequestException as e:
            self.logger.debug(f"遍历 releases 匹配失败: {e}")
        return None

    # ── 公共方法 ──

    def check_self_update(self, force: bool = False) -> bool:
        """
        检查并准备自身更新

        Args:
            force: 是否强制更新

        Returns:
            bool: 是否需要退出以完成更新
        """
        self.logger.info("正在检查软件更新...")

        if self._is_bundled is None:
            self._is_bundled, self._package_type = detect_package_type()
        if not self._is_bundled:
            self.logger.warning("当前为调试模式，跳过更新检查")
            return False

        if not self._check_system_requirements():
            return False

        try:
            release_info = self._fetch_latest_release()
            if not release_info:
                return False

            latest_version = release_info.get('tag_name', '')
            if not latest_version:
                self.logger.error("未能获取版本号")
                return False

            if not force and is_build_tag(self.current_version):
                self.logger.info("当前为 Build 版本，跳过更新")
                return False

            if not force:
                if version_newer_than(self.current_version, latest_version):
                    self.logger.info(f"检测到新版本: {latest_version}")
                else:
                    cur_tuple = version_to_tuple(self.current_version)
                    lat_tuple = version_to_tuple(latest_version)
                    if cur_tuple and lat_tuple:
                        self.logger.info("当前版本已最新")
                    else:
                        self.logger.error("版本号校验错误，跳过更新")
                    return False
            else:
                self.logger.info(f"强制更新模式，目标版本: {latest_version}")

            existing_state = UpdateState.load()
            if existing_state and existing_state.get("State", "state", fallback="") == "failed_disabled":
                failed_ver = existing_state["new_version"]
                if failed_ver == latest_version:
                    self.logger.info(f"版本 {latest_version} 存在更新失败记录，跳过更新")
                    return False
                self.logger.debug("新版本与失败记录不同，清除失败状态继续")
                existing_state.delete()

            package_type = self._package_type or "Nuitka"
            exe_url, exe_name = self._match_asset(release_info, package_type)
            if not exe_url:
                return False

            cache_root = self._resolve_self_update_root(get_exe_path().parent)
            cache_dir = cache_root / "UpdateCache" / "installs" / latest_version
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = cache_dir / f"{self.app_name}-{latest_version}.exe"
            sha_path = cache_dir / f"{self.app_name}-{latest_version}.sha256"

            new_sha256 = self._get_asset_sha256(release_info, exe_name)
            if not new_sha256:
                self.logger.critical("Github API 中未找到 SHA256 校验值，跳过更新")
                return False

            old_sha256 = self._fetch_current_release_sha256(package_type)
            if old_sha256:
                current_exe = get_exe_path()
                actual_current = calculate_sha256(current_exe)
                if actual_current != old_sha256:
                    self.logger.critical("当前程序 SHA256 与 GitHub 记录不一致，放弃更新")
                    self.logger.warning(f"GitHub: {old_sha256}")
                    self.logger.warning(f"本地:   {actual_current}")
                    return False
                self.logger.info("当前版本 SHA256 校验通过")
            else:
                self.logger.warning("未能获取当前版本 SHA256，跳过自身完整性校验")

            sha_path.write_text(new_sha256, encoding='ascii')

            if not self._download_and_verify(tmp_path, sha_path, exe_url,
                                              new_sha256, latest_version):
                return False

            self._replace_executable(tmp_path, sha_path, latest_version,
                                      old_sha256, new_sha256)
            return True
        except requests.RequestException as e:
            self.logger.critical(f"获取 GitHub release 信息失败: {e}")
            return False
        except Exception as e:
            self.logger.critical(f"检查软件更新时出错: {e}")
            return False

    def _download_and_verify(self, tmp_path: Path, sha_path: Path,
                              exe_url: str, new_sha256: str,
                              latest_version: str) -> bool:
        """下载新版本 exe 并校验 SHA256"""
        if tmp_path.exists():
            actual = calculate_sha256(tmp_path)
            if actual == new_sha256:
                self.logger.info(f"缓存文件校验通过，跳过下载: {tmp_path}")
                return True
            self.logger.warning("缓存文件 SHA256 校验失败，将重新下载")
            tmp_path.unlink(missing_ok=True)
            sha_path.unlink(missing_ok=True)

        max_retries = 3
        for attempt in range(max_retries):
            file_name = Path(exe_url).name
            if attempt > 0:
                self.logger.info(f"重试下载更新文件（{attempt + 1}/{max_retries}）: {file_name}")
            else:
                self.logger.info(f"开始下载更新文件: {file_name}")

            if not self._download_func(exe_url, str(tmp_path)):
                self.logger.error("下载失败")
                continue

            actual = calculate_sha256(tmp_path)
            if actual == new_sha256:
                self.logger.info("新版本已下载并校验通过")
                return True
            self.logger.error("SHA256 校验失败，准备重试")
        else:
            self.logger.critical("软件更新下载校验失败，已达到最大重试次数，跳过更新")
            tmp_path.unlink(missing_ok=True)
            sha_path.unlink(missing_ok=True)
            return False

    def _resolve_self_update_root(self, program_dir: Path) -> Path:
        """解析自更新运行时与缓存根目录。"""
        if self.temp_folder:
            temp_folder = Path(self.temp_folder)
        else:
            local_appdata = os.environ.get('LOCALAPPDATA')
            if local_appdata:
                temp_folder = Path(local_appdata) / self.app_name / 'SelfUpdate'
            else:
                temp_folder = program_dir / 'SelfUpdate'

        try:
            temp_folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            temp_folder = program_dir / 'SelfUpdate'
            temp_folder.mkdir(parents=True, exist_ok=True)

        return temp_folder

    def _resolve_runtime_dir(self, program_dir: Path, new_version: str) -> Path:
        """解析本次更新运行时目录。"""
        return self._resolve_self_update_root(program_dir) / new_version

    def _build_update_runtime_paths(
            self,
            current_exe: Path,
            new_version: str) -> dict[str, Path]:
        """构建本次更新涉及的运行时路径。"""
        program_dir = current_exe.parent
        runtime_dir = self._resolve_runtime_dir(program_dir, new_version)
        temp_folder = runtime_dir.parent
        return {
            'program_dir': program_dir,
            'state_file': program_dir / 'update_state.ini',
            'log_file': program_dir / 'update.log',
            'temp_folder': temp_folder,
            'runtime_dir': runtime_dir,
            'helper_ps1': runtime_dir / f'{self.app_name}_Update_Helper.ps1',
            'update_ps1': runtime_dir / f'{self.app_name}_Update.ps1',
            'lock_file': runtime_dir / 'update_started.lock',
            'new_file': runtime_dir / f'{current_exe.stem}.new.exe',
            'backup_file': runtime_dir / f'{current_exe.stem}.backup.exe',
        }

    def _replace_executable(self, tmp_path: Path, sha_path: Path,
                             new_version: str, old_sha256: str,
                             new_sha256: str) -> None:
        """
        准备替换：生成 helper.ps1 / update.ps1 → 写 INI → 启动 PowerShell

        Raises:
            RuntimeError: helper.ps1 启动失败
        """
        current_exe = get_exe_path()
        paths = self._build_update_runtime_paths(current_exe, new_version)
        paths['runtime_dir'].mkdir(parents=True, exist_ok=True)
        new_exe = paths['new_file']
        backup_exe = paths['backup_file']

        shutil.copy2(tmp_path, new_exe)
        self.logger.info(f"新版本已暂存: {new_exe}")

        state = UpdateState()
        state["state"] = "downloaded_verified"
        state["target"] = str(current_exe)
        state["new_file"] = str(new_exe)
        state["backup_file"] = str(backup_exe)
        state["runtime_dir"] = str(paths['runtime_dir'])
        state["helper_ps1"] = str(paths['helper_ps1'])
        state["update_ps1"] = str(paths['update_ps1'])
        state["lock_file"] = str(paths['lock_file'])
        state["old_version"] = self.current_version
        state["new_version"] = new_version
        state["old_sha256"] = old_sha256
        state["new_sha256"] = new_sha256
        state.set("Retry", "retry_count", _get_existing_retry_count())
        state.set("Retry", "max_retry", "3")
        state.save()

        self._generate_helper_ps1(paths)
        self._generate_update_ps1(paths)
        self.logger.info(f"已生成更新脚本到目录: {paths['runtime_dir']}")

        state.transition("helper_started")

        self.logger.info("启动更新进程...")
        lock_file = paths['lock_file']
        if lock_file.exists():
            lock_file.unlink()

        helper_ps1 = paths['helper_ps1']
        proc = subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(helper_ps1),
                "-ParentPid", str(os.getpid()),
            ],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW,
        )

        deadline = time.time() + 15
        while time.time() < deadline:
            if lock_file.exists():
                return
            if proc.poll() is not None:
                raise RuntimeError(
                    f"启动更新脚本失败：helper.ps1 异常退出，退出码 {proc.returncode}"
                )
            time.sleep(0.1)

        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError("启动更新脚本失败：helper.ps1 未在 15 秒内就绪")

    # ── PS1 脚本生成 ──

    def _generate_helper_ps1(self, paths: dict[str, Path]) -> None:
        """
        生成 {app}_Update_Helper.ps1
        """.format(app=self.app_name)
        ps1_content = textwrap.dedent(r"""
            <#
            .SYNOPSIS
                __APP___Update_Helper
            .DESCRIPTION
                等待主进程退出 → 调用 update.ps1 替换 → 验证新版 → 提交或回滚
            #>
            param([int]$ParentPid)

            $runtimeDir = "{RUNTIME_DIR}"
            $lockFile   = "{LOCK_FILE}"

            try { New-Item -Path $lockFile -ItemType File -Force | Out-Null } catch {}

            $stateFile = "{STATE_FILE}"
            $logFile   = "{LOG_FILE}"
            $updatePs1 = "{UPDATE_PS1}"
        """).replace("__APP__", self.app_name)
        ps1_content += generate_common_base_functions_ps1()
        ps1_content += generate_helper_argument_functions_ps1()
        ps1_content += generate_sha256_function_ps1()
        ps1_content += generate_common_state_functions_ps1()
        ps1_content += generate_helper_retry_functions_ps1()
        ps1_content += generate_helper_file_cleanup_functions_ps1()
        ps1_content += generate_move_with_retry_ps1()
        ps1_content += generate_helper_lifecycle_functions_ps1()
        ps1_content += textwrap.dedent(r'''
            try {
                Set-UpdateStatus "helper_started" "helper_started" "更新 Helper 已启动" 10 "INFO"

                if ($ParentPid -gt 0) {
                    Set-UpdateStatus "helper_started" "wait_parent_exit" "等待主程序退出，PID: $ParentPid" 15 "INFO"
                    try { Wait-Process -Id $ParentPid -Timeout 60 -ErrorAction Stop }
                    catch {
                        $p = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
                        if ($p) { throw "parent still alive: $ParentPid" }
                    }
                }

                Set-UpdateStatus "replacing" "run_update_script" "开始执行文件替换脚本" 30 "INFO"
                $updateCode = Start-ProcWait "powershell.exe" @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $updatePs1) 120
                if ($updateCode -ne 0) {
                    Restore-Backup "update.ps1 failed: exit $updateCode"
                }

                Set-UpdateStatus "replacing" "verify_target_hash" "校验替换后的目标文件 SHA256" 60 "INFO"
                $target    = Read-IniValue "Files" "target"
                $newSha256 = Read-IniValue "Version" "new_sha256"
                Assert-NotEmpty "Files.target" $target
                if ($newSha256) {
                    $actual = Get-SHA256 $target
                    if ($actual -ne $newSha256.ToLowerInvariant()) {
                        Restore-Backup "target hash mismatch after replace"
                    }
                }

                Set-UpdateStatus "pending_new_verify" "start_new_exe_verify" "启动新版程序进行自检" 75 "INFO"
                $newVersion = Read-IniValue "Version" "new_version"
                $verifyArgs = @('--self-update-verify')
                if ($newSha256) {
                    $verifyArgs += @('--expected-sha256', $newSha256)
                }
                if ($newVersion) {
                    $verifyArgs += @('--expected-version', $newVersion)
                }
                $verifyCode = Start-ProcWait $target $verifyArgs 60 $true
                if ($verifyCode -ne 0) {
                    Restore-Backup "verify failed: exit $verifyCode"
                }

                if (-not (Commit-Update)) {
                    Write-Log "ERROR" "update commit failed"
                    exit 1
                }

                try {
                    Set-UpdateStatus "" "start_cleanup" "新版验证通过，启动清理模式" 100 "INFO"
                    Start-CleanupAppVisible $target @('--self-update-cleanup', '--self-update-parent-pid', "$PID")
                } catch {
                    Write-Log "ERROR" "cleanup start failed: $($_.Exception.Message)"
                }
                exit 0
            } catch {
                Write-Log "ERROR" "helper error: $($_.Exception.Message)"
                Restore-Backup $_.Exception.Message
            }
        ''').lstrip("\n")

        ps1_content = ps1_content.replace('{RUNTIME_DIR}', self._ps_quote(paths['runtime_dir']))
        ps1_content = ps1_content.replace('{LOCK_FILE}', self._ps_quote(paths['lock_file']))
        ps1_content = ps1_content.replace('{STATE_FILE}', self._ps_quote(paths['state_file']))
        ps1_content = ps1_content.replace('{LOG_FILE}', self._ps_quote(paths['log_file']))
        ps1_content = ps1_content.replace('{UPDATE_PS1}', self._ps_quote(paths['update_ps1']))

        paths['helper_ps1'].write_text(ps1_content, encoding='utf-8-sig')

    def _generate_update_ps1(self, paths: dict[str, Path]) -> None:
        """
        生成 {app}_Update.ps1
        """.format(app=self.app_name)
        ps1_content = textwrap.dedent(r"""
            <#
            .SYNOPSIS
                __APP___Update
            .DESCRIPTION
                替换 app.exe 为新版本：app.exe - app.backup.exe, app.new.exe - app.exe
            #>

            $runtimeDir = "{RUNTIME_DIR}"
            $stateFile  = "{STATE_FILE}"
            $logFile    = "{LOG_FILE}"
        """).replace("__APP__", self.app_name)
        ps1_content += generate_common_base_functions_ps1()
        ps1_content += generate_sha256_function_ps1()
        ps1_content += generate_common_state_functions_ps1()
        ps1_content += generate_move_with_retry_ps1()
        ps1_content += textwrap.dedent(r"""

            try {
                Set-UpdateStatus "replacing" "read_state" "读取更新状态文件" 35 "INFO"

                $target    = Read-IniValue "Files" "target"
                $newFile   = Read-IniValue "Files" "new_file"
                $backup    = Read-IniValue "Files" "backup_file"
                $newSha256 = Read-IniValue "Version" "new_sha256"

                Assert-NotEmpty "Files.target" $target
                Assert-NotEmpty "Files.new_file" $newFile
                Assert-NotEmpty "Files.backup_file" $backup
                if ($target -eq $newFile -or $target -eq $backup -or $newFile -eq $backup) {
                    throw "invalid file paths: target/new_file/backup_file must be different"
                }

                Set-UpdateStatus "replacing" "check_new_file" "检查新版本文件是否存在: $newFile" 40 "INFO"
                if (!(Test-Path -LiteralPath $newFile)) {
                    throw "new file not found: $newFile"
                }

                if ($newSha256) {
                    Set-UpdateStatus "replacing" "verify_new_file_hash" "校验新版本文件 SHA256" 45 "INFO"
                    $actual = Get-SHA256 $newFile
                    if ($actual -ne $newSha256.ToLowerInvariant()) {
                        throw "new file SHA256 mismatch: expected $newSha256, got $actual"
                    }
                }

                if (Test-Path -LiteralPath $backup) {
                    Set-UpdateStatus "replacing" "remove_old_backup" "删除旧备份文件: $backup" 50 "INFO"
                    Remove-Item -LiteralPath $backup -Force -ErrorAction Stop
                }

                if (Test-Path -LiteralPath $target) {
                    Set-UpdateStatus "replacing" "move_target_to_backup" "备份当前程序: $target -> $backup" 55 "INFO"
                    Move-WithRetry $target $backup 60
                }

                Set-UpdateStatus "replacing" "move_new_to_target" "替换为新版本: $newFile -> $target" 60 "INFO"
                Move-WithRetry $newFile $target 60

                Set-UpdateStatus "replacing" "replace_done" "文件替换完成" 65 "INFO"
                exit 0
            } catch {
                Set-UpdateStatus "failed_disabled" "replace_failed" "文件替换失败: $($_.Exception.Message)" 100 "ERROR"
                Write-Error $_.Exception.Message
                exit 1
            }
        """).lstrip("\n")

        ps1_content = ps1_content.replace('{RUNTIME_DIR}', self._ps_quote(paths['runtime_dir']))
        ps1_content = ps1_content.replace('{STATE_FILE}', self._ps_quote(paths['state_file']))
        ps1_content = ps1_content.replace('{LOG_FILE}', self._ps_quote(paths['log_file']))

        paths['update_ps1'].write_text(ps1_content, encoding='utf-8-sig')

    # ── 静态工具方法 ──

    @staticmethod
    def _ps_quote(path: Path) -> str:
        """将路径转换为 PowerShell 双引号字符串内容。"""
        return str(path).replace('`', '``').replace('$', '`$').replace('"', '`"')

    @staticmethod
    def self_update_verify(expected_sha256: str = "",
                           expected_version: str = "",
                           sha256_calc: Optional[Callable[[Path], str]] = None,
                           version_func: Optional[Callable[[], str]] = None) -> int:
        """
        新版程序健康检查

        Args:
            expected_sha256: 期望的 SHA256（不传则从状态文件读取）
            expected_version: 期望的版本号（不传则从状态文件读取）
            sha256_calc: SHA256 计算函数（不传则使用内建）
            version_func: 获取当前版本号的函数（不传则从状态文件读取）

        Returns:
            0 表示验证通过，非 0 表示失败
        """
        logger = logging.getLogger("SelfUpdater")

        if not expected_sha256:
            state = UpdateState.load()
            if state:
                expected_sha256 = state["new_sha256"]
                expected_version = expected_version or state["new_version"]

        if not expected_sha256:
            logger.critical("未找到 SHA256，无法验证")
            return 1

        calc = sha256_calc or calculate_sha256
        current_exe = get_exe_path()
        actual_sha256 = calc(current_exe)

        if actual_sha256 != expected_sha256:
            logger.critical(
                f"SHA256 不匹配:\n"
                f"GitHub: {expected_sha256}\n"
                f"本地:   {actual_sha256}"
            )
            return 2

        if expected_version and version_func:
            actual_version = version_func()
            if actual_version and actual_version != expected_version:
                logger.critical(
                    f"版本号不匹配:\n"
                    f"GitHub: {expected_version}\n"
                    f"本地:   {actual_version}"
                )
                return 3

        logger.info("新版验证全部通过")
        return 0

    @staticmethod
    def clean_update_cache(temp_folder: str, logger: logging.Logger) -> CleanupResult:
        """
        清理自更新缓存目录 UpdateCache

        Args:
            temp_folder: 自更新根目录；为空时回退到程序目录下的 SelfUpdate
            logger: 日志记录器

        Returns:
            清理结果
        """
        result = CleanupResult()
        if temp_folder:
            cache_dir = Path(temp_folder) / "UpdateCache"
        else:
            cache_dir = get_exe_path().parent / "SelfUpdate" / "UpdateCache"
        if cache_dir.exists():
            try:
                shutil.rmtree(cache_dir)
                logger.info("已清理自更新缓存目录")
                result.cleaned_paths.append(str(cache_dir))
                result.cache_deleted = True
            except OSError as e:
                logger.warning(f"清理自更新缓存目录失败: {e}")
                result.failed_paths.append(str(cache_dir))

        parent_dir = cache_dir.parent
        if parent_dir.exists() and parent_dir.is_dir():
            try:
                if not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
                    logger.info(f"已清理空的自更新根目录: {parent_dir}")
                    result.cleaned_paths.append(str(parent_dir))
                    result.root_deleted = True
            except OSError as e:
                logger.warning(f"清理空的自更新根目录失败: {e}")
                result.failed_paths.append(str(parent_dir))
        return result

    @staticmethod
    def clean_update_scripts(exe_dir: str, app_name: str,
                             logger: logging.Logger,
                             new_file: str = "",
                             backup_file: str = "") -> None:
        """[已废弃] 清理更新产生的文件 — 请改用 _cleanup_update_residue()"""
        import warnings
        warnings.warn(
            "clean_update_scripts 已废弃，请改用 SelfUpdater._cleanup_update_residue()",
            DeprecationWarning, stacklevel=2)
        base = Path(exe_dir)
        files = [
            base / f"{app_name}_Update_Helper.ps1",
            base / f"{app_name}_Update.ps1",
            base / f"{app_name}.new.exe",
            base / "update_started.lock",
            base / "update.log",
        ]
        if backup_file:
            files.append(Path(backup_file))
        if new_file:
            files.append(Path(new_file))
        for fp in files:
            try:
                if fp.exists():
                    fp.unlink()
                    logger.debug(f"已清理: {fp}")
            except OSError:
                pass

    @staticmethod
    def _cleanup_update_residue(logger: logging.Logger) -> CleanupResult:
        """按状态文件记录的路径清理更新运行时文件残留。

        仅当状态为 verified 或 rollback_done 等终端状态时才执行清理：
        1. 从状态文件读取运行时路径（helper_ps1 / update_ps1 / lock_file
           / new_file / backup_file）并逐个删除
        2. 尝试 rmdir runtime_dir（仅当目录为空时生效）
        3. 清理 update.log
        4. 仅在无失败对象时删除状态文件，否则保留（含精确路径）
           供下次清理

        Args:
            logger: 日志记录器

        Returns:
            清理结果
        """
        result = CleanupResult()
        state = UpdateState.load()
        if not state:
            return result

        current_state = state.get("State", "state", fallback="idle")
        if current_state not in ("verified", "rollback_done"):
            return result

        logger.info(f"清理更新残留（状态: {current_state}）...")

        def _try_unlink(path: Path, log_prefix: str) -> None:
            """尝试删除单个文件并逐项记录成功/失败。

            使用 unlink(missing_ok=True) 直接删除，避免 exists() 前置
            检查与 unlink() 之间的 TOCTOU 竞态导致 FileNotFoundError
            误报；文件不存在视为已清理，不记录成功也不记录失败。
            """
            try:
                existed = path.exists()
                path.unlink(missing_ok=True)
                if existed:
                    logger.debug(f"{log_prefix}: {path}")
                    result.cleaned_paths.append(str(path))
            except OSError as e:
                logger.warning(f"清理失败: {path}（{e}）")
                result.failed_paths.append(str(path))

        # 从状态文件读取运行时路径并删除
        runtime_keys = ("helper_ps1", "update_ps1", "lock_file", "new_file", "backup_file")
        for key in runtime_keys:
            file_path = state[key]
            if file_path:
                _try_unlink(Path(file_path), "已清理")

        legacy_fields = (
            state["helper_ps1"],
            state["update_ps1"],
            state["lock_file"],
            state["runtime_dir"],
        )
        if not any(legacy_fields):
            program_dir = state._file_path.parent
            target_stem = Path(state["target"]).stem
            if not target_stem:
                target_stem = Path(sys.argv[0]).resolve().stem
            legacy_files = (
                program_dir / f"{target_stem}_Update_Helper.ps1",
                program_dir / f"{target_stem}_Update.ps1",
                program_dir / "update_started.lock",
            )
            for p in legacy_files:
                _try_unlink(p, "已清理旧版更新残留")

        # 尝试 rmdir runtime_dir（仅空目录可删除）
        runtime_dir = state["runtime_dir"]
        if runtime_dir:
            rtd = Path(runtime_dir)
            try:
                if rtd.exists() and rtd.is_dir():
                    rtd.rmdir()
                    logger.debug(f"已清理运行时目录: {rtd}")
                    result.cleaned_paths.append(str(rtd))
            except OSError as e:
                logger.warning(f"清理运行时目录失败: {rtd}（{e}）")
                result.failed_paths.append(str(rtd))

        # 清理 update.log（与状态文件同目录）
        _try_unlink(state._file_path.with_name('update.log'), "已清理更新日志")

        # 仅当无失败对象时才删除状态文件，否则保留精确路径供下次清理
        if not result.failed_paths:
            state.delete()
            result.state_deleted = True
        else:
            logger.warning("存在未清理的更新残留，保留状态文件供下次清理")
        return result

    @staticmethod
    def cleanup_self_update(
            self_update_root: str,
            logger: logging.Logger) -> CleanupResult:
        """按固定顺序执行并汇总完整的自更新清理。"""
        result = CleanupResult()
        result.merge(SelfUpdater._cleanup_update_residue(logger))
        result.merge(SelfUpdater.clean_update_cache(self_update_root, logger))
        return result

    @staticmethod
    def rollback(logger: Optional[logging.Logger] = None) -> bool:
        """
        从 INI 状态文件读取 backup_file 路径，恢复旧版

        Returns:
            恢复是否成功
        """
        logger = logger or logging.getLogger("SelfUpdater")
        state = UpdateState.load()
        if not state:
            logger.critical("未找到更新状态文件，无法回滚")
            return False

        backup_file = Path(state["backup_file"])
        target = Path(state["target"])

        if not backup_file.exists():
            logger.critical(f"备份文件不存在: {backup_file}")
            return False

        try:
            if target.exists():
                target.unlink()
            backup_file.rename(target)
            logger.info(f"已回滚: {target}")
            state.transition("rollback_done")
            return True
        except OSError as e:
            logger.critical(f"回滚失败: {e}")
            return False

#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""自更新 PS1 片段模块化与运行时目录隔离测试"""

import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# 项目根目录（本文件在 tests/ 下）
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _assert_sha256_fallbacks(content: str, source_name: str) -> None:
    """验证 SHA256 多路径 fallback 片段（.NET → Get-FileHash → certutil）"""
    assert 'function Get-SHA256($filePath)' in content, \
        f'{source_name}: 缺少 Get-SHA256 函数定义'
    assert '[System.IO.File]::OpenRead' in content, \
        f'{source_name}: 缺少 .NET 文件流读取'
    assert '[System.Security.Cryptography.SHA256]::Create()' in content, \
        f'{source_name}: 缺少 .NET SHA256 创建'
    assert '$sha256.Dispose()' in content, \
        f'{source_name}: 缺少 SHA256 实例释放'
    assert '$stream.Dispose()' in content, \
        f'{source_name}: 缺少文件流释放'
    assert 'Get-Command Get-FileHash -ErrorAction SilentlyContinue' in content, \
        f'{source_name}: 缺少 Get-FileHash 可用性探测'
    assert 'Get-FileHash -Algorithm SHA256 -LiteralPath $filePath' in content, \
        f'{source_name}: 缺少 Get-FileHash 调用'
    assert 'certutil.exe -hashfile' in content, \
        f'{source_name}: 缺少 certutil fallback'
    assert '^[0-9A-Fa-f]{64}$' in content, \
        f'{source_name}: 缺少 64 位 hex 正则'
    assert 'throw "Get-SHA256 failed' in content, \
        f'{source_name}: 缺少 Get-SHA256 失败信息'


# ── 任务 1 测试 ──

def test_ps1_fragments_generate_sha256_function():
    """PS1 片段模块应生成 SHA256 多路径 fallback 函数"""
    from modules.ps1_fragments import generate_sha256_function_ps1

    content = generate_sha256_function_ps1()

    _assert_sha256_fallbacks(content, 'ps1_fragments.generate_sha256_function_ps1')
    assert content.count('function Get-SHA256') == 1
    assert '$errors = @()' in content
    assert '$LASTEXITCODE = 0' in content


# ── 任务 2 测试 ──

def test_ps1_fragments_generate_common_function_groups():
    """公共 PS1 片段应包含基础、状态与移动函数"""
    from modules.ps1_fragments import (
        generate_common_base_functions_ps1,
        generate_common_state_functions_ps1,
        generate_move_with_retry_ps1,
    )

    base = generate_common_base_functions_ps1()
    state = generate_common_state_functions_ps1()
    move = generate_move_with_retry_ps1()

    assert 'function Normalize-IniValue' in base
    assert 'function Assert-NotEmpty' in base
    assert 'function Write-Log' in base
    assert 'function Read-IniValue' in state
    assert 'function Write-IniValue' in state
    assert 'function Set-UpdateStatus' in state
    assert 'function Move-WithRetry' in move
    assert 'function Get-SHA256' not in base + state + move


# ── 任务 3 测试 ──

def test_ps1_fragments_generate_helper_only_function_groups():
    """Helper 独有 PS1 片段应按职责分组且不进入 Update 公共片段"""
    from modules.ps1_fragments import (
        generate_helper_argument_functions_ps1,
        generate_helper_file_cleanup_functions_ps1,
        generate_helper_lifecycle_functions_ps1,
        generate_helper_retry_functions_ps1,
    )

    argument = generate_helper_argument_functions_ps1()
    retry = generate_helper_retry_functions_ps1()
    cleanup = generate_helper_file_cleanup_functions_ps1()
    lifecycle = generate_helper_lifecycle_functions_ps1()

    assert 'function Quote-Arg' in argument
    assert 'function Get-RetryOrDefault' in retry
    assert 'function Remove-WithRetry' in cleanup
    assert 'function Commit-Update' in lifecycle
    assert 'function Restore-Backup' in lifecycle
    assert 'function Start-ProcWait' in lifecycle
    assert 'function Start-NormalAppVisible' in lifecycle


# ── 任务 4 测试 ──

def test_self_updater_build_runtime_paths_uses_localappdata(monkeypatch, tmp_path):
    """默认应使用 LOCALAPPDATA 下的应用 SelfUpdate 目录作为 runtime 根目录"""
    from modules.self_updater import SelfUpdater

    local_appdata = tmp_path / 'LocalAppData'
    exe = tmp_path / 'program' / 'TwoPush.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'exe')
    monkeypatch.setenv('LOCALAPPDATA', str(local_appdata))

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        temp_folder='',
        logger=logging.getLogger('test_runtime_paths'),
    )

    paths = updater._build_update_runtime_paths(exe, 'v2.0.0')

    assert paths['program_dir'] == exe.parent
    assert paths['state_file'] == exe.parent / 'update_state.ini'
    assert paths['log_file'] == exe.parent / 'update.log'
    assert paths['temp_folder'] == local_appdata / 'TwoPush' / 'SelfUpdate'
    assert paths['runtime_dir'] == local_appdata / 'TwoPush' / 'SelfUpdate' / 'v2.0.0'
    assert paths['helper_ps1'] == paths['runtime_dir'] / 'TwoPush_Update_Helper.ps1'
    assert paths['update_ps1'] == paths['runtime_dir'] / 'TwoPush_Update.ps1'
    assert paths['lock_file'] == paths['runtime_dir'] / 'update_started.lock'
    assert paths['new_file'] == paths['runtime_dir'] / 'TwoPush.new.exe'
    assert paths['backup_file'] == paths['runtime_dir'] / 'TwoPush.backup.exe'


def test_self_updater_build_runtime_paths_uses_custom_temp_folder(tmp_path):
    """传入 temp_folder 时 runtime_dir 应为 temp_folder / version"""
    from modules.self_updater import SelfUpdater

    exe = tmp_path / 'program' / 'TwoPush.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'exe')
    custom_temp = tmp_path / 'custom-self-update'

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        temp_folder=str(custom_temp),
        logger=logging.getLogger('test_runtime_paths_custom'),
    )

    paths = updater._build_update_runtime_paths(exe, 'v2.0.0')

    assert paths['temp_folder'] == custom_temp
    assert paths['runtime_dir'] == custom_temp / 'v2.0.0'


def test_self_updater_build_runtime_paths_falls_back_to_program_dir(monkeypatch, tmp_path):
    """LOCALAPPDATA 不可用时应 fallback 到程序目录 SelfUpdate"""
    from modules.self_updater import SelfUpdater

    exe = tmp_path / 'program' / 'TwoPush.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'exe')
    monkeypatch.delenv('LOCALAPPDATA', raising=False)

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        temp_folder='',
        logger=logging.getLogger('test_runtime_paths_fallback'),
    )

    paths = updater._build_update_runtime_paths(exe, 'v2.0.0')

    assert paths['temp_folder'] == exe.parent / 'SelfUpdate'
    assert paths['runtime_dir'] == exe.parent / 'SelfUpdate' / 'v2.0.0'


# ── 任务 5 测试 ──

def test_generated_update_scripts_use_injected_absolute_paths(tmp_path):
    """生成的 PS1 应使用注入路径，不再从 scriptDir 推导 state/log"""
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    runtime_dir = tmp_path / 'runtime' / 'v2.0.0'
    program_dir.mkdir()
    runtime_dir.mkdir(parents=True)

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        temp_folder='',
        logger=logging.getLogger('test_injected_paths'),
    )
    paths = {
        'state_file': program_dir / 'update_state.ini',
        'log_file': program_dir / 'update.log',
        'runtime_dir': runtime_dir,
        'helper_ps1': runtime_dir / 'TwoPush_Update_Helper.ps1',
        'update_ps1': runtime_dir / 'TwoPush_Update.ps1',
        'lock_file': runtime_dir / 'update_started.lock',
    }

    updater._generate_helper_ps1(paths)
    updater._generate_update_ps1(paths)

    helper_text = paths['helper_ps1'].read_text(encoding='utf-8-sig')
    update_text = paths['update_ps1'].read_text(encoding='utf-8-sig')

    # Should use injected paths
    assert '$stateFile = "' + str(paths['state_file']) + '"' in helper_text
    assert '$logFile   = "' + str(paths['log_file']) + '"' in helper_text
    assert '$lockFile   = "' + str(paths['lock_file']) + '"' in helper_text
    assert '$updatePs1 = "' + str(paths['update_ps1']) + '"' in helper_text
    assert '$stateFile  = "' + str(paths['state_file']) + '"' in update_text
    assert '$logFile    = "' + str(paths['log_file']) + '"' in update_text
    # Should NOT use Join-Path $scriptDir
    assert '$stateFile = Join-Path $scriptDir "update_state.ini"' not in helper_text
    assert '$stateFile = Join-Path $scriptDir "update_state.ini"' not in update_text


def test_replace_executable_writes_runtime_paths_to_state(monkeypatch, tmp_path):
    """替换准备阶段应将 runtime 路径写入 update_state.ini"""
    from modules.config_self_updater import UpdateState
    from modules.self_updater import SelfUpdater

    exe = tmp_path / 'program' / 'TwoPush.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'old')
    tmp_new = tmp_path / 'downloaded.exe'
    tmp_new.write_bytes(b'new')
    sha = tmp_path / 'downloaded.sha256'
    sha.write_text('hash', encoding='utf-8')
    custom_temp = tmp_path / 'runtime-root'

    monkeypatch.setattr('modules.self_updater.get_exe_path', lambda: exe)
    monkeypatch.setattr('modules.self_updater.os.getpid', lambda: 1234)

    class FakeProc:
        returncode = None
        def poll(self):
            return None
        def kill(self):
            return None

    def fake_popen(*args, **kwargs):
        lock_file = custom_temp / 'v2.0.0' / 'update_started.lock'
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text('started', encoding='utf-8')
        return FakeProc()

    monkeypatch.setattr('modules.self_updater.subprocess.Popen', fake_popen)
    monkeypatch.setattr(sys, 'argv', [str(exe)])

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        temp_folder=str(custom_temp),
        logger=logging.getLogger('test_replace_runtime_state'),
    )

    updater._replace_executable(tmp_new, sha, 'v2.0.0', 'oldhash', 'newhash')

    state = UpdateState.load()
    assert state is not None
    assert state['runtime_dir'] == str(custom_temp / 'v2.0.0')
    assert state['helper_ps1'] == str(custom_temp / 'v2.0.0' / 'TwoPush_Update_Helper.ps1')
    assert state['update_ps1'] == str(custom_temp / 'v2.0.0' / 'TwoPush_Update.ps1')
    assert state['lock_file'] == str(custom_temp / 'v2.0.0' / 'update_started.lock')
    assert state['new_file'] == str(custom_temp / 'v2.0.0' / 'TwoPush.new.exe')
    assert state['backup_file'] == str(custom_temp / 'v2.0.0' / 'TwoPush.backup.exe')


# ── 任务 6 测试 ──

def test_cleanup_update_residue_removes_recorded_runtime_files(monkeypatch, tmp_path):
    """清理更新残留时应只删除状态文件记录的运行时文件"""
    from modules.config_self_updater import UpdateState
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    runtime_dir = tmp_path / 'runtime' / 'v2.0.0'
    program_dir.mkdir()
    runtime_dir.mkdir(parents=True)

    target = program_dir / 'TwoPush.exe'
    target.write_bytes(b'target')
    helper = runtime_dir / 'TwoPush_Update_Helper.ps1'
    update = runtime_dir / 'TwoPush_Update.ps1'
    lock = runtime_dir / 'update_started.lock'
    new_file = runtime_dir / 'TwoPush.new.exe'
    backup = runtime_dir / 'TwoPush.backup.exe'
    foreign = program_dir / 'Other_Update_Helper.ps1'

    for path in [helper, update, lock, new_file, backup, foreign]:
        path.write_text('test', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [str(target)])
    state = UpdateState()
    state['state'] = 'verified'
    state['target'] = str(target)
    state['runtime_dir'] = str(runtime_dir)
    state['helper_ps1'] = str(helper)
    state['update_ps1'] = str(update)
    state['lock_file'] = str(lock)
    state['new_file'] = str(new_file)
    state['backup_file'] = str(backup)
    state.save()

    log_file = program_dir / 'update.log'
    log_file.write_text('log', encoding='utf-8')

    SelfUpdater._cleanup_update_residue(logging.getLogger('test_cleanup_runtime'))

    assert not helper.exists()
    assert not update.exists()
    assert not lock.exists()
    assert not new_file.exists()
    assert not backup.exists()
    assert not runtime_dir.exists()
    assert not (program_dir / 'update_state.ini').exists()
    assert not log_file.exists()
    assert foreign.exists()


def test_cleanup_update_residue_keeps_runtime_dir_when_not_verified(monkeypatch, tmp_path):
    """更新未 verified 时不应主动清理 runtime_dir"""
    from modules.config_self_updater import UpdateState
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    runtime_dir = tmp_path / 'runtime' / 'v2.0.0'
    program_dir.mkdir()
    runtime_dir.mkdir(parents=True)
    target = program_dir / 'TwoPush.exe'
    target.write_bytes(b'target')
    backup = runtime_dir / 'TwoPush.backup.exe'
    backup.write_text('backup', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [str(target)])
    state = UpdateState()
    state['state'] = 'replacing'
    state['target'] = str(target)
    state['runtime_dir'] = str(runtime_dir)
    state['backup_file'] = str(backup)
    state.save()

    SelfUpdater._cleanup_update_residue(logging.getLogger('test_cleanup_not_verified'))

    assert runtime_dir.exists()
    assert backup.exists()
    assert (program_dir / 'update_state.ini').exists()


# ── 任务 7 测试 ──

def test_rollback_uses_backup_file_in_runtime_dir(monkeypatch, tmp_path):
    """回滚应使用状态文件中 runtime_dir 内的 backup_file"""
    from modules.config_self_updater import UpdateState
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    runtime_dir = tmp_path / 'runtime' / 'v2.0.0'
    program_dir.mkdir()
    runtime_dir.mkdir(parents=True)
    target = program_dir / 'TwoPush.exe'
    backup = runtime_dir / 'TwoPush.backup.exe'
    target.write_bytes(b'broken')
    backup.write_bytes(b'old')

    monkeypatch.setattr(sys, 'argv', [str(target)])
    state = UpdateState()
    state['state'] = 'failed_disabled'
    state['target'] = str(target)
    state['runtime_dir'] = str(runtime_dir)
    state['backup_file'] = str(backup)
    state.save()

    assert SelfUpdater.rollback(logging.getLogger('test_rollback_runtime')) is True
    assert target.read_bytes() == b'old'
    assert not backup.exists()


def test_readme_documents_self_update_runtime_layout():
    """README 应说明自更新运行时目录布局"""
    readme = os.path.join(ROOT_DIR, 'README.md')
    with open(readme, 'r', encoding='utf-8') as f:
        content = f.read()

    assert '自更新运行时文件布局' in content
    assert 'update_state.ini' in content
    assert 'update.log' in content
    assert r'%LOCALAPPDATA%\TwoPush\SelfUpdate\{version}' in content
    assert r'program_dir\SelfUpdate\{version}' in content
    for key in ['runtime_dir', 'helper_ps1', 'update_ps1', 'lock_file', 'new_file', 'backup_file']:
        assert key in content

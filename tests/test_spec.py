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

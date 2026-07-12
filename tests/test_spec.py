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

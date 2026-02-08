# -*- mode: python ; coding: utf-8 -*-

"""
ALAS-LOG 项目 PyInstaller 配置文件
用于将 Python 脚本打包为可执行文件
"""

import os
from PyInstaller.utils.hooks import collect_data_files

# 获取当前目录
current_dir = os.path.dirname(os.path.abspath(SPEC))

# 基本配置
block_cipher = None

# 数据文件收集
datas = []

# 隐藏导入（如果需要）
hiddenimports = []

# 分析配置
a = Analysis(
    ['ALAS_Logs_Archive.py'],
    pathex=[current_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 过滤不需要的文件
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 可执行文件配置
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ALAS_Logs_Archive',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

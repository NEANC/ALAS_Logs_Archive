# -*- mode: python ; coding: utf-8 -*-

"""
ALAS-LOG 项目 PyInstaller 配置文件
用于将 Python 脚本打包为可执行文件
"""

import os
import fnmatch


def _filter_binaries(toc, patterns):
    """从 TOC 中移除文件名匹配 patterns 的二进制文件（仅排除来自非系统的路径）"""
    filtered = []
    for item in toc:
        dest_name = os.path.basename(item[0]).lower()
        src_path = item[1]
        if any(fnmatch.fnmatch(dest_name, p) for p in patterns):
            if 'Java' in src_path or 'temurin' in src_path.lower():
                continue
        filtered.append(item)
    return filtered


# 获取当前目录
current_dir = os.path.dirname(os.path.abspath(SPEC))

# 基本配置
block_cipher = None

# 分析配置
a = Analysis(
    ['ALAS_Logs_Archive.py'],
    pathex=[current_dir],
    binaries=[],
    datas=[],
    hiddenimports=[
        'modules.config_manager',
        'modules.config_migration',
        'modules.logger_manager',
        'modules.version',
        'modules.zip_compress',
        'modules.zip_decompress',
        'modules.alas_logger_processor',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'altgraph',
        'astroid',
        'atomicwrites',
        'attrs',
        'babel',
        'bcrypt',
        'black',
        'blinker',
        'boto',
        'boto3',
        'botocore',
        'cairo',
        'cffi',
        'cryptography',
        'curses',
        'distutils',
        'docutils',
        'easy_install',
        'faulthandler',
        'flask',
        'future',
        'gevent',
        'greenlet',
        'h5py',
        'idlelib',
        'ipykernel',
        'IPython',
        'isort',
        'jinja2',
        'jupyter',
        'lib2to3',
        'markupsafe',
        'matplotlib',
        'mock',
        'multiprocessing',
        'nacl',
        'numpy',
        'paramiko',
        'pexpect',
        'pickle',
        'pickleshare',
        'PIL',
        'pip',
        'pkg_resources',
        'prompt_toolkit',
        'psutil',
        'ptyprocess',
        'pyasn1',
        'pycodestyle',
        'pycparser',
        'pyflakes',
        'pygame',
        'pygments',
        'pylint',
        'pynacl',
        'PyQt4',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'pytest',
        'scipy',
        'setuptools',
        'shelve',
        'sphinx',
        'sqlalchemy',
        'tkinter',
        'toml',
        'tornado',
        'traitlets',
        'unittest',
        'wcwidth',
        'wheel',
        'wx',
        'xml',
        'xmlrpc',
        'yaml',
        'zmq',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 过滤 runner 环境泄漏的 DLL（api-ms-win-*, ucrtbase）
a.binaries = _filter_binaries(
    a.binaries,
    ['api-ms-win-*.dll', 'ucrtbase.dll'],
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
    upx_exclude=['python3*.dll', 'VCRUNTIME*.dll', 'api-ms-win-*.dll'],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

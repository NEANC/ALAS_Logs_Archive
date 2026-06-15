#!/usr/bin/env python3
# -_- coding: utf-8 -*-
"""CI 端到端测试辅助脚本

生成测试数据 + 调用 ALAS_Logs_Archive 执行归档/解压/验证。
CI workflow 调用此脚本，不与单元测试混淆。

用法:
    python tests/ci_e2e_helper.py --mode source --target TMPDIR --archive ARCDIR [--exe PATH]
"""

import argparse
import hashlib
import os
import random
import string
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


# 配置常量
FILES_PER_DATE = 10
DATES = [
    (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),  # 前天
    (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),  # 昨天
    datetime.now().strftime("%Y-%m-%d"),                         # 今天
    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),   # 明天
]
LARGE_FILE_SIZE = 500 * 1024 * 1024  # 500MB
SMALL_FILE_MIN = 1024                 # 1KB
SMALL_FILE_MAX = 512 * 1024           # 512KB
ALGORITHMS = ["zstd", "lzma", "bzip2"]
ARCHIVE_NAME = "ci_test_存档"


def random_content(size: int) -> bytes:
    """生成可均匀压缩的伪随机内容（非全零但不至于压缩到 0B）"""
    chunk = bytearray(random.getrandbits(8) for _ in range(min(size, 8192)))
    return (chunk * ((size // len(chunk)) + 1))[:size]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(1048576)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def generate_test_data(target_dir: str) -> dict:
    """在 target_dir 下生成测试日志文件

    每个日期生成 FILES_PER_DATE 个 log 文件 + 若干 txt/_gui 文件，
    其中每个日期阵列中至少有一个 500MB 的大文件。

    Returns:
        {文件名: {"sha256": ..., "size": ...}, ...}  原文清单
    """
    os.makedirs(target_dir, exist_ok=True)
    manifest = {}

    for date_str in DATES:
        date_prefix = f"{date_str}_"
        # 生成 idx=0 为 500MB 大文件，其余为小文件
        for idx in range(FILES_PER_DATE):
            size = LARGE_FILE_SIZE if idx == 0 else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
            filename = f"{date_prefix}挂机-测试{idx:02d}.log"
            path = os.path.join(target_dir, filename)
            with open(path, "wb") as f:
                f.write(random_content(size))
            manifest[filename] = {"sha256": sha256_file(path), "size": size}

        # 混入 _gui.txt 文件（各 1 个）
        gui_name = f"{date_prefix}挂机-测试_gui.txt"
        path = os.path.join(target_dir, gui_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(random.choice(string.ascii_letters) for _ in range(1024)))
        manifest[gui_name] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

        # 混入普通 txt 文件
        txt_name = f"{date_prefix}notes.txt"
        path = os.path.join(target_dir, txt_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("test notes\n" * 100)
        manifest[txt_name] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    # 额外在根目录放一个独立的 error 文件夹（模拟待清理的 error）
    error_dir = os.path.join(target_dir, "error")
    os.makedirs(error_dir, exist_ok=True)
    err_file = os.path.join(error_dir, "crash.log")
    with open(err_file, "wb") as f:
        f.write(b"ERROR CONTENT\n" * 100)

    print(f"[CI] 测试数据已生成: {target_dir}  ({len(manifest)} 个文件)")
    return manifest


def run_cmd(args: list, cwd: str = None, timeout: int = 1200) -> subprocess.CompletedProcess:
    """运行命令并返回结果。超时默认 20 分钟。"""
    print(f"[CI] 运行: {' '.join(args)}")
    return subprocess.run(args, cwd=cwd or ".", capture_output=False, timeout=timeout)


def verify_decompression(output_dir: str, expected_manifest: dict) -> bool:
    """验证解压输出与原始清单一致（只检查归档内的文件）"""
    ok = True
    for fname, info in expected_manifest.items():
        out_path = os.path.join(output_dir, fname)
        if not os.path.isfile(out_path):
            print(f"[CI] 缺失: {fname}", file=sys.stderr)
            ok = False
            continue
        actual_sha = sha256_file(out_path)
        if actual_sha != info["sha256"]:
            print(f"[CI] HASH 不匹配: {fname}  expected={info['sha256'][:16]} actual={actual_sha[:16]}", file=sys.stderr)
            ok = False
        else:
            print(f"[CI] 验证通过: {fname} ({info['size']} B)")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["source", "exe"])
    parser.add_argument("--exe", default="", help="exe 路径 (mode=exe 时必需)")
    parser.add_argument("--target", required=True, help="源日志目录")
    parser.add_argument("--archive", required=True, help="归档输出目录")
    parser.add_argument("--decompress", default="", help="解压输出目录")
    args = parser.parse_args()

    target_dir = args.target
    archive_dir = args.archive
    decompress_dir = args.decompress or os.path.join(archive_dir, "ci_decompressed")

    # 构建命令前缀
    if args.mode == "exe":
        if not args.exe or not os.path.isfile(args.exe):
            print(f"[CI] FATAL: exe 不存在: {args.exe}", file=sys.stderr)
            sys.exit(1)
        cmd_base = [args.exe]
    else:
        cmd_base = [sys.executable, "ALAS_Logs_Archive.py"]

    # 清理旧目录
    for d in [target_dir, archive_dir, decompress_dir]:
        try:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


    # 1. 滚动模式 压缩 (默认 zstd)
    print("\n" + "=" * 60)
    print("[CI] 阶段 1: 滚动模式 (zstd)")
    print("=" * 60)
    manifest = generate_test_data(target_dir)
    run_cmd(cmd_base + [
        "-t", target_dir,
        "-a", archive_dir,
        "-n", ARCHIVE_NAME,
        "-m", "scroll",
        "-c", "zstd",
        "-l", "3",
        "-w", "4",
        "-L", "false",
    ])

    # 验证：滚动模式下 ZIP 应已创建，原文件应已删除
    scroll_zips = sorted(Path(archive_dir).glob(f"*{ARCHIVE_NAME}*.zip"))
    if not scroll_zips:
        print("[CI] FAIL: 滚动模式未生成归档", file=sys.stderr)
        sys.exit(1)
    print(f"[CI] 滚动归档: {[z.name for z in scroll_zips]}")

    # 验证原文件被清除（除当日和明日文件外）
    remaining = list(Path(target_dir).glob("*.log")) + list(Path(target_dir).glob("*.txt"))
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    unexpected = [f.name for f in remaining if today not in f.name and tomorrow not in f.name]
    if unexpected:
        print(f"[CI] FAIL: 滚动模式未删除旧文件: {unexpected}", file=sys.stderr)
        sys.exit(1)
    print("[CI] 滚动模式 归档验证通过 ✓")

    # 解压滚动归档并验证
    os.makedirs(decompress_dir + "_scroll", exist_ok=True)
    for z in scroll_zips:
        run_cmd(cmd_base + [
            "-d", str(z),
            "-o", decompress_dir + "_scroll",
            "-L", "false",
        ])
    # 过滤出非今日/明日的文件验证（那些是今天和明天的未归档文件）
    expected_non_today = {k: v for k, v in manifest.items()
                          if not k.startswith(today) and not k.startswith(tomorrow)}
    if not verify_decompression(decompress_dir + "_scroll", expected_non_today):
        sys.exit(1)
    print("[CI] 滚动模式 解压验证通过 ✓")


    # 2. 增量模式 (混用算法)
    # 重新生成测试数据（只生成昨天的，模拟分批追加）
    print("\n" + "=" * 60)
    print("[CI] 阶段 2: 增量模式 (混用算法)")
    print("=" * 60)
    # 清理并重新生成
    import shutil
    shutil.rmtree(target_dir, ignore_errors=True)
    # 分批生成：一半用 zstd 压缩，追加后用 lzma，最后用 bzip2
    batch_size = FILES_PER_DATE // 2
    all_manifest = {}

    # 第 1 批：只生成前 batch_size 个文件
    yesterday = DATES[1]  # 昨天
    date_prefix = f"{yesterday}_"
    for idx in range(batch_size):
        size = LARGE_FILE_SIZE if idx == 0 else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{date_prefix}挂机-测试{idx:02d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}
    # 加上 gui / txt
    for suffix, ext in [("_gui", "txt"), ("notes", "txt")]:
        fn = f"{date_prefix}{'挂机-测试' + suffix if '_gui' in suffix else ''}{'.' + ext if ext else ''}"
        if suffix == "_gui":
            fn = f"{date_prefix}挂机-测试_gui.txt"
        elif suffix == "notes":
            fn = f"{date_prefix}notes.txt"
        path = os.path.join(target_dir, fn)
        with open(path, "w" if ext == "txt" else "wb") as f:
            f.write("A" * 2048 if ext == "txt" else b"B" * 2048)
        all_manifest[fn] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    # 运行增量模式-zstd
    inc_archive = os.path.join(archive_dir, "inc_存档.zip")
    run_cmd(cmd_base + [
        "-t", target_dir,
        "-a", archive_dir,
        "-n", "inc_存档",
        "-m", "incremental",
        "-c", "zstd",
        "-l", "3",
        "-w", "4",
        "-L", "false",
    ])
    print("[CI] 增量批1 (zstd) 完成")

    # 第 2 批：生成剩余文件
    for idx in range(batch_size, FILES_PER_DATE):
        size = random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{date_prefix}挂机-测试{idx:02d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}

    run_cmd(cmd_base + [
        "-t", target_dir,
        "-a", archive_dir,
        "-n", "inc_存档",
        "-m", "incremental",
        "-c", "lzma",
        "-l", "3",
        "-w", "4",
        "-L", "false",
    ])
    print("[CI] 增量批2 (lzma) 完成")

    # 第 3 批：再生成一批（次日日期）
    tomorrow_str = DATES[3]
    tp = f"{tomorrow_str}_"
    for idx in range(FILES_PER_DATE):
        size = LARGE_FILE_SIZE if idx == 0 else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{tp}挂机-测试{idx:02d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}

    run_cmd(cmd_base + [
        "-t", target_dir,
        "-a", archive_dir,
        "-n", "inc_存档",
        "-m", "incremental",
        "-c", "bzip2",
        "-l", "3",
        "-w", "4",
        "-L", "false",
    ])
    print("[CI] 增量批3 (bzip2) 完成")

    # 解压增量归档并验证
    os.makedirs(decompress_dir + "_inc", exist_ok=True)
    run_cmd(cmd_base + [
        "-d", inc_archive,
        "-o", decompress_dir + "_inc",
        "-L", "false",
    ])
    if not verify_decompression(decompress_dir + "_inc", all_manifest):
        sys.exit(1)
    print("[CI] 增量模式 (混用算法) 解压验证通过 ✓")


    # 3. 解压独立测试（用滚动模式的 ZIP 再测一次）
    print("\n" + "=" * 60)
    print("[CI] 阶段 3: 独立解压测试")
    print("=" * 60)
    shutil.rmtree(decompress_dir, ignore_errors=True)
    for z in scroll_zips:
        run_cmd(cmd_base + [
            "-d", str(z),
            "-o", decompress_dir,
            "-L", "false",
        ])
    if not verify_decompression(decompress_dir, expected_non_today):
        sys.exit(1)
    print("[CI] 独立解压测试通过 ✓")

    print("\n" + "=" * 60)
    print("[CI] 全部 E2E 测试通过 ✓")
    print("=" * 60)


if __name__ == "__main__":
    main()

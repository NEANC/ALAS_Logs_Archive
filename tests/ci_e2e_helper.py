#!/usr/bin/env python3
# -_- coding: utf-8 -*-
"""CI E2E test helper script.

Generates test log files, invokes ALAS_Logs_Archive for
archive / decompress / verify flows. Used by e2e-test.yml.
"""

import argparse
import hashlib
import os
import random
import shutil
import string
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Config
FILES_PER_DATE = 10
DATES = [
    (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),  # day-before-yesterday
    (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),  # yesterday
    datetime.now().strftime("%Y-%m-%d"),                         # today
    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),   # tomorrow
]
LARGE_FILE_SIZE = 500 * 1024 * 1024  # 500MB
SMALL_FILE_MIN = 1024                  # 1KB
SMALL_FILE_MAX = 512 * 1024            # 512KB
ARCHIVE_NAME = "ci_test_archive"


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{_now()}] [CI] {msg}", flush=True)


def random_content(size: int) -> bytes:
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
    """Generate test log files under target_dir.

    Each date gets FILES_PER_DATE .log files (one 500MB) + _gui.txt + notes.txt.
    Also creates an 'error' folder with a crash.log.

    Returns: {filename: {"sha256": ..., "size": ...}, ...}
    """
    os.makedirs(target_dir, exist_ok=True)
    manifest = {}

    for date_str in DATES:
        prefix = f"{date_str}_"
        for idx in range(FILES_PER_DATE):
            size = LARGE_FILE_SIZE if idx == 0 else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
            filename = f"{prefix}test_{idx:02d}.log"
            path = os.path.join(target_dir, filename)
            with open(path, "wb") as f:
                f.write(random_content(size))
            manifest[filename] = {"sha256": sha256_file(path), "size": size}

        gui_name = f"{prefix}test_gui.txt"
        path = os.path.join(target_dir, gui_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(random.choice(string.ascii_letters) for _ in range(1024)))
        manifest[gui_name] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

        txt_name = f"{prefix}notes.txt"
        path = os.path.join(target_dir, txt_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("test notes\n" * 100)
        manifest[txt_name] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    error_dir = os.path.join(target_dir, "error")
    os.makedirs(error_dir, exist_ok=True)
    err_file = os.path.join(error_dir, "crash.log")
    with open(err_file, "wb") as f:
        f.write(b"ERROR CONTENT\n" * 100)

    log(f"Test data generated: {target_dir}  ({len(manifest)} files)")
    return manifest


def run_archive(args: list, cwd: str = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    """Run archive subprocess and print its output."""
    log(f"Run: {' '.join(args)}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        args,
        cwd=cwd or os.getcwd(),
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    sys.stdout.write(result.stdout.decode("utf-8", errors="replace"))
    sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
    sys.stdout.flush()
    sys.stderr.flush()
    if result.returncode != 0:
        log(f"FAILED with exit code {result.returncode}")
    return result


def verify_decompression(output_dir: str, expected_manifest: dict) -> bool:
    """Verify decompressed files match original manifest."""
    ok = True
    for fname, info in expected_manifest.items():
        out_path = os.path.join(output_dir, fname)
        if not os.path.isfile(out_path):
            log(f"MISSING: {fname}")
            ok = False
            continue
        actual_sha = sha256_file(out_path)
        if actual_sha != info["sha256"]:
            log(f"HASH MISMATCH: {fname}  expected={info['sha256'][:16]} actual={actual_sha[:16]}")
            ok = False
    if ok:
        log(f"Verification passed: {len(expected_manifest)} files OK")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["source", "exe"])
    parser.add_argument("--exe", default="", help="exe path (required when mode=exe)")
    parser.add_argument("--target", required=True, help="source log directory")
    parser.add_argument("--archive", required=True, help="archive output directory")
    args = parser.parse_args()

    target_dir = args.target
    archive_dir = args.archive
    decompress_dir = os.path.join(archive_dir, "ci_decompressed")

    if args.mode == "exe":
        if not args.exe or not os.path.isfile(args.exe):
            log(f"FATAL: exe not found: {args.exe}")
            sys.exit(1)
        cmd_base = [args.exe]
    else:
        cmd_base = [sys.executable, "ALAS_Logs_Archive.py"]

    # Clean up old directories
    for d in [target_dir, archive_dir, decompress_dir]:
        shutil.rmtree(d, ignore_errors=True)

    # Phase 1: Scroll mode (zstd)
    log("=" * 50)
    log("Phase 1: Scroll mode (zstd)")
    log("=" * 50)

    manifest = generate_test_data(target_dir)
    result = run_archive(cmd_base + [
        "-t", target_dir,
        "-a", archive_dir,
        "-n", ARCHIVE_NAME,
        "-m", "scroll",
        "-c", "zstd",
        "-l", "3",
        "-w", "4",
        "-L", "false",
    ])
    if result.returncode != 0:
        log("Phase 1 FAILED: archive tool exited non-zero")
        sys.exit(1)

    scroll_zips = sorted(Path(archive_dir).glob(f"*{ARCHIVE_NAME}*.zip"))
    if not scroll_zips:
        log("FAIL: no archive created by scroll mode")
        sys.exit(1)
    log(f"Scroll archives: {[z.name for z in scroll_zips]}")

    # Verify old source files were deleted (except today and tomorrow)
    remaining = list(Path(target_dir).glob("*.log")) + list(Path(target_dir).glob("*.txt"))
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    unexpected = [f.name for f in remaining if today not in f.name and tomorrow not in f.name]
    if unexpected:
        log(f"FAIL: scroll mode did not delete old files: {unexpected}")
        sys.exit(1)
    log("Scroll mode archive verification passed")

    # Decompress scroll archives
    scroll_out = decompress_dir + "_scroll"
    os.makedirs(scroll_out, exist_ok=True)
    for z in scroll_zips:
        run_archive(cmd_base + ["-d", str(z), "-o", scroll_out, "-L", "false"])

    expected_non_today = {k: v for k, v in manifest.items()
                          if not k.startswith(today) and not k.startswith(tomorrow)}
    if not verify_decompression(scroll_out, expected_non_today):
        sys.exit(1)
    log("Phase 1: Scroll decompress verification passed")

    # Phase 2: Incremental mode (mixed algorithms)
    log("=" * 50)
    log("Phase 2: Incremental mode (mixed algorithms)")
    log("=" * 50)

    shutil.rmtree(target_dir, ignore_errors=True)
    batch_size = FILES_PER_DATE // 2
    all_manifest = {}

    yesterday = DATES[1]
    prefix = f"{yesterday}_"

    # Batch 1: zstd
    for idx in range(batch_size):
        size = LARGE_FILE_SIZE if idx == 0 else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{prefix}test_{idx:02d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}

    fn = f"{prefix}test_gui.txt"
    path = os.path.join(target_dir, fn)
    with open(path, "w", encoding="utf-8") as f:
        f.write("A" * 2048)
    all_manifest[fn] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    fn = f"{prefix}notes.txt"
    path = os.path.join(target_dir, fn)
    with open(path, "w", encoding="utf-8") as f:
        f.write("B" * 2048)
    all_manifest[fn] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    inc_archive = os.path.join(archive_dir, "inc_archive.zip")
    run_archive(cmd_base + [
        "-t", target_dir, "-a", archive_dir, "-n", "inc_archive",
        "-m", "incremental", "-c", "zstd", "-l", "3", "-w", "4", "-L", "false",
    ])
    log("Incremental batch 1 (zstd) done")

    # Batch 2: lzma
    for idx in range(batch_size, FILES_PER_DATE):
        size = random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{prefix}test_{idx:02d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}

    run_archive(cmd_base + [
        "-t", target_dir, "-a", archive_dir, "-n", "inc_archive",
        "-m", "incremental", "-c", "lzma", "-l", "3", "-w", "4", "-L", "false",
    ])
    log("Incremental batch 2 (lzma) done")

    # Batch 3: bzip2 (tomorrow date)
    tomorrow_str = DATES[3]
    tp = f"{tomorrow_str}_"
    for idx in range(FILES_PER_DATE):
        size = LARGE_FILE_SIZE if idx == 0 else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{tp}test_{idx:02d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}

    run_archive(cmd_base + [
        "-t", target_dir, "-a", archive_dir, "-n", "inc_archive",
        "-m", "incremental", "-c", "bzip2", "-l", "3", "-w", "4", "-L", "false",
    ])
    log("Incremental batch 3 (bzip2) done")

    inc_out = decompress_dir + "_inc"
    os.makedirs(inc_out, exist_ok=True)
    run_archive(cmd_base + ["-d", inc_archive, "-o", inc_out, "-L", "false"])
    if not verify_decompression(inc_out, all_manifest):
        sys.exit(1)
    log("Phase 2: Incremental (mixed algorithms) verification passed")

    # Phase 3: Standalone decompress test
    log("=" * 50)
    log("Phase 3: Standalone decompress test")
    log("=" * 50)

    shutil.rmtree(decompress_dir, ignore_errors=True)
    os.makedirs(decompress_dir, exist_ok=True)
    for z in scroll_zips:
        run_archive(cmd_base + ["-d", str(z), "-o", decompress_dir, "-L", "false"])
    if not verify_decompression(decompress_dir, expected_non_today):
        sys.exit(1)
    log("Phase 3: Standalone decompress passed")

    log("=" * 50)
    log("ALL E2E TESTS PASSED")
    log("=" * 50)


if __name__ == "__main__":
    main()

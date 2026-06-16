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
FILES_PER_DATE_MIN = 10
FILES_PER_DATE_MAX = 30
DATES = [
    (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),  # day-before-yesterday
    (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),  # yesterday
    datetime.now().strftime("%Y-%m-%d"),                         # today
    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),   # tomorrow
]
LARGE_FILE_MIN = 256 * 1024 * 1024  # 256MB
LARGE_FILE_MAX = 512 * 1024 * 1024  # 512MB
SMALL_FILE_MIN = 10 * 1024          # 10KB
SMALL_FILE_MAX = 1 * 1024 * 1024    # 1MB
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

    Each date gets a random count [FILES_PER_DATE_MIN, FILES_PER_DATE_MAX]
    of .log files (1 large 300MB-1GB + rest 1MB-256MB),
    a matching number of _gui.txt files, plus notes.txt.
    Also creates an 'error' folder.

    Returns: {filename: {"sha256": ..., "size": ...}, ...}
    """
    os.makedirs(target_dir, exist_ok=True)
    manifest = {}

    for date_str in DATES:
        prefix = f"{date_str}_"
        n_files = random.randint(FILES_PER_DATE_MIN, FILES_PER_DATE_MAX)
        large_idx = random.randint(0, n_files - 1)  # which index gets the large file
        large_size = random.randint(LARGE_FILE_MIN, LARGE_FILE_MAX)

        for idx in range(n_files):
            size = large_size if idx == large_idx else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
            filename = f"{prefix}test_{idx:03d}.log"
            path = os.path.join(target_dir, filename)
            with open(path, "wb") as f:
                f.write(random_content(size))
            manifest[filename] = {"sha256": sha256_file(path), "size": size}

        # matching number of _gui.txt files
        for idx in range(n_files):
            gui_name = f"{prefix}test_{idx:03d}_gui.txt"
            path = os.path.join(target_dir, gui_name)
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join(random.choice(string.ascii_letters) for _ in range(1024)))
            manifest[gui_name] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

        # extra notes.txt
        txt_name = f"{prefix}notes.txt"
        path = os.path.join(target_dir, txt_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("test notes\n" * 100)
        manifest[txt_name] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    _make_error_folder(target_dir)

    log(f"Test data generated: {target_dir}  ({len(manifest)} files)")
    return manifest


def run_archive(args: list, cwd: str = None, timeout: int = 1800, stdin: bytes = None) -> subprocess.CompletedProcess:
    """Run archive subprocess and print its output."""
    log(f"Run: {' '.join(args)}")
    result = subprocess.run(
        args,
        cwd=cwd or os.getcwd(),
        capture_output=True,
        timeout=timeout,
        input=stdin,
    )
    # Write stdout/stderr to console bypassing Python's encoding layer
    # (sys.stdout uses cp1252 on Windows CI runners, which chokes on UTF-8)
    try:
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.buffer.write(result.stderr)
    except OSError:
        # Fallback for edge cases
        sys.stdout.write(result.stdout.decode("utf-8", errors="replace"))
        sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()
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


def _generate_small_test_set(target_dir: str) -> dict:
    """Generate a small batch of test files (no 500MB) for config-file test."""
    manifest = {}
    yesterday = DATES[1]
    prefix = f"{yesterday}_"
    for idx in range(4):
        size = random.randint(1024, 128 * 1024)
        fn = f"{prefix}test_{idx:02d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        manifest[fn] = {"sha256": sha256_file(path), "size": size}
    _make_error_folder(target_dir)
    log(f"Small test data generated: {target_dir} ({len(manifest)} files)")
    return manifest


def _make_error_folder(target_dir: str) -> None:
    """Create a mock error folder with a crash log."""
    error_dir = os.path.join(target_dir, "error")
    os.makedirs(error_dir, exist_ok=True)
    err_file = os.path.join(error_dir, "crash.log")
    with open(err_file, "wb") as f:
        f.write(b"ERROR CONTENT\n" * 100)


def _write_config_ini(path: str, target_dir: str, archive_dir: str) -> None:
    """Write a minimal valid config.ini."""
    content = f"""[settings]
target_folder = {target_dir}
archive_folder = {archive_dir}

[zip]
archive_name_format = 存档.zip
compression_algorithm = zstd
compression_level = 9
archive_mode = scroll
max_workers = 1

[log]
save_logs = false
log_folder = logs
max_log_files = 15
log_level = INFO
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_zip_slip_zip(path: str) -> None:
    """Create a malicious ZIP with a ../ path traversal entry (Zip Slip test)."""
    import zipfile
    info = zipfile.ZipInfo("../escaped.txt")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(info, b"ESCAPED CONTENT\n")


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
        "-l", "15",
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
    os.makedirs(target_dir, exist_ok=True)
    _make_error_folder(target_dir)
    all_manifest = {}

    yesterday = DATES[1]
    prefix = f"{yesterday}_"
    total_files = random.randint(FILES_PER_DATE_MIN, FILES_PER_DATE_MAX)
    large_idx = random.randint(0, total_files - 1)
    large_size = random.randint(LARGE_FILE_MIN, LARGE_FILE_MAX)
    batch1_end = total_files // 2
    batch2_end = total_files

    # Batch 1: zstd (first half)
    for idx in range(batch1_end):
        size = large_size if idx == large_idx else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{prefix}test_{idx:03d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}

    inc_archive = os.path.join(archive_dir, "inc_archive.zip")
    run_archive(cmd_base + [
        "-t", target_dir, "-a", archive_dir, "-n", "inc_archive",
        "-m", "incremental", "-c", "zstd", "-l", "15", "-w", "4", "-L", "false",
    ])
    log("Incremental batch 1 (zstd) done")

    # Batch 2: lzma (second half) + gui + notes
    for idx in range(batch1_end, batch2_end):
        size = large_size if idx == large_idx else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{prefix}test_{idx:03d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}

    # gui files
    for idx in range(total_files):
        fn = f"{prefix}test_{idx:03d}_gui.txt"
        path = os.path.join(target_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            f.write("A" * 1536)
        all_manifest[fn] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    fn = f"{prefix}notes.txt"
    path = os.path.join(target_dir, fn)
    with open(path, "w", encoding="utf-8") as f:
        f.write("B" * 2048)
    all_manifest[fn] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    run_archive(cmd_base + [
        "-t", target_dir, "-a", archive_dir, "-n", "inc_archive",
        "-m", "incremental", "-c", "lzma", "-l", "19", "-w", "4", "-L", "false",
    ])
    log("Incremental batch 2 (lzma) done")


    # Batch 3: bzip2 (tomorrow date)
    tomorrow_str = DATES[3]
    tp = f"{tomorrow_str}_"
    n3 = random.randint(FILES_PER_DATE_MIN, FILES_PER_DATE_MAX)
    large_idx3 = random.randint(0, n3 - 1)
    large_size3 = random.randint(LARGE_FILE_MIN, LARGE_FILE_MAX)
    for idx in range(n3):
        size = large_size3 if idx == large_idx3 else random.randint(SMALL_FILE_MIN, SMALL_FILE_MAX)
        fn = f"{tp}test_{idx:03d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        all_manifest[fn] = {"sha256": sha256_file(path), "size": size}

    for idx in range(n3):
        fn = f"{tp}test_{idx:03d}_gui.txt"
        path = os.path.join(target_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            f.write("C" * 1536)
        all_manifest[fn] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    run_archive(cmd_base + [
        "-t", target_dir, "-a", archive_dir, "-n", "inc_archive",
        "-m", "incremental", "-c", "bzip2", "-l", "9", "-w", "4", "-L", "false",
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

    # Phase 4: Config-file-driven mode (no -t/-a on CLI)
    log("=" * 50)
    log("Phase 4: Config-file-driven mode (no -t/-a)")
    log("=" * 50)

    cfg_target = os.path.join(archive_dir, "cfg_target")
    cfg_arcdir = os.path.join(archive_dir, "cfg_archive")
    for d in [cfg_target, cfg_arcdir]:
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(cfg_target, exist_ok=True)
    os.makedirs(cfg_arcdir, exist_ok=True)

    # Write a config.ini that points to the test directories
    config_ini = "config.ini"
    _write_config_ini(config_ini, cfg_target, cfg_arcdir)
    log(f"Written {config_ini} -> target={cfg_target} archive={cfg_arcdir}")

    cfg_manifest = _generate_small_test_set(cfg_target)
    # Run without -t and -a — must pick up target/archive from config.ini
    run_archive(cmd_base + [
        "-m", "scroll",
        "-c", "zstd",
        "-l", "15",
        "-w", "4",
        "-L", "false",
    ])
    try:
        os.unlink(config_ini)
    except OSError:
        pass
    log(f"Removed {config_ini}")

    cfg_zips = sorted(Path(cfg_arcdir).glob("*.zip"))
    if not cfg_zips:
        log("FAIL: config-file-driven scroll mode did not create archive")
        sys.exit(1)
    log(f"Config-mode archives: {[z.name for z in cfg_zips]}")

    cfg_out = decompress_dir + "_cfg"
    os.makedirs(cfg_out, exist_ok=True)
    for z in cfg_zips:
        run_archive(cmd_base + ["-d", str(z), "-o", cfg_out, "-L", "false"])
    if not verify_decompression(cfg_out, cfg_manifest):
        sys.exit(1)
    log("Phase 4: Config-file-driven mode passed")

    # Phase 5: LZMA level 9 scroll mode
    log("=" * 50)
    log("Phase 5: LZMA level 9 scroll mode")
    log("=" * 50)

    shutil.rmtree(target_dir, ignore_errors=True)
    os.makedirs(target_dir, exist_ok=True)
    _make_error_folder(target_dir)

    lzma_manifest = {}
    lzma_prefix = f"{DATES[1]}_"
    n_lzma = random.randint(FILES_PER_DATE_MIN, FILES_PER_DATE_MAX)
    for idx in range(n_lzma):
        size = random.randint(SMALL_FILE_MIN, LARGE_FILE_MIN)
        fn = f"{lzma_prefix}test_{idx:03d}.log"
        path = os.path.join(target_dir, fn)
        with open(path, "wb") as f:
            f.write(random_content(size))
        lzma_manifest[fn] = {"sha256": sha256_file(path), "size": size}
    for idx in range(n_lzma):
        fn = f"{lzma_prefix}test_{idx:03d}_gui.txt"
        path = os.path.join(target_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            f.write("D" * 1536)
        lzma_manifest[fn] = {"sha256": sha256_file(path), "size": os.path.getsize(path)}

    lzma_arcdir = os.path.join(archive_dir, "lzma_scroll")
    os.makedirs(lzma_arcdir, exist_ok=True)
    run_archive(cmd_base + [
        "-t", target_dir, "-a", lzma_arcdir,
        "-n", "lzma9_test",
        "-m", "scroll", "-c", "lzma",
        "-l", "9", "-w", "4", "-L", "false",
    ])
    lzma_zips = sorted(Path(lzma_arcdir).glob("*.zip"))
    if not lzma_zips:
        log("FAIL: LZMA level 9 did not create archive")
        sys.exit(1)
    lzma_out = decompress_dir + "_lzma9"
    os.makedirs(lzma_out, exist_ok=True)
    for z in lzma_zips:
        run_archive(cmd_base + ["-d", str(z), "-o", lzma_out, "-L", "false"])
    if not verify_decompression(lzma_out, lzma_manifest):
        sys.exit(1)
    log("Phase 5: LZMA level 9 passed")

    # Phase 6: Zip Slip path traversal protection
    log("=" * 50)
    log("Phase 6: Zip Slip path traversal protection")
    log("=" * 50)

    zip_slip_zip = os.path.join(archive_dir, "zip_slip_test.zip")
    _make_zip_slip_zip(zip_slip_zip)

    # Sub-test A: no stdin → EOFError → auto-reject → exit code 1
    slip_out_a = decompress_dir + "_slip_a"
    os.makedirs(slip_out_a, exist_ok=True)
    result_a = run_archive(cmd_base + ["-d", zip_slip_zip, "-o", slip_out_a, "-L", "false"])
    if result_a.returncode != 1:
        log(f"FAIL (A): path traversal not blocked when stdin absent, exit code {result_a.returncode}")
        sys.exit(1)
    if os.path.exists(os.path.join(slip_out_a, "escaped.txt")):
        log("FAIL (A): path traversal file was extracted when stdin absent")
        sys.exit(1)
    log("Phase 6A: Auto-reject (no stdin) passed")

    # Sub-test B: pipe "y\n" → user confirms → exit code 0
    slip_out_b = decompress_dir + "_slip_b"
    os.makedirs(slip_out_b, exist_ok=True)
    result_b = run_archive(cmd_base + ["-d", zip_slip_zip, "-o", slip_out_b, "-L", "false"],
                           stdin=b"y\n")
    if result_b.returncode != 0:
        log(f"FAIL (B): confirmed path traversal not allowed, exit code {result_b.returncode}")
        sys.exit(1)
    escaped = os.path.realpath(os.path.join(slip_out_b, "..", "escaped.txt"))
    if not os.path.isfile(escaped):
        log(f"FAIL (B): path traversal file not extracted after user confirmation")
        sys.exit(1)
    with open(escaped, "rb") as f:
        if f.read().strip() != b"ESCAPED CONTENT":
            log("FAIL (B): extracted file content mismatch")
            sys.exit(1)
    try:
        os.unlink(escaped)
    except OSError:
        pass
    log("Phase 6B: User-confirmed (y) passed")

    # Sub-test C: pipe "\ny\n" → empty → re-prompt → y → exit 0
    slip_out_c = decompress_dir + "_slip_c"
    os.makedirs(slip_out_c, exist_ok=True)
    result_c = run_archive(cmd_base + ["-d", zip_slip_zip, "-o", slip_out_c, "-L", "false"],
                           stdin=b"\ny\n")
    if result_c.returncode != 0:
        log(f"FAIL (C): empty-then-y not allowed, exit code {result_c.returncode}")
        sys.exit(1)
    escaped = os.path.realpath(os.path.join(slip_out_c, "..", "escaped.txt"))
    try:
        os.unlink(escaped)
    except OSError:
        pass
    log("Phase 6C: Empty-input retry passed")

    # Sub-test D: pipe "no\n" → user rejects → exit code 1
    slip_out_d = decompress_dir + "_slip_d"
    os.makedirs(slip_out_d, exist_ok=True)
    result_d = run_archive(cmd_base + ["-d", zip_slip_zip, "-o", slip_out_d, "-L", "false"],
                           stdin=b"no\n")
    if result_d.returncode != 1:
        log(f"FAIL (D): n/no reject not honored, exit code {result_d.returncode}")
        sys.exit(1)
    log("Phase 6D: User-reject (no) passed")

    log("Phase 6: Zip Slip protection passed")

    log("=" * 50)
    log("ALL E2E TESTS PASSED")
    log("=" * 50)


if __name__ == "__main__":
    main()

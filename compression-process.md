# 压缩流程：小文件并发内存 vs 大文件流式

## 分流策略

`create_archive_generic()` 在压缩前预扫描所有文件来判断是否分流

```python
for fp in files:
    if os.path.getsize(fp) > 256MB:
        large_files.append(fp)
    else:
        small_files.append(fp)
```

| 单文件大小 | 路径                                                     |
| ---------- | -------------------------------------------------------- |
| ≤ 256 MB   | 并发内存压缩 → `writestr()` 写入 ZIP                     |
| > 256 MB   | 单线程流式压缩到临时文件 → `open().write()` 分块写入 ZIP |

两个路径在**同一个 `zipfile.ZipFile` 上下文**内顺序执行：先并发处理所有小文件，再单线程处理大文件。

---

## 小文件路径（≤ 256MB）

源文件 → 分块读入内存 → 一次性压缩 → 整块写入 ZIP

```
文件A.log ─→ read_file_chunked() ─→ bytes ─→ compress() ─→ (name, data, size) ─┐
文件B.log ─→ read_file_chunked() ─→ bytes ─→ compress() ─→ (name, data, size) ─┤  ThreadPoolExecutor
文件C.log ─→ read_file_chunked() ─→ bytes ─→ compress() ─→ (name, data, size) ─┘  并发压缩
                                                                                    │
                                                                  主线程 as_completed 循环:
                                                                     writestr() 写入 ZIP
                                                                     del data 释放内存
```

### 特征

| 项目         | 描述                                             |
| ------------ | ------------------------------------------------ |
| 并发         | `ThreadPoolExecutor`，`max_workers` 个线程       |
| 结果存放     | Python 进程内存（`bytes` 对象）                  |
| 写入 ZIP     | `zipf.writestr()` 一次性整块写入                 |
| 峰值内存     | ≈ `max_workers` × 最大单文件（≤256MB）压缩后大小 |
| 每个文件 I/O | 读 1 次                                          |

---

## 大文件路径（> 256MB）

源文件 → 流式压缩 → 临时文件 → 读回 → 分块写入 ZIP entry → 删除临时文件

```
文件.log ─→ chunk read ─→ compressor.compress(chunk) ─→ 临时文件 (%TEMP%\alas_large_xxx)
                                                              │
                                                   读取临时文件
                                                              │
                                              zipf.open(zinfo, 'w').write(chunk)
                                                              │
                                                       ZIP entry
                                                              │
                                                      删除临时文件
```

### 特征

| 项目         | 描述                                                |
| ------------ | --------------------------------------------------- |
| 并发         | 单线程顺序（避免多线程同时写 ZIP）                  |
| 结果存放     | 磁盘单个临时文件（用完即删）                        |
| 写入 ZIP     | `zipf.open(zinfo, 'w').write(chunk)` 分块流式       |
| 峰值内存     | ≈ 1 个临时文件大小（压缩后）                        |
| 每个文件 I/O | 读源 1 次 + 写临时 1 次 + 读临时 1 次 + 写 ZIP 1 次 |

---

## 完整流程

```
create_archive_generic(files, ...)

  ├─ 1) 增量模式：读 existing_files，过滤重复
  │
  ├─ 2) 按单文件大小分流：small_files / large_files
  │
  ├─ 3) 打开 ZIP（mode "a" 或 "w"）
  │
  ├─ 4) 小文件：ThreadPoolExecutor 并发压缩
  │      as_completed → writestr() → del data
  │
  ├─ 5) 大文件：单线程顺序
  │      _stream_compress_to_zip() → 临时文件 → ZIP entry
  │
  ├─ 6) close ZIP
  │
  ├─ 7) 校验：本次新增文件名集合 ⊆ ZIP 中文件名集合
  │
  ├─ 8) 校验通过 → 统一删除源文件
  │
  └─ 9) 输出统计（压缩率、耗时、大小）
```

---

## 对比总表

| 维度         |       小文件（≤256MB）       |   大文件（>256MB）    |
| ------------ | :--------------------------: | :-------------------: |
| 并发         |      ThreadPoolExecutor      |        单线程         |
| 压缩结果存放 |         内存 `bytes`         |     磁盘临时文件      |
| 写入 ZIP     |     `writestr()` 一次性      | `open().write()` 分块 |
| 峰值内存     | `max_workers` × 单文件压缩后 |   ≈ 1 个压缩后大小    |
| 磁盘 I/O     |            1 次读            |    2 次读 + 1 次写    |
| 源文件删除   |  ZIP close + 校验通过后统一  |          同           |

## 本质权衡

- **小文件**：并发 + 内存缓存，性能优先。多数日志文件 < 100MB，内存压力可控。
- **大文件**：流式 + 临时文件，内存可控。即使单文件达数 GB，内存也不会暴涨。
- **安全性**：两类文件均在校验通过后才删除源文件，ZIP 写入过程中不会丢数据。

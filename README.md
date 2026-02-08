> [!CAUTION]
> 本项目使用 TRAE IDE 生成与迭代

> [!WARNING]
> 请注意：由 AI 生成的代码可能有：不可预知的风险和错误！
> 如您需要直接使用本项目，请**审查并测试后再使用**；
> 如您要将本项目引用到其他项目，请**重构后再使用**。

# ALAS 日志归档工具

自动归档 AzurLaneAutoScript 日志文件的 Python 工具。

## 功能特性

- 自动删除历史 `_gui.txt` 日志文件（保留当日文件）
- 自动删除 `error` 文件夹
- 将非当日日志文件打包压缩存档
- 压缩完成后自动删除原始文件
- 支持 LZMA / BZIP2 压缩算法
- 支持高级命令行参数

## 快速开始

### 运行

首次运行时，程序会自动生成 `config.ini` 配置文件：

```bash
python ALAS_Logs_Archive.py
```

### 命令行参数

程序支持通过命令行参数覆盖配置文件中的设置：

| 参数            | 短参数 | 说明                                 | 示例                             |
| --------------- | ------ | ------------------------------------ | -------------------------------- |
| `--help`        | `-h`   | 显示帮助信息                         | `-h` 或 `--help`                 |
| `--target`      | `-t`   | 目标文件夹路径                       | `-t "C:\AzurLaneAutoScript\log"` |
| `--archive`     | `-a`   | 存档文件夹路径                       | `-a "D:\ALAS_Logs"`              |
| `--name`        | `-n`   | 存档文件名（必须包含 {date} 占位符） | `-n "备份_{date}.zip"`           |
| `--compression` | `-c`   | 压缩算法                             | `-c lzma` 或 `-c bzip2`          |
| `--level`       | `-l`   | 压缩等级                             | `-l 9`                           |
| `--mode`        | `-m`   | 存档模式（覆盖 或 追加）             | `-m overwrite` 或 `-m append`    |
| `--workers`     | `-w`   | 最大工作线程数                       | `-w 4` 或 `--workers 4`          |

**示例：**

```bash
python ALAS_Logs_Archive.py -t "C:\AzurLaneAutoScript\log" -a "D:\ALAS_Logs" -c lzma -l 9 -w 4 -m append
```

### 配置文件

| 配置项                  | 说明                                   | 默认值                      |
| ----------------------- | -------------------------------------- | --------------------------- |
| `target_folder`         | 目标文件夹路径                         | `X:\AzurLaneAutoScript\log` |
| `archive_folder`        | 存档文件夹路径                         | `X:\ALAS_Logs`              |
| `archive_name_format`   | 存档文件名（必须包含 {date} 占位符）） | `{date}_存档.zip`           |
| `compression_algorithm` | 压缩算法                               | `bzip2`                     |
| `compression_level`     | 压缩等级                               | `9`                         |
| `archive_mode`          | 存档模式（覆盖 或 追加）               | `overwrite`                 |
| `max_workers`           | 最大工作线程数                         | `1`                         |
| `chunk_size`            | 读取块大小（字节）                     | `8192`                      |
| `log_folder`            | 程序日志文件夹                         | `logs`                      |
| `max_log_files`         | 保留的最大日志文件数                   | `15`                        |
| `log_level`             | 日志等级                               | `INFO`                      |

#### 存档模式说明

`archive_mode` 用于控制当日多次运行时的存档行为：

| 模式        | 说明                                     |
| ----------- | ---------------------------------------- |
| `append`    | 追加模式，当日多次运行时创建新存档文件   |
| `overwrite` | 覆盖模式，当日多次运行时覆盖已有存档文件 |

#### 压缩算法

`compression_algorithm` 用于选择压缩算法，支持以下选项：

| 算法    | 说明                             | 压缩比 | 速度 | 适用场景               |
| ------- | -------------------------------- | ------ | ---- | ---------------------- |
| `lzma`  | LZMA2 压缩（最高压缩比）         | 最高   | 最慢 | 需要最大化节省磁盘空间 |
| `bzip2` | BZIP2 压缩（高压缩比，速度适中） | 高     | 中等 | 平衡压缩比和速度       |

#### 压缩等级

`compression_level` 用于控制压缩等级，范围 1-9：

| 等级 | 说明                       | 压缩比 | 速度 | 适用场景               |
| ---- | -------------------------- | ------ | ---- | ---------------------- |
| `1`  | 最快压缩，压缩比最低       | 最低   | 最快 | 快速处理大量文件       |
| `5`  | 中等压缩，平衡速度和压缩比 | 中等   | 中等 |                        |
| `9`  | 最高压缩，压缩比最高       | 最高   | 最慢 | 需要最大化节省磁盘空间 |

#### 存档文件名格式

`archive_name_format` 支持自定义存档文件名，使用 `{date}` 占位符表示日期。

---

## 本地打包

### 安装 PyInstaller 或 Nuitka

```bash
pip install pyinstaller
pip install nuitka
```

### 打包命令

```bash
pyinstaller -F -n "ALAS_Logs_Archive" ALAS_Logs_Archive.py

python -m nuitka --standalone --onefile --enable-plugin=anti-bloat --jobs=0 --output-filename=ALAS_Logs_Archive.exe ALAS_Logs_Archive.py
```

---

## License

[WTFPL](./LICENSE)

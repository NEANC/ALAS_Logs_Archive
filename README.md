> [!CAUTION]
> 本项目使用 TRAE IDE 生成与迭代

> [!WARNING]
> 请注意：由 AI 生成的代码可能有：不可预知的风险和错误！  
> 如您需要直接使用本项目，请**审查并测试后再使用**；  
> 如您要将本项目引用到其他项目，请**重构后再使用**。

---

# ALAS 日志归档工具

自动归档 AzurLaneAutoScript 日志文件的 Python 工具。

> [!CAUTION]
> **必须使用本工具解压归档文件**  
> 常规压缩软件提取得到的是压缩后的二进制乱码，无法直接查看。

---

> [!TIP]
>
> 1. 可将 ZIP 直接拖放到 exe 上，自动解压到当前目录
> 2. 增量模式可混合使用不同压缩算法，解压时会自动识别压缩算法
> 3. 解压时可用 `-L true` 控制日志文件输出

## 功能特性

- 自动删除历史 `_gui.txt` 文件和 `error` 文件夹
- 将非当日日志文件打包压缩存档并删除原始文件
- 支持 ZSTD / BZIP2 / LZMA 压缩算法
- 支持滚动模式（每次创建新归档）和增量模式（追加到同一 ZIP）
- 支持解压归档文件（自动识别压缩算法）
- 支持命令行参数调用并覆盖配置
- 支持拖放 ZIP 直接解压
- 压缩流程：`原始文件 → LZMA/BZIP2 压缩 → 压缩后的二进制包 → ZIP 存档`

## 快速开始

首次运行自动生成 `config.ini`，修改 `target_folder` 和 `archive_folder` 后重新运行即可。

### 命令行参数

程序支持通过命令行参数覆盖配置文件中的设置：

| 参数              | 短参数 | 说明                                | 示例                             |
| ----------------- | ------ | ----------------------------------- | -------------------------------- |
| `--help`          | `-h`   | 显示帮助信息                        |                                  |
| `--target`        | `-t`   | 目标文件夹路径                      | `-t "C:\AzurLaneAutoScript\log"` |
| `--archive`       | `-a`   | 存档文件夹路径                      | `-a "D:\ALAS_Logs"`              |
| `--name`          | `-n`   | 存档文件名（支持 {date} 占位符）    | `-n "备份_{date}.zip"`           |
| `--compression`   | `-c`   | 压缩算法：`zstd` / `lzma` / `bzip2` | `-c lzma`                        |
| `--level`         | `-l`   | 压缩等级 1-9                        | `-l 9`                           |
| `--mode`          | `-m`   | 存档模式（滚动 或 增量）            | `-m scroll` 或 `-m incremental`  |
| `--workers`       | `-w`   | 压缩时的并发线程数                  | `-w 4`                           |
| `--save-logs`     | `-L`   | 日志文件输出控制                    | `-L false`                       |
| `--decompress`    | `-d`   | 解压指定 ZIP 文件（需指定文件路径） | `-d "D:\ALAS_Logs\存档.zip"`     |
| `--output`        | `-o`   | 解压输出目录（配合 `-d`）           | `-o "E:\ALAS_存档"`              |
| `--console-level` | `-C`   | 控制台日志等级                      | `-C DEBUG`                       |

#### 示例

```powershell
# 压缩归档
.\ALAS_Logs_Archive.exe -t "X:\AzurLaneAutoScript\log" -a "X:\ALAS_Logs" -c lzma -l 9 -w 4

# 解压到指定目录
.\ALAS_Logs_Archive.exe -d "X:\ALAS_Logs\存档.zip" -o "E:\ALAS_存档"

# 解压并保存日志
.\ALAS_Logs_Archive.exe -d "存档.zip" -L true
```

### 配置文件

| 配置项                  | 说明                                | 默认值                      |
| ----------------------- | ----------------------------------- | --------------------------- |
| `target_folder`         | 目标文件夹路径                      | `X:\AzurLaneAutoScript\log` |
| `archive_folder`        | 存档文件夹路径                      | `X:\ALAS_Logs`              |
| `archive_name_format`   | 存档文件名（支持 `{date}` 占位符）  | `存档`                      |
| `compression_algorithm` | 压缩算法（lzma 或 bzip2）           | `bzip2`                     |
| `compression_level`     | 压缩等级（1-9，数字越大压缩比越高） | `9`                         |
| `archive_mode`          | 存档模式（滚动 或 增量）            | `scroll`                    |
| `max_workers`           | 压缩时的并发线程数                  | `1`                         |
| `save_logs`             | 日志文件输出控制                    | `true`                      |
| `log_folder`            | 程序日志文件夹                      | `logs`                      |
| `max_log_files`         | 保留的最大日志文件数                | `15`                        |
| `log_level`             | 日志等级                            | `INFO`                      |

#### 存档模式说明

`archive_mode` 用于控制当日多次运行时的存档行为：

| 模式          | 说明                                   |
| ------------- | -------------------------------------- |
| `scroll`      | 滚动模式，当日多次运行时创建新存档文件 |
| `incremental` | 增量模式，将文件追加到同一ZIP文件中    |

#### 压缩算法

> [!TIP]
> 增量模式可混合使用不同压缩算法，解压时会自动识别压缩算法。

`compression_algorithm` 用于选择压缩算法，支持以下选项：

| 算法    | 说明           | 压缩比 | 速度 |
| ------- | -------------- | ------ | ---- |
| `lzma`  | LZMA2 压缩     | 最高   | 最慢 |
| `bzip2` | BZIP2 压缩     | 高     | 中等 |
| `zstd`  | Zstandard 压缩 | 高     | 最快 |

#### 压缩等级

`compression_level` 用于控制压缩等级，范围 1-9：

| 等级 | 说明                       | 压缩比 | 速度 | 适用场景               |
| ---- | -------------------------- | ------ | ---- | ---------------------- |
| `1`  | 最快压缩，压缩比最低       | 最低   | 最快 | 快速处理大量文件       |
| `5`  | 中等压缩，平衡速度和压缩比 | 中等   | 中等 |                        |
| `9`  | 最高压缩，压缩比最高       | 最高   | 最慢 | 需要最大化节省磁盘空间 |

#### 存档文件名格式

`archive_name_format` 支持自定义存档文件名，使用 `{date}` 占位符表示日期位置。

#### 压缩流程说明

详见 [compression-process.md](./compression-process.md)

---

## License

[WTFPL](./LICENSE)

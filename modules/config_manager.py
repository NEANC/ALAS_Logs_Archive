#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import configparser
import logging
import os
import re
import sys
from typing import Optional

from modules.config_migration import apply_migrations

# 硬编码常量，供主入口导入
CONFIG_FILE = "config.ini"


class ConfigManager:
    """配置管理器，负责配置初始化、加载、验证"""

    DEFAULT_SECTIONS = {
        'settings': {
            'target_folder': r'X:\AzurLaneAutoScript\log',
            'archive_folder': r'X:\ALAS_Logs',
        },
        'zip': {
            'archive_name_format': '存档',
            'compression_algorithm': 'lzma',
            'compression_level': '9',
            'archive_mode': 'incremental',
            'max_workers': '1',
            'chunk_size': '8192',
        },
        'log': {
            'save_logs': 'true',
            'log_folder': 'logs',
            'max_log_files': '15',
            'log_level': 'INFO',
        },
    }

    _COMMENTS = {
        'settings.target_folder': '目标文件夹路径：需要归档的日志文件所在目录',
        'settings.archive_folder': '归档文件夹路径：生成的归档文件保存目录',
        'zip.archive_name_format': '归档文件名\n'
                                   '# - 增量模式：直接使用该值作为文件名（自动添加 .zip 扩展名）\n'
                                   '# - 滚动模式：如果包含 {date} 占位符会被替换为实际日期，否则在文件名前添加日期前缀',
        'zip.compression_algorithm': '压缩算法：支持的压缩算法\n'
                                     '# zstd：压缩速度最快，压缩率适中\n'
                                     '# bzip2：压缩速度较快，压缩率适中\n'
                                     '# lzma：压缩率较高，压缩速度较慢',
        'zip.compression_level': '压缩等级：压缩算法的压缩等级（1-9）',
        'zip.archive_mode': '归档模式：控制归档文件的创建方式\n'
                            '# scroll：滚动模式，当日多次运行时创建新归档文件\n'
                            '# incremental：增量模式，将文件追加到同一 ZIP 文件中',
        'zip.max_workers': '最大工作线程数：压缩文件时使用的线程数',
        'zip.chunk_size': '读取块大小：文件读写时的块大小（字节）',
        'log.save_logs': '是否保存日志文件：控制是否将程序日志保存到本地文件',
        'log.log_folder': '日志保存文件夹',
        'log.max_log_files': '最大日志文件数：保留的程序日志文件的最大数量',
        'log.log_level': '日志等级：控制台输出的日志记录等级（日志文件始终记录完整输出）',
    }

    @classmethod
    def _build_default_config(cls) -> str:
        """从 DEFAULT_SECTIONS + _COMMENTS 生成默认配置文件内容

        Returns:
            str: 默认配置文件内容
        """
        lines = []
        for section, keys in cls.DEFAULT_SECTIONS.items():
            lines.append(f'[{section}]')
            for key, val in keys.items():
                comment = cls._COMMENTS.get(f'{section}.{key}', '')
                if comment:
                    for cl in comment.split('\n'):
                        lines.append(f'# {cl}')
                lines.append(f'{key} = {val}')
            lines.append('')
        return '\n'.join(lines)

    def __init__(self, config_file: str, logger: Optional[logging.Logger] = None):
        """初始化配置管理器

        Args:
            config_file: 配置文件路径
            logger: 日志记录器（可选，未提供时使用 print 输出）
        """
        self.config_file = config_file
        self._logger = logger
        self.config = configparser.ConfigParser(strict=False)

        self.target_folder = ''
        self.archive_folder = ''
        self.archive_name_format = '存档'
        self.compression_algorithm = 'lzma'
        self.compression_level = 9
        self.archive_mode = 'scroll'
        self.max_workers = 1
        self.chunk_size = 8192
        self.save_logs = True
        self.log_folder = 'logs'
        self.max_log_files = 15
        self.log_level = logging.INFO

    def _log(self, level: str, msg: str) -> None:
        """统一日志输出：有 logger 时用 logger，否则用 print

        Args:
            level: 日志等级（debug/info/warning/error/critical）
            msg: 日志消息
        """
        if self._logger:
            log_func = getattr(self._logger, level, self._logger.info)
            log_func(msg)
        else:
            prefix = {'info': '', 'warning': '警告: ', 'error': '错误: ', 'critical': '致命: '}
            print(f"{prefix.get(level, '')}{msg}")

    def set_logger(self, logger: logging.Logger) -> None:
        """在日志系统初始化后设置 logger

        Args:
            logger: 日志记录器
        """
        self._logger = logger

    def _generate_default_config(self) -> None:
        """生成默认配置文件并退出"""
        default_config = self._build_default_config()
        tmp_path = self.config_file + '.tmp'
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(default_config)
            os.replace(tmp_path, self.config_file)
            self._log('info', f"已生成默认配置文件: {self.config_file}")
            self._log('info', "请修改配置文件中的 target_folder 和 archive_folder 后重新运行程序")
            sys.exit(0)
        except OSError as e:
            self._log('error', f"生成配置文件失败: {e}")
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            sys.exit(1)

    def _regenerate_config_file(self) -> None:
        """重建配置文件，保留所有已有值，仅补充缺失的模板键"""
        lines = []
        for section in self.config.sections():
            if section.upper() == 'DEFAULT' or section == '__migrations__':
                continue
            lines.append(f'[{section}]')
            template = self.DEFAULT_SECTIONS.get(section, {})
            written_keys = set()

            for key, default_val in template.items():
                written_keys.add(key)
                comment = self._COMMENTS.get(f'{section}.{key}', '')
                if comment:
                    for cl in comment.split('\n'):
                        lines.append(f'# {cl}')
                current = self.config.get(section, key, fallback=default_val)
                lines.append(f'{key} = {current}')

            for key, val in self.config.items(section):
                if key not in written_keys and key not in (self.config.defaults() or {}):
                    if not key.strip():
                        continue
                    lines.append(f'{key} = {val}')

            lines.append('')

        for section, keys in self.DEFAULT_SECTIONS.items():
            if not self.config.has_section(section):
                lines.append(f'[{section}]')
                for key, val in keys.items():
                    comment = self._COMMENTS.get(f'{section}.{key}', '')
                    if comment:
                        for cl in comment.split('\n'):
                            lines.append(f'# {cl}')
                    lines.append(f'{key} = {val}')
                lines.append('')

        tmp_path = self.config_file + '.tmp'
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            os.replace(tmp_path, self.config_file)
        except OSError as e:
            self._log('error', f"写入配置文件失败: {e}")
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _sanitize_config_file(self) -> None:
        """逐行清理损坏行：空键值行删除，无 = 行注释掉"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except OSError:
            return

        fixed = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or stripped.startswith(';'):
                new_lines.append(line)
                continue
            if re.match(r'^\[.+\]$', stripped):
                new_lines.append(line)
                continue
            if '=' not in stripped:
                new_lines.append(f'# [已修复] {line}')
                fixed = True
                continue
            key, sep, val = stripped.partition('=')
            if not key.strip():
                fixed = True
                continue
            new_lines.append(line)

        if fixed:
            tmp_path = self.config_file + '.tmp'
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                os.replace(tmp_path, self.config_file)
            except OSError as e:
                self._log('error', f"修复配置文件失败: {e}")
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _recover_orphan_keys(self) -> bool:
        """将误归属的模板键还原到正确的节，返回是否做了修改

        Returns:
            bool: 是否做了修改
        """
        changed = False
        defaults = self.config.defaults()
        if defaults:
            for key, val in list(defaults.items()):
                for section, keys in self.DEFAULT_SECTIONS.items():
                    if (key in keys and self.config.has_section(section)
                            and not self.config.has_option(section, key)):
                        self.config.set(section, key, val)
                        self.config.remove_option('DEFAULT', key)
                        self._log('warning', f"键 {key} 已还原到 [{section}]")
                        changed = True
                        break

        for source_section in list(self.config.sections()):
            if source_section.upper() == 'DEFAULT' or source_section == '__migrations__':
                continue
            template = self.DEFAULT_SECTIONS.get(source_section, {})
            for key, val in list(self.config.items(source_section)):
                if not key.strip():
                    continue
                if key in (self.config.defaults() or {}):
                    continue
                if key in template:
                    continue
                for tgt_section, tgt_keys in self.DEFAULT_SECTIONS.items():
                    if (key in tgt_keys and tgt_section != source_section
                            and self.config.has_section(tgt_section)
                            and not self.config.has_option(tgt_section, key)):
                        self.config.set(tgt_section, key, val)
                        self.config.remove_option(source_section, key)
                        self._log('warning', f"键 {key}={val} 从 [{source_section}] 还原到 [{tgt_section}]")
                        changed = True
                        break
        return changed

    def load(self) -> 'ConfigManager':
        """加载配置文件并填充属性

        Returns:
            ConfigManager: 返回自身以支持链式调用
        """
        if not os.path.exists(self.config_file):
            self._log('info', "配置文件不存在，将生成默认配置文件")
            self._generate_default_config()

        for pass_num in range(3):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config.read_file(f)
                break
            except configparser.Error as e:
                if pass_num == 0:
                    self._log('warning', f"配置文件解析错误，正在尝试修复: {e}")
                    self._sanitize_config_file()
                elif pass_num == 1:
                    self._log('critical', "修复失败，将重新生成配置文件")
                    self._generate_default_config()
                else:
                    self._log('critical', f"配置文件无法修复: {e}")
                    self._log('critical', f"配置文件 {self.config_file} 已损坏且无法自动修复。")
                    self._log('critical', "请检查文件内容或删除后重新运行软件以生成默认配置。")
                    raise SystemExit(1)

        migrated = apply_migrations(self.config, self._logger)
        dirty = migrated

        for section in self.DEFAULT_SECTIONS:
            if not self.config.has_section(section):
                self.config.add_section(section)
                dirty = True

        orphaned = self._recover_orphan_keys()

        for section, keys in self.DEFAULT_SECTIONS.items():
            for key, val in keys.items():
                if not self.config.has_option(section, key):
                    self.config.set(section, key, val)
                    dirty = True
                    self._log('warning', f"配置节: [{section}] 缺少键: {key}，已自动补充默认值")

        if dirty or orphaned:
            self._regenerate_config_file()

        # 解析各节为属性
        # [settings]
        self.target_folder = self._get_str('settings', 'target_folder')
        self.archive_folder = self._get_str('settings', 'archive_folder')

        # [zip]
        self.archive_name_format = self._get_str('zip', 'archive_name_format', '存档')
        self.compression_algorithm = self._get_str('zip', 'compression_algorithm', 'lzma').lower()
        self.compression_level = self._get_int('zip', 'compression_level', 9)
        self.archive_mode = self._get_str('zip', 'archive_mode', 'scroll').lower()
        self.max_workers = self._get_int('zip', 'max_workers', 1)
        self.chunk_size = self._get_int('zip', 'chunk_size', 8192)

        # [log]
        self.save_logs = self._get_bool('log', 'save_logs', True)
        self.log_folder = self._get_str('log', 'log_folder', 'logs')
        self.max_log_files = self._get_int('log', 'max_log_files', 15)

        log_level_str = self._get_str('log', 'log_level', 'INFO').upper()
        log_level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL,
        }
        self.log_level = log_level_map.get(log_level_str, logging.INFO)

        return self

    def _get_str(self, section: str, option: str, fallback: str = '') -> str:
        """获取字符串配置值并去除引号

        Args:
            section: 节名
            option: 键名
            fallback: 默认值

        Returns:
            str: 配置值
        """
        if not self.config.has_section(section) or not self.config.has_option(section, option):
            return fallback
        value = self.config.get(section, option, fallback=fallback)
        if isinstance(value, str):
            value = value.strip('"\'')
        return value

    def _get_int(self, section: str, option: str, fallback: int = 0) -> int:
        """获取整数配置值

        Args:
            section: 节名
            option: 键名
            fallback: 默认值

        Returns:
            int: 配置值
        """
        value = self._get_str(section, option, str(fallback))
        try:
            return int(value)
        except (ValueError, TypeError):
            return fallback

    def _get_bool(self, section: str, option: str, fallback: bool = False) -> bool:
        """获取布尔配置值

        Args:
            section: 节名
            option: 键名
            fallback: 默认值

        Returns:
            bool: 配置值
        """
        value = self._get_str(section, option, str(fallback)).lower()
        return value in ('true', 'yes', '1')

    def validate(self) -> bool:
        """验证配置文件是否合法

        Returns:
            bool: 配置是否合法
        """
        if not self.target_folder:
            self._log('error', "配置错误: target_folder 未配置")
            return False

        if not self.archive_folder:
            self._log('error', "配置错误: archive_folder 未配置")
            return False

        if self.compression_algorithm not in ('zstd', 'bzip2', 'lzma'):
            self._log('error', f"配置错误: 不支持的压缩算法 {self.compression_algorithm}")
            return False

        if not 1 <= self.compression_level <= 9:
            self._log('error', f"配置错误: 无效的压缩等级 {self.compression_level}")
            return False

        if self.archive_mode not in ('scroll', 'incremental'):
            self._log('error', f"配置错误: 无效的归档模式 {self.archive_mode}")
            return False

        if self.max_workers < 1:
            self._log('error', f"配置错误: 无效的工作线程数 {self.max_workers}")
            return False

        self._log('info', "配置验证通过")
        return True

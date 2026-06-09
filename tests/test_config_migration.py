#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 modules.config_migration 模块"""

import configparser
import logging

from modules.config_migration import (
    _apply_rename_key,
    _apply_rename_value,
    _get_applied_migrations,
    _mark_applied,
    apply_migrations,
    MIGRATION_MARKER,
    MIGRATION_HANDLERS,
)


class TestRenameKey:
    """_apply_rename_key 函数测试"""

    def test_rename_key_success(self):
        """基本重命名成功"""
        config = configparser.ConfigParser()
        config.add_section("test")
        config.set("test", "old_name", "my_value")
        result = _apply_rename_key(config, "test", "old_name", "new_name")
        assert result is True
        assert config.get("test", "new_name") == "my_value"
        assert not config.has_option("test", "old_name")

    def test_rename_key_section_missing(self):
        """目标节不存在时应返回False"""
        config = configparser.ConfigParser()
        result = _apply_rename_key(config, "missing", "old", "new")
        assert result is False

    def test_rename_key_old_key_missing(self):
        """旧键不存在时应返回False"""
        config = configparser.ConfigParser()
        config.add_section("test")
        result = _apply_rename_key(config, "test", "nonexistent", "new")
        assert result is False

    def test_rename_key_new_already_exists(self):
        """新键已存在时不覆盖，只删除旧键"""
        config = configparser.ConfigParser()
        config.add_section("test")
        config.set("test", "old_name", "old_value")
        config.set("test", "new_name", "existing_value")
        result = _apply_rename_key(config, "test", "old_name", "new_name")
        assert result is True
        assert config.get("test", "new_name") == "existing_value"
        assert not config.has_option("test", "old_name")


class TestRenameValue:
    """_apply_rename_value 函数测试"""

    def test_rename_value_success(self):
        """值重命名成功"""
        config = configparser.ConfigParser()
        config.add_section("test")
        config.set("test", "status", "old_val")
        result = _apply_rename_value(config, "test", "status", "old_val", "new_val")
        assert result is True
        assert config.get("test", "status") == "new_val"

    def test_rename_value_no_match(self):
        """值不匹配时返回False"""
        config = configparser.ConfigParser()
        config.add_section("test")
        config.set("test", "status", "other_val")
        result = _apply_rename_value(config, "test", "status", "old_val", "new_val")
        assert result is False
        assert config.get("test", "status") == "other_val"

    def test_rename_value_section_missing(self):
        """节不存在时返回False"""
        config = configparser.ConfigParser()
        result = _apply_rename_value(config, "missing", "key", "a", "b")
        assert result is False

    def test_rename_value_key_missing(self):
        """键不存在时返回False"""
        config = configparser.ConfigParser()
        config.add_section("test")
        result = _apply_rename_value(config, "test", "missing", "a", "b")
        assert result is False


class TestMigrationTracking:
    """迁移跟踪功能测试"""

    def test_get_applied_empty(self):
        """无迁移记录时返回空集合"""
        config = configparser.ConfigParser()
        assert _get_applied_migrations(config) == set()

    def test_mark_and_get_migrations(self):
        """标记和获取迁移记录"""
        config = configparser.ConfigParser()
        _mark_applied(config, 1)
        _mark_applied(config, 3)
        applied = _get_applied_migrations(config)
        assert applied == {1, 3}

    def test_migration_marker_section(self):
        """验证迁移标记节名正确"""
        config = configparser.ConfigParser()
        _mark_applied(config, 1)
        assert config.has_section(MIGRATION_MARKER)
        assert config.get(MIGRATION_MARKER, "1") == "done"


class TestApplyMigrations:
    """apply_migrations 集成测试"""

    def test_apply_migrations_noop(self):
        """无迁移时返回False且无变更"""
        config = configparser.ConfigParser()
        logger = logging.getLogger("test")
        changed = apply_migrations(config, logger)
        assert changed is False

    def test_apply_migrations_idempotent(self):
        """已应用的迁移不重复执行"""
        config = configparser.ConfigParser()
        logger = logging.getLogger("test")
        # 先标记迁移1已应用
        _mark_applied(config, 1)
        changed = apply_migrations(config, logger)
        assert changed is False


class TestMigrationHandlers:
    """迁移处理器字典验证"""

    def test_handlers_registered(self):
        """验证两种handler类型均已注册"""
        assert 'rename_key' in MIGRATION_HANDLERS
        assert 'rename_value' in MIGRATION_HANDLERS
        assert callable(MIGRATION_HANDLERS['rename_key'])
        assert callable(MIGRATION_HANDLERS['rename_value'])

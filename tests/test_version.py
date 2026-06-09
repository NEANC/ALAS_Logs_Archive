#!/usr/bin/env python3
# -_- coding: utf-8 -_

"""测试 modules.version 模块"""

import re
import sys
from io import StringIO

from modules.version import VERSION, print_info


class TestVersion:
    """版本模块测试"""

    def test_version_format(self):
        """验证版本号格式为 vX.Y.Z"""
        assert re.match(r'^v\d+\.\d+\.\d+$', VERSION), f"版本号格式不正确: {VERSION}"

    def test_print_info_output(self):
        """验证 print_info 输出包含关键信息"""
        captured = StringIO()
        sys.stdout = captured
        try:
            print_info()
        finally:
            sys.stdout = captured

        output = captured.getvalue()
        assert "ALAS Logs Archive" in output
        assert "ALAS 日志归档工具" in output
        assert VERSION in output
        assert "WTFPL" in output

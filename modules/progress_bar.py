#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import sys
from colorama import Style, Fore
from tqdm import tqdm

# ── 进度条颜色常量 ──
BAR_FG = Style.BRIGHT + Fore.WHITE              # 进度条主体：白色加粗
BAR_AUX = Fore.LIGHTBLACK_EX                    # 辅助信息：浅灰色
BAR_OK = Fore.LIGHTGREEN_EX                     # 完成状态：亮绿色
BAR_WARN = Style.BRIGHT + Fore.LIGHTYELLOW_EX   # 警告状态：亮黄色加粗
BAR_ERR = Style.BRIGHT + Fore.LIGHTRED_EX       # 错误/失败：亮红色加粗
BAR_RST = Style.RESET_ALL                       # 颜色重置

# ── 进度条格式 ──
BAR_FORMAT = (
    '{desc}'                                    # 任务名称（默认终端色）
    + BAR_FG + '{bar}' + BAR_RST + ' '          # 进度条（白色加粗）
    + BAR_AUX                                   # 辅助信息开始（浅灰色）
    + '{n_fmt}/{total_fmt} | ETA: {remaining} | {rate_fmt}'
    + BAR_RST                                   # 辅助信息结束
)


def make_byte_bar(total_bytes: int, desc: str) -> tqdm:
    """创建字节级 tqdm 进度条

    Args:
        total_bytes: 总字节数
        desc: 任务名称

    Returns:
        tqdm: 进度条实例
    """
    return tqdm(
        total=total_bytes,
        desc=desc,
        unit='B',
        unit_scale=True,
        unit_divisor=1024,
        bar_format=BAR_FORMAT,
        file=sys.stdout,
        dynamic_ncols=True,
        leave=False,
    )


def format_error(desc: str, reason: str) -> str:
    """格式化错误消息（亮红色加粗）"""
    return f"{BAR_ERR}{desc}: 失败 {reason}{BAR_RST}"


def format_warn(msg: str) -> str:
    """格式化警告消息（亮黄色加粗）"""
    return f"{BAR_WARN}{msg}{BAR_RST}"

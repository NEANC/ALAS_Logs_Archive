#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""下载管理器：带 tqdm 进度条的 HTTP 文件下载，注入 SelfUpdater。"""

import logging
import requests

from pathlib import Path

from modules.progress_bar import make_byte_bar, format_error


def download_with_progress(url: str, save_path: str, proxy: str,
                           logger: logging.Logger) -> bool:
    """
    下载文件并显示 tqdm 进度条

    Args:
        url: 下载 URL
        save_path: 保存路径
        proxy: 代理地址（空字符串表示无代理）
        logger: 日志记录器

    Returns:
        bool: 下载是否成功
    """
    file_name = Path(url).name
    try:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        headers = {'User-Agent': 'ALAS_Logs_Archive'}
        proxies = {'http': proxy, 'https': proxy} if proxy else None

        with requests.get(url, headers=headers, proxies=proxies,
                          timeout=120, stream=True) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0

            with open(save_path, 'wb') as f:
                with make_byte_bar(total_size, desc=f"下载 {file_name}") as pbar:
                    for chunk in response.iter_content(chunk_size=1048576):
                        if chunk:
                            f.write(chunk)
                            chunk_len = len(chunk)
                            downloaded += chunk_len
                            if total_size > 0:
                                pbar.update(chunk_len)

            logger.info(f"下载完成: {file_name}")
            return True
    except requests.RequestException as e:
        print(format_error(f"下载 {file_name}", str(e)))
        logger.error(f"下载失败: {e}")
        return False

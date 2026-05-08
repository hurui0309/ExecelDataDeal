"""xlrd patch — 容忍部分 .xls 文件中的非法 UTF-16 代理对。

部分 VBA 保护的 .xls 文件在 SST/FORMAT 记录中含有非法 UTF-16 序列，
原生 xlrd.unpack_unicode 会抛 UnicodeDecodeError。这里替换为 errors='replace' 版本。

使用方式：在程序入口（main.py）顶部一次性 `import services.xlrd_patch` 即可，
其它模块无需重复 patch。
"""

from __future__ import annotations

import logging
import struct as _struct

logger = logging.getLogger("datadeal")

_PATCHED = False


def apply() -> bool:
    """幂等地应用 xlrd patch。已 patch 过则直接返回 True。"""
    global _PATCHED
    if _PATCHED:
        return True

    try:
        import xlrd.biffh as _biffh
    except ImportError:
        logger.debug("xlrd 未安装，跳过 patch")
        return False

    def _patched_unpack_unicode(data, pos, lenlen=2):
        fmt = "<H" if lenlen == 2 else "<B"
        str_len = _struct.unpack(fmt, data[pos:pos + lenlen])[0]
        pos += lenlen
        if not str_len:
            return ""
        flag = data[pos]
        pos += 1
        if flag & 0x01:
            rawstrg = data[pos:pos + str_len * 2]
            return rawstrg.decode("utf_16_le", errors="replace")
        rawstrg = data[pos:pos + str_len]
        return rawstrg.decode("latin1", errors="replace")

    _biffh.unpack_unicode = _patched_unpack_unicode
    try:
        import xlrd.formatting as _fmt
        if hasattr(_fmt, "unpack_unicode"):
            _fmt.unpack_unicode = _patched_unpack_unicode
    except Exception:
        pass

    _PATCHED = True
    logger.debug("xlrd unpack_unicode patched (UTF-16 tolerant)")
    return True


# 模块导入即应用 patch（保持与旧行为一致：import 即生效）
apply()

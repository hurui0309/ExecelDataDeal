"""测试启动器：把项目根目录加到 sys.path，让测试可以直接 import services / strategies / agents。

每个 test_*.py 顶部 import _bootstrap 即可。
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

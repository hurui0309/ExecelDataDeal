"""Classifier 决策输出的结构化契约。

为什么用 dataclass 而不是 pydantic？
- 项目目前没有 pydantic 依赖，dataclass 标准库够用；
- 如果将来需要更严格的字段校验，可以平滑迁到 pydantic.BaseModel
  （字段定义已经是属性形式，迁移成本很低）。

主要职责：
1. 定义 ClassifierDecision 数据结构（带默认值，覆盖 LLM 偶尔漏字段的情况）；
2. 提供 ``from_raw_dict()`` 把 LLM 解析出来的 dict 安全地转成 dataclass
   实例（脏数据/类型错误自动兜底为合法值）；
3. ``to_dict()`` 反向序列化，让 worker / orchestrator 仍可像旧版一样以 dict 访问。

合法 strategy 取值由 ``strategies.BUILTIN_STRATEGIES`` 决定，
SKIP/UNKNOWN 是兜底状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# 兜底状态，不属于真正的策略，但允许 classifier 返回
SENTINEL_STATUSES = ("SKIP", "UNKNOWN")


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class ClassifierDecision:
    """Classifier 决策结果的统一契约。"""

    strategy: str = "UNKNOWN"
    params: dict[str, Any] = field(default_factory=dict)
    table_name_hint: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    # 可选：分类器可能直接顶层给出 regions（横向 / 纵向子表）
    regions: list[dict] | None = None
    # 错误信息（解析失败时填入）
    error: str = ""

    # —— Class-level helpers ——
    @classmethod
    def from_raw_dict(cls, raw: Any, valid_strategies: set[str] | None = None
                      ) -> "ClassifierDecision":
        """从 LLM 原始 dict 构造 ClassifierDecision，做严格的字段类型校验。

        - raw 不是 dict 时，整个视为 UNKNOWN
        - strategy 不在 valid_strategies ∪ SENTINEL_STATUSES 时改为 UNKNOWN
        - params 不是 dict 时改为 {}
        - confidence 不是数字时改为 0.0，clamp 到 [0, 1]
        - regions 不是 list[dict] 时改为 None
        """
        if not isinstance(raw, dict):
            return cls(strategy="UNKNOWN",
                       error=f"classifier 返回非 dict: {type(raw).__name__}")

        strategy = str(raw.get("strategy", "UNKNOWN") or "UNKNOWN")
        allowed = set(SENTINEL_STATUSES)
        if valid_strategies:
            allowed |= set(valid_strategies)
        if strategy not in allowed:
            return cls(
                strategy="UNKNOWN",
                params={},
                table_name_hint=str(raw.get("table_name_hint", "") or ""),
                confidence=0.0,
                reasoning=str(raw.get("reasoning", "") or ""),
                error=f"非法 strategy: {strategy!r}（已强制改为 UNKNOWN）",
            )

        params = raw.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # confidence clamp 到 [0, 1]
        conf = _coerce_float(raw.get("confidence"), default=0.0)
        if conf < 0.0:
            conf = 0.0
        if conf > 1.0:
            conf = 1.0

        regions = raw.get("regions")
        if regions is not None:
            if not isinstance(regions, list) or not all(isinstance(r, dict) for r in regions):
                regions = None

        return cls(
            strategy=strategy,
            params=params,
            table_name_hint=str(raw.get("table_name_hint", "") or ""),
            confidence=conf,
            reasoning=str(raw.get("reasoning", "") or ""),
            regions=regions,
            error="",
        )

    def to_dict(self) -> dict:
        """把 dataclass 序列化为 dict，让旧代码继续能 ``decision.get(...)``。

        对 regions=None 不输出该 key（与旧版兼容：只有 LLM 给了 regions 才出现）。
        """
        d = asdict(self)
        if d.get("regions") is None:
            d.pop("regions", None)
        if not d.get("error"):
            d.pop("error", None)
        return d

    # 为兼容旧代码 ``decision.get("xxx", default)`` 写法
    def get(self, key: str, default=None):
        return self.to_dict().get(key, default)

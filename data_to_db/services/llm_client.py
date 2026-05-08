"""Service: llm_client — 统一大模型调用封装"""

import json
import re
import time
import logging
from openai import OpenAI, APIError, AuthenticationError, RateLimitError, APIConnectionError

logger = logging.getLogger("datadeal")

# 可重试的异常类型
_RETRYABLE_ERRORS = (RateLimitError, APIConnectionError)
# 不可重试的异常（如鉴权失败），但也尝试重试一次（可能是临时问题）
_MAX_RETRIES = 2
_RETRY_DELAY = 2  # 秒

# role 名称兼容映射：旧名称 → 新名称
_ROLE_MAP = {
    "reviewer": "standard",
    "general": "standard",
    "advanced": "standard",
}


class LLMClient:
    """统一大模型调用客户端"""

    def __init__(self, config: dict):
        """
        参数:
            config: 全局配置，需包含 llm.standard
        """
        llm_config = config.get("llm", {})
        self.standard_cfg = llm_config.get("standard") or llm_config.get("general", {})
        self.default_temperature = llm_config.get("temperature", 0.1)
        self.default_max_tokens = llm_config.get("max_tokens", 4096)

        # 预创建客户端
        self._standard_client = self._create_client(self.standard_cfg)

    def _create_client(self, cfg: dict) -> OpenAI:
        """根据配置创建 OpenAI 兼容客户端"""
        return OpenAI(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("api_base", ""),
        )

    def _resolve_role(self, role: str) -> str:
        """将 role 统一为 standard"""
        return _ROLE_MAP.get(role, role)

    def chat(self, role: str, messages: list, temperature: float = None,
             max_tokens: int = None) -> str:
        """
        调用大模型聊天接口。

        参数:
            role: "standard"（兼容旧值 "advanced" / "reviewer" / "general"）
            messages: 消息列表，如 [{"role": "user", "content": "..."}]
            temperature: 生成温度，默认使用配置值
            max_tokens: 最大 token 数，默认使用配置值

        返回:
            模型输出的文本内容
        """
        resolved = self._resolve_role(role)
        client = self._standard_client
        model = self.standard_cfg.get("model", "")

        temperature = temperature if temperature is not None else self.default_temperature
        max_tokens = max_tokens if max_tokens is not None else self.default_max_tokens

        t0 = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed = time.time() - t0
        content = response.choices[0].message.content
        # 推理模型（如 glm-5.1-tc）可能 content 为 None（reasoning 消耗完 token）
        if content is None:
            logger.warning(f"    [LLM] 模型返回 content 为 None (model={model}, "
                          f"finish_reason={response.choices[0].finish_reason}) "
                          f"[耗时] {elapsed:.1f}s")
            return ""
        logger.info(f"    [LLM] model={model} tokens={getattr(response.usage, 'total_tokens', '?')} "
                    f"[耗时] {elapsed:.1f}s")
        return content.strip()

    def chat_with_retry(self, role: str, messages: list, temperature: float = None,
                        max_tokens: int = None) -> str:
        """带重试的 chat 调用，对临时性 API 错误自动重试。

        - 限流 / 网络错误：最多重试 _MAX_RETRIES 次
        - 5xx 服务端错误：可重试
        - 4xx 鉴权错误：直接抛出（重试无意义）
        - 其它 APIError：直接抛出
        """
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return self.chat(role, messages, temperature, max_tokens)
            except AuthenticationError:
                # 鉴权错误重试无意义，直接抛
                raise
            except _RETRYABLE_ERRORS as e:
                last_error = e
                logger.warning(f"    [LLM] 可重试错误 (attempt {attempt + 1}/{_MAX_RETRIES}): {e}")
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY)
            except APIError as e:
                status_code = getattr(e, "status_code", None)
                if status_code and status_code >= 500:
                    last_error = e
                    logger.warning(f"    [LLM] 服务端错误 (attempt {attempt + 1}/{_MAX_RETRIES}): {e}")
                    if attempt < _MAX_RETRIES - 1:
                        time.sleep(_RETRY_DELAY)
                else:
                    raise
        # 所有重试用尽
        if last_error is not None:
            raise last_error
        # 理论上不会到这（last_error 必然存在），加 fallback 以满足类型检查
        raise RuntimeError("chat_with_retry exhausted retries without exception")

    def chat_json(self, role: str, messages: list, temperature: float = None,
                  max_tokens: int = None) -> dict | None:
        """
        调用大模型并解析 JSON 响应。

        参数同 chat()，返回解析后的 dict 或 None（解析失败时）
        """
        content = self.chat(role, messages, temperature, max_tokens)
        return parse_json_response(content)


def parse_json_response(content: str) -> dict | None:
    """从大模型响应中解析 JSON"""
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 尝试找第一个 { 到最后一个 }
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None

    
"""
Purpose: VLM (Vision Language Model) 客户端封装。
支持 Ark/Doubao 等 OpenAI-compatible 的视觉理解 API，用于图片细粒度语义理解。

设计要点：
- 非流式调用：VLM 用于图片理解只需一次调用，不需要 SSE 流式
- 与 ArkChatClient 风格一致：httpx、retry、circuit breaker
- 图片通过 base64 data URL 嵌入到 message content 中
"""

from typing import Protocol


class VLMClient(Protocol):
    """VLM 客户端协议。"""

    def describe_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        prompt: str = "",
    ) -> str:
        """对单张图片进行语义理解，返回自然语言描述。"""
        ...


class ArkVLMClient:
    """Ark/Doubao 视觉理解客户端（OpenAI-compatible Chat Completions）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 10.0,
        retry_attempts: int = 2,
        circuit_breaker_failures: int = 3,
        circuit_breaker_reset_seconds: float = 30.0,
        max_tokens: int = 256,
        transport: object | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retry_policy = _make_retry_policy(attempts=retry_attempts)
        self.circuit_breaker = _make_circuit_breaker(
            failure_threshold=circuit_breaker_failures,
            reset_seconds=circuit_breaker_reset_seconds,
        )
        self.max_tokens = max_tokens
        self.transport = transport

    def describe_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        prompt: str = "",
    ) -> str:
        """对图片进行语义理解，返回自然语言描述。同步阻塞调用。"""
        import httpx

        if not self.api_key:
            raise RuntimeError("ARK_API_KEY is not configured for VLM.")
        if not prompt.strip():
            prompt = self._default_prompt()

        data_url = _image_data_url(image_bytes, mime_type)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                ],
            }
        ]

        attempts = max(1, self.retry_policy.attempts)
        for attempt in range(1, attempts + 1):
            self.circuit_breaker.before_call_sync()
            try:
                result = self._describe_once(messages, httpx)
                self.circuit_breaker.record_success_sync()
                return result
            except Exception as exc:
                self.circuit_breaker.record_failure_sync()
                if attempt >= attempts or not _is_retryable_vlm_error(exc):
                    raise
                import time
                time.sleep(self.retry_policy.delay_for_attempt(attempt))

        raise RuntimeError("VLM describe_image failed after all retries.")

    def _describe_once(self, messages: list[dict], httpx_module) -> str:
        timeout = httpx_module.Timeout(self.timeout_seconds)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        with httpx_module.Client(timeout=timeout, transport=self.transport) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return _parse_vlm_response(response.json())

    def _default_prompt(self) -> str:
        return (
            "请详细描述这张图片中的商品特征。"
            "重点关注：商品品类、颜色、款式、面料材质、图案、版型、适用场景。"
            "如果图片中有品牌标识或文字，也请一并说明。"
            "请用简洁的中文回答，控制在 100 字以内。"
        )


class NoOpVLMClient:
    """空实现，用于 VLM 未启用时的降级。"""

    def describe_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        prompt: str = "",
    ) -> str:
        return ""


def _make_retry_policy(attempts: int):
    from server.gateway.resilience import RetryPolicy
    return RetryPolicy(attempts=attempts)


def _make_circuit_breaker(failure_threshold: int, reset_seconds: float):
    from server.gateway.resilience import CircuitBreaker
    return CircuitBreaker(
        failure_threshold=failure_threshold,
        reset_seconds=reset_seconds,
    )


def _image_data_url(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    import base64
    safe_mime = mime_type if mime_type.startswith("image/") else "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{safe_mime};base64,{encoded}"


def _is_retryable_vlm_error(exc: Exception) -> bool:
    import httpx
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


def _parse_vlm_response(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("VLM response contains no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    return content.strip()

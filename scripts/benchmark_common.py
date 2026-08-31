"""
Purpose: Benchmark 共享工具：.env 读取、DeepSeek API 调用、商品数据加载、检索指标计算。
供 benchmark_*.py 系列脚本复用。
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

EVAL_DIR = ROOT_DIR / "data" / "evaluation"
RESULTS_DIR = ROOT_DIR / "results"


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """简易 .env 解析（不依赖 pydantic），返回键值字典。"""
    env_path = path or (ROOT_DIR / ".env")
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def get_deepseek_config(env: dict[str, str] | None = None) -> dict[str, str]:
    env = env or load_env_file()
    return {
        "api_key": os.environ.get("DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY", ""),
        "base_url": (os.environ.get("DEEPSEEK_BASE_URL") or env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/"),
        "model": os.environ.get("DEEPSEEK_MODEL") or env.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "judge_model": os.environ.get("DEEPSEEK_JUDGE_MODEL") or env.get("DEEPSEEK_JUDGE_MODEL", "deepseek-v4-pro"),
    }


def deepseek_chat(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    json_mode: bool = False,
    timeout_seconds: float = 60.0,
    retries: int = 2,
) -> dict:
    """调用 DeepSeek Chat Completions（非流式）。

    返回 {"content": str, "latency_ms": float, "usage": dict, "model": str}
    失败时抛出带 HTTP 详情的 RuntimeError。
    """
    cfg = get_deepseek_config()
    if not cfg["api_key"]:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置（.env 或环境变量）")

    payload: dict = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    url = f"{cfg['base_url']}/chat/completions"

    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            latency_ms = (time.perf_counter() - started) * 1000
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            return {
                "content": (message.get("content") or "").strip(),
                "latency_ms": round(latency_ms, 1),
                "usage": data.get("usage") or {},
                "model": data.get("model", payload["model"]),
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                if status != 429 and status < 500:
                    body = exc.response.text[:300]
                    raise RuntimeError(f"DeepSeek 调用失败 HTTP {status}: {body}") from exc
            if attempt <= retries:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"DeepSeek 调用重试后仍失败: {last_error}")


def load_products(path: Path | None = None) -> list[dict]:
    data_path = path or (ROOT_DIR / "data" / "products_ref.json")
    return json.loads(data_path.read_text(encoding="utf-8"))


def percentile(values: list[float], p: float) -> float:
    """线性插值百分位数，p 取 0-100。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (p / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return round(ordered[low] + (ordered[high] - ordered[low]) * fraction, 2)


def recall_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    hits = sum(1 for pid in ranked_ids[:k] if pid in relevant_ids)
    return hits / len(relevant_ids)


def ndcg_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    import math

    def dcg(ids: list[str]) -> float:
        return sum(
            (1.0 if pid in relevant_ids else 0.0) / math.log2(index + 2)
            for index, pid in enumerate(ids[:k])
        )

    ideal_count = min(len(relevant_ids), k)
    ideal_dcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_count))
    if ideal_dcg == 0:
        return 0.0
    return dcg(ranked_ids) / ideal_dcg


def mrr(ranked_ids: list[str], relevant_ids: set[str]) -> float:
    for index, pid in enumerate(ranked_ids):
        if pid in relevant_ids:
            return 1.0 / (index + 1)
    return 0.0


def save_json(data: dict | list, name: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

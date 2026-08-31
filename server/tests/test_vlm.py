"""
Purpose: VLM 模块单元测试。
覆盖：VLMClient 协议、ArkVLMClient 响应解析、VLMImageUnderstandingProvider 结构化提取。
"""

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.llm.vlm_client import (
    ArkVLMClient,
    NoOpVLMClient,
    _image_data_url,
    _is_retryable_vlm_error,
    _parse_vlm_response,
)
from server.inputs.vlm_understanding import (
    ImageUnderstandingResult,
    NoOpImageUnderstandingProvider,
    VLMImageUnderstandingProvider,
    _extract_brand,
    _extract_field,
)


class TestNoOpVLMClient:
    def test_returns_empty_string(self) -> None:
        client = NoOpVLMClient()
        assert client.describe_image(b"fake", mime_type="image/jpeg") == ""


class TestImageDataUrl:
    def test_encodes_bytes_correctly(self) -> None:
        result = _image_data_url(b"hello", "image/png")
        assert result.startswith("data:image/png;base64,")
        assert base64.b64decode(result.split(",")[1]) == b"hello"

    def test_defaults_to_jpeg_for_invalid_mime(self) -> None:
        result = _image_data_url(b"x", "application/octet-stream")
        assert result.startswith("data:image/jpeg;base64,")


class TestIsRetryableVLMError:
    def test_timeout_is_retryable(self) -> None:
        import httpx
        assert _is_retryable_vlm_error(httpx.TimeoutException("timeout")) is True

    def test_429_is_retryable(self) -> None:
        import httpx
        response = MagicMock()
        response.status_code = 429
        exc = httpx.HTTPStatusError("429", request=MagicMock(), response=response)
        assert _is_retryable_vlm_error(exc) is True

    def test_400_is_not_retryable(self) -> None:
        import httpx
        response = MagicMock()
        response.status_code = 400
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=response)
        assert _is_retryable_vlm_error(exc) is False


class TestParseVLMResponse:
    def test_parses_valid_response(self) -> None:
        payload = {
            "choices": [
                {"message": {"content": "  这是一件红色连衣裙。  "}}
            ]
        }
        assert _parse_vlm_response(payload) == "这是一件红色连衣裙。"

    def test_raises_on_empty_choices(self) -> None:
        with pytest.raises(RuntimeError, match="no choices"):
            _parse_vlm_response({"choices": []})

    def test_raises_on_missing_choices(self) -> None:
        with pytest.raises(RuntimeError, match="no choices"):
            _parse_vlm_response({})


class TestNoOpImageUnderstandingProvider:
    def test_returns_empty_result(self) -> None:
        provider = NoOpImageUnderstandingProvider()
        result = provider.understand(b"fake")
        assert result.raw_description == ""
        assert result.confidence == "low"


class TestVLMImageUnderstandingProvider:
    def test_understand_returns_structured_result(self) -> None:
        mock_client = MagicMock()
        mock_client.describe_image.return_value = (
            "这是一件红色碎花连衣裙，V领设计，雪纺面料，"
            "适合春夏日常穿着。"
        )
        provider = VLMImageUnderstandingProvider(mock_client)
        result = provider.understand(b"fake", mime_type="image/jpeg")

        assert "红色碎花连衣裙" in result.raw_description
        assert "红色" in result.color_hint
        assert "连衣裙" in result.product_type_hint
        assert "雪纺" in result.fabric_hint
        assert "碎花" in result.pattern_hint
        assert "V领" in result.style_hint
        assert "春夏" in result.occasion_hint
        assert result.confidence == "high"

    def test_understand_returns_low_confidence_for_partial_info(self) -> None:
        mock_client = MagicMock()
        mock_client.describe_image.return_value = "这是红色的。"
        provider = VLMImageUnderstandingProvider(mock_client)
        result = provider.understand(b"fake")

        assert result.color_hint == "红色"
        # 仅识别出颜色（得分 1）按新规则判 low，不参与下游找货
        assert result.confidence == "low"

    def test_understand_returns_low_confidence_for_empty(self) -> None:
        mock_client = MagicMock()
        mock_client.describe_image.return_value = ""
        provider = VLMImageUnderstandingProvider(mock_client)
        result = provider.understand(b"fake")

        assert result.raw_description == ""
        assert result.confidence == "low"

    def test_custom_prompt_template(self) -> None:
        mock_client = MagicMock()
        mock_client.describe_image.return_value = "test"
        provider = VLMImageUnderstandingProvider(
            mock_client, prompt_template="custom prompt"
        )
        provider.understand(b"fake")
        mock_client.describe_image.assert_called_once_with(
            image_bytes=b"fake",
            mime_type="image/jpeg",
            prompt="custom prompt",
        )


class TestExtractField:
    def test_extracts_matching_keywords(self) -> None:
        text = "这是一件红色碎花连衣裙"
        result = _extract_field(text, ["红色", "碎花", "连衣裙", "蓝色"])
        assert "红色" in result
        assert "碎花" in result
        assert "连衣裙" in result
        assert "蓝色" not in result

    def test_returns_empty_when_no_match(self) -> None:
        assert _extract_field("hello world", ["红色", "蓝色"]) == ""

    def test_deduplicates_and_limits_to_5(self) -> None:
        text = "红色红色红色碎花碎花连衣裙"
        result = _extract_field(text, ["红色", "碎花", "连衣裙"])
        # 去重后最多 5 个，用、连接
        parts = result.split("、")
        assert len(parts) <= 5
        assert "红色" in parts


class TestExtractBrand:
    def test_extracts_known_brand(self) -> None:
        assert _extract_brand("这是 Nike 的运动鞋") == "Nike"

    def test_extracts_chinese_brand(self) -> None:
        assert _extract_brand("优衣库的T恤") == "优衣库"

    def test_returns_empty_for_unknown_brand(self) -> None:
        assert _extract_brand("这是一个无名品牌") == ""


class TestArkVLMClientDefaults:
    def test_default_prompt(self) -> None:
        client = ArkVLMClient(
            api_key="test",
            base_url="https://test.com",
            model="test-model",
        )
        prompt = client._default_prompt()
        assert "品类" in prompt
        assert "颜色" in prompt
        assert "面料" in prompt

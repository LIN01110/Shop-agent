"""
答辩重点 🔴（请求入口）
任务：Day 1 任务 1.2 — 所有请求的入口。
核心：FastAPI Router，POST /chat 返回 StreamingResponse，event_stream() 调用 orchestrator.stream_chat()。
      SSE 格式：event: xxx\ndata: {json}\n\n；同时提供 WebSocket 备用接口 /ws/chat。
高频追问：
  - “SSE 和 WebSocket 在这个项目中的取舍？”
    → SSE 适合单向服务器推流，实现简单；WebSocket 用于双工场景
  - “ChatRequest 的校验规则？” → message 或 image 至少一个
  - “画一下 HTTP 请求 → FastAPI → Orchestrator → SSE 返回的时序图”
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from server.agent.orchestrator import Orchestrator
from server.app_container import get_orchestrator
from server.config import Settings, get_settings
from server.inputs.upload_store import ImageUploadStore
from server.security.input_guard import InputGuard


# 聊天模块路由，所有接口在 OpenAPI 文档中归入 "chat" 标签。
# 使用 APIRouter 而非直接在 main.py 中写 @app.post，实现了职责分离：
# 每个模块独立管理自己的路由，新增接口只改对应文件，不动主入口。
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    """用户单次聊天请求的数据模型。

    支持纯文本、图片 base64 或引用已上传图片三种输入方式。
    """

    # 答辩追问：每个字段都有 max_length 限制，这是工程防御性设计——
    # 防止超长输入占满 LLM context（message 1000 字）、超大图片撑爆内存（base64 4.5MB）、
    # 或恶意伪造超长 ID 做攻击（session_id 128 字符）。
    message: str = Field(default="", max_length=1000)
    session_id: str = Field(default="default", min_length=1, max_length=128)
    image_base64: str = Field(default="", max_length=4_500_000)  # 直接上传图片 base64
    image_id: str = Field(default="", max_length=64)              # 引用已上传图片
    image_mime_type: str = Field(default="", max_length=80)
    image_filename: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def require_message_or_image(self) -> "ChatRequest":
        """答辩追问：为什么用 model_validator 而不是 Field(min_length=1)？
        
        因为这是跨字段联合校验——message、image_base64、image_id 三者至少一个。
        Field 校验是单个字段级别的，无法表达这种多字段"至少一个"的逻辑。
        model_validator(mode="after") 在所有字段赋值后执行，这是 Pydantic v2 特性。
        """
        if not self.message.strip() and not self.image_base64.strip() and not self.image_id.strip():
            raise ValueError("message or image is required")
        return self


def sse(event: str, data: dict) -> str:
    """答辩追问：SSE 协议格式为什么这样设计？
    
    SSE（Server-Sent Events）标准格式要求：
      event: <事件类型>\n
      data: <JSON 载荷>\n\n
    两个换行符表示一条 SSE 事件结束。Android 端的 OkHttp SSE 按此格式解析，
    按 event 类型分发到不同 callbacks（onToken/onProduct/onCart/onDone）。
    
    ensure_ascii=False 确保中文直接输出 UTF-8，而不是转义成 \u4e2d\u6587。
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/chat")
async def chat(
    request: ChatRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """HTTP 流式聊天接口，以 SSE 方式逐步返回 Agent 的思考与回复。

    答辩追问：为什么用 Depends 注入 Orchestrator 而不是全局变量？
    答：三个好处：① 延迟初始化——只在请求到达时创建；② 可测试——单元测试
    可用 override_dependency 换成 Mock；③ 生命周期管理——FastAPI 自动处理。

    答辩追问：为什么用 SSE 而不是普通 JSON 响应？
    答：普通 HTTP 是一次性返回完整 body，SSE 是 text/event-stream 类型，
    服务端可分多次 yield 数据，客户端逐事件接收，实现"打字机"流式效果。
    """

    # 输入安全检查（Prompt Injection 防御）
    if settings.enable_input_guard:
        guard = InputGuard(enabled=True)
        guard_result = guard.check(request.message)
        if not guard_result.safe:

            async def blocked_stream():
                yield sse("error", {
                    "code": "input_guard",
                    "message": "这个问题我好像没法回答，换个方式问问看？",
                })
                yield sse("done", {"status": "blocked"})

            return StreamingResponse(blocked_stream(), media_type="text/event-stream")

    async def event_stream() -> AsyncIterator[str]:
        """核心入口——把 Orchestrator 的 dict 事件流包装成 SSE 报文返回。

        设计要点：为什么用内嵌 async 生成器而非直接在 chat() 里写 for 循环？
        因为 StreamingResponse 需要一个异步生成器作为输入。内嵌函数保持 chat()
        的签名干净，同时把"业务调用"和"协议转换"（dict → SSE 字符串）分离。
        """
        # 优先根据 image_id 从本地存储还原图片二进制及元数据
        image_bytes, image_mime_type, image_filename = resolve_image_payload(request, settings)

        # 调用编排器流式处理对话，逐事件包装为 SSE 格式返回
        # Orchestrator 内部输出统一格式：{"event": "token", "data": {...}}
        # 这里只负责传输层转换，不做任何业务逻辑——体现分层架构。
        async for item in orchestrator.stream_chat(
            session_id=request.session_id,
            user_message=request.message,
            image_base64=request.image_base64,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
            image_filename=image_filename,
        ):
            yield sse(item["event"], item["data"])

    # StreamingResponse 基于 Starlette 的 StreamingResponse，底层使用 HTTP
    # Transfer-Encoding: chunked 分块发送。每次 yield 发一块数据，
    # Android 的 OkHttp SSE 按 \n\n 分隔符解析成独立事件。
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.websocket("/ws/chat")
async def chat_websocket(
    websocket: WebSocket,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    settings: Settings = Depends(get_settings),
) -> None:
    """WebSocket 备用接口，与 /chat SSE 逻辑等价，只是传输层不同。

    答辩追问：为什么同一个业务逻辑要维护两个接口？
    答：SSE 是主接口，满足当前 Demo 单向流式需求。WebSocket 是预留接口，
    未来做语音双向流、实时推送等场景可无缝切换。两者共享同一个
    orchestrator.stream_chat()，业务逻辑零重复，只在外层做传输层适配。
    """
    await websocket.accept()
    try:
        while True:
            # 接收并解析客户端 JSON 消息
            payload = await websocket.receive_json()
            try:
                request = ChatRequest.model_validate(payload)
            except ValidationError as exc:
                # 生产级错误处理：校验失败不抛异常断开连接，而是发送标准化
                # error 事件并 continue。不能因为一条坏消息就断开整个长连接。
                await websocket.send_json(
                    {
                        "event": "error",
                        "data": {
                            "code": "invalid_request",
                            "message": "请求参数不完整或格式不正确。",
                            "details": exc.errors(),
                        },
                    }
                )
                continue

            # 解析图片载荷（支持 image_id 指向的已上传图片）
            image_bytes, image_mime_type, image_filename = resolve_image_payload(request, settings)

            # 流式推送编排器返回的每个事件
            async for item in orchestrator.stream_chat(
                session_id=request.session_id,
                user_message=request.message,
                image_base64=request.image_base64,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
                image_filename=image_filename,
            ):
                await websocket.send_json(item)
    except WebSocketDisconnect:
        # 客户端断开连接属于 WebSocket 正常生命周期，静默退出即可，
        # 不需要记错误或抛异常。这是与 SSE 的差异：SSE 由浏览器自动重连，
        # WebSocket 需要手动实现心跳/重连，但这里 Demo 级别不做处理。
        return


def resolve_image_payload(request: ChatRequest, settings: Settings) -> tuple[bytes, str, str]:
    """将聊天请求中的图片引用解析为可直接使用的二进制及元数据。

    答辩追问：image_base64 和 image_id 为什么分开处理？
    答：这是两种使用场景。image_base64 适合小图直接携带（截图、拍照），
    一次请求完成。image_id 适合大图片或重复引用——先走上传接口拿到 ID，
    后续对话引用该 ID，不用重复传图。分开处理既兼容即时发送，也支持预上传流程。

    答辩追问：如果后续要把图片存储从本地文件换成阿里云 OSS，这个文件要改哪里？
    答：不改这个文件。只改 ImageUploadStore 的实现。resolve_image_payload 只依赖
    ImageUploadStore 的接口（read_bytes），不关心底层存储。这是依赖注入 + 接口隔离的好处。
    """
    image_bytes = b""
    image_mime_type = request.image_mime_type
    image_filename = request.image_filename

    # 没有 image_id 时无需查询本地存储，直接回退。
    # 此时 image_base64 由 orchestrator 的 input_processor 自行处理。
    if not request.image_id:
        return image_bytes, image_mime_type, image_filename

    # 初始化本地图片上传存储。参数均来自 settings 配置，而非硬编码：
    # max_bytes 防止超大图片攻击，ttl_seconds 防止存储无限膨胀。
    store = ImageUploadStore(
        settings.upload_image_path,
        max_bytes=settings.upload_image_max_bytes,
        ttl_seconds=settings.upload_image_ttl_seconds,
    )

    # 按 session_id 维度读取图片，隔离不同会话的数据。
    # 这是安全设计：用户 A 无法通过伪造 image_id 读取用户 B 上传的图片。
    stored = store.read_bytes(request.image_id, session_id=request.session_id)
    if stored is None:
        # 读取失败（已过期或不存在）时回退为空 bytes，不抛异常。
        # 让 Orchestrator 按无图逻辑处理，保证服务不中断——防御性设计。
        return image_bytes, image_mime_type, image_filename

    # 读取成功：返回真实二进制数据及存储时的元数据
    image_bytes, metadata = stored
    return image_bytes, metadata.mime_type, metadata.filename

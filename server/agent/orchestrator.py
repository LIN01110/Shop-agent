"""
答辩重点 🔴（后端大脑）
任务：Day 1 任务 1.3 — 必须逐行理解 stream_chat()。
核心：串联一次完整请求的 10+ 步流程：trace → session → input_processor →
      semantic_planner → context_filters → scope_transition → merge_filters →
      AgentTurnContext → workflow.stream() → query_feedback → trace_store → save。
高频追问：
  - “用户说‘第二个怎么样’，代码怎么走？”
    → plan 识别 reference_type=ordinal → ContextFollowUpHandler 取 candidate_product_cards 第二个
  - “图片上传后怎么处理？”
    → MultimodalInputProcessor 算视觉签名 → 相似匹配 → image_summary → yield image_analysis
  - “为什么要先 merge_filters 再进 workflow？”
    → 保证上下文过滤条件（预算/品牌/排除）持续累积，会话状态一致
"""

from collections.abc import AsyncIterator
import time
from uuid import uuid4

from server.agent.context import extract_contextual_filters
from server.agent.filters import extract_filters
from server.agent.default_handlers import build_default_workflow
from server.agent.workflow import AgentTurnContext, AgentWorkflow
from server.agent.query_rewriter import rewrite_query_async
from server.agent.query_feedback import QueryFeedbackStore, build_query_feedback_event
from server.agent.semantic_llm import SemanticPlanner
from server.agent.scope_transition import ScopeTransitionPolicy
from server.agent.tracing import InMemoryTraceStore, build_trace
from server.inputs.base import TextProcessor
from server.inputs.multimodal import MultimodalInputProcessor
from server.llm.ark_client import LLMClient
from server.rag.post_process import SearchFilters
from server.session.memory import refresh_session_memory
from server.session.state import PersistentSessionStore
from server.tools.registry import ToolRegistry


class Orchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        sessions: PersistentSessionStore,
        llm_client: LLMClient | None = None,
        lite_llm_client: LLMClient | None = None,
        workflow: AgentWorkflow | None = None,
        semantic_planner: SemanticPlanner | None = None,
        scope_transition_policy: ScopeTransitionPolicy | None = None,
        trace_store: InMemoryTraceStore | None = None,
        query_feedback_store: QueryFeedbackStore | None = None,
        input_processor: TextProcessor | MultimodalInputProcessor | None = None,
        recommendation_llm_budget_seconds: float = 0.0,
        retrieval_timeout_seconds: float = 5.0,
    ) -> None:
        """答辩追问：为什么所有组件都是可注入的，还有默认值？
        答：这是可测试架构的核心。每个组件都是接口/抽象类，默认实现通过
        build_default_workflow() / SemanticPlanner() / ScopeTransitionPolicy() 等构造。
        单元测试时可以注入 Mock：替换 workflow 测分派逻辑，替换 planner 测回退逻辑，
        替换 sessions 测状态持久化，全部无需改动 Orchestrator 本身。
        """
        self.registry = registry
        self.sessions = sessions
        self.llm_client = llm_client
        self.lite_llm_client = lite_llm_client
        self.workflow = workflow or build_default_workflow()
        self.semantic_planner = semantic_planner or SemanticPlanner()
        self.scope_transition_policy = scope_transition_policy or ScopeTransitionPolicy()
        self.trace_store = trace_store or InMemoryTraceStore()
        self.query_feedback_store = query_feedback_store
        self.input_processor = input_processor or TextProcessor()
        self.recommendation_llm_budget_seconds = recommendation_llm_budget_seconds
        self.retrieval_timeout_seconds = max(0.0, retrieval_timeout_seconds)

    async def stream_chat(
        self,
        session_id: str,
        user_message: str,
        image_base64: str = "",
        image_bytes: bytes = b"",
        image_mime_type: str = "",
        image_filename: str = "",
    ) -> AsyncIterator[dict]:
        """答辩：一个请求的完整 10+ 步处理流程。必须能不看代码口述每一步。

        流程总览：
          1. trace_id 生成 + 状态事件推送
          2. 获取/创建会话
          3. 记录用户消息到对话历史
          4. 输入处理（文本分词 / 图片视觉签名 / 相似匹配）
          5. 语义规划：规则 fallback → LLM 增强 → 合并校验
          6. 上下文追问过滤条件（如"再便宜点"追加更低价格）
          7. 品类切换判断（用户从"跑鞋"切到"手机"时清理旧条件）
          8. 合并过滤条件到 session，保证多轮条件持续累积
          9. 构建 AgentTurnContext（把 plan/session/registry/llm 打包）
         10. workflow 按优先级遍历 Handler，第一个 matches 的执行
         11. 查询反馈：记录意图、过滤条件、事件，用于后续评估
         12. trace 落盘：便于调试、压测、问题定位
         13. finally: 刷新会话摘要 + 持久化，保证下一轮拿到最新上下文
        """
        started_at = time.time()
        trace_id = uuid4().hex
        session = None
        emitted: list[dict] = []

        # 第 1 步：向前端推送 "thinking" 状态，降低用户等待焦虑。
        # 这是产品体验优化：用户输入后不是空白，而是先看到"正在思考"，
        # 直到后端检索完成开始流式输出 token 时才会被替换。
        status_event = {
            "event": "status",
            "data": {"state": "thinking", "text": "正在思考"},
        }
        emitted.append(status_event)
        yield status_event

        # 第 2 步：获取或创建会话。session_id 是跨轮对话的唯一标识。
        # 如果是新 session，sessions.get() 会创建空 SessionState；
        # 如果是已有 session，返回持久化存储中的完整状态（对话历史、过滤条件、购物车）。
        session = self.sessions.get(session_id)
        try:
            # 第 3 步：记录用户消息到对话历史。这是后续查询改写和上下文引用的基础。
            session.add_user_message(user_message)

            # 第 4 步：输入处理——统一文本/图片的预处理。
            # TextProcessor：分词、关键词提取、数量解析。
            # MultimodalInputProcessor：图片 → 12x12 视觉签名 → 与商品主图相似匹配 → 生成 image_summary。
            # 两种输入的产物统一为"文本查询 + 可选图片线索"，后续走同一条 RAG 链路。
            processed = self.input_processor.process(
                user_message,
                image_base64=image_base64,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
                image_filename=image_filename,
            )
            if processed.image_summary:
                # 图片处理结果：向前端推送 image_analysis 事件，
                # 让用户知道"后端识别到了图片中的 XX 商品"。同时图片线索
                # 会拼入 processed.text，进入后续语义规划和检索。
                image_event = {
                    "event": "image_analysis",
                    "data": {
                        "summary": processed.image_summary,
                        "matches": processed.visual_matches,
                    },
                }
                emitted.append(image_event)
                yield image_event

            # 第 5 步：语义规划——项目的核心设计层。
            # 双层设计：① 快速规则 fallback（build_rule_plan）保证低延迟；
            # ② 短预算内尝试 LLM JSON plan（默认 0.8s），超时/失败/校验不通过自动回退。
            # 输出 SemanticPlan 包含：intent、filters、cart_action、reference_type、query 等。
            plan = await self.semantic_planner.plan(processed.text, session)
            extracted = plan.to_filter_conditions()

            # 第 6 步：上下文追问产生的过滤条件。
            # 例如用户说"再便宜点"→追加更低价格上限；"要轻量的"→追加关键词。
            # 这些条件与 plan 中的 filters 合并，形成更完整的检索约束。
            extracted.extend(extract_contextual_filters(processed.text, session, plan))

            # 第 7 步：品类切换判断——防止长会话中旧品类污染新需求。
            # 例如用户前两轮在聊"跑鞋"，这一轮突然说"推荐手机"。
            # scope_transition 检测到 product_type 从运动鞋变为数码，决定清理旧过滤条件。
            scope_transition = self.scope_transition_policy.decide(
                processed.text,
                plan,
                session,
                extracted,
            )
            self.scope_transition_policy.apply(session, scope_transition)

            # 第 8 步：合并过滤条件到 session。
            # 为什么先 merge 再进 workflow？保证 session 的 filters 是多轮累积的——
            # 第一轮"预算500"→第二轮"要轻量"→最终检索同时满足两个条件。
            # auto_scope_reset=False 表示 merge 时不自动清理旧范围，第 7 步已经做了清理。
            session.merge_filters(extracted, auto_scope_reset=False)

            # 查询改写：如果 plan 没给出明确 query，根据会话状态重写搜索词。
            # 增强版：规则兜底 + 小模型 LLM 重写，解决"好用的""推荐一个"等模糊输入。
            intent = plan.to_user_intent()
            query = plan.query or await rewrite_query_async(
                processed.text, session, plan=plan, lite_client=self.lite_llm_client
            )
            filters = SearchFilters.from_session(session)

            # 第 9 步：构建 AgentTurnContext——把当前 turn 需要的全部上下文打包。
            # 包括：trace_id、session_id、intent、query、filters、plan、session、
            # registry（工具）、llm_client（LLM）、超时配置。
            # 这是一个值对象，不持有可变状态，只传递给下游 Handler。
            context = AgentTurnContext(
                trace_id=trace_id,
                session_id=session_id,
                message=processed.text,
                intent=intent,
                query=query,
                filters=filters,
                plan=plan,
                session=session,
                registry=self.registry,
                llm_client=self.llm_client,
                lite_llm_client=self.lite_llm_client,
                recommendation_llm_budget_seconds=self.recommendation_llm_budget_seconds,
                retrieval_timeout_seconds=self.retrieval_timeout_seconds,
            )
            # 把 NLU 置信度和证据写入 metadata，后续 trace 和调试可见。
            if plan.query_understanding:
                context.metadata["query_understanding"] = plan.query_understanding
            context.metadata["nlu"] = {
                "confidence_by_field": dict(plan.confidence_by_field),
                "evidence": dict(plan.evidence),
            }
            context.metadata["scope_transition"] = scope_transition.as_metadata()

            # 第 10 步：AgentWorkflow 按优先级遍历 Handler，第一个 matches 返回 True 的执行。
            # 优先级：Clarification → Cart → Compare → Scenario → ContextFollowUp → Recommendation。
            # 每个 Handler 的 handle() 是异步生成器，逐事件 yield 给前端。
            async for item in self.workflow.stream(context):
                emitted.append(item)
                yield item

            # 第 11 步：查询反馈——记录本次 turn 的完整上下文和事件。
            # 用于：① 离线评估（evaluate_agent.py）；② 失败样例回流；③ 意图/过滤条件准确度分析。
            # query_feedback_store 可选，没有配置时不记录，不影响主链路。
            feedback_event = build_query_feedback_event(
                trace_id=trace_id,
                session_id=session_id,
                message=processed.text,
                plan=plan,
                filters=filters,
                events=emitted,
                metadata=context.metadata,
            )
            if feedback_event is not None:
                context.metadata["query_feedback"] = {
                    "recorded": self.query_feedback_store is not None,
                    "reasons": list(feedback_event.reasons),
                }
                if self.query_feedback_store is not None:
                    self.query_feedback_store.record(feedback_event)

            # 第 12 步：trace 落盘——调试和问题定位的核心工具。
            # 包含完整信息：trace_id、session_id、用户输入、plan、query、filters、
            # 命中哪个 handler、所有 emitted 事件、耗时、metadata。
            # 可通过 GET /debug/traces?limit=5 查看最近对话。
            self.trace_store.add(
                build_trace(
                    trace_id=trace_id,
                    session_id=session_id,
                    message=processed.text,
                    handler=context.selected_handler,
                    plan=plan,
                    query=query,
                    filters=filters,
                    events=emitted,
                    started_at=started_at,
                    metadata=context.metadata,
                )
            )
        finally:
            # 第 13 步：finally 保证无论成功还是异常，session 都会被刷新和保存。
            # 为什么用 try-finally 而不是 try-except？因为异常处理由外层 FastAPI 的异常中间件负责，
            # Orchestrator 只确保"状态不丢失"。
            # refresh_session_memory：刷新对话摘要和候选商品缓存，控制内存大小。
            # sessions.save：持久化到 memory/sqlite/redis，服务重启后购物车不丢失。
            if session is not None:
                refresh_session_memory(session)
                self.sessions.save(session)


def get_orchestrator() -> Orchestrator:
    """延迟导入 app_container，避免循环依赖。"""
    from server.app_container import get_orchestrator as get_app_orchestrator
    return get_app_orchestrator()


def create_session_store(settings) -> PersistentSessionStore:
    """工厂函数：根据 settings 创建 memory/sqlite/redis 三种 session 后端之一。"""
    from server.app_container import create_session_store as create_app_session_store
    return create_app_session_store(settings)


def create_store(settings):
    """工厂函数：根据 settings 创建 VectorStore（ChromaStore 或 LocalJsonVectorStore）。"""
    from server.app_container import create_store as create_app_store
    return create_app_store(settings)


def create_llm_client(settings) -> LLMClient | None:
    """工厂函数：根据 settings 中 ARK_API_KEY 是否存在，决定是否创建 LLM 客户端。"""
    from server.app_container import create_llm_client as create_app_llm_client
    return create_app_llm_client(settings)


def create_input_processor(settings) -> MultimodalInputProcessor:
    """工厂函数：根据 settings 决定是否启用多模态输入处理器（图片找货）。"""
    from server.app_container import create_input_processor as create_app_input_processor
    return create_app_input_processor(settings)

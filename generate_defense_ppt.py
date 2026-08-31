"""
Purpose: 答辩PPT生成脚本：根据答辩PPT大纲自动生成PPT文件。
"""

# -*- coding: utf-8 -*-
"""Generate defense PPT for RAG Shopping Agent project.
Usage: python generate_defense_ppt.py
Requires: python-pptx (pip install python-pptx)
"""

import os
import sys

# Ensure python-pptx is available
try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RgbColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.oxml.ns import qn
    from pptx.oxml import parse_xml
except ImportError:
    print("Installing python-pptx...")
    os.system(f"{sys.executable} -m pip install python-pptx -q")
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RgbColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.enum.shapes import MSO_SHAPE

# Colors
DARK_BG = RgbColor(0x1A, 0x1A, 0x2E)
ACCENT = RgbColor(0x4E, 0xC5, 0xF1)
WHITE = RgbColor(0xFF, 0xFF, 0xFF)
GRAY = RgbColor(0xCC, 0xCC, 0xCC)
LIGHT_GRAY = RgbColor(0x88, 0x88, 0x88)
DARK_CARD = RgbColor(0x25, 0x25, 0x40)

def set_slide_bg(slide, color):
    """Set solid background color for a slide."""
    background = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height
    )
    background.fill.solid()
    background.fill.fore_color.rgb = color
    background.line.fill.background()
    # Send to back
    spTree = slide.shapes._spTree
    sp = background._element
    spTree.remove(sp)
    spTree.insert(2, sp)

def add_text(slide, left, top, width, height, text, font_size=20, bold=False, color=WHITE, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.name = "Microsoft YaHei"
    p.alignment = align
    return box

def add_bullet_list(slide, left, top, width, height, bullets, font_size=18, color=GRAY):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    for i, bullet in enumerate(bullets):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = bullet
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        p.font.name = "Microsoft YaHei"
        p.space_before = Pt(10)
        p.level = 0
    return box

def add_accent_line(slide, top):
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, top, prs.slide_width, Inches(0.06))
    line.fill.solid()
    line.fill.fore_color.rgb = ACCENT
    line.line.fill.background()
    # Send to back behind content but above bg
    spTree = slide.shapes._spTree
    sp = line._element
    spTree.remove(sp)
    spTree.insert(2, sp)

# Create presentation
prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

W = prs.slide_width
H = prs.slide_height

# ============================================================
# Slide 1: Title
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
set_slide_bg(slide, DARK_BG)
add_text(slide, Inches(1), Inches(2.5), Inches(11.333), Inches(1.2),
         "RAG 多模态电商智能导购 Agent", 44, True, WHITE, PP_ALIGN.CENTER)
add_text(slide, Inches(1), Inches(3.8), Inches(11.333), Inches(0.8),
         "基于 FastAPI + Chroma + Doubao/Ark 的对话式电商导购系统", 24, False, ACCENT, PP_ALIGN.CENTER)
add_text(slide, Inches(1), Inches(6.5), Inches(11.333), Inches(0.5),
         "答辩人: XXX  |  指导教师: XXX  |  2025年", 16, False, LIGHT_GRAY, PP_ALIGN.CENTER)

# ============================================================
# Slide 2: Agenda
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "答辩目录", 32, True, WHITE)

agenda = [
    "1. 背景与项目目标",
    "2. 技术选型与架构设计",
    "3. 核心功能演示 (6大场景)",
    "4. 技术深度解析: RAG检索准确性 / Agent语义规划 / 防幻觉",
    "5. 工程规范与测试评估",
    "6. 成果总结与未来展望",
]
add_bullet_list(slide, Inches(1.5), Inches(1.5), Inches(10), Inches(5), agenda, 22, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "25分钟演讲 + 15分钟 Q&A", 16, False, LIGHT_GRAY, PP_ALIGN.LEFT)

# ============================================================
# Slide 3: Background
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "背景: 传统电商搜索的局限", 32, True, WHITE)

bullets = [
    "用户需要精确输入关键词, 无法表达复杂需求",
    "\"适合油皮, 预算200以内, 不要日系品牌\" -- 传统搜索无法处理",
    "多轮条件累积 (预算 -> 品牌 -> 排除) 需要反复筛选页面",
    "图片找货, 场景搭配 (\"三亚度假装备\") 缺乏对话式交互",
    "现有方案: 关键词匹配 + 人工筛选, 体验割裂, 效率低下",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), bullets, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "目标: 用户像跟导购员聊天一样, 逐步明确需求, Agent逐步收敛推荐", 18, False, ACCENT)

# ============================================================
# Slide 4: Project Goals
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "项目目标: 对话式智能导购 Agent", 32, True, WHITE)

goals = [
    "自然语言多轮对话: 用户用大白话描述, Agent通过追问逐步收敛",
    "RAG检索增强: 基于商品数据库检索, 严格约束回答范围, 防止幻觉",
    "多模态输入: 支持图片找货, 上传图片即可找相似商品",
    "场景化组合推荐: \"三亚度假\"自动拆分为防晒+穿搭+出行多槽位",
    "对话式购物车: 加购/删除/改数量全程自然语言交互",
    "工程规范: 可测试, 可扩展, 可观测的完整端到端链路",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), goals, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "评分权重: 基础功能35% + 工程质量25% + 效果可靠性20% + 加分项20%", 18, False, LIGHT_GRAY)

# ============================================================
# Slide 5: Tech Stack
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "技术选型: 为什么选这套方案?", 32, True, WHITE)

tech = [
    "客户端: Android + Kotlin + Compose -- 原生开发 (课题硬性要求)",
    "后端: FastAPI -- 原生async, StreamingResponse直接支持SSE流式",
    "向量库: Chroma -- 50~100条零运维, VectorStore抽象可替换",
    "Embedding: Doubao-embedding-vision -- 预留多模态向量空间",
    "大模型: Doubao-Seed-2.0-lite -- 课题提供API Key, OpenAI兼容接口",
    "配置: .env + pydantic-settings -- API Key不入Git, 工程规范",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), tech, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "选型原则: 最小闭环成本最低, 架构层面全部预留扩展接口", 18, False, ACCENT)

# ============================================================
# Slide 6: Architecture
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "系统架构: 端到端链路", 32, True, WHITE)

arch_lines = [
    "Android Compose  ->  OkHttp SSE  ->  FastAPI /chat",
    "  ->  Orchestrator编排器  ->  SemanticPlanner语义规划",
    "  ->  AgentWorkflow分派Handler  ->  ToolRegistry调用工具",
    "  ->  ProductRetrievalPipeline检索  ->  LLM生成 / 模板回退",
    "  ->  GroundingGuard校验  ->  SSE流式返回",
    "  ->  Android逐字渲染 + 商品卡片 + 购物车面板",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), arch_lines, 20, ACCENT)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "6大抽象接口确保: 任何加分项都是新增模块, 而非重构链路", 18, False, WHITE)

# ============================================================
# Slide 7: 13-Step Data Flow
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "核心数据流: 一个请求的13步处理", 32, True, WHITE)

steps = [
    "1. trace_id + thinking状态  ->  2. 获取/创建会话  ->  3. 记录用户消息",
    "4. 输入处理 (文本/图片)  ->  5. 语义规划 (规则+LLM)  ->  6. 上下文追问过滤",
    "7. 品类切换判断  ->  8. 合并过滤条件  ->  9. 构建AgentTurnContext",
    "10. workflow分派Handler  ->  11. 查询反馈记录  ->  12. trace落盘",
    "13. finally: 刷新会话摘要 + 持久化",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), steps, 18, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "orchestrator.py是后端大脑, 面试追问必考", 18, False, ACCENT)

# ============================================================
# Slide 8: Feature 1 - Multi-turn + Exclusion
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "功能演示①: 多轮对话 + 反选", 32, True, WHITE)

f1 = [
    "用户: 推荐一款跑鞋",
    "Agent: 追问预算和偏好 (ClarificationHandler)",
    "用户: 预算500以内, 要轻量的",
    "Agent: 返回商品卡片 (检索 -> LLM -> 流式回复)",
    "用户: 不要日系品牌",
    "Agent: ExclusionFilter程序化过滤 -> 排除后重新检索 -> 返回新结果",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), f1, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "基础闭环, 满足评审合格标准全部5条", 18, False, ACCENT)

# ============================================================
# Slide 9: Feature 2 - Context Follow-up
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "功能演示②: 上下文追问 (技术亮点)", 32, True, WHITE)

f2 = [
    "\"第二个怎么样?\" -> plan识别reference_type=ordinal -> ContextFollowUpHandler取candidate_product_cards[1]",
    "\"再便宜点\" -> 自动追加更低价格上限 -> 重新检索",
    "\"AHC那款熬夜党能用吗?\" -> 品牌引用解析 -> 基于metadata回答, 不依赖固定句式",
    "\"那支给我来两件\" -> 结构化plan识别隐式加购 + 数量",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), f2, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "深度实现的加分项: 会话状态管理 + 引用解析 + 查询改写", 18, False, ACCENT)

# ============================================================
# Slide 10: Feature 3 - Image Search
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "功能演示③: 多模态图片找货", 32, True, WHITE)

f3 = [
    "用户: 上传运动鞋图片",
    "后端: 12x12 RGB视觉签名 -> 与商品主图签名库相似匹配",
    "生成image_summary: \"上传图片最像商品X, 品牌Y\"",
    "图片线索拼入查询 -> 进入同一RAG检索流程 -> 返回相似商品",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), f3, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "非完整VLM但真实可运行, 后续替换MultimodalInputProcessor即可升级", 18, False, ACCENT)

# ============================================================
# Slide 11: Feature 4 - Scenario Bundle
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "功能演示④: 场景化组合推荐", 32, True, WHITE)

f4 = [
    "用户: \"我要去三亚度假, 推荐一套装备\"",
    "ScenarioBundleHandler加载JSON配置 -> 拆分为多槽位:",
    "  防晒保护(必备) + 轻便上衣(必备) + 舒适出行(必备) + 拍照记录(可选)",
    "每个槽位独立检索 -> BundleOptimizer在预算/去重/完整度间做选择",
    "低预算时自动裁剪optional slot (如\"拍照记录\")",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), f4, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "新增场景只需扩展JSON配置和评估样例, 无需改代码", 18, False, ACCENT)

# ============================================================
# Slide 12: Feature 5 - Compare
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "功能演示⑤: 商品对比", 32, True, WHITE)

f5 = [
    "用户: \"AHC和科颜氏的眼霜哪个好?\"",
    "CompareHandler解析\"X vs Y\" -> 分别检索两侧候选",
    "避免普通Top-K只召回一侧品牌",
    "返回三类SSE: token(对比结论) + product_card(两侧商品) + comparison_card(结构化数据)",
    "超预算候选在tradeoffs中标记, 结论优先推荐预算内商品",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), f5, 20, GRAY)

# ============================================================
# Slide 13: Feature 6 - Cart
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "功能演示⑥: 对话式购物车", 32, True, WHITE)

f6 = [
    "用户: \"把刚才那款加到购物车\" -> CartHandler识别引用+操作 -> 修改SessionState.cart",
    "用户: 查看购物车 / 删除第一个 / 数量改成2",
    "每次变化返回cart_update SSE事件 -> Android展示购物车面板",
    "持久化到sqlite/redis -> 服务重启后购物车不丢失",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), f6, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "满足结构化数据CRUD + 客户端实时反馈的加分项方向", 18, False, ACCENT)

# ============================================================
# Slide 14: Tech Deep 1 - RAG Pipeline (ACCURACY FOCUS)
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8),
         "技术深度①: RAG检索Pipeline (准确性核心)", 32, True, WHITE)

rag = [
    "Stage 1: Store预过滤召回 -> candidate_multiplier=10, min_pool=50",
    "Stage 2-7: PostProcessor依次过滤 -> 价格/品类/品牌/库存/关键词/排除",
    "  - 即使store已做metadata预过滤, 后处理仍作为安全兜底",
    "Stage 8: Dedupe去重 -> 按商品ID去重",
    "Stage 9: Rerank重排序 -> 相关性加分: 品类+20 / 关键词+6 / 品牌+5 / 预算内+2",
    "  - 结构化匹配 > 关键词匹配 > 预算匹配",
    "Chroma不可用时自动fallback到LocalJsonVectorStore (倒排索引+特征缓存)",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), rag, 18, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "retrieval_pipeline.py是RAG核心, 面试追问最高频", 18, False, ACCENT)

# ============================================================
# Slide 15: Tech Deep 2 - SemanticPlanner (ACCURACY FOCUS)
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8),
         "技术深度②: SemanticPlanner (语义准确性)", 32, True, WHITE)

plan = [
    "Layer 1: 快速规则fallback -> build_rule_plan() -> 低延迟, 确定性高",
    "Layer 2: LLM JSON plan -> 短预算0.8s -> 超时/失败自动回退",
    "合并: merge_semantic_plans() -> confidence<0.5全用fallback; cart/compare/bundle优先规则",
    "校验: Pydantic model_validate + PlannerPolicy -> LLM只输出建议, 不直接执行业务动作",
    "输出: intent/filters/cart_action/reference_type/query + confidence_by_field + evidence",
    "安全: 购物车写操作(add/remove/update)必须有明确证据, 否则降级为澄清",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), plan, 18, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "规则为主 + LLM为辅 + 合并校验: 各取所长, 安全与泛化并重", 18, False, ACCENT)

# ============================================================
# Slide 16: Tech Deep 3 - AgentWorkflow
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "技术深度③: AgentWorkflow (6个Handler)", 32, True, WHITE)

handlers = [
    "1. ClarificationHandler  -> 宽泛需求先追问, 不硬推商品",
    "2. CartHandler           -> 购物车操作 (add/remove/update/view)",
    "3. CompareHandler        -> 商品对比 (X vs Y)",
    "4. ScenarioBundleHandler -> 场景组合 (三亚/通勤/健身)",
    "5. ContextFollowUpHandler-> 上下文追问 (第二个/再便宜点/那款)",
    "6. RecommendationHandler -> 普通推荐, 兜底Handler",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), handlers, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "新增Handler只改workflow装配, 不动主链路; 后续可替换为LangGraph", 18, False, ACCENT)

# ============================================================
# Slide 17: Tech Deep 4 - GroundingGuard (ACCURACY FOCUS)
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8),
         "技术深度④: GroundingGuard (防幻觉三层 - 准确性生命线)", 32, True, WHITE)

guard = [
    "Layer 1: Prompt约束 -> 只能依据检索上下文回答, 价格/品牌/类目来自metadata",
    "Layer 2: 证据绑定 -> 商品卡携带结构化evidence (ID/名称/品牌/价格/检索分数)",
    "Layer 3: 后校验 (4条规则):",
    "  ① 拦截未提供的优惠/库存/销量/好评率",
    "  ② 拦截候选商品和预算以外的价格",
    "  ③ 拦截没有引用任何候选商品的回答",
    "  ④ 拦截绝对化/医疗化承诺 (\"根治\" \"100%\")",
    "触发降级: yield guardrail事件 -> 改用确定性模板回答, 商品卡仍来自metadata",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), guard, 18, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "电商导购\"不出错\"比\"说得漂亮\"更重要; 评审效果可靠性的核心打分点", 18, False, ACCENT)

# ============================================================
# Slide 18: Tech Deep 5 - Session State & Scope Transition
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "技术深度⑤: 会话状态与品类切换", 32, True, WHITE)

session = [
    "SessionState核心字段: history / filters / exclusions / candidate_product_cards / cart / pending_subject",
    "merge_filters: 多轮条件累积 -> 第一轮\"预算500\" + 第二轮\"要轻量\" = 同时满足",
    "scope_transition: 品类切换检测 -> 跑鞋->手机时 reset_product_scope() 清理旧条件",
    "  - 防止旧品类(跑鞋的\"轻量\")污染新需求(手机的检索)",
    "  - 注意: 切换再切回会丢失旧条件, 这是防止污染的权衡",
    "  - 对话历史保留在history中, LLM可从文本恢复部分上下文",
    "持久化: memory/sqlite/redis; try-finally保证状态不丢失",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), session, 18, GRAY)

# ============================================================
# Slide 19: Tech Deep 6 - Multimodal Input
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "技术深度⑥: 多模态输入处理", 32, True, WHITE)

multi = [
    "InputProcessor抽象: TextProcessor + MultimodalInputProcessor",
    "图片处理: 12x12 RGB视觉签名 -> cosine相似度比较 -> image_summary",
    "产物统一: 文本查询 + 可选图片线索 -> 进入同一RAG链路",
    "预留接口: ASRProcessor(语音), VLMProcessor(真实视觉模型)",
    "新增处理器只需实现process()方法, 不改变Orchestrator主流程",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), multi, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "架构原则: 多模态不是独立链路, 而是输入层统一适配后进入同一RAG流程", 18, False, ACCENT)

# ============================================================
# Slide 20: Engineering Depth
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "工程深度: 6大抽象接口与扩展点", 32, True, WHITE)

eng = [
    "VectorStore: ChromaStore / LocalJsonStore -> 换库只改一个适配类",
    "ToolRegistry: search/compare/cart -> 新增工具不动编排逻辑",
    "InputProcessor: 文本/图片/预留语音/VLM -> 即插即用",
    "PostProcessor: Range/Type/Exclusion... -> 新增过滤器不动Pipeline",
    "流式事件协议: token/card/cart/guardrail/done -> 新类型向后兼容",
    "SessionStore: memory/sqlite/redis -> 切换后端只改配置",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), eng, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "6大抽象接口确保: 任何加分项都是新增模块, 而非重构链路", 18, False, ACCENT)

# ============================================================
# Slide 21: Testing & Evaluation
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "工程规范: 测试与离线评估", 32, True, WHITE)

test = [
    "40+单元测试: 覆盖API层, Agent编排, RAG检索, 购物车, 会话状态",
    "evaluate_agent.py: 离线评估 -> 检查plan/handler/商品命中/购物车行为",
    "evaluate_query_plans.py: 查询规划回归 -> intent/品类/预算/否定/引用解析",
    "benchmark_retrieval.py: 检索压测 -> 50K本地 + 1K Chroma数据",
    "benchmark_first_token.py: 首Token延迟压测 -> 监控用户体验",
    "debug/traces: 实时trace查看 -> 定位planner/handler/检索链路问题",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), test, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "测试不是\"有就行\", 而是覆盖核心链路 + 可复现评估 + 性能基准 + 可观测", 18, False, ACCENT)

# ============================================================
# Slide 22: Performance
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "性能优化: 压测数据与策略", 32, True, WHITE)

perf = [
    "50K本地数据fallback: 倒排索引+特征缓存 -> 毫秒级召回",
    "Chroma 1K数据: metadata where预过滤下推 -> 减少向量搜索空间",
    "即时token: \"正在为您搜索...\" -> 降低首屏等待感",
    "Embedding缓存: 避免重复计算相同查询的embedding",
    "语义规划短预算: 0.8s超时自动回退规则 -> 保证响应稳定性",
    "热门查询缓存: 高频请求直接命中 -> 降低延迟和模型调用",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), perf, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "性能不是优化到完美, 而是有压测/有指标/有策略/有fallback", 18, False, ACCENT)

# ============================================================
# Slide 23: Results
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "项目成果", 32, True, WHITE)

results = [
    "端到端链路完整: Android -> FastAPI -> RAG -> LLM/模板 -> SSE -> 商品卡片",
    "6大核心功能: 多轮对话, 反选, 上下文追问, 图片找货, 场景组合, 对比, 购物车",
    "4层技术深度: SemanticPlanner, GroundingGuard, AgentWorkflow, 检索Pipeline",
    "6大抽象接口: 确保加分项是新增模块, 而非重构链路",
    "40+测试 + 压测 + 评估: 可演示, 可复现, 可观测",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), results, 20, ACCENT)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "量化成果: 压测报告, 评估报告, trace记录, 全部可运行", 18, False, WHITE)

# ============================================================
# Slide 24: Personal Takeaways
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "个人收获", 32, True, WHITE)

takeaways = [
    "RAG链路设计: 检索->生成->校验的完整闭环, Pipeline分层的工程价值",
    "LLM幻觉控制: Prompt+证据+后校验三层防线; \"不出错\"比\"说得漂亮\"更重要",
    "Agent工作流编排: 规则+LLM混合策略, 各取所长, 不是全规则也不是全LLM",
    "多模态输入处理: 统一抽象后接入同一链路, \"输入层适配, 业务层统一\"",
    "可测试与可扩展: 依赖注入, 接口隔离, 工厂模式 -> Demo能演化为产品",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), takeaways, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "\"如果重来一次, 我会更早加入离线评估和压测, 而不是功能堆完再补。\"", 18, False, ACCENT)

# ============================================================
# Slide 25: Future Work
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_accent_line(slide, Inches(0.8))
add_text(slide, Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8), "未来优化方向", 32, True, WHITE)

future = [
    "检索层: BM25+向量混合召回, 接入外部reranker (BGE-Reranker)",
    "LLM层: 接入真实VLM替换视觉签名, 实现\"看图说话\"找货",
    "Agent层: 替换为LangGraph, 实现更复杂的循环和条件分支",
    "数据层: 扩充到万级商品, 测试Chroma在大规模下的性能瓶颈",
    "评估层: 构建1000+样例评估集, 实现自动A/B测试框架",
    "产品层: 接入真实电商API (价格/库存/优惠), 实现真正下单链路",
]
add_bullet_list(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(4.5), future, 20, GRAY)
add_text(slide, Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4),
         "每个方向都已在当前架构中预留接口, 实现成本可控", 18, False, ACCENT)

# ============================================================
# Slide 26: End
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide, DARK_BG)
add_text(slide, Inches(1), Inches(2.8), Inches(11.333), Inches(1.2),
         "谢谢聆听", 52, True, WHITE, PP_ALIGN.CENTER)
add_text(slide, Inches(1), Inches(4.2), Inches(11.333), Inches(0.8),
         "Q&A 环节  |  欢迎提问", 28, False, ACCENT, PP_ALIGN.CENTER)
add_text(slide, Inches(1), Inches(6.5), Inches(11.333), Inches(0.5),
         "答辩人: XXX  |  联系邮箱: xxx@example.com", 16, False, LIGHT_GRAY, PP_ALIGN.CENTER)

# ============================================================
# Save
# ============================================================
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "答辩PPT_RAG电商导购Agent_26页.pptx")
prs.save(output_path)
print(f"PPT saved to: {output_path}")
print(f"Total slides: {len(prs.slides)}")

"""
查询重写增强版：
  1. 规则兜底（高确定性上下文推断）
  2. 模糊检测（判断 query 是否需要增强）
  3. LLM 重写（小模型，轻量、低成本）

调用链路：
  plan.query 明确？ → 直接用
  有确定性上下文？ → 规则补全（零 LLM 消耗）
  仍模糊？ → doubao-lite 重写（~0.01元/次）
"""

from __future__ import annotations

import asyncio
import logging
import re

from server.agent.semantic_schema import SemanticPlan
from server.session.state import SessionState

logger = logging.getLogger(__name__)

# ── 模糊检测规则 ───────────────────────────────

# 明确品类/产品关键词（至少匹配一个才认为 query 有方向）
_PRODUCT_KEYWORDS = frozenset([
    "手机", "电脑", "笔记本", "平板", "耳机", "手表", "相机", "护肤", "口红",
    "防晒", "洁面", "精华", "面膜", "香水", "洗发", "护发", "零食", "饮料",
    "牛奶", "咖啡", "茶", "衣服", "裤子", "鞋", "包", "手表", "珠宝", "家电",
    "电视", "冰箱", "空调", "洗衣机", "厨具", "家具", "文具", "书籍", "玩具",
    "运动", "户外", "旅行", "美妆", "护肤", "彩妆", "母婴", "宠物", "食品",
    "生鲜", "酒水", "保健品", "药品", "医疗器械", "汽车", "配件", "数码",
    "办公", "家装", "个护", "清洁", "纸品", "收纳", "灯具", "五金", "工具",
    "手机壳", "充电器", "数据线", "充电宝", "鼠标", "键盘", "显示器", "音响",
    "路由器", "智能", "手环", "体重秤", "牙刷", "剃须刀", "吹风机", "熨斗",
    "榨汁机", "电饭煲", "微波炉", "烤箱", "扫地", "净化器", "加湿器",
    "取暖器", "风扇", "门锁", "摄像头", "台灯", "闹钟", "计算器", "订书机",
    "剪刀", "胶带", "笔", "本子", "文件夹", "书包", "行李箱", "雨伞", "帽子",
    "围巾", "手套", "袜子", "内衣", "睡衣", "泳衣", "眼镜", "墨镜", "腰带",
    "钱包", "卡包", "化妆", "镜子", "梳子", "指甲", "剃须", "脱毛", "面膜",
    "眼霜", "乳液", "面霜", "粉底", "眼影", "眉笔", "睫毛", "唇彩", "香水",
    "香薰", "蜡烛", "花", "植物", "种子", "肥料", "宠物粮", "猫砂", "狗绳",
    "鱼", "鸟", "仓鼠", "玩具", "积木", "拼图", "乐", "娃娃", "模型", "手办",
    "图书", "教材", "小说", "漫画", "杂志", "音乐", "乐器", "吉他", "钢琴",
    "运动", "瑜伽", "跑步", "健身", "球", "拍", "泳", "钓", "露营", "登山",
    "骑行", "滑雪", "滑板", "轮滑", "舞蹈", "武术", "棋", "牌", "麻将",
    # 英文常见品类
    "iphone", "ipad", "macbook", "airpods", "apple watch", "ps5", "switch",
    "kindle", "dyson", "bose", "sony", "canon", "nikon", "gopro", "kindle",
    "surface", "thinkpad", "xps", "alienware", "rog", "legion",
])

# 品牌词（有助于判断 query 是否有明确指向）
_BRAND_KEYWORDS = frozenset([
    "苹果", "华为", "小米", "oppo", "vivo", "三星", "索尼", "佳能", "尼康",
    "雅诗兰黛", "兰蔻", "skii", "资生堂", "欧莱雅", "玉兰油", "珀莱雅",
    "花西子", "完美日记", "colorkey", "mac", "迪奥", "香奈儿", "ysl",
    "耐克", "阿迪", "安踏", "李宁", "优衣库", "zara", "hm", "gap",
    "宜家", "无印良品", "名创优品", "海底捞", "喜茶", "奈雪", "瑞幸",
    "星巴克", "可口可乐", "百事", "农夫山泉", "蒙牛", "伊利",
    "戴森", "飞利浦", "松下", "美的", "格力", "海尔", "海信", "tcl",
    "联想", "戴尔", "惠普", "华硕", "宏碁", "机械革命", "雷神",
    "罗技", "雷蛇", "赛睿", "樱桃", "斐尔可", "宁芝",
    "周大福", "老凤祥", "潘多拉", "施华洛世奇",
    # 英文品牌
    "apple", "huawei", "xiaomi", "samsung", "sony", "canon", "nikon",
    "estee lauder", "lancome", "dior", "chanel", "nike", "adidas",
    "uniqlo", "ikea", "muji", "dyson", "philips", "panasonic", "logitech",
    "razer", "cherry", "rolex", "casio", "swatch", "tissot",
])

# 纯模糊词（如果 query 只有这些，认为无意义）
_VAGUE_ONLY_WORDS = frozenset([
    "推荐", "一个", "一款", "一些", "几个", "东西", "商品", "产品",
    "好的", "不错", "还行", "可以", "推荐一下", "看看", "有什么",
    "介绍", "说说", "讲一讲", "想要", "想买", "想买个", "想看一下",
    "帮我", "给我", "找", "选", "挑", "哪个", "什么", "怎么",
    "随便", "都行", "都可以", "无所谓", "看着办", "你决定",
])


def _has_product_keyword(text: str) -> bool:
    """检查文本是否包含明确的产品/品类关键词。"""
    text_lower = text.lower()
    return any(kw in text_lower for kw in _PRODUCT_KEYWORDS)


def _has_brand_keyword(text: str) -> bool:
    """检查文本是否包含品牌关键词。"""
    text_lower = text.lower()
    return any(kw in text_lower for kw in _BRAND_KEYWORDS)


def _is_vague_only(text: str) -> bool:
    """检查文本是否只有模糊词，没有任何实质内容。"""
    # 去掉常见助词、标点，看剩下的是否全是模糊词
    cleaned = re.sub(r"[的了吗呢啊吧哦哈嘛呀呐\s，。！？、\-_\/]", "", text.lower())
    if not cleaned:
        return True
    # 如果清洁后内容全是模糊词或其子串，认为纯模糊
    for kw in _VAGUE_ONLY_WORDS:
        cleaned = cleaned.replace(kw, "")
    return len(cleaned.strip()) == 0


def is_query_ambiguous(query: str, plan: SemanticPlan | None = None) -> tuple[bool, str]:
    """
    判断 query 是否模糊，返回 (is_ambiguous, reason)。

    模糊标准（满足任一）：
      1. 长度 < 4 且无品牌/品类词
      2. 纯模糊词（如"推荐一个"）
      3. 无品类关键词且无品牌关键词
      4. plan.intent == "clarify"（意图本身就是要澄清）
      5. 只有数字/量词（如"300块"）
    """
    q = query.strip()
    if not q:
        return True, "query为空"

    # 标准 4：意图为 clarify
    if plan and plan.intent == "clarify":
        return True, "意图为clarify"

    # 标准 1：太短且无品牌/品类
    if len(q) < 4 and not (_has_product_keyword(q) or _has_brand_keyword(q)):
        return True, "query过短且无明确指向"

    # 标准 2：纯模糊词
    if _is_vague_only(q):
        return True, "query为纯模糊词"

    # 标准 5：只有数字/量词/价格词
    cleaned = re.sub(r"[\d\s，。！？、元块百十千万个款种件套瓶支包袋盒杯条双只片粒罐箱吨斤公里寸英]", "", q)
    if len(cleaned.strip()) < 2:
        return True, "query仅含数字/量词"

    # 标准 3：无品类且无品牌
    if not (_has_product_keyword(q) or _has_brand_keyword(q)):
        return True, "query无品类/品牌关键词"

    return False, "query明确"


# ── 规则兜底 ───────────────────────────────────


def _rule_rewrite(message: str, session: SessionState, plan: SemanticPlan) -> str | None:
    """
    基于确定性上下文的规则补全。
    返回 None 表示规则无法确定，需要走 LLM。
    """
    # 规则 1：pending_subject 存在 → 高确定性
    if session.pending_subject and session.pending_subject not in message:
        return f"{session.pending_subject} {message}"

    # 规则 2：plan 中有 product_type filter → 用户已确认品类
    product_type = ""
    for f in plan.filters:
        if f.kind in ("product_type", "category"):
            product_type = f.value
            break
    # 也检查 session 中已确认的 filters
    if not product_type:
        for f in session.filters:
            if f.kind in ("product_type", "category"):
                product_type = f.value
                break
    if product_type and product_type not in message:
        return f"{product_type} {message}"

    # 规则 3：session 有最近浏览的候选商品 → 取最近商品名
    if session.candidate_product_cards and len(session.candidate_product_cards) > 0:
        last_card = session.candidate_product_cards[-1]
        name = last_card.get("name", "")
        brand = last_card.get("brand", "")
        # 只有当用户输入明显是在追问该商品时才用（如"怎么样""好用吗"）
        followup_hints = ["怎么样", "好用吗", "可以吗", "推荐吗", "值不值", "呢"]
        if any(h in message for h in followup_hints):
            if name and name not in message:
                return f"{name} {message}"
            if brand and brand not in message:
                return f"{brand} {message}"

    # 规则 4：user_profile 中有偏好品类
    if session.user_profile.product_types:
        preferred = session.user_profile.product_types[-1]  # 最近偏好
        if preferred and preferred not in message:
            # 只在用户输入非常短（<6字）时用，避免过度推断
            if len(message.strip()) < 6:
                return f"{preferred} {message}"

    return None


# ── LLM 重写 ───────────────────────────────────


def _build_rewrite_prompt(message: str, session: SessionState, plan: SemanticPlan) -> list[dict]:
    """构建 query rewriting 的 LLM prompt（精简版，小模型友好）。"""
    # 取最近 3 轮对话
    recent = []
    for turn in session.history[-6:]:  # 最多6轮，user/assistant各3轮
        recent.append(f"{'用户' if turn.role == 'user' else '助手'}：{turn.content}")
    history_text = "\n".join(recent) if recent else "无"

    # 用户偏好
    prefs = []
    if session.user_profile.preferred_brands:
        prefs.append(f"偏好品牌：{', '.join(session.user_profile.preferred_brands)}")
    if session.user_profile.keywords:
        prefs.append(f"关注关键词：{', '.join(session.user_profile.keywords)}")
    if session.user_profile.budget_ceiling:
        prefs.append(f"预算上限：{session.user_profile.budget_ceiling}元")
    prefs_text = "\n".join(prefs) if prefs else "无"

    # 已确认的过滤条件
    filters_text = "、".join(f"{f.kind}={f.value}" for f in session.filters) if session.filters else "无"

    content = (
        f"用户最新输入：{message}\n\n"
        f"最近对话：\n{history_text}\n\n"
        f"用户偏好：{prefs_text}\n\n"
        f"已确认条件：{filters_text}\n\n"
        f"用户意图：{plan.intent}\n\n"
        "任务：根据上下文，将用户输入重写为一个明确的电商搜索查询。\n"
        "要求：\n"
        "1. 必须包含具体品类（如手机、防晒霜、运动鞋）\n"
        "2. 保留用户的所有约束（预算、品牌、功能需求）\n"
        "3. 只输出搜索查询本身，不要解释、不要加引号\n"
        "4. 如果信息实在不足，输出：CLARIFY\n"
        "输出："
    )

    return [
        {"role": "system", "content": "你是电商搜索查询重写助手。只输出搜索词，不解释。"},
        {"role": "user", "content": content},
    ]


async def _llm_rewrite_query(
    lite_client,
    message: str,
    session: SessionState,
    plan: SemanticPlan,
    max_tokens: int = 128,
) -> str:
    """调用小模型重写 query。"""
    if lite_client is None:
        return message

    messages = _build_rewrite_prompt(message, session, plan)
    try:
        chunks: list[str] = []
        async for token in lite_client.stream_messages(messages, max_tokens=max_tokens):
            chunks.append(token)
        rewritten = "".join(chunks).strip()

        # 如果模型要求澄清
        if rewritten.upper() == "CLARIFY" or "CLARIFY" in rewritten.upper():
            return message  # 保持原样，由 ClarificationHandler 处理

        # 去掉可能的引号
        rewritten = rewritten.strip('""').strip("'").strip()

        if rewritten and len(rewritten) >= 3:
            logger.info("LLM query rewrite: '%s' -> '%s'", message, rewritten)
            return rewritten
    except Exception as exc:
        logger.warning("LLM query rewrite failed: %s", exc)

    return message


# ── 主入口 ─────────────────────────────────────


def rewrite_query(
    message: str,
    session: SessionState,
    plan: SemanticPlan | None = None,
    lite_client=None,
) -> str:
    """
    查询重写主入口（同步，规则兜底）。
    如果需要 LLM，返回原 message，由调用方异步处理。
    """
    # Step 1: 基础拼接（保留原有逻辑）
    filters = " ".join(item.value for item in session.filters)
    exclusions = " ".join(item.value for item in session.exclusions)
    parts = []
    if session.pending_subject and session.pending_subject not in message:
        parts.append(session.pending_subject)
    parts.append(message.strip())
    if filters:
        parts.append(f"已确认条件: {filters}")
    if exclusions:
        parts.append(f"排除条件: {exclusions}")
    base_query = " ".join(parts)

    # Step 2: 模糊检测
    is_ambiguous, reason = is_query_ambiguous(base_query, plan)
    if not is_ambiguous:
        return base_query

    logger.info("Ambiguous query detected (%s): '%s'", reason, base_query)

    # Step 3: 规则兜底
    rule_result = _rule_rewrite(message, session, plan) if plan else None
    if rule_result:
        logger.info("Rule rewrite: '%s' -> '%s'", message, rule_result)
        # 规则补全后，再检查是否还模糊
        is_ambiguous2, _ = is_query_ambiguous(rule_result, plan)
        if not is_ambiguous2:
            return rule_result
        # 还模糊，但规则给了方向，用这个作为 LLM 的输入
        base_query = rule_result

    # Step 4: LLM 重写（异步，由调用方决定是否在 orchestrator 中执行）
    # 这里返回 base_query，如果调用方传了 lite_client，可以在外部异步调用 _llm_rewrite_query
    if lite_client is not None:
        # 注意：这个函数是同步的，返回 base_query 给 orchestrator
        # orchestrator 可以选择是否异步调用 llm rewrite
        # 为了简化，我们在 orchestrator 中检测后异步调用
        pass

    return base_query


async def rewrite_query_async(
    message: str,
    session: SessionState,
    plan: SemanticPlan | None = None,
    lite_client=None,
) -> str:
    """
    查询重写异步版（完整链路：规则 + LLM）。
    由 orchestrator 调用。
    """
    # Step 1-3: 同步规则处理
    filters = " ".join(item.value for item in session.filters)
    exclusions = " ".join(item.value for item in session.exclusions)
    parts = []
    if session.pending_subject and session.pending_subject not in message:
        parts.append(session.pending_subject)
    parts.append(message.strip())
    if filters:
        parts.append(f"已确认条件: {filters}")
    if exclusions:
        parts.append(f"排除条件: {exclusions}")
    base_query = " ".join(parts)

    # 模糊检测
    is_ambiguous, reason = is_query_ambiguous(base_query, plan)
    if not is_ambiguous:
        return base_query

    logger.info("Ambiguous query detected (%s): '%s'", reason, base_query)

    # 规则兜底
    rule_result = _rule_rewrite(message, session, plan) if plan else None
    if rule_result:
        logger.info("Rule rewrite: '%s' -> '%s'", message, rule_result)
        is_ambiguous2, _ = is_query_ambiguous(rule_result, plan)
        if not is_ambiguous2:
            return rule_result
        base_query = rule_result

    # LLM 重写（小模型）
    if lite_client is not None and plan is not None:
        rewritten = await _llm_rewrite_query(lite_client, message, session, plan)
        if rewritten != message:
            return rewritten

    return base_query

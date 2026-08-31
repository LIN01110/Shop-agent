"""
Purpose: VLM 图像语义理解器。
将 VLM 的自然语言描述进一步结构化，提取关键商品特征，用于增强 RAG 检索 query。

核心设计：
- VLM 负责"看懂"图片（颜色、款式、品类等）
- 本模块负责将 VLM 输出结构化，与现有视觉匹配结果融合
- 最终生成 enriched image_summary，拼入检索 query
"""

from dataclasses import dataclass, field
from typing import Protocol

from server.llm.vlm_client import NoOpVLMClient, VLMClient


@dataclass(frozen=True)
class ImageUnderstandingResult:
    """VLM 对图片的结构化理解结果。"""

    raw_description: str = ""               # VLM 原始输出
    product_type_hint: str = ""             # 推断的品类（如"连衣裙"）
    color_hint: str = ""                    # 颜色（如"红色"）
    style_hint: str = ""                    # 款式（如"V领、碎花、中长款"）
    fabric_hint: str = ""                   # 面料（如"雪纺"）
    pattern_hint: str = ""                  # 图案（如"碎花"）
    occasion_hint: str = ""                 # 适用场景（如"春夏日常"）
    brand_hint: str = ""                    # 品牌线索
    confidence: str = "medium"              # 理解置信度：high / medium / low


class ImageUnderstandingProvider(Protocol):
    """图像理解提供者协议。"""

    def understand(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> ImageUnderstandingResult:
        """对图片进行结构化理解。"""
        ...


class VLMImageUnderstandingProvider:
    """基于 VLM 的图像理解实现。"""

    def __init__(
        self,
        vlm_client: VLMClient,
        prompt_template: str = "",
    ) -> None:
        self.vlm_client = vlm_client
        self.prompt_template = prompt_template or self._default_prompt()

    def understand(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> ImageUnderstandingResult:
        raw = self.vlm_client.describe_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=self.prompt_template,
        )
        if not raw:
            return ImageUnderstandingResult(
                raw_description="",
                confidence="low",
            )
        return self._parse_description(raw)

    def _parse_description(self, raw: str) -> ImageUnderstandingResult:
        """从 VLM 自然语言描述中提取结构化特征。"""
        lower = raw.lower()

        # 颜色关键词提取
        color_hint = _extract_field(lower, _COLOR_KEYWORDS)

        # 品类关键词提取
        product_type_hint = _extract_field(lower, _PRODUCT_TYPE_KEYWORDS)

        # 面料关键词提取
        fabric_hint = _extract_field(lower, _FABRIC_KEYWORDS)

        # 图案关键词提取
        pattern_hint = _extract_field(lower, _PATTERN_KEYWORDS)

        # 款式/版型关键词提取
        style_hint = _extract_field(lower, _STYLE_KEYWORDS)

        # 场景关键词提取
        occasion_hint = _extract_field(lower, _OCCASION_KEYWORDS)

        # 品牌（简单正则匹配常见品牌名）
        brand_hint = _extract_brand(raw)

        # 置信度：描述越长、匹配字段越多，置信度越高
        confidence = self._estimate_confidence(raw, color_hint, product_type_hint, style_hint)

        return ImageUnderstandingResult(
            raw_description=raw,
            product_type_hint=product_type_hint,
            color_hint=color_hint,
            style_hint=style_hint,
            fabric_hint=fabric_hint,
            pattern_hint=pattern_hint,
            occasion_hint=occasion_hint,
            brand_hint=brand_hint,
            confidence=confidence,
        )

    def _estimate_confidence(
        self,
        raw: str,
        color: str,
        product_type: str,
        style: str,
    ) -> str:
        """根据提取到的字段丰富度估计置信度。"""
        score = 0
        if len(raw) > 20:
            score += 1
        if color:
            score += 1
        if product_type:
            score += 2
        if style:
            score += 1
        if score >= 4:
            return "high"
        if score >= 2:
            return "medium"
        return "low"

    def _default_prompt(self) -> str:
        return (
            "请详细描述这张图片中的商品。按以下维度回答，每个维度用一句话：\n"
            "1. 商品品类（如连衣裙、运动鞋、手机壳）\n"
            "2. 主要颜色\n"
            "3. 款式/版型（如V领、圆领、宽松、修身）\n"
            "4. 面料/材质（如棉、雪纺、皮革、硅胶）\n"
            "5. 图案/花纹（如纯色、碎花、条纹、格子）\n"
            "6. 适用场景/季节\n"
            "7. 品牌（如有可见品牌标识）\n"
            "请用中文简洁回答。"
        )


class NoOpImageUnderstandingProvider:
    """空实现，用于 VLM 未启用时。"""

    def understand(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> ImageUnderstandingResult:
        return ImageUnderstandingResult(raw_description="", confidence="low")


# 关键词表
_COLOR_KEYWORDS = [
    "红色", "黑色", "白色", "蓝色", "绿色", "黄色", "紫色", "粉色", "橙色",
    "灰色", "棕色", "米色", "藏青", "酒红", "墨绿", "卡其", "深蓝", "浅蓝",
    "玫红", "荧光", "透明", "银色", "金色", "玫瑰金", "香槟", "驼色", "杏色",
    "大红", "鲜红", "暗红", "深红", "浅红", "正红", "砖红", "绯红", "赤红",
    "纯黑", "漆黑", "深灰", "浅灰", "银灰", "烟灰", "炭灰", "奶白", "米白",
    "乳白", "纯白", "象牙白", "天蓝", "湖蓝", "钴蓝", "宝蓝", "靛蓝", "蔚蓝",
    "翠绿", "草绿", "橄榄绿", "墨绿", "嫩绿", "黄绿", "青绿", "湖绿", "苹果绿",
    "柠檬黄", "鹅黄", "土黄", "金黄", "姜黄", "橘黄", "橙黄", "棕黄", "栗色",
    "紫色", "淡紫", "紫红", "紫罗兰", "藕荷", "葡萄紫", "茄紫", "青紫",
]

_PRODUCT_TYPE_KEYWORDS = [
    "连衣裙", "半身裙", "短裙", "长裙", "A字裙", "百褶裙", "包臀裙", "伞裙",
    "衬衫", "T恤", "卫衣", "毛衣", "针织衫", "外套", "夹克", "风衣", "大衣",
    "西装", "马甲", "背心", "吊带", "抹胸", "旗袍", "汉服",
    "牛仔裤", "休闲裤", "西裤", "运动裤", "瑜伽裤", "打底裤", "短裤", "工装裤",
    "运动鞋", "跑鞋", "篮球鞋", "板鞋", "帆布鞋", "高跟鞋", "平底鞋", "凉鞋", "拖鞋", "靴子", "马丁靴", "雪地靴", "乐福鞋", "穆勒鞋", "玛丽珍鞋",
    "包包", "手提包", "单肩包", "双肩包", "斜挎包", "钱包", "手拿包", "旅行包", "腰包", "帆布包", "托特包", "水桶包", "马鞍包", "链条包", "贝壳包",
    "眼霜", "面霜", "精华", "乳液", "爽肤水", "面膜", "洗面奶", "卸妆", "防晒", "隔离", "粉底", "口红", "眼影", "睫毛膏", "腮红", "眉笔", "眼线", "遮瑕", "气垫", "散粉", "定妆",
    "手机", "手机壳", "耳机", "充电宝", "数据线", "充电器", "平板", "笔记本", "键盘", "鼠标", "显示器", "智能手表", "手环",
    "零食", "坚果", "巧克力", "饼干", "糖果", "果干", "肉干", "海苔", "薯片", "饮料", "茶", "咖啡", "奶粉", "蛋白粉", "维生素",
]

_FABRIC_KEYWORDS = [
    "棉", "纯棉", "棉质", "全棉", "有机棉",
    "丝", "真丝", "蚕丝", "丝绸", "缎面",
    "麻", "亚麻", "苎麻",
    "羊毛", "羊绒", "呢子", "毛呢", "双面呢",
    "化纤", "聚酯纤维", "涤纶", "锦纶", "氨纶", "腈纶", "粘胶", "莫代尔", "莱赛尔", "天丝",
    "牛仔", "牛仔布", "牛仔面料",
    "皮革", "真皮", "PU", "人造革", "麂皮", "绒面革",
    "蕾丝", "欧根纱", "雪纺", "纱", "网纱",
    "针织", "针织面料", "毛圈", "摇粒绒", "珊瑚绒", "法兰绒",
    "帆布", "牛津纺", "灯芯绒", "塔夫绸", "绸缎", "丝绒", "天鹅绒",
    "硅胶", "TPU", "PC", "ABS", "铝合金", "不锈钢", "玻璃", "陶瓷", "木质", "塑料", "橡胶", "碳纤维",
]

_PATTERN_KEYWORDS = [
    "纯色", "单色", "素色", "净色", "裸色",
    "碎花", "小花", "大花", "花朵", "花卉",
    "条纹", "横条纹", "竖条纹", "斜条纹", "条纹图案",
    "格子", "格纹", "千鸟格", "苏格兰格", "棋盘格",
    "波点", "圆点", "波点图案", "点状",
    "豹纹", "斑马纹", "蛇纹", "动物纹",
    "迷彩", "迷彩图案", "数码迷彩",
    "几何", "几何图案", "抽象", "抽象图案",
    "印花", "数码印花", "热转印", "胶印", "刺绣", "刺绣图案", "提花", "织花", "镂空", "钩花", "压花", "烫金",
    "渐变色", "渐层", "晕染", "扎染", "扎染图案", "蜡染", "蜡染图案",
    "卡通", "卡通图案", "动漫", "联名", "IP", "联名图案",
    "Logo", "标志", "品牌标志", "字母", "字母图案", "文字", "印花文字",
    "撞色", "拼色", "拼接", "渐变", "彩虹", "彩虹色",
    "透明", "半透明", "磨砂", "哑光", "亮面", "珠光", "金属光泽", "镜面",
]

_STYLE_KEYWORDS = [
    "V领", "圆领", "U领", "方领", "一字领", "高领", "半高领", "低领", "深V", "荡领", "荷叶领", "娃娃领", "衬衫领", "立领", "翻领", "POLO领",
    "长袖", "短袖", "无袖", "七分袖", "五分袖", "九分袖", "泡泡袖", "蝙蝠袖", "喇叭袖", "灯笼袖", "插肩袖", "落肩", "飞飞袖", "荷叶袖",
    "修身", "宽松", "oversize", "直筒", "A字", "茧型", "H型", "X型", "收腰", "高腰", "低腰", "中腰", "松紧腰",
    "短款", "中长款", "长款", "超长款", "及膝", "过膝", "及踝", "迷你", "迷你裙", "超短", "九分", "七分", "五分", "三分",
    "开叉", "侧开叉", "后开叉", "前开叉", "不对称", "不规则", "拼接", "破洞", "磨白", "做旧", "水洗", "酵素洗", "石磨",
    "复古", "港风", "法式", "日系", "韩系", "欧美", "极简", "极简风", "性冷淡", "极简主义", "极简风格",
    "休闲", "商务", "正装", "职业装", "运动", "运动风", "街头", "街头风", "嘻哈", "朋克", "摇滚", "哥特", "洛丽塔", "JK", "学院风", "通勤", "通勤风", "度假", "度假风", "沙滩", "田园", "森系", "文艺", "文艺风", "复古风", "古风", "国潮", "新中式", "中式", "汉服", "旗袍风",
    "甜美", "可爱", "少女", "少女风", "成熟", "知性", "优雅", "优雅风", "性感", "性感风", "简约", "简约风", "大气", "高级", "高级感", "高街", "轻奢", "轻奢风", "奢华", "奢华风", "华丽",
]

_OCCASION_KEYWORDS = [
    "日常", "日常穿搭", "日常穿着", "日常休闲", "休闲", "居家", "居家服", "睡衣", "居家穿着",
    "通勤", "上班", "职场", "商务", "商务休闲", "正式", "正装", "宴会", "晚宴", "派对", "聚会",
    "运动", "健身", "瑜伽", "跑步", "户外", "徒步", "登山", "骑行", "游泳", "潜水", "滑雪",
    "旅行", "旅游", "度假", "沙滩", "海边", "露营", "野餐",
    "约会", "逛街", "购物", "拍照", "出片", "网红", "打卡",
    "春夏", "春秋", "秋冬", "夏季", "冬季", "早春", "初夏", "深秋", "初冬", "四季", "换季", "全季",
    "春", "夏", "秋", "冬", "四季", "全季节", "换季",
    "晨跑", "夜跑", "晨间", "夜间", "白天", "夜晚", "白天晚上", "昼夜",
]

_BRAND_KEYWORDS = [
    "ZARA", "H&M", "UNIQLO", "优衣库", "MUJI", "无印良品", "GAP", "Forever21", "Topshop", "Mango", "Stradivarius", "Pull&Bear", "Bershka", "MassimoDutti", "Oysho", "Uterque",
    "Nike", "耐克", "Adidas", "阿迪达斯", "Puma", "彪马", "Reebok", "锐步", "NewBalance", "新百伦", "Converse", "匡威", "Vans", "万斯", "FILA", "斐乐", "Skechers", "斯凯奇", "Asics", "亚瑟士", "Mizuno", "美津浓", "UnderArmour", "安德玛", "Lululemon", "露露乐檬", "Columbia", "哥伦比亚", "TheNorthFace", "北面", "Patagonia", "巴塔哥尼亚", "Arc'teryx", "始祖鸟", "Salomon", "萨洛蒙", "Hoka", "On昂跑", "OnRunning",
    "Chanel", "香奈儿", "Dior", "迪奥", "Gucci", "古驰", "Prada", "普拉达", "LV", "LouisVuitton", "路易威登", "Hermes", "爱马仕", "Burberry", "博柏利", "Balenciaga", "巴黎世家", "Givenchy", "纪梵希", "YSL", "YvesSaintLaurent", "圣罗兰", "Celine", "思琳", "Fendi", "芬迪", "Valentino", "华伦天奴", "Versace", "范思哲", "Armani", "阿玛尼", "Dolce&Gabbana", "杜嘉班纳", "BottegaVeneta", "葆蝶家", "MiuMiu", "缪缪", "Loewe", "罗意威", "AlexanderMcQueen", "亚历山大麦昆", "Balmain", "巴尔曼", "MaxMara", "麦丝玛拉", "ToryBurch", "汤丽柏琦", "Coach", "蔻驰", "MichaelKors", "迈克高仕", "KateSpade", "凯特丝蓓", "MarcJacobs", "马克雅可布", "Tiffany", "蒂芙尼", "Cartier", "卡地亚", "Bvlgari", "宝格丽", "VanCleef&Arpels", "梵克雅宝",
    "Apple", "苹果", "iPhone", "iPad", "MacBook", "AirPods", "AppleWatch", "三星", "Samsung", "华为", "Huawei", "小米", "Xiaomi", "OPPO", "Vivo", "一加", "OnePlus", "荣耀", "Honor", "红米", "Redmi", "Realme", "真我", "魅族", "Meizu", "联想", "Lenovo", "戴尔", "Dell", "惠普", "HP", "华硕", "ASUS", "宏碁", "Acer", "微软", "Microsoft", "Surface", "索尼", "Sony", "任天堂", "Nintendo", "Switch",
    "SK-II", "雅诗兰黛", "EsteeLauder", "兰蔻", "Lancome", "迪奥", "Dior", "香奈儿", "Chanel", "资生堂", "Shiseido", "倩碧", "Clinique", "海蓝之谜", "LaMer", "赫莲娜", "HR", "莱珀妮", "LaPrairie", "娇兰", "Guerlain", "娇韵诗", "Clarins", "馥蕾诗", "Fresh", "科颜氏", "Kiehl's", "理肤泉", "LaRoche-Posay", "薇姿", "Vichy", "雅漾", "Avene", "欧缇丽", "Caudalie", "修丽可", "SkinCeuticals", "宝拉珍选", "Paula'sChoice", "TheOrdinary", "HFP", "HomeFacialPro", "完美日记", "PerfectDiary", "花西子", "Floriasis", "橘朵", "Judydoll", "Colorkey", "珂拉琪", "IntoYou", "Intoyou", "酵色", "Joocyee", "Girlcult", "毛戈平", "MAOGEPING", "彩棠", "TIMAGE", "珀莱雅", "PROYA", "薇诺娜", "WINONA", "润百颜", "BIOHYALUX", "夸迪", "QUADHA", "华熙生物", "BLOOMAGE", "自然堂", "CHANDO", "百雀羚", "PECHOIN", "相宜本草", "INOHERB", "佰草集", "Herborist", "玉泽", "Dr.Yu", "敷尔佳", "VOOLGA", "可复美", "COMFY", "韩束", "KANS", "欧诗漫", "OSM", "水密码", "WETCODE", "丸美", "MARUBI", "卡姿兰", "Carslan", "玛丽黛佳", "MarieDalgar", "ZEESEA", "滋色", "美宝莲", "Maybelline", "欧莱雅", "L'Oreal", "美即", "MG", "妮维雅", "NIVEA", "曼秀雷敦", "Mentholatum", "DHC", "蝶翠诗", "Fancl", "芳珂", "SKINFOOD", "思亲肤", "Innisfree", "悦诗风吟", "EtudeHouse", "爱丽小屋", "TheFaceShop", "菲诗小铺", "Missha", "谜尚", "TonyMoly", "魔法森林", "HolikaHolika", "3CE", "Stylenanda", "CLIO", "珂莱欧", "Peripera", "菲丽菲拉", "Romand", "MAC", "魅可", "NARS", "纳斯", "BobbiBrown", "芭比波朗", "Benefit", "贝玲妃", "UrbanDecay", "衰败城市", "Anastasia", "TooFaced", "Tarte", "KatVonD", "FentyBeauty", "Glossier", "CharlotteTilbury", "PatMcGrath", "HudaBeauty", "KylieCosmetics", "Kylie", "JeffreeStar", "Morphe", "ColourPop", "卡乐泡泡", "WetnWild", "湿又野", "e.l.f.", "Essence", "Catrice", "Rimmel", "芮谜", "Revlon", "露华浓", "L.A.Girl", "洛杉矶女孩", "Milani", "PhysiciansFormula", "W7", "MakeupRevolution", " revolution", "Sleek", " BarryM", "Collection", "MissSporty", "Bourjois", "妙巴黎", "MaxFactor", "蜜丝佛陀", "Covergirl", "CoverGirl", "Maybelline", "美宝莲纽约", "Garnier", "卡尼尔", "L'OrealParis", "巴黎欧莱雅", "GarnierSkinActive", "Neutrogena", "露得清", "Olay", "玉兰油", "Ponds", "旁氏", "Clean&Clear", "可伶可俐", "Cetaphil", "丝塔芙", "CeraVe", "适乐肤", "Eucerin", "优色林", "Aveeno", "艾惟诺", "Vaseline", "凡士林", "Palmers", "帕玛氏", "Bio-Oil", "百洛油", "Lucas'Papaw", "木瓜膏", "Burt'sBees", "小蜜蜂", "Kiehl's", "科颜氏", "Origins", "悦木之源", "Clinique", "倩碧", "EsteeLauder", "雅诗兰黛", "LaMer", "海蓝之谜", "Sisley", "希思黎", "Sulwhasoo", "雪花秀", "Whoo", "后", "Hera", "赫妍", "IOPE", "亦博", "Laneige", "兰芝", "Mamonde", "梦妆", "Innisfree", "悦诗风吟", "TheFaceShop", "菲诗小铺", "NatureRepublic", "自然乐园", "EtudeHouse", "爱丽小屋", "Missha", "谜尚", "HolikaHolika", "Skinfood", "思亲肤", "TonyMoly", "魔法森林", "3CE", "Stylenanda", "Peripera", "菲丽菲拉", "Romand", "CLIO", "珂莱欧", "BanilaCo", "芭妮兰", "Heimish", "heimish", "COSRX", "柯丝艾丝", "SomeByMi", "莎柏蜜", "Dr.Jart+", "蒂佳婷", " mediheal", "美迪惠尔", "Jayjun", "JAYJUN", "SNP", "PapaRecipe", "春雨", "AHC", "爱和纯", "PyunkangYul", "扁康率", "RoundLab", "柔恩莱", "Isntree", "艾丝特树", "Benton", "本顿", "Purito", "璞丽拓", "DearKlairs", "克莱尔斯", "I'mFrom", "爱肤兰", "ByWishtrend", "慧诗纯", "Neogen", "妮珍", "RealBarrier", "丽欧蓓莉", "A'pieu", "奥普", "TheSaem", "得鲜", "SecretKey", "丝柯莉", "It'sSkin", "伊思", "Acwell", "艾珂薇", "Leaders", "丽得姿", "Hanskin", "韩斯清", "TooCoolForSchool", "涂酷", "3WClinic", "W.Lab", "Wlab", "WLab", "Chosungah22", "潮盛雅22", "AprilSkin", "魔法素颜", "Eglips", "伊格丽", "Lilybyred", " lilybyred", "BBIA", "Bbia", "bbia", "Colorgram", "ColorgramTok", "Colorgram:tok", "WAKEMAKE", "Wakemake", "wakemake", "Hince", "hince", "Hera", "赫拉", "Espoir", "艾丝珀", "Moonshot", "茉姗", "Stila", "诗狄娜", "Tarte", "Benefit", "贝玲妃", "Anastasia", "BrowWiz", "ABH", "Norvina", "Subculture", "ModernRenaissance", "SoftGlam", "CarliBybel", "JackieAina", "Riviera", "AlyssaEdwards", "Amrezy", "Amrezy", "Sultry", "MarioDedivanovic", "MasterPalette", "Prism", "Aura", "Sunset", "DesertDusk", "NewNude", "MercuryRetrograde", "RoseGold", "Lilac", "Topaz", "Ruby", "Emerald", "Sapphire", "Amethyst", "RoseQuartz", "Aqua", "Coral", "TigerEye", "Obsidian", "Scorpio", "Libra", "Virgo", "Leo", "Cancer", "Gemini", "Taurus", "Aries", "Pisces", "Aquarius", "Capricorn", "Sagittarius",
]


def _extract_field(text: str, keywords: list[str]) -> str:
    """从文本中提取匹配的关键词，返回逗号分隔的匹配项。"""
    text_lower = text.lower()
    matches = [kw for kw in keywords if kw.lower() in text_lower]
    if not matches:
        return ""
    # 去重并保持原始顺序
    seen = set()
    unique = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            unique.append(m)
    return "、".join(unique[:5])  # 最多取 5 个


def _extract_brand(text: str) -> str:
    """从文本中提取品牌名。"""
    for brand in _BRAND_KEYWORDS:
        if brand.lower() in text.lower():
            return brand
    return ""

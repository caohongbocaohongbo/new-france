"""龙虎榜席位归一化与画像纯函数（离线可测）。"""
import re

# 知名游资席位人工映射（营业部改名/别名需人工维护）
KNOWN_SEATS = {
    "中国银河证券绍兴": "章盟主",
    "国泰君安上海江苏路": "章盟主",
    "华鑫证券上海宛平南路": "炒股养家",
    "东方财富拉萨团结路": "拉萨天团",
    "东方财富拉萨东环路": "拉萨天团",
    "国盛证券宁波桑田路": "宁波桑田路",
    "华泰证券深圳益田路荣超商务中心": "深圳益田路",
}

QUANT_MARKERS = ("量化", "灵均", "九坤", "幻方", "明汯", "衍复", "启林")


def normalize_seat_name(name: str) -> str:
    """去空格/括号/营业部后缀，得到归一化席位名。"""
    text = str(name or "").strip()
    text = re.sub(r"[（(].*?[)）]", "", text)
    text = text.replace("证券营业部", "").replace("营业部", "")
    text = text.replace("股份有限公司", "").replace("有限责任公司", "")
    text = text.replace("有限公司", "").replace("证券", "")
    text = re.sub(r"\s+", "", text)
    return text.strip() or str(name or "").strip()


def classify_seat_type(name: str) -> str:
    """席位类型：机构/沪股通/深股通/量化/游资/普通。"""
    text = str(name or "")
    if "机构专用" in text:
        return "机构"
    if "沪股通" in text:
        return "沪股通"
    if "深股通" in text:
        return "深股通"
    if any(m in text for m in QUANT_MARKERS):
        return "量化"
    if _known_seat_label(text):
        return "游资"
    return "普通"


def known_seat_label(name: str):
    """返回知名游资标签（无则 None）。"""
    return _known_seat_label(name)


def _known_seat_label(name: str):
    norm = normalize_seat_name(name)
    for key, label in KNOWN_SEATS.items():
        k = normalize_seat_name(key)
        if k and (k in norm or norm in k):
            return label
    return None


def match_confidence(raw_name: str) -> float:
    """归一化置信度：括号被剥离或含别名时置信度下调，低置信度不自动合并。"""
    text = str(raw_name or "")
    confidence = 1.0
    if re.search(r"[（(]", text):
        confidence -= 0.2
    if len(text) < 6:
        confidence -= 0.3
    return round(max(0.0, min(1.0, confidence)), 2)


def build_seat_profile(samples: list) -> dict:
    """按席位聚合历史样本统计胜率/平均收益/偏好。

    samples: [{ret_t1, ret_t3, ret_t5, board_type, theme, side}]
    无前视：样本收益应为上榜日之后的 T+N（由 service 保证）。
    """
    if not samples:
        return {}
    n = len(samples)
    def win_rate(key):
        vals = [s.get(key) for s in samples if s.get(key) is not None]
        return round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None
    boards = [s.get("board_type") for s in samples if s.get("board_type")]
    themes = [s.get("theme") for s in samples if s.get("theme")]
    avg = [s.get("ret_t1") for s in samples if s.get("ret_t1") is not None]
    return {
        "sample_count": n,
        "win_rate_t1": win_rate("ret_t1"),
        "win_rate_t3": win_rate("ret_t3"),
        "win_rate_t5": win_rate("ret_t5"),
        "avg_return_t1": round(sum(avg) / len(avg), 4) if avg else None,
        "prefer_board": max(set(boards), key=boards.count) if boards else None,
        "prefer_theme": max(set(themes), key=themes.count) if themes else None,
    }

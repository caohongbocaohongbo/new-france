"""26 事件驱动雷达：事件分类 + 影响打分（纯函数，可单测）。"""

# (事件类型, 影响分, 关键词列表)
EVENT_RULES = [
    ("重组并购", 5, ["重大资产重组", "资产重组", "收购", "并购", "借壳", "要约收购", "吸收合并"]),
    ("增持回购", 4, ["增持", "回购", "举牌", "实际控制人增持", "高管增持"]),
    ("减持套现", -4, ["减持", "大宗交易", "清仓式减持", "股份质押预警"]),
    ("研报上调", 3, ["买入评级", "增持评级", "目标价上调", "上调评级", "强推"]),
    ("研报下调", -3, ["卖出评级", "减持评级", "目标价下调", "下调评级", "回避"]),
    ("龙虎榜机构", 3, ["机构专用", "机构净买入", "机构席位"]),
    ("限售解禁", -2, ["解禁", "限售股上市流通", "首发原股东限售"]),
]


def classify_score(title: str, description: str = "") -> dict:
    """对事件文本分类并给影响分。返回 {event_type, impact_score, matched_keywords}。"""
    text = f"{title or ''} {description or ''}"
    best = {"event_type": "其他", "impact_score": 0, "matched_keywords": []}
    for etype, score, keywords in EVENT_RULES:
        hits = [kw for kw in keywords if kw in text]
        if hits and abs(score) > abs(best["impact_score"]):
            best = {"event_type": etype, "impact_score": score, "matched_keywords": hits}
    return best

"""毒素类别参考数据：潜伏期、留观窗口与关键复查间隔。

外观描述与手机识图只能给出疑似类别，剩余样本毒检才能确认；
只要某类别被疑似或确认，其窗口与复查要求即并入留观计划。
未明毒素按最严口径留观。
"""

TOXIN_CLASSES: dict[str, dict] = {
    "amatoxin": {
        "label": "鹅膏肽类（肝损害型）",
        "latency_hours": 24,
        "watch_hours": 96,
        "critical_labs": {"ALT": 12, "AST": 12, "INR": 12, "CREATININE": 12},
    },
    "gyromitrin": {
        "label": "鹿花菌素类（溶血型）",
        "latency_hours": 12,
        "watch_hours": 48,
        "critical_labs": {"ALT": 12, "HGB": 12, "CREATININE": 12},
    },
    "rhabdo": {
        "label": "横纹肌溶解型",
        "latency_hours": 24,
        "watch_hours": 72,
        "critical_labs": {"CK": 12, "CREATININE": 12, "K": 12},
    },
    "orellanine": {
        "label": "奥来毒素类（肾损害型）",
        "latency_hours": 72,
        "watch_hours": 336,
        "critical_labs": {"CREATININE": 24, "BUN": 24},
    },
    "muscarine": {
        "label": "毒蝇碱类（副交感兴奋型）",
        "latency_hours": 2,
        "watch_hours": 12,
        "critical_labs": {},
    },
    "gi_irritant": {
        "label": "胃肠炎型",
        "latency_hours": 3,
        "watch_hours": 24,
        "critical_labs": {},
    },
    "neurotoxic": {
        "label": "神经精神型",
        "latency_hours": 3,
        "watch_hours": 24,
        "critical_labs": {},
    },
    "unknown": {
        "label": "未明毒素（按最严口径留观）",
        "latency_hours": 24,
        "watch_hours": 72,
        "critical_labs": {"ALT": 12, "AST": 12, "INR": 12, "CREATININE": 12},
    },
}

DEFAULT_CLASS = "unknown"

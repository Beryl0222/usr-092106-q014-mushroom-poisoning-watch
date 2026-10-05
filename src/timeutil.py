"""时间解析工具：一律使用带时区的 ISO 8601。"""

from datetime import datetime


def parse(value: str) -> datetime:
    """解析带时区的 ISO 8601 时间；缺少时区视为数据错误。"""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"时间必须带时区：{value}")
    return moment


def iso(moment: datetime) -> str:
    return moment.isoformat()

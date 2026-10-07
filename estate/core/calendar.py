"""The operating calendar matches the Korean dashboard's scheduled refresh."""
from datetime import datetime
from zoneinfo import ZoneInfo


def today(now=None):
    return (now or datetime.now(ZoneInfo('Asia/Seoul'))).astimezone(ZoneInfo('Asia/Seoul')).date()

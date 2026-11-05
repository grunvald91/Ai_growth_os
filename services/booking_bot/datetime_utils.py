"""UTC timestamps compatible with ServGo's existing timezone-naive columns."""
from datetime import datetime, timezone

def utc_now() -> datetime:
    """Keep the stored UTC format without using deprecated datetime.utcnow."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

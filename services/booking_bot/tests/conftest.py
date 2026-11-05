"""Общие настройки тестов: блог не ходит в сеть за лентой платформы."""
import os

os.environ.setdefault("BLOG_FEED_URL", "")

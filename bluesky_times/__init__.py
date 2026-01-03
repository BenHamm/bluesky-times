"""
Bluesky Times - A daily printed digest of your Bluesky feed
"""
from .generator import BlueskyTimesGenerator
from .config import FAVORITE_ACCOUNTS, DEFAULT_MODEL

__version__ = "1.0.0"
__all__ = ["BlueskyTimesGenerator", "FAVORITE_ACCOUNTS", "DEFAULT_MODEL"]


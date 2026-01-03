"""
Configuration for Bluesky Times
"""
import os

# LLM Configuration (via OpenRouter)
OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY",
    "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"
)
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

# Bluesky credentials (from environment)
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "vmh6-2owg-yhql-lkr2")

# Favorite accounts - prioritized voices
# These appear first in themed sections and get dedicated "From Voices I Follow" section
FAVORITE_ACCOUNTS = [
    "theophite.bsky.social",      # Rev. Howard Arson
    "jbouie.bsky.social",          # Jamelle Bouie
    "proptermalone.bsky.social",   # post malone ergo propter malone
    "samthielman.com",             # Sam Thielman (CHOAM Nomsky)
    "dieworkwear.bsky.social",     # Derek Guy
    "reckless.bsky.social",        # Nilay Patel
]

# Timeline settings
DEFAULT_POST_LIMIT = 250  # Number of posts to fetch
TARGET_PAGE_COUNT = (9, 11)  # Target page range for PDF

# Cache settings
CACHE_FILE = "cache.json"


"""
Configuration for Bluesky Times

Set these environment variables or create a .env file:
- BLUESKY_HANDLE: Your Bluesky handle (e.g., user.bsky.social)
- BLUESKY_APP_PASSWORD: App password from Bluesky settings
- OPENROUTER_API_KEY: API key from openrouter.ai
"""
import os
import sys

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, use environment variables directly

# LLM Configuration (via OpenRouter)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

# Bluesky credentials (from environment)
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD")

def validate_config():
    """Check that required environment variables are set"""
    missing = []
    if not OPENROUTER_API_KEY:
        missing.append("OPENROUTER_API_KEY")
    if not BLUESKY_APP_PASSWORD:
        missing.append("BLUESKY_APP_PASSWORD")
    
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("   Set them in your environment or create a .env file.")
        print("   See env.example for a template.")
        sys.exit(1)

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


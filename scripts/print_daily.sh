#!/bin/bash
# Bluesky Times - Daily Print Script
# 
# Usage: ./scripts/print_daily.sh [handle]
# Add to crontab: 0 7 * * * /path/to/bluesky-times/scripts/print_daily.sh

set -e

# Navigate to project root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Activate venv and set library path for WeasyPrint
source venv/bin/activate
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"

# Use provided handle or default
HANDLE="${1:-${BLUESKY_HANDLE:-benergetic.bsky.social}}"

echo "📰 Generating The Bluesky Times for @${HANDLE}..."

# Generate today's edition
python -m bluesky_times.cli "$HANDLE"

# Get today's PDF filename
PDF_FILE="bluesky_times_$(date +%Y-%m-%d).pdf"

# Print if file exists
if [ -f "$PDF_FILE" ]; then
    echo "🖨️  Sending to printer..."
    lpr -o sides=two-sided-long-edge "$PDF_FILE"
    echo "✅ Sent to printer: $PDF_FILE"
else
    echo "❌ PDF not found: $PDF_FILE"
    exit 1
fi

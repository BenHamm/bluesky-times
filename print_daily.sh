#!/bin/bash
# Bluesky Times - Daily Print Script
# Add to crontab: 0 7 * * * /Users/bhamm/Bluesky\ Times/print_daily.sh

cd "/Users/bhamm/Bluesky Times"

# Activate venv and set library path for WeasyPrint
source venv/bin/activate
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"

# Generate today's edition
python bluesky_times.py benergetic.bsky.social

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


#!/bin/bash
# Print the daily Bluesky Times
# Cross-platform compatible (macOS + Linux)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# macOS needs this for WeasyPrint
if [[ "$OSTYPE" == "darwin"* ]]; then
    export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"
fi

# Generate PDF
echo "Generating Bluesky Times..."
python -m bluesky_times.cli "${1:-benergetic.bsky.social}" -o /tmp/bluesky_times_today.pdf

# Print using available command
PDF_FILE="/tmp/bluesky_times_today.pdf"

if command -v lpr &> /dev/null; then
    # macOS and some Linux distros
    lpr -o sides=two-sided-long-edge "$PDF_FILE"
    echo "Sent to printer via lpr"
elif command -v lp &> /dev/null; then
    # Ubuntu and other Linux distros
    lp -o sides=two-sided-long-edge "$PDF_FILE"
    echo "Sent to printer via lp"
else
    echo "ERROR: No print command found (lpr or lp)"
    echo "PDF saved to: $PDF_FILE"
    exit 1
fi

echo "Done!"

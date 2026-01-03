# The Bluesky Times 📰

A daily printed digest of your Bluesky feed, formatted as a beautiful newspaper-style PDF.

![Example Output](docs/example.png)

## Features

- **Newspaper-style layout** - Two-column format with elegant typography
- **Smart theme organization** - LLM-powered grouping of posts by topic (using Claude via OpenRouter)
- **Favorite voices prioritized** - Configure accounts whose posts appear prominently
- **Image support** - Embedded images rendered at print-quality sizes
- **Thread context** - Reply chains shown with proper context and summaries
- **Print-optimized** - Designed for double-sided printing on letter paper

## Quick Start

### 1. Install dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install -r requirements.txt

# Install system dependencies (macOS)
brew install pango glib
```

### 2. Configure

Copy `env.example` to `.env` and fill in your credentials:

```bash
cp env.example .env
```

You'll need:
- **Bluesky App Password**: Generate at Settings → App Passwords in Bluesky
- **OpenRouter API Key**: Get one at [openrouter.ai](https://openrouter.ai/) (for theme classification)

### 3. Generate your newspaper

```bash
# Using command line
python -m bluesky_times.cli your-handle.bsky.social

# Or with the original script
python bluesky_times.py your-handle.bsky.social
```

## Command Line Options

```
python -m bluesky_times.cli [OPTIONS] [HANDLE]

Arguments:
  HANDLE                  Your Bluesky handle (e.g., user.bsky.social)

Options:
  --cache                 Use cached data instead of fetching fresh
  --no-save               Don't save fetched data to cache
  --no-themes             Skip LLM theme classification
  --model MODEL           LLM model for themes (default: anthropic/claude-sonnet-4.5)
  --output, -o PATH       Output PDF path
```

## Customization

### Favorite Accounts

Edit `bluesky_times/config.py` to set your favorite accounts:

```python
FAVORITE_ACCOUNTS = [
    "favorite1.bsky.social",
    "favorite2.bsky.social",
    # ...
]
```

These accounts will:
- Appear first within themed sections
- Have a dedicated "From Voices I Follow" section
- Be marked with ★ in the output

### Daily Automation

For automated daily printing, add to your shell profile (e.g., `~/.zshrc`):

```bash
# Print Bluesky Times on first terminal of the day
if [ ! -f /tmp/bluesky_times_printed_$(date +%Y%m%d) ]; then
    cd /path/to/bluesky-times
    source venv/bin/activate
    python -m bluesky_times.cli your-handle.bsky.social
    lpr -o sides=two-sided-long-edge bluesky_times_*.pdf
    touch /tmp/bluesky_times_printed_$(date +%Y%m%d)
fi
```

## Project Structure

```
bluesky-times/
├── bluesky_times/           # Main package
│   ├── __init__.py
│   ├── cli.py               # Command-line interface
│   ├── config.py            # Configuration (favorites, API keys)
│   └── generator.py         # Core generation logic
├── scripts/
│   └── print_daily.sh       # Print automation script
├── requirements.txt
├── env.example              # Environment template
└── README.md
```

## How It Works

1. **Fetch** - Retrieves your Bluesky timeline via the AT Protocol
2. **Process** - Extracts posts, threads, images, and metadata
3. **Classify** - Uses Claude to identify 2-3 major themes
4. **Organize** - Groups posts by theme, prioritizes favorites
5. **Render** - Generates newspaper-style HTML with CSS for print
6. **Export** - Converts to PDF via WeasyPrint

## Contributing

Contributions welcome! Please open an issue or PR.

## License

MIT


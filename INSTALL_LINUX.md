# Installing Bluesky Times on Linux

This guide covers installation on Ubuntu/Debian-based systems, including NVIDIA Jetson devices.

## System Requirements

- Python 3.8+ (Python 3.9+ recommended for latest dependencies)
- Network printer configured via CUPS

## System Dependencies

```bash
sudo apt update
sudo apt install -y \
    python3-venv \
    python3-pip \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    cups-client
```

## Installation

### Clone the repository
```bash
git clone https://github.com/BenHamm/bluesky-times.git
cd bluesky-times
```

### Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### Install Python dependencies

**For Python 3.9+:**
```bash
pip install -r requirements.txt
```

**For Python 3.8 (Ubuntu 20.04, Jetson JetPack 5.x):**
```bash
pip install -r requirements-py38.txt
```

### Configure credentials
```bash
cp env.example .env
# Edit .env with your Bluesky and OpenRouter credentials
```

## Printer Setup

### Check available printers
```bash
lpstat -p -d
```

### Set default printer (if needed)
```bash
sudo lpadmin -d YOUR_PRINTER_NAME
```

## Usage

### Generate and print
```bash
source venv/bin/activate
python -m bluesky_times.cli your-handle.bsky.social
lp -o sides=two-sided-long-edge bluesky_times_*.pdf
```

### Or use the script
```bash
./scripts/print_daily.sh your-handle.bsky.social
```

### Quick test (cached data, no API calls)
```bash
python -m bluesky_times.cli --cache --no-themes
```

## Home Assistant Integration

See `ORIN_SETUP_DOCS.md` for full Home Assistant integration with Alexa voice commands.

### Basic shell_command integration
```yaml
# In configuration.yaml
shell_command:
  print_bluesky_times: '/path/to/bluesky-times/scripts/print_daily.sh'
```

## Troubleshooting

### WeasyPrint import errors on Python 3.8
Make sure you're using `requirements-py38.txt` which pins compatible versions.

### Print command not found
Ubuntu uses `lp` instead of `lpr`. The `print_daily.sh` script handles this automatically.

### PDF generates but doesn't print
Check printer status:
```bash
lpstat -p        # Printer status
lpstat -o        # Print queue
```

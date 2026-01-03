#!/usr/bin/env python3
"""
Command-line interface for Bluesky Times
"""
import os
import sys
import argparse
import httpx

from .config import (
    BLUESKY_HANDLE,
    BLUESKY_APP_PASSWORD,
    DEFAULT_MODEL,
    OPENROUTER_API_KEY,
    validate_config,
)
from .generator import BlueskyTimesGenerator


def main():
    parser = argparse.ArgumentParser(
        description='Generate The Bluesky Times - A daily printed digest of your Bluesky feed',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  bluesky-times user.bsky.social          # Generate PDF for user
  bluesky-times --cache                   # Regenerate from cached data
  bluesky-times user.bsky.social --no-themes  # Skip LLM theme classification
        """
    )
    parser.add_argument(
        'handle', 
        nargs='?', 
        help='Bluesky handle (e.g., user.bsky.social)'
    )
    parser.add_argument(
        '--cache', 
        action='store_true', 
        help='Use cached data instead of fetching fresh'
    )
    parser.add_argument(
        '--no-save', 
        action='store_true', 
        help='Do not save fetched data to cache'
    )
    parser.add_argument(
        '--no-themes', 
        action='store_true', 
        help='Skip LLM theme classification'
    )
    parser.add_argument(
        '--model', 
        default=DEFAULT_MODEL, 
        help=f'LLM model for themes (default: {DEFAULT_MODEL})'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output PDF path (default: bluesky_times_YYYY-MM-DD.pdf)'
    )
    
    args = parser.parse_args()
    
    # Validate required environment variables
    # Need Bluesky creds for fresh fetch, OpenRouter for themes
    if not args.cache or not args.no_themes:
        validate_config()
    
    # Get credentials from args or environment
    handle = args.handle or BLUESKY_HANDLE
    password = BLUESKY_APP_PASSWORD
    
    if not handle and not args.cache:
        handle = input("Enter your Bluesky handle (e.g., user.bsky.social): ")
    
    # Initialize generator
    if args.cache:
        # Minimal init for cache-only mode
        generator = BlueskyTimesGenerator.__new__(BlueskyTimesGenerator)
        generator.http_client = httpx.Client(timeout=10.0)
        generator.model = args.model
        generator.user_handle = handle or ""
    else:
        generator = BlueskyTimesGenerator(handle, password, model=args.model)
    
    # Generate the PDF
    output_path = generator.generate_pdf(
        output_path=args.output,
        use_cache=args.cache, 
        save_cache=not args.no_save, 
        use_themes=not args.no_themes
    )
    
    return 0


if __name__ == '__main__':
    sys.exit(main())


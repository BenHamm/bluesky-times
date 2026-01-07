#!/usr/bin/env python3
"""
The Arson Times - A digest of Rev Howard Arson's Bluesky posts
Focused single-author edition
"""
import os
import sys
import json
import base64
import httpx
from datetime import datetime
from dateutil import parser as date_parser
from atproto import Client
from jinja2 import Template
from weasyprint import HTML
import pytz

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configuration
ARSON_HANDLE = "theophite.bsky.social"
ARSON_DISPLAY_NAME = "Rev. Howard Arson"
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE", "benergetic.bsky.social")
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD")

# How many posts to fetch
POST_LIMIT = 100


class ArsonTimes:
    def __init__(self):
        self.client = Client()
        self.client.login(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)
        self.http_client = httpx.Client(timeout=10.0)
        self._post_cache = {}  # Cache for fetched posts
    
    def fetch_post_by_uri(self, uri: str) -> dict:
        """Fetch a single post by URI to hydrate quote posts (including images)"""
        if uri in self._post_cache:
            return self._post_cache[uri]
        
        try:
            response = self.client.get_posts([uri])
            if response.posts:
                post = response.posts[0]
                record = post.record
                
                # Extract images from the quoted post (prefer post.embed as it's already hydrated)
                images = []
                image_urls_seen = set()
                
                # First try post.embed (hydrated version with fullsize URLs)
                if hasattr(post, 'embed') and post.embed:
                    embed = post.embed
                    if hasattr(embed, 'images'):
                        for img in embed.images:
                            if hasattr(img, 'fullsize') and img.fullsize not in image_urls_seen:
                                images.append({'url': img.fullsize, 'alt': getattr(img, 'alt', '')})
                                image_urls_seen.add(img.fullsize)
                
                # Fallback to record.embed if no images found
                if not images and hasattr(record, 'embed') and record.embed:
                    embed = record.embed
                    if hasattr(embed, 'images'):
                        for img in embed.images:
                            if hasattr(img, 'fullsize') and img.fullsize not in image_urls_seen:
                                images.append({'url': img.fullsize, 'alt': getattr(img, 'alt', '')})
                                image_urls_seen.add(img.fullsize)
                            elif hasattr(img, 'image'):
                                blob = img.image
                                if hasattr(blob, 'ref') and hasattr(blob.ref, 'link'):
                                    url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{post.author.did}/{blob.ref.link}@jpeg"
                                    if url not in image_urls_seen:
                                        images.append({'url': url, 'alt': getattr(img, 'alt', '')})
                                        image_urls_seen.add(url)
                
                data = {
                    'author_name': getattr(post.author, 'display_name', '') or post.author.handle,
                    'author_handle': post.author.handle,
                    'text': post.record.text if hasattr(post.record, 'text') else '',
                    'uri': uri,
                    'images': images,
                }
                self._post_cache[uri] = data
                return data
        except Exception as e:
            print(f"Warning: Could not fetch {uri}: {e}")
        return None
    
    def fetch_arson_feed(self, limit: int = POST_LIMIT) -> list:
        """Fetch Rev Howard Arson's posts directly"""
        print(f"📰 Fetching {ARSON_DISPLAY_NAME}'s posts...")
        
        all_posts = []
        cursor = None
        
        while len(all_posts) < limit:
            batch_size = min(100, limit - len(all_posts))
            feed = self.client.get_author_feed(
                actor=ARSON_HANDLE, 
                limit=batch_size,
                cursor=cursor
            )
            all_posts.extend(feed.feed)
            
            if not feed.cursor or len(feed.feed) < batch_size:
                break
            cursor = feed.cursor
        
        print(f"   Found {len(all_posts)} posts")
        return all_posts[:limit]
    
    def download_image_as_base64(self, url: str) -> str:
        """Download an image and return as base64 data URI"""
        if not url:
            return None
        try:
            response = self.http_client.get(url)
            if response.status_code == 200:
                content_type = response.headers.get('content-type', 'image/jpeg')
                b64 = base64.b64encode(response.content).decode('utf-8')
                return f"data:{content_type};base64,{b64}"
        except:
            pass
        return None
    
    def format_time(self, iso_time: str) -> str:
        """Format timestamp to PST"""
        if not iso_time:
            return ""
        try:
            dt = date_parser.parse(iso_time)
            pst = pytz.timezone('US/Pacific')
            dt_pst = dt.astimezone(pst)
            return dt_pst.strftime('%-I:%M %p')
        except:
            return ""
    
    def extract_post_data(self, feed_item) -> dict:
        """Extract relevant data from a feed item"""
        post = feed_item.post
        record = post.record
        
        # Detect if this is a self-reply (thread continuation)
        is_thread_continuation = False
        reply_to = None
        reply_parent_uri = None
        
        if hasattr(feed_item, 'reply') and feed_item.reply:
            parent = feed_item.reply.parent
            if hasattr(parent, 'author') and hasattr(parent.author, 'handle'):
                try:
                    parent_handle = parent.author.handle
                    # Check if replying to self (thread continuation)
                    if parent_handle == ARSON_HANDLE:
                        is_thread_continuation = True
                        reply_parent_uri = parent.uri if hasattr(parent, 'uri') else None
                    else:
                        reply_to = {
                            'author_name': getattr(parent.author, 'display_name', None) or parent_handle,
                            'author_handle': parent_handle,
                            'text': parent.record.text if hasattr(parent.record, 'text') else '',
                        }
                except AttributeError:
                    pass  # BlockedAuthor or similar
        
        # Extract images (with deduplication)
        images = []
        image_urls_seen = set()
        if hasattr(record, 'embed') and record.embed:
            embed = record.embed
            if hasattr(embed, 'images'):
                for img in embed.images:
                    if hasattr(img, 'fullsize') and img.fullsize not in image_urls_seen:
                        images.append({'url': img.fullsize, 'alt': getattr(img, 'alt', '')})
                        image_urls_seen.add(img.fullsize)
                    elif hasattr(img, 'image'):
                        blob = img.image
                        if hasattr(blob, 'ref') and hasattr(blob.ref, 'link'):
                            url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{post.author.did}/{blob.ref.link}@jpeg"
                            if url not in image_urls_seen:
                                images.append({'url': url, 'alt': getattr(img, 'alt', '')})
                                image_urls_seen.add(url)
        
        # Extract quote post - store URI for hydration if content not available
        quote_post = None
        quote_uri = None
        if hasattr(record, 'embed') and record.embed:
            embed = record.embed
            if hasattr(embed, 'record'):
                quoted = embed.record
                # Try to get content directly
                if hasattr(quoted, 'value') and hasattr(quoted.value, 'text'):
                    quote_post = {
                        'author_name': quoted.author.display_name if hasattr(quoted, 'author') else '',
                        'author_handle': quoted.author.handle if hasattr(quoted, 'author') else '',
                        'text': quoted.value.text,
                    }
                elif hasattr(quoted, 'record') and hasattr(quoted.record, 'text'):
                    quote_post = {
                        'author_name': quoted.author.display_name if hasattr(quoted, 'author') else '',
                        'author_handle': quoted.author.handle if hasattr(quoted, 'author') else '',
                        'text': quoted.record.text,
                    }
                elif hasattr(quoted, 'uri'):
                    # Content not hydrated - store URI for later fetch
                    quote_uri = quoted.uri
        
        # Extract external link
        external_link = None
        if hasattr(record, 'embed') and record.embed:
            embed = record.embed
            if hasattr(embed, 'external'):
                ext = embed.external
                external_link = {
                    'title': getattr(ext, 'title', ''),
                    'description': getattr(ext, 'description', ''),
                    'uri': getattr(ext, 'uri', ''),
                }
        
        return {
            'author_name': post.author.display_name or post.author.handle,
            'author_handle': post.author.handle,
            'text': record.text if hasattr(record, 'text') else '',
            'created_at': record.created_at if hasattr(record, 'created_at') else None,
            'uri': post.uri,
            'like_count': post.like_count or 0,
            'repost_count': post.repost_count or 0,
            'reply_count': post.reply_count or 0,
            'reply_to': reply_to,
            'is_thread_continuation': is_thread_continuation,
            'reply_parent_uri': reply_parent_uri,
            'images': images,
            'quote_post': quote_post,
            'quote_uri': quote_uri,  # For hydration
            'external_link': external_link,
        }
    
    def download_images(self, posts: list):
        """Download all images and convert to base64"""
        print("🖼️  Downloading images...")
        count = 0
        for post in posts:
            for img in post.get('images', []):
                if img.get('url') and not img.get('data'):
                    img['data'] = self.download_image_as_base64(img['url'])
                    if img['data']:
                        count += 1
        print(f"   Downloaded {count} images")
    
    def hydrate_quote_posts(self, posts: list):
        """Fetch content for quote posts that weren't hydrated"""
        print("💬 Hydrating quote posts...")
        count = 0
        img_count = 0
        for post in posts:
            if post.get('quote_uri') and not post.get('quote_post'):
                hydrated = self.fetch_post_by_uri(post['quote_uri'])
                if hydrated:
                    post['quote_post'] = hydrated
                    count += 1
                    # Download images from hydrated quote posts
                    for img in hydrated.get('images', []):
                        if img.get('url') and not img.get('data'):
                            img['data'] = self.download_image_as_base64(img['url'])
                            if img['data']:
                                img_count += 1
        print(f"   Hydrated {count} quote posts ({img_count} images)")
    
    def consolidate_threads(self, posts: list) -> list:
        """Group self-reply threads together"""
        print("🧵 Consolidating threads...")
        
        # Build URI -> post mapping
        uri_to_post = {p['uri']: p for p in posts}
        
        # Find thread continuations and group them
        thread_heads = []  # Posts that start threads
        continuation_uris = set()  # URIs of posts that are continuations
        
        for post in posts:
            if post.get('is_thread_continuation') and post.get('reply_parent_uri'):
                continuation_uris.add(post['uri'])
        
        # Process posts
        consolidated = []
        for post in posts:
            if post['uri'] in continuation_uris:
                continue  # Skip, will be attached to parent
            
            # Find all continuations of this post
            thread_posts = [post]
            current_uri = post['uri']
            
            # Look for posts that reply to this one
            for other in posts:
                if other.get('reply_parent_uri') == current_uri and other.get('is_thread_continuation'):
                    thread_posts.append(other)
                    current_uri = other['uri']
            
            if len(thread_posts) > 1:
                # This is a thread - combine the text
                post['thread_continuation'] = thread_posts[1:]
                post['is_thread_head'] = True
            
            consolidated.append(post)
        
        print(f"   Consolidated {len(posts) - len(consolidated)} thread continuations")
        return consolidated
    
    def generate_html(self, posts: list) -> str:
        """Generate newspaper-style HTML"""
        
        template = Template('''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>The Arson Times</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Inter:wght@400;500&display=swap');
        
        @page {
            size: letter;
            margin: 0.4in 0.4in;
            
            @top-center {
                content: "THE ARSON TIMES";
                font-family: 'Playfair Display', serif;
                font-size: 7pt;
                letter-spacing: 0.15em;
                color: #888;
                padding-top: 0.1in;
            }
            
            @bottom-center {
                content: counter(page);
                font-family: 'Inter', sans-serif;
                font-size: 7pt;
                color: #888;
            }
        }
        
        @page:first {
            @top-center { content: none; }
        }
        
        * { box-sizing: border-box; }
        
        body {
            font-family: 'Source Serif 4', Georgia, serif;
            font-size: 10pt;
            line-height: 1.5;
            color: #1a1a1a;
            max-width: 100%;
            margin: 0;
            padding: 0;
            column-count: 2;
            column-gap: 0.25in;
            column-rule: 1px solid #ddd;
            text-align: justify;
            hyphens: auto;
        }
        
        .masthead {
            column-span: all;
            text-align: center;
            border-bottom: 3px double #1a1a1a;
            padding-bottom: 0.12in;
            margin-bottom: 0.15in;
        }
        
        .masthead h1 {
            font-family: 'Playfair Display', serif;
            font-size: 42pt;
            font-weight: 900;
            letter-spacing: 0.02em;
            margin: 0;
            text-transform: uppercase;
        }
        
        .masthead .subtitle {
            font-family: 'Inter', sans-serif;
            font-size: 8pt;
            text-transform: uppercase;
            letter-spacing: 0.2em;
            color: #555;
            margin-top: 0.06in;
        }
        
        .masthead .tagline {
            font-family: 'Source Serif 4', serif;
            font-style: italic;
            font-size: 10pt;
            color: #333;
            margin-top: 0.08in;
        }
        
        .masthead .date {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            margin-top: 0.06in;
            color: #666;
        }
        
        .post {
            margin-bottom: 0.18in;
            padding-bottom: 0.12in;
            border-bottom: 1px solid #ddd;
        }
        
        .post:nth-child(odd) {
            background: #fafafa;
            padding: 0.08in;
            margin-left: -0.08in;
            margin-right: -0.08in;
        }
        
        .post-time {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            color: #888;
            text-align: right;
            margin-top: 0.04in;
        }
        
        .post-text {
            margin-bottom: 0.08in;
        }
        
        .reply-context {
            background: #f5f5f5;
            border-left: 2px solid #ccc;
            padding: 0.06in 0.1in;
            margin-bottom: 0.1in;
            font-size: 9pt;
        }
        
        .reply-context .context-author {
            font-weight: 600;
            font-size: 8pt;
        }
        
        .reply-context .context-handle {
            font-size: 7pt;
            color: #666;
        }
        
        .reply-label {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.04in;
        }
        
        .post-images img {
            width: 100%;
            max-height: 4in;
            object-fit: contain;
            border: 1px solid #ddd;
            margin-bottom: 0.06in;
            background: #fafafa;
        }
        
        .quote-post {
            background: #f8f8f8;
            border: 1px solid #e0e0e0;
            padding: 0.08in;
            margin: 0.08in 0;
            font-size: 9pt;
        }
        
        .quote-author {
            font-weight: 600;
            font-size: 8pt;
        }
        
        .quote-handle {
            font-size: 7pt;
            color: #666;
        }
        
        .external-link {
            background: #f0f0f0;
            border: 1px solid #ddd;
            padding: 0.08in;
            margin: 0.08in 0;
        }
        
        .link-title {
            font-weight: 600;
            font-size: 9pt;
            color: #0066cc;
        }
        
        .link-desc {
            font-size: 8pt;
            color: #555;
        }
        
        .post-stats {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            color: #888;
            margin-top: 0.06in;
        }
        
        .post-stats span {
            margin-right: 0.12in;
        }
        
        /* Thread continuation styling */
        .thread-marker {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.04in;
            border-left: 2px solid #888;
            padding-left: 0.08in;
        }
        
        .thread-continuation {
            border-left: 2px solid #ccc;
            padding-left: 0.1in;
            margin-top: 0.08in;
            margin-left: 0.05in;
        }
        
        .thread-continuation .cont-text {
            margin-bottom: 0.06in;
        }
        
        .thread-continuation .cont-time {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            color: #999;
        }
    </style>
</head>
<body>
    <div class="masthead">
        <h1>The Arson Times</h1>
        <div class="subtitle">A Digest of Rev. Howard Arson</div>
        <div class="tagline">"All I can figure is that Steve Yegge has built the TempleOS of coding agents"</div>
        <div class="date">{{ date }}</div>
    </div>
    
    {% for post in posts %}
    <div class="post">
        {% if post.is_thread_head %}
        <div class="thread-marker">Thread · {{ (post.thread_continuation|length) + 1 }} posts</div>
        {% endif %}
        
        {% if post.reply_to %}
        <div class="reply-label">Replying to @{{ post.reply_to.author_handle }}</div>
        <div class="reply-context">
            <span class="context-author">{{ post.reply_to.author_name }}</span>
            <span class="context-handle">@{{ post.reply_to.author_handle }}</span>
            <div>{{ post.reply_to.text[:200] }}{% if post.reply_to.text|length > 200 %}...{% endif %}</div>
        </div>
        {% endif %}
        
        <div class="post-text">{{ post.text }}</div>
        
        {% if post.images %}
        <div class="post-images">
            {% for img in post.images %}
                {% if img.data %}
                <img src="{{ img.data }}" alt="">
                {% endif %}
            {% endfor %}
        </div>
        {% endif %}
        
        {% if post.quote_post and (post.quote_post.text or post.quote_post.images) %}
        <div class="quote-post">
            <span class="quote-author">{{ post.quote_post.author_name }}</span>
            <span class="quote-handle">@{{ post.quote_post.author_handle }}</span>
            {% if post.quote_post.text %}<div>{{ post.quote_post.text }}</div>{% endif %}
            {% if post.quote_post.images %}
            <div class="post-images">
                {% for img in post.quote_post.images %}
                    {% if img.data %}
                    <img src="{{ img.data }}" alt="">
                    {% endif %}
                {% endfor %}
            </div>
            {% endif %}
        </div>
        {% endif %}
        
        {# Thread continuations #}
        {% if post.thread_continuation %}
        <div class="thread-continuation">
            {% for cont in post.thread_continuation %}
            <div class="cont-text">{{ cont.text }}</div>
            {% if cont.images %}
            <div class="post-images">
                {% for img in cont.images %}
                    {% if img.data %}
                    <img src="{{ img.data }}" alt="">
                    {% endif %}
                {% endfor %}
            </div>
            {% endif %}
            {% if cont.quote_post and cont.quote_post.text %}
            <div class="quote-post">
                <span class="quote-author">{{ cont.quote_post.author_name }}</span>
                <span class="quote-handle">@{{ cont.quote_post.author_handle }}</span>
                <div>{{ cont.quote_post.text }}</div>
            </div>
            {% endif %}
            <span class="cont-time">{{ cont.formatted_time }}</span>
            {% if not loop.last %}<hr style="border: none; border-top: 1px dotted #ddd; margin: 0.06in 0;">{% endif %}
            {% endfor %}
        </div>
        {% endif %}
        
        {% if post.external_link %}
        <div class="external-link">
            <div class="link-title">{{ post.external_link.title }}</div>
            {% if post.external_link.description %}
            <div class="link-desc">{{ post.external_link.description[:150] }}{% if post.external_link.description|length > 150 %}...{% endif %}</div>
            {% endif %}
        </div>
        {% endif %}
        
        {% if post.like_count > 5 or post.repost_count > 2 %}
        <div class="post-stats">
            {% if post.like_count %}<span>♥ {{ post.like_count }}</span>{% endif %}
            {% if post.repost_count %}<span>↻ {{ post.repost_count }}</span>{% endif %}
            {% if post.reply_count %}<span>💬 {{ post.reply_count }}</span>{% endif %}
        </div>
        {% endif %}
        <div class="post-time">{{ post.formatted_time }}</div>
    </div>
    {% endfor %}
</body>
</html>
        ''')
        
        # Add formatted time (including thread continuations)
        for post in posts:
            post['formatted_time'] = self.format_time(post['created_at'])
            for cont in post.get('thread_continuation', []):
                cont['formatted_time'] = self.format_time(cont.get('created_at'))
        
        today = datetime.now().strftime('%A, %B %d, %Y')
        return template.render(posts=posts, date=today)
    
    def generate_pdf(self, output_path: str = None):
        """Main method to generate the PDF"""
        if not output_path:
            today = datetime.now().strftime('%Y-%m-%d')
            output_path = f"arson_times_{today}.pdf"
        
        # Fetch posts
        feed = self.fetch_arson_feed()
        
        # Extract data
        print(f"📝 Processing posts...")
        posts = [self.extract_post_data(f) for f in feed]
        
        # Hydrate quote posts that weren't included in feed
        self.hydrate_quote_posts(posts)
        
        # Consolidate self-reply threads
        posts = self.consolidate_threads(posts)
        
        # Download images (including from thread continuations)
        self.download_images(posts)
        for post in posts:
            for cont in post.get('thread_continuation', []):
                self.download_images([cont])
        
        # Generate HTML
        print("🎨 Generating layout...")
        html_content = self.generate_html(posts)
        
        # Save HTML
        html_path = output_path.replace('.pdf', '.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # Generate PDF
        print("🖨️  Creating PDF...")
        HTML(string=html_content).write_pdf(output_path)
        
        print(f"\n✅ Done! The Arson Times is ready: {output_path}")
        print(f"   HTML version: {html_path}")
        
        return output_path


def main():
    if not BLUESKY_APP_PASSWORD:
        print("❌ BLUESKY_APP_PASSWORD not set")
        sys.exit(1)
    
    arson = ArsonTimes()
    arson.generate_pdf()


if __name__ == '__main__':
    main()


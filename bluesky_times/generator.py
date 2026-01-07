"""
Bluesky Times Generator - Core logic for fetching, processing, and rendering
"""
import os
import re
import json
import base64
import httpx
from datetime import datetime
from dateutil import parser as date_parser
from atproto import Client
from jinja2 import Template
from weasyprint import HTML, CSS
from collections import defaultdict
from openai import OpenAI

from .config import (
    OPENROUTER_API_KEY,
    DEFAULT_MODEL,
    FAVORITE_ACCOUNTS,
    DEFAULT_POST_LIMIT,
    CACHE_FILE,
)


class BlueskyTimesGenerator:
    def __init__(self, handle: str, app_password: str, model: str = None):
        self.client = Client()
        self.client.login(handle, app_password)
        self.profile = self.client.get_profile(handle)
        self.user_handle = handle  # Store for filtering own posts
        self.http_client = httpx.Client(timeout=10.0)
        self.model = model or DEFAULT_MODEL
        
    def fetch_timeline(self, limit: int = 200) -> list:
        """Fetch recent posts from timeline with pagination"""
        all_posts = []
        cursor = None
        
        while len(all_posts) < limit:
            batch_size = min(100, limit - len(all_posts))
            timeline = self.client.get_timeline(limit=batch_size, cursor=cursor)
            all_posts.extend(timeline.feed)
            
            if not timeline.cursor or len(timeline.feed) < batch_size:
                break  # No more posts
            cursor = timeline.cursor
        
        return all_posts[:limit]
    
    def fetch_single_post(self, uri: str) -> dict:
        """Fetch a single post by URI and return basic data"""
        try:
            thread = self.client.get_post_thread(uri=uri, depth=0, parent_height=0)
            if hasattr(thread.thread, 'post'):
                post = thread.thread.post
                return {
                    'author_handle': post.author.handle,
                    'author_name': post.author.display_name or post.author.handle,
                    'text': post.record.text if hasattr(post.record, 'text') else '',
                    'uri': post.uri,
                }
        except:
            pass
        return None
    
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
        except Exception as e:
            print(f"  ⚠ Could not download image: {e}")
        return None
    
    def fetch_thread_context(self, post_uri: str, reply_root: str = None) -> list:
        """Fetch the thread context (parent posts) for a reply"""
        try:
            thread = self.client.get_post_thread(uri=post_uri, depth=0, parent_height=10)
            
            # Walk up the parent chain
            parents = []
            current = thread.thread
            hit_not_found = False
            
            # Get the parent chain
            if hasattr(current, 'parent') and current.parent:
                parent = current.parent
                while parent:
                    if hasattr(parent, 'post'):
                        post = parent.post
                        parent_data = {
                            'author_handle': post.author.handle,
                            'author_name': post.author.display_name or post.author.handle,
                            'text': post.record.text if hasattr(post.record, 'text') else '',
                            'created_at': post.record.created_at if hasattr(post.record, 'created_at') else None,
                            'uri': post.uri,
                        }
                        parents.insert(0, parent_data)  # Insert at beginning to maintain order
                        parent = parent.parent if hasattr(parent, 'parent') else None
                    else:
                        # Hit a NotFoundPost or BlockedPost - chain is broken
                        hit_not_found = True
                        break
            
            # If chain was broken and we have a root URI, try to fetch the root directly
            if hit_not_found and reply_root:
                # Check if we already have the root
                have_root = any(p.get('uri') == reply_root for p in parents)
                if not have_root:
                    try:
                        root_thread = self.client.get_post_thread(uri=reply_root, depth=0, parent_height=0)
                        if hasattr(root_thread.thread, 'post'):
                            root_post = root_thread.thread.post
                            root_data = {
                                'author_handle': root_post.author.handle,
                                'author_name': root_post.author.display_name or root_post.author.handle,
                                'text': root_post.record.text if hasattr(root_post.record, 'text') else '',
                                'created_at': root_post.record.created_at if hasattr(root_post.record, 'created_at') else None,
                                'uri': root_post.uri,
                                'is_root': True,  # Mark this as the root
                            }
                            # Insert a gap indicator if there's missing context
                            if parents:
                                parents.insert(0, {'is_gap': True, 'text': '...'})
                            parents.insert(0, root_data)
                    except Exception:
                        pass  # Can't fetch root, that's ok
            
            return parents
        except Exception as e:
            print(f"  ⚠ Could not fetch thread context: {e}")
            return []
    
    def summarize_thread(self, posts: list) -> str:
        """Use LLM to summarize a long thread"""
        if not posts:
            return ""
        
        thread_text = "\n".join([f"@{p['author_handle']}: {p['text']}" for p in posts])
        
        client = self.get_llm_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": f"""Summarize this social media thread in 1-2 sentences, capturing the key point being discussed:

{thread_text}

Write a brief, neutral summary (no "This thread discusses..." - just state what's being said)."""
                }],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"  ⚠ Could not summarize thread: {e}")
            return f"[Thread with {len(posts)} posts]"
    
    def add_reply_context_for_favorites(self, posts: list):
        """Add thread context for favorite accounts' replies, consolidating same-thread replies"""
        print("💬 Fetching reply context for favorite voices...")
        
        # Build set of all post URIs we already have
        all_post_uris = {p['uri'] for p in posts}
        
        # Group favorite replies by reply_root
        replies_by_root = {}
        for i, post in enumerate(posts):
            if post['author_handle'] not in FAVORITE_ACCOUNTS:
                continue
            if not post.get('reply_root'):
                continue
            
            root = post['reply_root']
            if root not in replies_by_root:
                replies_by_root[root] = []
            replies_by_root[root].append((i, post))
        
        context_count = 0
        seen_roots = set()
        
        for root, reply_group in replies_by_root.items():
            if len(reply_group) == 0:
                continue
            
            # Fetch context once per thread root
            first_post = reply_group[0][1]
            parents = self.fetch_thread_context(first_post['uri'], reply_root=root)
            
            # Filter out posts we already have in our feed (keep gap indicators)
            parents = [p for p in parents if p.get('is_gap') or p.get('uri') not in all_post_uris]
            
            if not parents:
                continue
            
            # For single replies, use old behavior
            if len(reply_group) == 1:
                idx, post = reply_group[0]
                if len(parents) <= 4:
                    post['reply_context'] = {
                        'type': 'full',
                        'posts': parents
                    }
                else:
                    summary = self.summarize_thread(parents)
                    post['reply_context'] = {
                        'type': 'summary',
                        'summary': summary,
                        'post_count': len(parents)
                    }
                context_count += 1
            else:
                # Multiple replies to same thread - consolidate
                # First reply gets the context, others get marked as "continued"
                summary = self.summarize_thread(parents) if len(parents) > 4 else None
                
                # Sort replies by time
                reply_group.sort(key=lambda x: x[1].get('created_at', ''))
                
                for j, (idx, post) in enumerate(reply_group):
                    if j == 0:
                        # First reply gets full context or summary
                        if len(parents) <= 4:
                            post['reply_context'] = {
                                'type': 'full',
                                'posts': parents
                            }
                        else:
                            post['reply_context'] = {
                                'type': 'summary',
                                'summary': summary,
                                'post_count': len(parents)
                            }
                        # Mark that more replies follow
                        post['thread_continues'] = len(reply_group) - 1
                        context_count += 1
                    else:
                        # Subsequent replies - just show immediate parent for context
                        immediate_parents = self.fetch_thread_context(post['uri'], reply_root=root)
                        if immediate_parents and len(immediate_parents) > 0:
                            # Only show the immediate 1-2 parent posts, not the whole thread
                            post['reply_context'] = {
                                'type': 'continued',
                                'posts': immediate_parents[-2:] if len(immediate_parents) > 2 else immediate_parents
                            }
                        context_count += 1
        
        print(f"   Added context to {context_count} replies ({len(replies_by_root)} unique threads)")
    
    def consolidate_thread_participations(self, threads: list) -> list:
        """Consolidate multiple favorite replies to the same thread into single thread items"""
        print("🔗 Consolidating thread participations...")
        
        # Build set of all post URIs we already have
        all_uris = set()
        for thread in threads:
            for post in thread['posts']:
                all_uris.add(post['uri'])
        
        # Find threads where favorites have reply_root
        # Group by reply_root
        roots_to_threads = {}  # reply_root -> list of (thread_idx, post)
        
        for idx, thread in enumerate(threads):
            for post in thread['posts']:
                if post['author_handle'] not in FAVORITE_ACCOUNTS:
                    continue
                if not post.get('reply_root'):
                    continue
                
                root = post['reply_root']
                if root not in roots_to_threads:
                    roots_to_threads[root] = []
                roots_to_threads[root].append((idx, post))
        
        # Find roots with multiple threads (same thread, multiple mentions in feed)
        threads_to_remove = set()
        consolidations = {}  # thread_idx to keep -> list of additional posts to include
        
        for root, thread_posts in roots_to_threads.items():
            if len(thread_posts) <= 1:
                continue
            
            # Sort by created_at to get chronological order
            thread_posts.sort(key=lambda x: x[1].get('created_at', ''))
            
            # Keep the first thread, consolidate others into it
            first_idx = thread_posts[0][0]
            first_post = thread_posts[0][1]
            
            additional_posts = []
            for idx, post in thread_posts[1:]:
                if idx != first_idx:
                    threads_to_remove.add(idx)
                    additional_posts.append(post)
            
            if additional_posts:
                if first_idx not in consolidations:
                    consolidations[first_idx] = []
                consolidations[first_idx].extend(additional_posts)
        
        # Apply consolidations - add 'thread_replies' to first post
        # Also fetch immediate parent for each reply to show intermediate context
        for thread_idx, additional_posts in consolidations.items():
            thread = threads[thread_idx]
            
            # Fetch immediate parent for each additional reply
            for reply in additional_posts:
                parent_uri = reply.get('reply_parent')
                if parent_uri and parent_uri not in all_uris:
                    try:
                        parent_data = self.fetch_single_post(parent_uri)
                        if parent_data:
                            reply['immediate_parent'] = parent_data
                    except:
                        pass
            
            for post in thread['posts']:
                if post['author_handle'] in FAVORITE_ACCOUNTS and post.get('reply_root'):
                    # This is the post that should hold all the replies
                    post['thread_replies'] = additional_posts
                    break
        
        # Remove consolidated threads
        if threads_to_remove:
            new_threads = [t for i, t in enumerate(threads) if i not in threads_to_remove]
            print(f"   Consolidated {len(threads_to_remove)} duplicate thread appearances")
            return new_threads
        
        return threads
    
    def extract_post_data(self, feed_view) -> dict:
        """Extract relevant data from a feed view post"""
        post = feed_view.post
        record = post.record
        
        # Basic post data
        data = {
            'uri': post.uri,
            'cid': post.cid,
            'author_handle': post.author.handle,
            'author_name': post.author.display_name or post.author.handle,
            'author_avatar': post.author.avatar,
            'text': record.text if hasattr(record, 'text') else '',
            'created_at': record.created_at if hasattr(record, 'created_at') else None,
            'like_count': post.like_count or 0,
            'repost_count': post.repost_count or 0,
            'reply_count': post.reply_count or 0,
            'is_repost': False,
            'reposted_by': None,
            'reply_parent': None,
            'reply_root': None,
            'quote_post': None,
            'images': [],
            'external_link': None,
        }
        
        # Check if this is a repost
        if hasattr(feed_view, 'reason') and feed_view.reason:
            reason_type = getattr(feed_view.reason, 'py_type', '')
            if 'reasonRepost' in str(reason_type):
                data['is_repost'] = True
                data['reposted_by'] = feed_view.reason.by.display_name or feed_view.reason.by.handle
        
        # Check for reply context
        if hasattr(record, 'reply') and record.reply:
            data['reply_parent'] = record.reply.parent.uri if record.reply.parent else None
            data['reply_root'] = record.reply.root.uri if record.reply.root else None
        
        # Check for embedded content
        if hasattr(post, 'embed') and post.embed:
            embed = post.embed
            embed_type = getattr(embed, 'py_type', '')
            
            # Images
            if 'images' in str(embed_type).lower():
                if hasattr(embed, 'images'):
                    for img in embed.images:
                        img_data = {
                            'alt': img.alt if hasattr(img, 'alt') else '',
                            'thumb': img.thumb if hasattr(img, 'thumb') else None,
                            'fullsize': img.fullsize if hasattr(img, 'fullsize') else None,
                        }
                        data['images'].append(img_data)
            
            # Also check for images in recordWithMedia
            if 'recordwithmedia' in str(embed_type).lower():
                if hasattr(embed, 'media') and hasattr(embed.media, 'images'):
                    for img in embed.media.images:
                        img_data = {
                            'alt': img.alt if hasattr(img, 'alt') else '',
                            'thumb': img.thumb if hasattr(img, 'thumb') else None,
                            'fullsize': img.fullsize if hasattr(img, 'fullsize') else None,
                        }
                        data['images'].append(img_data)
            
            # Quote post
            if 'record' in str(embed_type).lower():
                quoted = None
                quoted_embed = None
                
                if hasattr(embed, 'record'):
                    quoted = embed.record
                    # Handle recordWithMedia - the media is on the embed, record is nested
                    if hasattr(embed, 'media'):
                        quoted_embed = embed.media
                    # Check if quoted itself has embeds
                    if hasattr(quoted, 'embeds') and quoted.embeds:
                        quoted_embed = quoted.embeds[0] if quoted.embeds else None
                    # Handle nested record
                    if hasattr(quoted, 'record'):
                        quoted = quoted.record
                
                if quoted and hasattr(quoted, 'value'):
                    quote_data = {
                        'author_handle': quoted.author.handle if hasattr(quoted, 'author') else 'unknown',
                        'author_name': (quoted.author.display_name or quoted.author.handle) if hasattr(quoted, 'author') else 'unknown',
                        'text': quoted.value.text if hasattr(quoted.value, 'text') else '',
                        'images': [],
                    }
                    # Check for images in quoted post's embeds
                    if hasattr(quoted, 'embeds') and quoted.embeds:
                        for qembed in quoted.embeds:
                            if hasattr(qembed, 'images'):
                                for img in qembed.images:
                                    quote_data['images'].append({
                                        'alt': img.alt if hasattr(img, 'alt') else '',
                                        'thumb': img.thumb if hasattr(img, 'thumb') else None,
                                        'fullsize': img.fullsize if hasattr(img, 'fullsize') else None,
                                    })
                    data['quote_post'] = quote_data
                elif quoted and hasattr(quoted, 'author') and hasattr(quoted.author, 'handle'):
                    # Direct record view (skip blocked authors)
                    quote_data = {
                        'author_handle': quoted.author.handle,
                        'author_name': quoted.author.display_name or quoted.author.handle,
                        'text': quoted.value.text if hasattr(quoted, 'value') and hasattr(quoted.value, 'text') else (quoted.record.text if hasattr(quoted, 'record') and hasattr(quoted.record, 'text') else ''),
                        'images': [],
                    }
                    # Check for images in quoted post's embeds
                    if hasattr(quoted, 'embeds') and quoted.embeds:
                        for qembed in quoted.embeds:
                            if hasattr(qembed, 'images'):
                                for img in qembed.images:
                                    quote_data['images'].append({
                                        'alt': img.alt if hasattr(img, 'alt') else '',
                                        'thumb': img.thumb if hasattr(img, 'thumb') else None,
                                        'fullsize': img.fullsize if hasattr(img, 'fullsize') else None,
                                    })
                    data['quote_post'] = quote_data
            
            # External link
            if 'external' in str(embed_type).lower():
                if hasattr(embed, 'external'):
                    ext = embed.external
                    data['external_link'] = {
                        'uri': ext.uri if hasattr(ext, 'uri') else '',
                        'title': ext.title if hasattr(ext, 'title') else '',
                        'description': ext.description if hasattr(ext, 'description') else '',
                    }
        
        return data
    
    def organize_threads(self, posts: list) -> list:
        """Group posts into threads and organize them"""
        posts_by_uri = {p['uri']: p for p in posts}
        threads = []
        seen_uris = set()
        
        # Find root posts and build threads
        for post in posts:
            if post['uri'] in seen_uris:
                continue
                
            # If this is a reply, try to find the thread
            if post['reply_root'] and post['reply_root'] in posts_by_uri:
                root_uri = post['reply_root']
                if root_uri in seen_uris:
                    continue
                    
                # Build thread from root
                thread = [posts_by_uri[root_uri]]
                seen_uris.add(root_uri)
                
                # Find all replies in this thread
                for p in posts:
                    if p['reply_root'] == root_uri and p['uri'] not in seen_uris:
                        thread.append(p)
                        seen_uris.add(p['uri'])
                
                # Sort thread by time
                thread.sort(key=lambda x: x['created_at'] or '')
                threads.append({'type': 'thread', 'posts': thread})
            else:
                # Standalone post
                seen_uris.add(post['uri'])
                threads.append({'type': 'single', 'posts': [post]})
        
        return threads
    
    def add_thread_context_for_favorites(self, threads: list):
        """Add context to threads containing favorites who are replying to external posts"""
        print("💬 Adding context to favorite threads...")
        context_added = 0
        
        for thread in threads:
            # Only process threads (not single posts) that contain favorites
            if thread['type'] != 'thread' or len(thread['posts']) <= 1:
                continue
            
            has_favorite = any(p['author_handle'] in FAVORITE_ACCOUNTS for p in thread['posts'])
            if not has_favorite:
                continue
            
            # Get all URIs in this thread
            thread_uris = {p['uri'] for p in thread['posts']}
            
            # Find favorite posts replying to something NOT in this thread
            external_replies = []
            for post in thread['posts']:
                if post['author_handle'] not in FAVORITE_ACCOUNTS:
                    continue
                parent = post.get('reply_parent')
                if parent and parent not in thread_uris:
                    external_replies.append((post, parent))
            
            if not external_replies:
                continue
            
            # Fetch context for the first external reply (to give thread context)
            first_post, parent_uri = external_replies[0]
            try:
                parent_thread = self.client.get_post_thread(uri=parent_uri, depth=0, parent_height=5)
                
                # Build context from the parent and its ancestors
                context_posts = []
                current = parent_thread.thread
                
                # Add the immediate parent
                if hasattr(current, 'post'):
                    p = current.post
                    context_posts.append({
                        'author_handle': p.author.handle,
                        'author_name': p.author.display_name or p.author.handle,
                        'text': p.record.text if hasattr(p.record, 'text') else '',
                    })
                
                # Walk up parents
                parent = current.parent if hasattr(current, 'parent') else None
                while parent and hasattr(parent, 'post') and len(context_posts) < 4:
                    p = parent.post
                    context_posts.insert(0, {
                        'author_handle': p.author.handle,
                        'author_name': p.author.display_name or p.author.handle,
                        'text': p.record.text if hasattr(p.record, 'text') else '',
                    })
                    parent = parent.parent if hasattr(parent, 'parent') else None
                
                if context_posts:
                    # Summarize if too many external replies
                    if len(external_replies) > 3:
                        summary = self.summarize_thread(context_posts)
                        first_post['reply_context'] = {
                            'type': 'summary',
                            'summary': summary,
                            'post_count': len(context_posts)
                        }
                    else:
                        first_post['reply_context'] = {
                            'type': 'full',
                            'posts': context_posts
                        }
                    context_added += 1
                    
            except Exception as e:
                # Silently skip if we can't fetch
                pass
        
        print(f"   Added context to {context_added} favorite threads")
    
    def add_basic_reply_context(self, threads: list):
        """Add basic context (immediate parent) for ALL reply posts that don't have context yet"""
        print("💬 Adding basic reply context...")
        context_added = 0
        
        for thread in threads:
            for post in thread['posts']:
                # Skip if already has context or not a reply
                if post.get('reply_context') or not post.get('reply_parent'):
                    continue
                
                # Fetch the immediate parent
                try:
                    parent_data = self.fetch_single_post(post['reply_parent'])
                    if parent_data:
                        post['reply_context'] = {
                            'type': 'full',
                            'posts': [parent_data]
                        }
                        context_added += 1
                except:
                    pass  # Skip if we can't fetch
        
        print(f"   Added basic context to {context_added} replies")
    
    def format_time(self, iso_time: str) -> str:
        """Format ISO time to readable format in PST"""
        if not iso_time:
            return ''
        try:
            from datetime import timezone, timedelta
            dt = date_parser.parse(iso_time)
            # Convert to PST (UTC-8)
            pst = timezone(timedelta(hours=-8))
            if dt.tzinfo is not None:
                dt = dt.astimezone(pst)
            else:
                # Assume UTC if no timezone
                dt = dt.replace(tzinfo=timezone.utc).astimezone(pst)
            return dt.strftime('%I:%M %p').lstrip('0')
        except:
            return ''
    
    def save_cache(self, threads: list, cache_path: str = "cache.json"):
        """Save threads data to a JSON cache file"""
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(threads, f, indent=2, ensure_ascii=False)
        print(f"💾 Saved cache to {cache_path}")
    
    def load_cache(self, cache_path: str = "cache.json") -> list:
        """Load threads data from a JSON cache file"""
        with open(cache_path, 'r', encoding='utf-8') as f:
            threads = json.load(f)
        print(f"📂 Loaded cache from {cache_path}")
        return threads
    
    def get_llm_client(self):
        """Get OpenRouter client"""
        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
    
    def identify_themes(self, threads: list) -> list:
        """Use LLM to identify 2-3 major themes from all posts"""
        # Collect all post texts for analysis (including quoted content)
        all_texts = []
        for thread in threads:
            for post in thread['posts']:
                text = post.get('text', '').strip()
                # Include quoted content for better theme detection
                if post.get('quote_post') and post['quote_post'].get('text'):
                    text += f" [quoting: {post['quote_post']['text'][:100]}]"
                if text:
                    # Include author for context
                    all_texts.append(f"@{post['author_handle']}: {text[:250]}")
        
        # Create a summary of posts for theme identification
        posts_summary = "\n".join(all_texts[:80])  # First 80 posts for context
        
        client = self.get_llm_client()
        
        print(f"🔍 Identifying major themes (using {self.model})...")
        response = client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": f"""Analyze these social media posts and identify the 2-3 MAJOR themes/topics that dominate the conversation today. These should be specific, newsworthy topics (not generic categories like "politics" or "humor").

Posts:
{posts_summary}

Return ONLY a JSON array of theme objects, each with:
- "id": short lowercase slug (e.g., "nyt-trans-coverage", "solar-energy-2025")
- "title": Human-readable headline for this theme (e.g., "NYT Trans Coverage Controversy")
- "description": One sentence explaining this theme

Example format:
[
  {{"id": "nyt-trans-coverage", "title": "NYT Trans Coverage Controversy", "description": "Discussion of a former NYT editor's interview about anti-trans editorial direction."}},
  {{"id": "mamdani-inauguration", "title": "Mamdani NYC Inauguration", "description": "Reactions to Zohran Mamdani's mayoral inauguration in New York City."}}
]

Return ONLY the JSON array, no other text."""
            }],
        )
        
        try:
            content = response.choices[0].message.content
            # Strip markdown code blocks if present
            content = content.strip()
            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:])  # Remove first line
            if content.endswith("```"):
                content = content.rsplit("```", 1)[0]
            content = content.strip()
            
            themes = json.loads(content)
            print(f"   Found themes: {[t['title'] for t in themes]}")
            return themes
        except (json.JSONDecodeError, Exception) as e:
            print(f"   ⚠ Could not parse themes ({e}), using default")
            return [{"id": "misc", "title": "Today's Posts", "description": ""}]
    
    def classify_posts(self, threads: list, themes: list) -> dict:
        """Classify each thread into a theme using LLM (with image support)"""
        theme_ids = [t['id'] for t in themes]
        theme_descriptions = "\n".join([f"- {t['id']}: {t['title']} - {t['description']}" for t in themes])
        
        # Prepare threads for classification (include quoted content and images)
        threads_for_classification = []
        for i, thread in enumerate(threads):
            texts = []
            images = []
            for p in thread['posts']:
                post_text = p.get('text', '')[:150]
                # Include quoted post text for better thematic grouping
                if p.get('quote_post') and p['quote_post'].get('text'):
                    quote_text = p['quote_post']['text'][:100]
                    post_text += f" [quoting: {quote_text}]"
                texts.append(post_text)
                
                # Collect images (limit to first 2 per thread to manage tokens)
                if len(images) < 2:
                    for img in p.get('images', [])[:1]:
                        if img.get('data') and img['data'].startswith('data:'):
                            images.append(img['data'])
                    # Also check quote post images
                    if p.get('quote_post'):
                        for img in p['quote_post'].get('images', [])[:1]:
                            if img.get('data') and img['data'].startswith('data:'):
                                images.append(img['data'])
            
            combined_text = " | ".join(texts)
            threads_for_classification.append({
                "index": i,
                "text": combined_text[:400],
                "images": images[:2]  # Max 2 images per thread
            })
        
        # Batch classify - smaller chunks when images present
        classified = {}
        chunk_size = 15  # Smaller chunks for multimodal
        client = self.get_llm_client()
        
        print("📑 Classifying posts into themes (with image analysis)...")
        for chunk_start in range(0, len(threads_for_classification), chunk_size):
            chunk = threads_for_classification[chunk_start:chunk_start + chunk_size]
            
            # Build multimodal content
            content_parts = []
            posts_text = []
            
            for p in chunk:
                posts_text.append(f"[{p['index']}]: {p['text']}")
                # Add images inline with post reference
                for img_data in p.get('images', []):
                    content_parts.append({
                        "type": "text",
                        "text": f"[Image for post {p['index']}]:"
                    })
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": img_data}
                    })
            
            # Main text prompt
            prompt_text = f"""Classify each numbered post into ONE of these themes, or "misc" if it doesn't fit.
IMPORTANT: Look at the images - they often contain screenshot tweets or news that reveal the topic.

Themes:
{theme_descriptions}
- misc: Posts that don't fit the major themes

Posts:
{chr(10).join(posts_text)}

Return ONLY a JSON object mapping post index to theme id.
Example: {{"0": "nyt-trans-coverage", "1": "misc", "2": "mamdani-inauguration"}}

Return ONLY the JSON object, no other text."""

            # Combine: text first, then images
            message_content = [{"type": "text", "text": prompt_text}] + content_parts
            
            response = client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": message_content
                }],
            )
            
            try:
                content = response.choices[0].message.content
                # Strip markdown code blocks if present
                content = content.strip()
                if content.startswith("```"):
                    content = "\n".join(content.split("\n")[1:])
                if content.endswith("```"):
                    content = content.rsplit("```", 1)[0]
                content = content.strip()
                
                chunk_classified = json.loads(content)
                for idx_str, theme_id in chunk_classified.items():
                    classified[int(idx_str)] = theme_id if theme_id in theme_ids else "misc"
            except (json.JSONDecodeError, Exception):
                # Default to misc if parsing fails
                for p in chunk:
                    classified[p['index']] = "misc"
        
        # Consolidation pass: re-check misc posts against themes (with images)
        misc_indices = [i for i, t in classified.items() if t == "misc"]
        if misc_indices and len(misc_indices) < 50:  # Only if reasonable number
            themed_posts = []
            for i, t in classified.items():
                if t != "misc" and i < len(threads):
                    texts = [p.get('text', '')[:100] for p in threads[i]['posts']]
                    themed_posts.append(f"[{t}]: {' '.join(texts)[:150]}")
            
            if themed_posts:
                themed_context = "\n".join(themed_posts[:15])
                
                misc_for_review = []
                misc_images = []  # Collect images for multimodal review
                for idx in misc_indices[:20]:  # Review first 20 misc
                    if idx < len(threads):
                        texts = [p.get('text', '')[:100] for p in threads[idx]['posts']]
                        quote_text = ""
                        for p in threads[idx]['posts']:
                            if p.get('quote_post'):
                                quote_text = f" [quotes: {p['quote_post'].get('text', '')[:80]}]"
                            # Collect images for this misc post
                            for img in p.get('images', [])[:1]:
                                if img.get('data') and img['data'].startswith('data:'):
                                    misc_images.append({"index": idx, "data": img['data']})
                        misc_for_review.append({
                            "index": idx,
                            "text": ' '.join(texts)[:200] + quote_text
                        })
                
                if misc_for_review:
                    misc_text = "\n".join([f"[{p['index']}]: {p['text']}" for p in misc_for_review])
                    
                    # Build multimodal content for consolidation
                    # Include theme descriptions for better context
                    theme_desc = "\n".join([f"- {t['id']}: {t['title']} - {t['description']}" for t in themes])
                    
                    prompt_text = f"""Some posts were classified as "misc" but may actually relate to these themes. Re-check them.
IMPORTANT: Look at the images - they often contain screenshot tweets or news that reveal connections to themes.
Think about indirect connections: reactions to the same event, the same people involved, or consequences of the same news.

THEME DEFINITIONS:
{theme_desc}

EXAMPLE POSTS FROM EACH THEME:
{themed_context}

MISC POSTS TO RE-CHECK:
{misc_text}

For each misc post, return the theme ID if it's actually related (even indirectly - same event, same person, reaction to same news, consequences of the event), or "misc" if truly unrelated.

Return JSON object like {{"index": "theme-id-or-misc"}}"""

                    # Add images to the message
                    content_parts = [{"type": "text", "text": prompt_text}]
                    for img_info in misc_images[:10]:  # Limit images
                        content_parts.append({
                            "type": "text",
                            "text": f"[Image for post {img_info['index']}]:"
                        })
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": img_info['data']}
                        })
                    
                    response = client.chat.completions.create(
                        model=self.model,
                        messages=[{
                            "role": "user",
                            "content": content_parts
                        }],
                    )
                    
                    try:
                        content = response.choices[0].message.content.strip()
                        if content.startswith("```"):
                            content = "\n".join(content.split("\n")[1:])
                        if content.endswith("```"):
                            content = content.rsplit("```", 1)[0]
                        
                        reclassified = json.loads(content.strip())
                        reclassify_count = 0
                        for idx_str, theme_id in reclassified.items():
                            idx = int(idx_str)
                            if theme_id != "misc" and theme_id in theme_ids:
                                classified[idx] = theme_id
                                reclassify_count += 1
                        if reclassify_count > 0:
                            print(f"   Consolidation: moved {reclassify_count} posts from misc to themes")
                    except:
                        pass
        
        # Count classifications
        theme_counts = defaultdict(int)
        for theme_id in classified.values():
            theme_counts[theme_id] += 1
        print(f"   Final classification: {dict(theme_counts)}")
        
        return classified
    
    def organize_by_theme(self, threads: list, themes: list, classifications: dict) -> list:
        """Organize threads into themed sections with favorites prioritized"""
        sections = []
        used_thread_indices = set()
        
        # Track which favorite posts are used in themes
        favorite_threads_used = set()
        
        # Add major themes first
        for theme in themes:
            theme_indices = [
                i for i, t_id in classifications.items() 
                if t_id == theme['id'] and i < len(threads)
            ]
            
            if not theme_indices:
                continue
            
            # Separate favorites from others within this theme
            favorite_threads = []
            other_threads = []
            
            for idx in theme_indices:
                thread = threads[idx]
                # Check if any post in thread is from a favorite
                is_favorite = any(
                    post['author_handle'] in FAVORITE_ACCOUNTS 
                    for post in thread['posts']
                )
                if is_favorite:
                    favorite_threads.append(thread)
                    favorite_threads_used.add(idx)
                else:
                    other_threads.append(thread)
                used_thread_indices.add(idx)
            
            # Sort each group chronologically (earliest first), then favorites first
            def get_thread_time(thread):
                times = [p.get('created_at', '') for p in thread['posts'] if p.get('created_at')]
                return min(times) if times else ''
            
            favorite_threads.sort(key=get_thread_time)
            other_threads.sort(key=get_thread_time)
            ordered_threads = favorite_threads + other_threads
            
            sections.append({
                "title": theme['title'],
                "description": theme['description'],
                "threads": ordered_threads,
                "favorite_count": len(favorite_threads)
            })
        
        # "From Voices I Follow" - favorite posts NOT in any theme
        misc_indices = [
            i for i, t_id in classifications.items() 
            if t_id == "misc" and i < len(threads)
        ]
        
        favorite_misc_threads = []
        other_misc_threads = []
        
        for idx in misc_indices:
            thread = threads[idx]
            is_favorite = any(
                post['author_handle'] in FAVORITE_ACCOUNTS 
                for post in thread['posts']
            )
            if is_favorite:
                favorite_misc_threads.append(thread)
            else:
                other_misc_threads.append(thread)
        
        # Sort helper
        def get_thread_time(thread):
            times = [p.get('created_at', '') for p in thread['posts'] if p.get('created_at')]
            return min(times) if times else ''
        
        # Add "From Voices I Follow" section if there are any
        if favorite_misc_threads:
            favorite_misc_threads.sort(key=get_thread_time)
            sections.append({
                "title": "From Voices I Follow",
                "description": "",
                "threads": favorite_misc_threads,
                "favorite_count": len(favorite_misc_threads),
                "is_voices_section": True
            })
        
        # Add misc section at the end (non-favorites only)
        if other_misc_threads:
            other_misc_threads.sort(key=get_thread_time)
            sections.append({
                "title": "Also Today",
                "description": "",
                "threads": other_misc_threads,
                "favorite_count": 0
            })
        
        return sections
    
    def download_images_for_threads(self, threads: list):
        """Download all images and add base64 data to posts"""
        image_count = 0
        for thread in threads:
            for post in thread['posts']:
                # Download main post images
                for img in post['images']:
                    # Use fullsize for legibility of text in charts/screenshots
                    url = img.get('fullsize') or img.get('thumb')
                    if url:
                        img['data'] = self.download_image_as_base64(url)
                        if img['data']:
                            image_count += 1
                
                # Download quote post images
                if post.get('quote_post') and post['quote_post'].get('images'):
                    for img in post['quote_post']['images']:
                        url = img.get('fullsize') or img.get('thumb')
                        if url:
                            img['data'] = self.download_image_as_base64(url)
                            if img['data']:
                                image_count += 1
        return image_count
    
    def generate_html(self, threads: list) -> str:
        """Generate print-ready HTML"""
        
        template = Template('''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>The Bluesky Times</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Inter:wght@400;500&display=swap');
        
        @page {
            size: letter;
            margin: 0.4in 0.4in;
            
            @top-center {
                content: "THE BLUESKY TIMES";
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
        
        * {
            box-sizing: border-box;
        }
        
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
            border-bottom: 2px solid #1a1a1a;
            padding-bottom: 0.08in;
            margin-bottom: 0.12in;
        }
        
        .masthead h1 {
            font-family: 'Playfair Display', serif;
            font-size: 36pt;
            font-weight: 900;
            letter-spacing: 0.02em;
            margin: 0;
            text-transform: uppercase;
        }
        
        .masthead .tagline {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            text-transform: uppercase;
            letter-spacing: 0.25em;
            color: #555;
            margin-top: 0.04in;
        }
        
        .masthead .date {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            margin-top: 0.04in;
            color: #333;
        }
        
        .post {
            margin-bottom: 0.15in;
            padding-bottom: 0.1in;
            padding-top: 0.06in;
            border-bottom: 1px solid #e0e0e0;
        }
        
        .post:last-child {
            border-bottom: none;
        }
        
        .post.alt-bg {
            background: #f4f4f4;
            margin-left: -0.06in;
            margin-right: -0.06in;
            padding-left: 0.06in;
            padding-right: 0.06in;
        }
        
        .thread-container.alt-bg {
            background: #f0f0f0;
        }
        
        .post-header {
            display: flex;
            align-items: baseline;
            flex-wrap: wrap;
            gap: 0.04in;
            margin-bottom: 0.02in;
        }
        
        .author-name {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            font-size: 8pt;
            color: #1a1a1a;
        }
        
        .author-name.favorite::before {
            content: "★ ";
        }
        
        .author-name.favorite {
            font-weight: 600;
        }
        
        .post.favorite-post {
            margin-left: -0.04in;
            padding-left: 0.04in;
            border-left: 2px solid #333;
        }
        
        .thread-container.favorite-thread {
            border-left: 3px solid #333;
        }
        
        .author-handle {
            font-family: 'Inter', sans-serif;
            font-size: 6.5pt;
            color: #666;
        }
        
        .post-time {
            font-family: 'Inter', sans-serif;
            font-size: 6.5pt;
            color: #888;
            text-align: right;
            margin-top: 0.03in;
            display: block;
        }
        
        .post-text {
            margin: 0.02in 0;
        }
        
        .repost-indicator {
            font-family: 'Inter', sans-serif;
            font-size: 6.5pt;
            color: #2a7;
            margin-bottom: 0.02in;
        }
        
        .repost-indicator::before {
            content: "↻ ";
        }
        
        .quote-post {
            background: #f8f8f8;
            border-left: 2px solid #ccc;
            padding: 0.04in 0.08in;
            margin: 0.04in 0;
            font-size: 8pt;
        }
        
        .quote-post .quote-author {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            font-size: 7pt;
            color: #444;
            margin-bottom: 0.01in;
        }
        
        .quote-post .quote-text {
            color: #333;
        }
        
        .quote-post .quote-images {
            margin-top: 0.04in;
        }
        
        .quote-post .quote-images img {
            max-width: 100%;
            max-height: 3in;
            object-fit: contain;
            border: 1px solid #ccc;
            background: #fff;
        }
        
        .reply-context {
            background: #f0f0f0;
            border-left: 2px solid #999;
            padding: 0.06in 0.1in;
            margin-bottom: 0.08in;
            font-size: 9pt;
        }
        
        .reply-context-label {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #666;
            margin-bottom: 0.04in;
        }
        
        .reply-context-post {
            margin-bottom: 0.05in;
            padding-bottom: 0.04in;
            border-bottom: 1px dashed #ccc;
        }
        
        .reply-context-post:last-child {
            border-bottom: none;
            margin-bottom: 0;
            padding-bottom: 0;
        }
        
        .reply-context-post .ctx-author {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            font-size: 8pt;
        }
        
        .reply-context-post .ctx-text {
            color: #444;
        }
        
        .reply-context-summary {
            font-style: italic;
            color: #444;
        }
        
        .thread-continues {
            font-weight: normal;
            font-style: italic;
            color: #666;
        }
        
        .reply-context-gap {
            text-align: center;
            color: #999;
            font-size: 10pt;
            padding: 0.02in 0;
        }
        
        .thread-reply {
            margin-top: 0.08in;
            padding-top: 0.06in;
            border-top: 1px dashed #999;
        }
        
        .intermediate-reply {
            background: #f0f0f0;
            border-left: 2px solid #888;
            padding: 0.05in 0.1in;
            margin-top: 0.08in;
            margin-bottom: 0.04in;
            font-size: 9pt;
        }
        
        .intermediate-reply .ctx-author {
            font-family: 'Inter', sans-serif;
            font-weight: 600;
            font-size: 9pt;
            color: #333;
        }
        
        .intermediate-reply .author-handle {
            font-size: 7pt;
            color: #666;
        }
        
        .intermediate-reply .ctx-text {
            color: #333;
            margin-top: 0.02in;
        }
        
        .thread-container {
            margin-bottom: 0.1in;
            padding: 0.06in;
            background: linear-gradient(to right, #fafafa, #fff);
            border-left: 2px solid #1a1a1a;
        }
        
        .thread-container.favorite-thread {
            background: linear-gradient(to right, #fffef5, #fff);
            border-left: 2px solid #c9a227;
        }
        
        .thread-label {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #666;
            margin-bottom: 0.04in;
        }
        
        .thread-container .post {
            margin-bottom: 0.06in;
            padding-bottom: 0.04in;
            padding-left: 0.06in;
            border-bottom: 1px dashed #ddd;
        }
        
        .thread-container .post:first-of-type {
            padding-left: 0;
        }
        
        .thread-connector {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            color: #999;
            margin: 0.01in 0;
        }
        
        .post-stats {
            font-family: 'Inter', sans-serif;
            font-size: 6.5pt;
            color: #888;
            margin-top: 0.02in;
        }
        
        .post-stats span {
            margin-right: 0.1in;
        }
        
        .external-link {
            background: #f5f5f5;
            padding: 0.03in 0.05in;
            margin: 0.03in 0;
            border: 1px solid #e0e0e0;
        }
        
        .external-link .link-title {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            font-size: 7.5pt;
            color: #1a5fb4;
        }
        
        .external-link .link-desc {
            font-size: 7pt;
            color: #555;
            margin-top: 0.01in;
        }
        
        .post-images {
            margin: 0.04in 0;
        }
        
        .post-images img {
            width: 100%;
            max-height: 4in;
            object-fit: contain;
            border: 1px solid #ddd;
            margin-bottom: 0.04in;
            background: #fafafa;
        }
        
        .post-images.multi img {
            max-height: 3in;
        }
        
        .image-alt {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            color: #666;
            font-style: italic;
        }
    </style>
</head>
<body>
    <div class="masthead">
        <h1>The Bluesky Times</h1>
        <div class="tagline">Your Daily Social Digest</div>
        <div class="date">{{ date }}</div>
    </div>
    
    {% for item in threads %}
        {% if item.type == 'thread' and item.posts|length > 1 %}
            <div class="thread-container">
                <div class="thread-label">Thread · {{ item.posts|length }} posts</div>
                {% for post in item.posts %}
                    <div class="post">
                        <div class="post-header">
                            <span class="author-name">{{ post.author_name }}</span>
                            <span class="author-handle">@{{ post.author_handle }}</span>
                        </div>
                        <div class="post-text">{{ post.text }}</div>
                        {% if post.images %}
                            <div class="post-images{% if post.images|length > 1 %} multi{% endif %}">
                                {% for img in post.images %}
                                    {% if img.data %}
                                        <img src="{{ img.data }}" alt="{{ img.alt or '' }}">
                                    {% endif %}
                                {% endfor %}
                            </div>
                            {% if post.images[0].alt %}
                            {% endif %}
                        {% endif %}
                        {% if post.quote_post %}
                            <div class="quote-post">
                                <div class="quote-author">{{ post.quote_post.author_name }} <span class="author-handle">@{{ post.quote_post.author_handle }}</span></div>
                                <div class="quote-text">{{ post.quote_post.text }}</div>
                            </div>
                        {% endif %}
                        {% if post.external_link %}
                            <div class="external-link">
                                <div class="link-title">{{ post.external_link.title }}</div>
                                {% if post.external_link.description %}
                                    <div class="link-desc">{{ post.external_link.description[:120] }}{% if post.external_link.description|length > 120 %}...{% endif %}</div>
                                {% endif %}
                            </div>
                        {% endif %}
                        <div class="post-time">{{ post.formatted_time }}</div>
                    </div>
                    {% if not loop.last %}
                        <div class="thread-connector">↓</div>
                    {% endif %}
                {% endfor %}
            </div>
        {% else %}
            {% set post = item.posts[0] %}
            <div class="post">
                {% if post.is_repost %}
                    <div class="repost-indicator">{{ post.reposted_by }} reposted</div>
                {% endif %}
                <div class="post-header">
                    <span class="author-name">{{ post.author_name }}</span>
                    <span class="author-handle">@{{ post.author_handle }}</span>
                </div>
                <div class="post-text">{{ post.text }}</div>
                {% if post.images %}
                    <div class="post-images{% if post.images|length > 1 %} multi{% endif %}">
                        {% for img in post.images %}
                            {% if img.data %}
                                <img src="{{ img.data }}" alt="{{ img.alt or '' }}">
                            {% endif %}
                        {% endfor %}
                    </div>
                {% endif %}
                {% if post.quote_post %}
                    <div class="quote-post">
                        <div class="quote-author">{{ post.quote_post.author_name }} <span class="author-handle">@{{ post.quote_post.author_handle }}</span></div>
                        <div class="quote-text">{{ post.quote_post.text }}</div>
                    </div>
                {% endif %}
                {% if post.external_link %}
                    <div class="external-link">
                        <div class="link-title">{{ post.external_link.title }}</div>
                        {% if post.external_link.description %}
                            <div class="link-desc">{{ post.external_link.description[:120] }}{% if post.external_link.description|length > 120 %}...{% endif %}</div>
                        {% endif %}
                    </div>
                {% endif %}
                {% if post.like_count > 10 or post.repost_count > 5 %}
                    <div class="post-stats">
                        {% if post.like_count %}<span>♥ {{ post.like_count }}</span>{% endif %}
                        {% if post.repost_count %}<span>↻ {{ post.repost_count }}</span>{% endif %}
                        {% if post.reply_count %}<span>💬 {{ post.reply_count }}</span>{% endif %}
                    </div>
                {% endif %}
                <div class="post-time">{{ post.formatted_time }}</div>
            </div>
        {% endif %}
    {% endfor %}
</body>
</html>
        ''')
        
        # Add formatted time to posts
        for thread in threads:
            for post in thread['posts']:
                post['formatted_time'] = self.format_time(post['created_at'])
        
        today = datetime.now().strftime('%A, %B %d, %Y')
        
        return template.render(threads=threads, date=today)
    
    def generate_html_with_sections(self, sections: list) -> str:
        """Generate print-ready HTML with themed sections"""
        
        template = Template('''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>The Bluesky Times</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Inter:wght@400;500&display=swap');
        
        @page {
            size: letter;
            margin: 0.4in 0.4in;
            
            @top-center {
                content: "THE BLUESKY TIMES";
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
        
        * {
            box-sizing: border-box;
        }
        
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
            border-bottom: 2px solid #1a1a1a;
            padding-bottom: 0.08in;
            margin-bottom: 0.12in;
        }
        
        .masthead h1 {
            font-family: 'Playfair Display', serif;
            font-size: 36pt;
            font-weight: 900;
            letter-spacing: 0.02em;
            margin: 0;
            text-transform: uppercase;
        }
        
        .masthead .tagline {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            text-transform: uppercase;
            letter-spacing: 0.25em;
            color: #555;
            margin-top: 0.04in;
        }
        
        .masthead .date {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            margin-top: 0.04in;
            color: #333;
        }
        
        .section {
            margin-bottom: 0.25in;
        }
        
        .section-header {
            column-span: all;
            border-bottom: 1px solid #1a1a1a;
            margin-bottom: 0.1in;
            padding-bottom: 0.04in;
            margin-top: 0.12in;
        }
        
        .section-header h2 {
            font-family: 'Playfair Display', serif;
            font-size: 16pt;
            font-weight: 700;
            margin: 0;
            color: #1a1a1a;
        }
        
        .section.voices-section .section-header h2::before {
            content: "★ ";
            color: #c9a227;
        }
        
        .section-header .section-desc {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            color: #666;
            margin-top: 0.02in;
        }
        
        .post {
            margin-bottom: 0.15in;
            padding-bottom: 0.1in;
            padding-top: 0.06in;
            border-bottom: 1px solid #e0e0e0;
        }
        
        .post:last-child {
            border-bottom: none;
        }
        
        .post.alt-bg {
            background: #f4f4f4;
            margin-left: -0.06in;
            margin-right: -0.06in;
            padding-left: 0.06in;
            padding-right: 0.06in;
        }
        
        .thread-container.alt-bg {
            background: #f0f0f0;
        }
        
        .post-header {
            display: flex;
            align-items: baseline;
            flex-wrap: wrap;
            gap: 0.04in;
            margin-bottom: 0.02in;
        }
        
        .author-name {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            font-size: 8pt;
            color: #1a1a1a;
        }
        
        .author-name.favorite::before {
            content: "★ ";
        }
        
        .author-name.favorite {
            font-weight: 600;
        }
        
        .post.favorite-post {
            margin-left: -0.04in;
            padding-left: 0.04in;
            border-left: 2px solid #333;
        }
        
        .thread-container.favorite-thread {
            border-left: 3px solid #333;
        }
        
        .author-handle {
            font-family: 'Inter', sans-serif;
            font-size: 6.5pt;
            color: #666;
        }
        
        .post-time {
            font-family: 'Inter', sans-serif;
            font-size: 6.5pt;
            color: #888;
            text-align: right;
            margin-top: 0.03in;
            display: block;
        }
        
        .post-text {
            margin: 0.02in 0;
        }
        
        .repost-indicator {
            font-family: 'Inter', sans-serif;
            font-size: 6.5pt;
            color: #2a7;
            margin-bottom: 0.02in;
        }
        
        .repost-indicator::before {
            content: "↻ ";
        }
        
        .quote-post {
            background: #f8f8f8;
            border-left: 2px solid #ccc;
            padding: 0.04in 0.08in;
            margin: 0.04in 0;
            font-size: 8pt;
        }
        
        .quote-post .quote-author {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            font-size: 7pt;
            color: #444;
            margin-bottom: 0.01in;
        }
        
        .quote-post .quote-text {
            color: #333;
        }
        
        .quote-post .quote-images {
            margin-top: 0.04in;
        }
        
        .quote-post .quote-images img {
            max-width: 100%;
            max-height: 3in;
            object-fit: contain;
            border: 1px solid #ccc;
            background: #fff;
        }
        
        .reply-context {
            background: #f0f0f0;
            border-left: 2px solid #999;
            padding: 0.06in 0.1in;
            margin-bottom: 0.08in;
            font-size: 9pt;
        }
        
        .reply-context-label {
            font-family: 'Inter', sans-serif;
            font-size: 7pt;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #666;
            margin-bottom: 0.04in;
        }
        
        .reply-context-post {
            margin-bottom: 0.05in;
            padding-bottom: 0.04in;
            border-bottom: 1px dashed #ccc;
        }
        
        .reply-context-post:last-child {
            border-bottom: none;
            margin-bottom: 0;
            padding-bottom: 0;
        }
        
        .reply-context-post .ctx-author {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            font-size: 8pt;
        }
        
        .reply-context-post .ctx-text {
            color: #444;
        }
        
        .reply-context-summary {
            font-style: italic;
            color: #444;
        }
        
        .thread-continues {
            font-weight: normal;
            font-style: italic;
            color: #666;
        }
        
        .reply-context-gap {
            text-align: center;
            color: #999;
            font-size: 10pt;
            padding: 0.02in 0;
        }
        
        .thread-reply {
            margin-top: 0.08in;
            padding-top: 0.06in;
            border-top: 1px dashed #999;
        }
        
        .intermediate-reply {
            background: #f0f0f0;
            border-left: 2px solid #888;
            padding: 0.05in 0.1in;
            margin-top: 0.08in;
            margin-bottom: 0.04in;
            font-size: 9pt;
        }
        
        .intermediate-reply .ctx-author {
            font-family: 'Inter', sans-serif;
            font-weight: 600;
            font-size: 9pt;
            color: #333;
        }
        
        .intermediate-reply .author-handle {
            font-size: 7pt;
            color: #666;
        }
        
        .intermediate-reply .ctx-text {
            color: #333;
            margin-top: 0.02in;
        }
        
        .thread-container {
            margin-bottom: 0.1in;
            padding: 0.06in;
            background: linear-gradient(to right, #fafafa, #fff);
            border-left: 2px solid #1a1a1a;
        }
        
        .thread-container.favorite-thread {
            background: linear-gradient(to right, #fffef5, #fff);
            border-left: 2px solid #c9a227;
        }
        
        .thread-label {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #666;
            margin-bottom: 0.04in;
        }
        
        .thread-container .post {
            margin-bottom: 0.06in;
            padding-bottom: 0.04in;
            padding-left: 0.06in;
            border-bottom: 1px dashed #ddd;
        }
        
        .thread-container .post:first-of-type {
            padding-left: 0;
        }
        
        .thread-connector {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            color: #999;
            margin: 0.01in 0;
        }
        
        .post-stats {
            font-family: 'Inter', sans-serif;
            font-size: 6.5pt;
            color: #888;
            margin-top: 0.02in;
        }
        
        .post-stats span {
            margin-right: 0.1in;
        }
        
        .external-link {
            background: #f5f5f5;
            padding: 0.03in 0.05in;
            margin: 0.03in 0;
            border: 1px solid #e0e0e0;
        }
        
        .external-link .link-title {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            font-size: 7.5pt;
            color: #1a5fb4;
        }
        
        .external-link .link-desc {
            font-size: 7pt;
            color: #555;
            margin-top: 0.01in;
        }
        
        .post-images {
            margin: 0.04in 0;
        }
        
        .post-images img {
            width: 100%;
            max-height: 4in;
            object-fit: contain;
            border: 1px solid #ddd;
            margin-bottom: 0.04in;
            background: #fafafa;
        }
        
        .post-images.multi img {
            max-height: 3in;
        }
        
        .image-alt {
            font-family: 'Inter', sans-serif;
            font-size: 6pt;
            color: #666;
            font-style: italic;
        }
    </style>
</head>
<body>
    <div class="masthead">
        <h1>The Bluesky Times</h1>
        <div class="tagline">Your Daily Social Digest</div>
        <div class="date">{{ date }}</div>
    </div>
    
    {% for section in sections %}
    <div class="section{% if section.is_voices_section %} voices-section{% endif %}">
        <div class="section-header">
            <h2>{{ section.title }}</h2>
            {% if section.description %}
                <div class="section-desc">{{ section.description }}</div>
            {% endif %}
        </div>
        
        {% for item in section.threads %}
            {% set thread_is_favorite = item.posts[0].author_handle in favorites %}
            {% if item.type == 'thread' and item.posts|length > 1 %}
                {% set first_post = item.posts[0] %}
                {% if first_post.reply_context %}
                    <div class="reply-context">
                        {% if first_post.reply_context.type == 'summary' %}
                            <div class="reply-context-label">Thread replying to:</div>
                            <div class="reply-context-summary">{{ first_post.reply_context.summary }}</div>
                        {% elif first_post.reply_context.type in ['full', 'continued'] %}
                            <div class="reply-context-label">Thread replying to:</div>
                            {% for ctx_post in first_post.reply_context.posts %}
                                {% if ctx_post.is_gap %}
                                    <div class="reply-context-gap">⋮</div>
                                {% else %}
                                    <div class="reply-context-post">
                                        <span class="ctx-author">{{ ctx_post.author_name }}</span>
                                        <span class="author-handle">@{{ ctx_post.author_handle }}</span>
                                        <div class="ctx-text">{{ ctx_post.text }}</div>
                                    </div>
                                {% endif %}
                            {% endfor %}
                        {% endif %}
                    </div>
                {% endif %}
                <div class="thread-container{% if thread_is_favorite %} favorite-thread{% endif %}{% if loop.index is odd %} alt-bg{% endif %}">
                    <div class="thread-label">Thread · {{ item.posts|length }} posts</div>
                    {% for post in item.posts %}
                        {% set is_fav = post.author_handle in favorites %}
                        <div class="post{% if is_fav %} favorite-post{% endif %}">
                            <div class="post-header">
                                <span class="author-name{% if is_fav %} favorite{% endif %}">{{ post.author_name }}</span>
                                <span class="author-handle">@{{ post.author_handle }}</span>
                            </div>
                            <div class="post-text">{{ post.text }}</div>
                            {% if post.images %}
                                <div class="post-images{% if post.images|length > 1 %} multi{% endif %}">
                                    {% for img in post.images %}
                                        {% if img.data %}
                                            <img src="{{ img.data }}" alt="{{ img.alt or '' }}">
                                        {% endif %}
                                    {% endfor %}
                                </div>
                            {% endif %}
                            {% if post.quote_post %}
                                <div class="quote-post">
                                    <div class="quote-author">{{ post.quote_post.author_name }} <span class="author-handle">@{{ post.quote_post.author_handle }}</span></div>
                                    <div class="quote-text">{{ post.quote_post.text }}</div>
                                    {% if post.quote_post.images %}
                                        <div class="quote-images">
                                            {% for img in post.quote_post.images %}
                                                {% if img.data %}
                                                    <img src="{{ img.data }}" alt="{{ img.alt or '' }}">
                                                {% endif %}
                                            {% endfor %}
                                        </div>
                                    {% endif %}
                                </div>
                            {% endif %}
                            {% if post.external_link %}
                                <div class="external-link">
                                    <div class="link-title">{{ post.external_link.title }}</div>
                                    {% if post.external_link.description %}
                                        <div class="link-desc">{{ post.external_link.description[:120] }}{% if post.external_link.description|length > 120 %}...{% endif %}</div>
                                    {% endif %}
                                </div>
                            {% endif %}
                            <div class="post-time">{{ post.formatted_time }}</div>
                        </div>
                    {% endfor %}
                </div>
            {% else %}
                {% set post = item.posts[0] %}
                {% set is_fav = post.author_handle in favorites %}
                <div class="post{% if is_fav %} favorite-post{% endif %}{% if loop.index is odd %} alt-bg{% endif %}">
                    {% if post.reply_context %}
                        <div class="reply-context">
                            {% if post.reply_context.type == 'continued' %}
                                <div class="reply-context-label">Continuing thread:</div>
                                {% for ctx_post in post.reply_context.posts %}
                                    {% if ctx_post.is_gap %}
                                        <div class="reply-context-gap">⋮</div>
                                    {% else %}
                                        <div class="reply-context-post">
                                            <span class="ctx-author">{{ ctx_post.author_name }}</span>
                                            <span class="author-handle">@{{ ctx_post.author_handle }}</span>
                                            <div class="ctx-text">{{ ctx_post.text }}</div>
                                        </div>
                                    {% endif %}
                                {% endfor %}
                            {% else %}
                                <div class="reply-context-label">Replying to thread:{% if post.thread_continues %} <span class="thread-continues">({{ post.thread_continues }} more replies below)</span>{% endif %}</div>
                                {% if post.reply_context.type == 'full' %}
                                    {% for ctx_post in post.reply_context.posts %}
                                        {% if ctx_post.is_gap %}
                                            <div class="reply-context-gap">⋮</div>
                                        {% else %}
                                            <div class="reply-context-post">
                                                <span class="ctx-author">{{ ctx_post.author_name }}</span>
                                                <span class="author-handle">@{{ ctx_post.author_handle }}</span>
                                                <div class="ctx-text">{{ ctx_post.text }}</div>
                                            </div>
                                        {% endif %}
                                    {% endfor %}
                                {% elif post.reply_context.type == 'summary' %}
                                    <div class="reply-context-summary">{{ post.reply_context.summary }}</div>
                                {% endif %}
                            {% endif %}
                        </div>
                    {% endif %}
                    {% if post.is_repost %}
                        <div class="repost-indicator">{{ post.reposted_by }} reposted</div>
                    {% endif %}
                    <div class="post-header">
                        <span class="author-name{% if is_fav %} favorite{% endif %}">{{ post.author_name }}</span>
                        <span class="author-handle">@{{ post.author_handle }}</span>
                    </div>
                    <div class="post-text">{{ post.text }}</div>
                    {% if post.images %}
                        <div class="post-images{% if post.images|length > 1 %} multi{% endif %}">
                            {% for img in post.images %}
                                {% if img.data %}
                                    <img src="{{ img.data }}" alt="{{ img.alt or '' }}">
                                {% endif %}
                            {% endfor %}
                        </div>
                    {% endif %}
                    {% if post.quote_post %}
                        <div class="quote-post">
                            <div class="quote-author">{{ post.quote_post.author_name }} <span class="author-handle">@{{ post.quote_post.author_handle }}</span></div>
                            <div class="quote-text">{{ post.quote_post.text }}</div>
                        </div>
                    {% endif %}
                    {% if post.external_link %}
                        <div class="external-link">
                            <div class="link-title">{{ post.external_link.title }}</div>
                            {% if post.external_link.description %}
                                <div class="link-desc">{{ post.external_link.description[:120] }}{% if post.external_link.description|length > 120 %}...{% endif %}</div>
                            {% endif %}
                        </div>
                    {% endif %}
                    {% if post.like_count > 10 or post.repost_count > 5 %}
                        <div class="post-stats">
                            {% if post.like_count %}<span>♥ {{ post.like_count }}</span>{% endif %}
                            {% if post.repost_count %}<span>↻ {{ post.repost_count }}</span>{% endif %}
                            {% if post.reply_count %}<span>💬 {{ post.reply_count }}</span>{% endif %}
                        </div>
                    {% endif %}
                    <div class="post-time">{{ post.formatted_time }}</div>
                    {% if post.thread_replies %}
                        {% for reply in post.thread_replies %}
                            {% if reply.immediate_parent %}
                                <div class="intermediate-reply">
                                    <span class="ctx-author">{{ reply.immediate_parent.author_name }}</span>
                                    <span class="author-handle">@{{ reply.immediate_parent.author_handle }}</span>
                                    <div class="ctx-text">{{ reply.immediate_parent.text }}</div>
                                </div>
                            {% endif %}
                            <div class="thread-reply">
                                <div class="post-header">
                                    <span class="author-name favorite">★ {{ reply.author_name }}</span>
                                    <span class="author-handle">@{{ reply.author_handle }}</span>
                                </div>
                                <div class="post-text">{{ reply.text }}</div>
                                <div class="post-time">{{ reply.formatted_time or '' }}</div>
                            </div>
                        {% endfor %}
                    {% endif %}
                </div>
            {% endif %}
        {% endfor %}
    </div>
    {% endfor %}
</body>
</html>
        ''')
        
        # Add formatted time to posts in all sections
        for section in sections:
            for thread in section['threads']:
                for post in thread['posts']:
                    post['formatted_time'] = self.format_time(post['created_at'])
                    # Also format times for consolidated thread replies
                    if post.get('thread_replies'):
                        for reply in post['thread_replies']:
                            reply['formatted_time'] = self.format_time(reply.get('created_at'))
        
        today = datetime.now().strftime('%A, %B %d, %Y')
        
        return template.render(sections=sections, date=today, favorites=FAVORITE_ACCOUNTS)
    
    def generate_pdf(self, output_path: str = None, use_cache: bool = False, save_cache: bool = True, use_themes: bool = True):
        """Main method to generate the PDF
        
        Args:
            output_path: Output PDF path (default: bluesky_times_YYYY-MM-DD.pdf)
            use_cache: If True, load from cache.json instead of fetching fresh
            save_cache: If True, save fetched data to cache.json for iteration
            use_themes: If True, use LLM to organize posts by theme
        """
        if output_path is None:
            output_path = f"bluesky_times_{datetime.now().strftime('%Y-%m-%d')}.pdf"
        
        if use_cache:
            threads = self.load_cache()
        else:
            print("📰 Fetching your Bluesky timeline...")
            feed = self.fetch_timeline(limit=250)
            
            print(f"📝 Processing {len(feed)} posts...")
            posts = [self.extract_post_data(f) for f in feed]
            
            # Filter out user's own posts
            own_posts = sum(1 for p in posts if p['author_handle'] == self.user_handle)
            if own_posts > 0:
                posts = [p for p in posts if p['author_handle'] != self.user_handle]
                print(f"   Filtered out {own_posts} of your own posts")
            
            # Add reply context for favorite voices
            self.add_reply_context_for_favorites(posts)
            
            print("🧵 Organizing threads...")
            threads = self.organize_threads(posts)
            
            # Add context to threads with favorites (for multi-person conversations)
            self.add_thread_context_for_favorites(threads)
            
            # Add basic context for ALL reply posts
            self.add_basic_reply_context(threads)
            
            print("🖼️  Downloading images...")
            image_count = self.download_images_for_threads(threads)
            print(f"   Downloaded {image_count} images")
            
            if save_cache:
                self.save_cache(threads)
        
        # Consolidate same-thread participations before theme classification
        threads = self.consolidate_thread_participations(threads)
        
        # Theme classification
        if use_themes:
            themes = self.identify_themes(threads)
            classifications = self.classify_posts(threads, themes)
            sections = self.organize_by_theme(threads, themes, classifications)
            
            print("🎨 Generating themed layout...")
            html_content = self.generate_html_with_sections(sections)
        else:
            print("🎨 Generating layout...")
            html_content = self.generate_html(threads)
        
        # Save HTML for debugging
        html_path = output_path.replace('.pdf', '.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print("🖨️  Creating PDF...")
        HTML(string=html_content).write_pdf(output_path)
        
        print(f"\n✅ Done! Your daily Bluesky Times is ready: {output_path}")
        print(f"   HTML version also saved: {html_path}")
        
        return output_path

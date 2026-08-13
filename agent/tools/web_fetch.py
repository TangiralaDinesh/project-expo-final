"""
Advanced web fetch tool with retry logic, redirect handling, and bot-detection bypass.

COMPATIBLE WITH BLOCK INTERFACE:
- Returns `str` (content only) like original, but with advanced retry/error handling
- Injectable seam via fetch_fn param maintains BlockInput.fetch_fn compatibility
- Internal logic advanced (403 handling, 429 backoff, redirects, timeouts)
- Raises exceptions on critical errors instead of silent fails (blocks can catch)
"""

from __future__ import annotations

import re
import logging
import random
import asyncio
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Reuse the HTML stripping from chunk.py
from ..blocks.semantic.chunk import strip_html

# Browser-like user-agents (rotating to avoid bot detection)
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

# Circuit breaker cache for problematic domains
_CIRCUIT_BREAKER = {}


def _anti_bot_headers(referrer: str = "https://www.google.com") -> dict[str, str]:
    """Headers to bypass Cloudflare, WAF, bot detection."""
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Referer": referrer,
        "Cache-Control": "max-age=0",
    }


def _check_circuit_breaker(url: str) -> bool:
    """Check if domain is rate-limited (circuit breaker)."""
    from urllib.parse import urlparse
    hostname = urlparse(url).hostname
    if not hostname:
        return True
    
    entry = _CIRCUIT_BREAKER.get(hostname)
    if not entry:
        return True
    
    if entry['blocked_until'] > 0 and entry['blocked_until'] > asyncio.get_event_loop().time():
        return False
    
    # Clear expired entry
    del _CIRCUIT_BREAKER[hostname]
    return True


def _record_circuit_breaker_failure(url: str, status: int):
    """Record 403/429 for circuit breaker."""
    if status not in (403, 429):
        return
    
    from urllib.parse import urlparse
    hostname = urlparse(url).hostname
    if not hostname:
        return
    
    entry = _CIRCUIT_BREAKER.get(hostname, {'fail_count': 0, 'blocked_until': 0})
    entry['fail_count'] += 1
    # Exponential backoff: 60s, 300s, 900s
    backoff_secs = min(60 * (2 ** (entry['fail_count'] - 1)), 900)
    entry['blocked_until'] = asyncio.get_event_loop().time() + backoff_secs
    _CIRCUIT_BREAKER[hostname] = entry
    
    logger.warning(f"Circuit breaker: {hostname} blocked for {backoff_secs}s (fail #{entry['fail_count']})")


async def fetch_url(
    url: str,
    *,
    max_chars: int = 50_000,
    timeout: float = 15.0,
    session: Optional[aiohttp.ClientSession] = None,
) -> str:
    """Fetch a URL and return cleaned text content.

    Advanced fallback with retry logic, anti-bot headers, 403/429 handling.
    For JavaScript-rendered or authenticated pages, inject custom fetch_fn.
    
    Raises:
        RuntimeError: On unrecoverable errors (bot detection, auth required)
        asyncio.TimeoutError: On timeout after retries
    
    Returns:
        Cleaned text content (str), truncated to max_chars
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    max_retries = 3
    backoff_base = 1.0
    last_error = None

    try:
        # Circuit breaker check
        if not _check_circuit_breaker(url):
            from urllib.parse import urlparse
            hostname = urlparse(url).hostname
            raise RuntimeError(f"Domain {hostname} rate-limited (circuit breaker). Retry later.")

        for attempt in range(max_retries):
            try:
                headers = _anti_bot_headers()
                
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=False,
                    ssl=False,  # Fallback for SSL cert issues
                ) as resp:
                    # Handle redirects
                    if resp.status in (301, 302, 303, 307, 308):
                        redirect_url = resp.headers.get("Location")
                        if redirect_url:
                            if not redirect_url.startswith("http"):
                                from urllib.parse import urljoin
                                redirect_url = urljoin(url, redirect_url)
                            # Log redirect but don't follow (let caller re-fetch)
                            logger.info(f"Redirect {resp.status}: {url} → {redirect_url}")
                            raise RuntimeError(f"Redirect detected (HTTP {resp.status}): {redirect_url}")

                    # 403 Forbidden → retry with backoff
                    if resp.status == 403:
                        if attempt < max_retries - 1:
                            sleep_time = backoff_base ** attempt
                            logger.info(f"403 Forbidden on {url}, backoff {sleep_time}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(sleep_time)
                            continue
                        else:
                            _record_circuit_breaker_failure(url, 403)
                            raise RuntimeError("Bot detection (HTTP 403). Consider injecting custom fetch_fn with Playwright.")

                    # 429 Rate Limited → respect backoff
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        sleep_time = float(retry_after) if retry_after and retry_after.isdigit() else (backoff_base ** attempt)
                        if attempt < max_retries - 1:
                            logger.info(f"429 Rate Limited on {url}, backoff {sleep_time}s")
                            await asyncio.sleep(min(sleep_time, 30))
                            continue
                        else:
                            _record_circuit_breaker_failure(url, 429)
                            raise RuntimeError(f"Rate limited (HTTP 429). Server requested backoff of {sleep_time}s.")

                    # 503 Service Unavailable → retry
                    if resp.status == 503:
                        if attempt < max_retries - 1:
                            sleep_time = backoff_base ** attempt
                            logger.info(f"503 Service Unavailable, backoff {sleep_time}s")
                            await asyncio.sleep(sleep_time)
                            continue

                    # 401 Unauthorized or other 4xx → don't retry
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}")

                    # Success → extract content
                    content_type = resp.headers.get("content-type", "")
                    raw = await resp.text(errors="replace")

                    # Strip HTML if needed
                    if "html" in content_type.lower() or raw.strip().startswith("<"):
                        text = strip_html(raw)
                    else:
                        text = raw

                    # Truncate
                    if len(text) > max_chars:
                        text = text[:max_chars] + "\n\n… [content truncated]"

                    logger.info(f"Fetched {len(raw)} bytes from {url}")
                    return text

            except asyncio.TimeoutError:
                last_error = "Timeout"
                if attempt < max_retries - 1:
                    sleep_time = backoff_base ** attempt
                    logger.info(f"Timeout on {url}, backoff {sleep_time}s")
                    await asyncio.sleep(sleep_time)
                    continue
            
            except aiohttp.ClientSSLError as e:
                # SSL errors usually unrecoverable
                raise RuntimeError(f"SSL/TLS error: {str(e)[:80]}")
            
            except aiohttp.ClientConnectorError as e:
                last_error = f"Connection: {str(e)[:50]}"
                if attempt < max_retries - 1:
                    sleep_time = backoff_base ** attempt
                    logger.info(f"Connection error on {url}, backoff {sleep_time}s")
                    await asyncio.sleep(sleep_time)
                    continue
            
            except aiohttp.ClientError as e:
                last_error = f"aiohttp error: {str(e)[:50]}"
                if attempt < max_retries - 1:
                    continue

        # All retries exhausted
        raise RuntimeError(f"Fetch failed after {max_retries} retries: {last_error or 'Unknown error'}")

    finally:
        if own_session and session:
            await session.close()


async def fetch_url_with_seam(
    url: str,
    *,
    fetch_fn=None,
    session: Optional[aiohttp.ClientSession] = None,
) -> str:
    """Use injected fetch_fn if provided, otherwise built-in with advanced retry logic.

    This is the function blocks call — they pass BlockInput.fetch_fn through,
    and this routes to either the custom tool or built-in.
    
    Raises:
        Exception: Propagates exceptions from fetch_fn or fetch_url for blocks to handle
    
    Returns:
        Cleaned text content (str)
    """
    if fetch_fn is not None:
        try:
            result = await fetch_fn(url)
            # Normalize if custom fn returns str
            if isinstance(result, str):
                return result
            return result
        except Exception as e:
            logger.warning(f"Custom fetch_fn failed for {url}: {e}, falling back to built-in")

    return await fetch_url(url, session=session)
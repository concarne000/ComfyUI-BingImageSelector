"""Bing image search and persistent, pre-execution image cache.

Uses the Bing images/async + murl approach demonstrated by ConCarneNode.
The implementation is independent; no model/transformers dependencies are needed.
"""
import concurrent.futures
import hashlib
import html
import io
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
import uuid
import warnings
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageOps

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
MAX_IMAGES = 64
MAX_DOWNLOAD = 20 * 1024 * 1024


def public_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Image URL must be a public HTTP(S) URL.")
    if parts.port not in (None, 80, 443):
        raise ValueError("Nonstandard URL port is not supported.")
    addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Private or local image addresses are not supported.")
    return url


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit=MAX_DOWNLOAD):
    public_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicRedirect())
    with opener.open(request, timeout=12) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeds the size limit.")
    return data


class BingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "m" not in attrs:
            return
        try:
            metadata = json.loads(attrs["m"])
            url = metadata.get("murl")
            if isinstance(url, str):
                self.items.append({"url": url, "title": str(metadata.get("t", "")), "source": str(metadata.get("purl", ""))})
        except (ValueError, TypeError, AttributeError):
            pass


def parse_results(document):
    parser = BingParser()
    parser.feed(document)
    items = parser.items
    if not items:
        decoded = html.unescape(document)
        for match in re.finditer(r'"?murl"\s*:\s*("(?:[^"\\]|\\.)*")', decoded):
            try:
                items.append({"url": json.loads(match[1]), "title": "", "source": ""})
            except ValueError:
                pass
    seen = set()
    result = []
    for item in items:
        if item["url"].startswith(("https://", "http://")) and item["url"] not in seen:
            seen.add(item["url"])
            result.append(item)
    return result


def search_links(query, count):
    results, seen = [], set()
    for page in range(6):
        params = urllib.parse.urlencode({"q": query, "first": page * 35, "count": 35, "adlt": "moderate"})
        document = fetch("https://www.bing.com/images/async?" + params, 5 * 1024 * 1024).decode("utf-8", errors="replace")
        fresh = [item for item in parse_results(document) if item["url"] not in seen]
        if not fresh:
            break
        for item in fresh:
            seen.add(item["url"])
            results.append(item)
        if len(results) >= count:
            break
    return results[:count]


def validate_search(query, count):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
        raise ValueError("Enter a search term of 1–500 characters.")
    if type(count) is not int or not 1 <= count <= MAX_IMAGES:
        raise ValueError(f"Number of images must be between 1 and {MAX_IMAGES}.")
    return query.strip(), count


def cache_image(item, directory):
    try:
        data = fetch(item["url"])
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.width * original.height > 40_000_000:
                    raise ValueError("Image is too large.")
                image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
        key = hashlib.sha256(item["url"].encode()).hexdigest()
        image.save(directory / f"{key}.png")
        width, height = image.size
        image.thumbnail((240, 180), Image.Resampling.LANCZOS)
        image.save(directory / f"{key}.jpg", quality=85)
        return {**item, "id": key, "width": width, "height": height}
    except Exception:
        # A dead/blocked URL must not prevent other search results from appearing.
        return None


def prepare_search(root, query, count):
    query, count = validate_search(query, count)
    candidates = search_links(query, min(count * 3, 192))
    if not candidates:
        raise ValueError("Bing returned no image links. Try another term; Bing may also be blocking automated searches.")
    session = uuid.uuid4().hex
    directory = Path(root) / session
    directory.mkdir(parents=True)
    items = []
    # Bounded chunks avoid downloading all extra candidates after the count is met.
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        cursor = 0
        while len(items) < count and cursor < len(candidates):
            size = min(count - len(items), 6)
            chunk = candidates[cursor:cursor + size]
            items.extend(item for item in pool.map(lambda entry: cache_image(entry, directory), chunk) if item)
            cursor += len(chunk)
    if not items:
        directory.rmdir()
        raise ValueError("Image links were found, but none could be downloaded. Try a different search.")
    manifest = {"session": session, "query": query, "count": count, "items": items}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


def session_path(root, session):
    if not isinstance(session, str) or not re.fullmatch(r"[0-9a-f]{32}", session):
        raise ValueError("Invalid search session. Search again.")
    return Path(root) / session


def resolve_selection(root, state, query, count):
    query, count = validate_search(query, count)
    try:
        state = json.loads(state)
        if not isinstance(state, dict):
            raise ValueError()
        directory = session_path(root, state.get("session"))
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (ValueError, TypeError, OSError):
        raise ValueError("Search for images and select thumbnails before running. Cached images must exist on this computer.") from None
    if manifest["query"] != query or manifest["count"] != count:
        raise ValueError("Search settings changed. Click Search images again and make a new selection.")
    selected = state.get("selected")
    if not isinstance(selected, list) or not selected or len(selected) > MAX_IMAGES or any(not isinstance(key, str) for key in selected):
        raise ValueError("Select at least one thumbnail before running.")
    entries = {item["id"]: item for item in manifest["items"]}
    if len(set(selected)) != len(selected) or any(key not in entries for key in selected):
        raise ValueError("Invalid image selection. Search again.")
    items = [entries[key] for key in selected]
    paths = [directory / f'{item["id"]}.png' for item in items]
    if any(not path.is_file() for path in paths):
        raise ValueError("A selected cached image is missing. Search again.")
    return items, paths


def output_dimensions(width, height, sizing_mode, megapixels, resolution_interval):
    import math
    if type(width) is not int or type(height) is not int or not (64 <= width <= 8192 and 64 <= height <= 8192):
        raise ValueError("Width and height must be between 64 and 8192.")
    if str(resolution_interval) not in ("8", "16", "32", "64", "128"):
        raise ValueError("Resolution interval must be 8, 16, 32, 64 or 128.")
    interval = int(resolution_interval)
    if sizing_mode == "megapixels":
        if not isinstance(megapixels, (int, float)) or not math.isfinite(megapixels) or not .05 <= megapixels <= 16:
            raise ValueError("Megapixels must be between 0.05 and 16.")
        scale = math.sqrt(megapixels * 1_000_000 / (width * height))
        width, height = width * scale, height * scale
    elif sizing_mode != "width_height":
        raise ValueError("Unknown sizing mode.")
    width, height = (max(interval, math.floor(value / interval + .5) * interval) for value in (width, height))
    if max(width, height) > 8192:
        raise ValueError("Rounded dimensions exceed 8192. Reduce megapixels or use a less extreme aspect ratio.")
    return width, height


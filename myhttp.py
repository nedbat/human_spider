"""HTTP helpers."""

import json
import logging
import mimetypes
import re
import socket
import time
import urllib.parse

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection

import aiohttp
from bs4 import BeautifulSoup


def slug_for_url(url: str) -> str:
    return re.sub(r"[^\w]", "_", url.partition("://")[-1])


def fix_url(url: str) -> str:
    url = url.rstrip("/")
    if ":" not in url:
        url = f"https://{url}"
    return url


def root_for_url(url: str) -> str:
    parts = urllib.parse.urlparse(fix_url(url))
    return f"{parts.scheme}://{parts.netloc}"


# Don't request from the same IP more than once per this many seconds.
ONE_PER = 0.25


class TryLater(Exception):
    """Raise this to try a work item again later."""

    def __init__(self, delay: float, reason: str) -> None:
        self.delay = delay
        self.reason = reason


@dataclass
class AccessInfo:
    """Information about when we can access a resource."""

    # Don't access it until after this time.
    next_ok: float
    # The farthest out time we told someone to wait.
    last_waiter: float


class RateLimiter:
    """Track access to resources."""

    def __init__(self, one_per: float) -> None:
        self.one_per = one_per
        self.resources: dict[str, AccessInfo] = {}

    def should_wait(self, resource: str) -> float:
        """How long should we wait to access this resource?"""
        now = time.time()
        now_next = now + self.one_per
        info = self.resources.get(resource)
        if info is None:
            self.resources[resource] = AccessInfo(next_ok=now_next, last_waiter=now_next)
            return 0
        elif now > info.next_ok:
            info.next_ok = info.last_waiter = now_next
            return 0
        else:
            my_time = info.last_waiter + self.one_per * 1.05
            info.last_waiter = my_time
            return my_time - now


limiter = RateLimiter(one_per=ONE_PER)


@dataclass
class Resp:
    resp: aiohttp.ClientResponse
    content: bytes
    _soup: Any = None

    def soup(self):
        if self._soup is None:
            self._soup = BeautifulSoup(self.content, "html.parser")
        return self._soup

    @property
    def url(self) -> str:
        return str(self.resp.url)

    def json(self) -> dict:
        return json.loads(self.content)

    def text(self) -> str:
        # Assume utf-8, fix it later if we need to
        return self.content.decode("utf-8")

    def content_type(self) -> str:
        return self.resp.headers.get("content-type", "").partition(";")[0].strip()

    def save(self, *, dirname: str = "") -> None:
        filename = slug_for_url(self.url)
        ext = mimetypes.guess_extension(self.content_type())
        if ext is None:
            if m := re.search(r"\.\w+$", self.url):
                ext = m.group(0)
        if ext is None:
            ext = ".dat"
        with Path(dirname, f"{filename}{ext}").open("wb") as f:
            f.write(self.content)


fetch_log = logging.getLogger("fetch")
fetch_log.setLevel(logging.DEBUG)
fetch_handler = logging.FileHandler("fetch.log", mode="w")
fetch_handler.setFormatter(
    logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
)
fetch_log.addHandler(fetch_handler)


@dataclass
class Req:
    url: str
    base: str = ""
    fail_ok: bool = False
    ok_errors: Collection[int] = ()
    ok_content_types: Collection[str] = ()

    async def get(self) -> Resp | None:
        if self.base:
            url = urllib.parse.urljoin(self.base, self.url)
        else:
            url = self.url
        url = fix_url(url)

        netloc = urllib.parse.urlparse(url).netloc
        ip = socket.gethostbyname(netloc)
        show_url = f"{url} ({ip})"
        delay = limiter.should_wait(ip)
        if delay > 0:
            fetch_log.info("Delay %s for %.1f", show_url, delay)
            raise TryLater(delay=delay, reason=f"limit for {netloc} ({ip})")

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "User-Agent": "nedbat's human website crawler, https://nedbatchelder.com/blog/202603/humanjson",
                }
                async with session.get(url, timeout=10, headers=headers) as resp:
                    # from report import print_both
                    # print_both(f"   Got {url} ({ip}): status={resp.status}")
                    fetch_log.info("Fetch %s, status=%s", show_url, resp.status)
                    if resp.status == 429:
                        raise TryLater(delay=3, reason="status=429")
                    if resp.status != 200 and self.fail_ok:
                        return None
                    if resp.status in self.ok_errors:
                        return None
                    if (
                        self.ok_content_types
                        and resp.content_type not in self.ok_content_types
                    ):
                        return None
                    resp.raise_for_status()
                    return Resp(resp, await resp.content.read())
        except Exception as e:
            fetch_log.info("Fetch %s ** %s: %s", show_url, e.__class__.__name__, e)
            raise

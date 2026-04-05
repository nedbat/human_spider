"""HTTP helpers."""

import json
import mimetypes
import random
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
    after: float
    # How many requesters have been told to wait.
    waiters: int


class RateLimiter:
    """Track access to resources."""

    def __init__(self, one_per: float) -> None:
        self.one_per = one_per
        self.resources: dict[str, AccessInfo] = {}

    def should_wait(self, resource: str) -> float:
        """How long should we wait to access this resource?"""
        now = time.time()
        info = self.resources.get(resource)
        if info is None or now > info.after:
            self.resources[resource] = AccessInfo(after=now + self.one_per, waiters=0)
            return 0
        else:
            info.waiters += 1
            # A random wait time, weighted toward the longer end.
            return (info.after - now) + (
                1 - random.random() ** 2
            ) * self.one_per * info.waiters


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
        delay = limiter.should_wait(ip)
        if delay > 0:
            raise TryLater(delay=delay, reason=f"limit for {netloc} ({ip})")

        async with aiohttp.ClientSession() as session:
            headers = {
                "User-Agent": "nedbat's human website crawler, https://nedbatchelder.com/blog/202603/humanjson",
            }
            async with session.get(url, timeout=10, headers=headers) as resp:
                #from report import print_both
                #print_both(f"   Got {url} ({ip}): status={resp.status}")
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

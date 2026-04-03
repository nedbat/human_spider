import json
import mimetypes
import re
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


class TryLater(Exception):
    def __init__(self, delay: int) -> None:
        self.delay = delay


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
        async with aiohttp.ClientSession() as session:
            headers = {
                "User-Agent": "nedbat's human website crawler, https://nedbatchelder.com/blog/202603/humanjson",
            }
            async with session.get(url, timeout=10, headers=headers) as resp:
                if resp.status == 429:
                    raise TryLater(delay=3)
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

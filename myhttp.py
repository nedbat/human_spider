import json
import mimetypes
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

import aiohttp
from bs4 import BeautifulSoup


def filename_for_url(url: str) -> str:
    return re.sub(r"[^\w]", "_", url.partition("://")[-1])


@dataclass
class Resp:
    resp: aiohttp.ClientResponse
    content: bytes

    def soup(self):
        return BeautifulSoup(self.content, "html.parser")

    @property
    def url(self) -> str:
        return str(self.resp.url)

    def json(self) -> dict:
        return json.loads(self.content)

    def save(self, *, dirname: str = "") -> None:
        filename = filename_for_url(self.url)
        content_type = (
            self.resp.headers.get("content-type", "").partition(";")[0].strip()
        )
        ext = mimetypes.guess_extension(content_type) or ".dat"
        with Path(dirname, f"{filename}{ext}").open("wb") as f:
            f.write(self.content)


@dataclass
class Req:
    url: str
    base: str = ""
    fail_ok: bool = False
    ok_errors: Collection[int] = ()
    reason: str = ""

    async def get(self) -> Resp | None:
        if self.base:
            url = urllib.parse.urljoin(self.base, self.url)
        else:
            url = self.url
        if ":" not in url:
            url = f"https://{url}"
        async with aiohttp.ClientSession() as session:
            headers = {
                "User-Agent": "nedbat's human.json crawler",
            }
            async with session.get(url, timeout=10, headers=headers) as aresp:
                if aresp.status != 200 and self.fail_ok:
                    return None
                if aresp.status in self.ok_errors:
                    return None
                aresp.raise_for_status()
                return Resp(aresp, await aresp.content.read())

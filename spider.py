# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "aiohttp",
#   "beautifulsoup4",
# ]
# ///

import asyncio
import json
import mimetypes
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

import aiohttp
from bs4 import BeautifulSoup


people = set()
human_jsons = {}


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
        filename = re.sub(r"[^\w]", "_", self.url.partition("://")[-1])
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
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10, allow_redirects=True) as aresp:
                if aresp.status != 200 and self.fail_ok:
                    return None
                if aresp.status in self.ok_errors:
                    return None
                aresp.raise_for_status()
                return Resp(aresp, await aresp.content.read())


async def get_human_json(url: str) -> dict | None:
    # fail_ok=True because bots might be forbidden
    page_resp = await Req(url, reason="main page", fail_ok=True).get()
    if page_resp is not None:
        soup = page_resp.soup()
        for item in soup.find_all("meta", {"name": "author", "content": True}):
            if author := item["content"]:
                print(f"{url} author: {author!r}")
                people.add(author)

        for item in soup.find_all("script", {"type": "application/ld+json"}):
            jsonld_str = item.string
            jsonld = json.loads(jsonld_str)
            at_type = jsonld.get("@type")
            if at_type:
                msg = f"{url} has {len(jsonld_str)} chars of {at_type!r} JSON-LD"
                if at_type == "Person":
                    name = jsonld.get("name", "")
                    if name:
                        msg += f", name is {name!r}"
                        people.add(name)
                author = jsonld.get("author", {}).get("name", "")
                if author:
                    msg += f", with author: {author!r}"
                    people.add(author)
                print(msg)
        url = page_resp.url
    else:
        soup = None

    guessed = False
    hjurl = ""
    if soup is not None:
        for item in soup.find_all("link", {"rel": "human-json", "href": True}):
            hjurl = item["href"]
            print(f"{url} points to {hjurl}")
            break
    if not hjurl:
        hjurl = "/human.json"
        guessed = True

    req = Req(hjurl, base=url, reason="human.json")
    if guessed:
        req.ok_errors = {403, 404, 406, 410}
    if (resp := await req.get()) is None:
        return None

    resp.save(dirname="data")
    if b"<html" in resp.content:
        if not guessed:
            error(f"{hjurl} served HTML")
        return None

    hj = resp.json()
    human_jsons[resp.url] = len(hj.get("vouches", []))
    return hj


def error(msg):
    print(f"** Error {msg}")
    print(f"** Error {msg}", file=sys.stderr)


async def worker(url_queue, urls_done):
    while True:
        url = await url_queue.get()

        hj = None
        try:
            hj = await get_human_json(url)
        except Exception as e:
            error(f"getting human.json from {url}: {e}")

        if hj is not None:
            try:
                print(f"Got {len(hj['vouches'])} from {url}")
                for vouch in hj["vouches"]:
                    vurl = vouch["url"].rstrip("/")
                    if vurl in urls_done:
                        urls_done[vurl].append(url)
                    else:
                        urls_done[vurl] = [url]
                        await url_queue.put(vurl)
            except Exception as e:
                error(f"reading human.json: {e}")

        url_queue.task_done()


async def main(start_url: str, n_workers: int):
    url_queue = asyncio.Queue()
    await url_queue.put(start_url)
    urls_done = {start_url: []}

    workers = [
        asyncio.create_task(worker(url_queue, urls_done)) for _ in range(n_workers)
    ]
    await url_queue.join()
    for w in workers:
        w.cancel()

    print(f"\n\nFound {len(urls_done)} urls:")
    print("\n".join(str(x) for x in sorted(urls_done.items())))

    print(f"\nFound {len(people)} people:")
    print("\n".join(sorted(people)))

    print(f"\nFound {len(human_jsons)} human.json files:")
    print("\n".join(f"{n:4d}: {u}" for u, n in sorted(human_jsons.items())))
    print(f"{sum(human_jsons.values()):4d}  total")


if __name__ == "__main__":
    asyncio.run(main("https://sethmlarson.dev", 20))

# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "aiohttp",
#   "beautifulsoup4",
# ]
# ///

import asyncio
import collections
import json
import mimetypes
import re
import sys
import traceback
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

import aiohttp
from bs4 import BeautifulSoup


human_jsons = {}


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


@dataclass
class Site:
    vouchers: list[str]
    author: str = ""


sites: dict[str, Site] = {}


people = collections.defaultdict(list)


def extract_facts_from_jsonld(url: str, jsonld: dict) -> None:
    if (at_type := jsonld.get("@type")):
        if at_type == "Person":
            name = jsonld.get("name", "")
            if name:
                people[name].append(f"Person on {url}")
        author = jsonld.get("author", {})
        if isinstance(author, dict):
            author = author.get("name", "")
        if isinstance(author, str) and author:
            people[author].append(f"{at_type} author on {url}")
    if (graph := jsonld.get("@graph")):
        for subld in graph:
            extract_facts_from_jsonld(url, subld)


async def get_human_json(url: str) -> dict | None:
    # fail_ok=True because bots might be forbidden
    page_resp = await Req(url, reason="main page", fail_ok=True).get()
    if page_resp is not None:
        soup = page_resp.soup()
        for item in soup.find_all("meta", {"name": "author", "content": True}):
            if author := item["content"]:
                sites[url].author = author
                people[author].append(f"Author of {url}")

        for i, item in enumerate(
            soup.find_all("script", {"type": "application/ld+json"})
        ):
            jsonld_str = item.string
            jsonld = json.loads(jsonld_str)
            with Path("data", filename_for_url(url) + f"_ldjson{i}.json").open("w") as f:
                json.dump(jsonld, f, indent=4)
            extract_facts_from_jsonld(url, jsonld)

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


async def worker(url_queue):
    while True:
        url = await url_queue.get()

        hj = None
        try:
            hj = await get_human_json(url)
        except Exception as e:
            error(f"getting human.json from {url}: {e.__class__.__name__}: {e}")
            # if "some erroring url" in url:
            #     print(traceback.format_exc(), file=sys.stderr)

        if hj is not None:
            try:
                print(f"Got {len(hj['vouches'])} from {url}")
                for vouch in hj["vouches"]:
                    vurl = vouch["url"].rstrip("/")
                    if vurl in sites:
                        sites[vurl].vouchers.append(url)
                    else:
                        sites[vurl] = Site(vouchers=[url])
                        await url_queue.put(vurl)
            except Exception as e:
                error(f"reading human.json: {e}")

        url_queue.task_done()


async def main(start_url: str, n_workers: int):
    url_queue = asyncio.Queue()

    sites[start_url] = Site(vouchers=[])
    await url_queue.put(start_url)

    workers = [asyncio.create_task(worker(url_queue)) for _ in range(n_workers)]
    await url_queue.join()
    for w in workers:
        w.cancel()

    print(f"\n\nFound {len(sites)} sites:")
    for url, site in sorted(sites.items()):
        print(url)
        print(f"    {len(site.vouchers)} vouchers")
        if site.author:
            print(f"    Author: {site.author}")

    print(f"\nFound {len(people)} people:")
    for name, person in sorted(people.items()):
        print(name)
        for fact in sorted(set(person)):
            print(f"    {fact}")

    print(f"\nFound {len(human_jsons)} human.json files:")
    print("\n".join(f"{n:4d}: {u}" for u, n in sorted(human_jsons.items())))
    print(f"{sum(human_jsons.values()):4d}  total")


if __name__ == "__main__":
    asyncio.run(main("https://nedbatchelder.com", 20))

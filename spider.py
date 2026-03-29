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
import sys
from pathlib import Path
from typing import Iterator

from myhttp import fix_url, slug_for_url, Req

human_jsons = {}


class Site:
    def __init__(self, url: str) -> None:
        self.url = url
        self.vouchers: list[str] = []
        self.author = ""

    def __str__(self) -> str:
        return self.url.rstrip("/")

    def __lt__(self, other: "Site") -> bool:
        return self.url < other.url


class Sites:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Site] = asyncio.Queue()
        self.all: list[Site] = []
        self.by_url: dict[str, Site] = {}

    async def for_url(self, url: str) -> Site:
        site = self.by_url.get(url)
        if site is None:
            site = Site(url)
            await self.queue.put(site)
            self.all.append(site)
            self.by_url[url] = site
        return site

    def __len__(self) -> int:
        return len(self.all)

    def __iter__(self) -> Iterator[Site]:
        return iter(self.all)


people = collections.defaultdict(list)


def extract_facts_from_jsonld(site: Site, jsonld: dict) -> None:
    if at_type := jsonld.get("@type"):
        if at_type == "Person":
            name = jsonld.get("name", "")
            if name:
                people[name].append(f"Person on {site}")
        author = jsonld.get("author", {})
        if isinstance(author, dict):
            author = author.get("name", "")
        if isinstance(author, str) and author:
            people[author].append(f"{at_type} author on {site}")
    if graph := jsonld.get("@graph"):
        for subld in graph:
            extract_facts_from_jsonld(site, subld)


async def get_site_data(sites: Sites, site: Site) -> None:
    # fail_ok=True because bots might be forbidden
    page_resp = await Req(site.url, reason="main page", fail_ok=True).get()
    if page_resp is not None:
        soup = page_resp.soup()
        for item in soup.find_all("meta", {"name": "author", "content": True}):
            if author := item["content"]:
                site.author = author
                people[author].append(f"Author of {site}")

        for i, item in enumerate(
            soup.find_all("script", {"type": "application/ld+json"})
        ):
            jsonld_str = item.string
            filename = slug_for_url(site.url) + f"_ldjson{i}.json"
            with Path("data", filename).open("w") as f:
                f.write(jsonld_str)
            try:
                jsonld = json.loads(jsonld_str)
            except Exception as e:
                error(f"parsing jsonld from {site}: {e.__class__.__name__}: {e}")
            else:
                extract_facts_from_jsonld(site, jsonld)

        site.url = page_resp.url
        sites.by_url[site.url] = site
    else:
        soup = None

    guessed = False
    hjurl = ""
    if soup is not None:
        for item in soup.find_all("link", {"rel": "human-json", "href": True}):
            hjurl = item["href"]
            print(f"{site} points to {hjurl}")
            break
    if not hjurl:
        hjurl = "/human.json"
        guessed = True

    req = Req(hjurl, base=site.url, reason="human.json")
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
    human_jsons[site.url] = len(hj.get("vouches", []))

    try:
        print(f"Got {len(hj['vouches'])} from {site}")
        for vouch in hj["vouches"]:
            vurl = fix_url(vouch["url"])
            vsite = await sites.for_url(vurl)
            vsite.vouchers.append(site.url)
    except Exception as e:
        error(f"reading human.json: {e}")


def error(msg):
    print(f"** Error {msg}")
    print(f"** Error {msg}", file=sys.stderr)


async def worker(sites: Sites):
    while True:
        site = await sites.queue.get()

        try:
            await get_site_data(sites, site)
        except Exception as e:
            error(f"processing {site}: {e.__class__.__name__}: {e}")
            # if 1:#"some erroring url" in url:
            #     import traceback
            #     print(traceback.format_exc(), file=sys.stderr)

        sites.queue.task_done()


async def main(sites: Sites, start_url: str, n_workers: int):
    await sites.for_url(start_url)
    workers = [asyncio.create_task(worker(sites)) for _ in range(n_workers)]
    await sites.queue.join()
    for w in workers:
        w.cancel()

    print(f"\n\nFound {len(sites)} sites:")
    for site in sorted(sites):
        print(site.url)
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
    sites = Sites()
    asyncio.run(main(sites, "https://nedbatchelder.com", 20))

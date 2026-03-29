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

from myhttp import fix_url, slug_for_url, Req

human_jsons = {}


class Site:
    def __init__(
        self,
        url: str,
    ) -> None:
        self.url = url
        self.vouchers = []
        self.author = ""

    def __str__(self) -> str:
        return self.url


sites: dict[str, Site] = {}


people = collections.defaultdict(list)


def extract_facts_from_jsonld(url: str, jsonld: dict) -> None:
    if at_type := jsonld.get("@type"):
        if at_type == "Person":
            name = jsonld.get("name", "")
            if name:
                people[name].append(f"Person on {url}")
        author = jsonld.get("author", {})
        if isinstance(author, dict):
            author = author.get("name", "")
        if isinstance(author, str) and author:
            people[author].append(f"{at_type} author on {url}")
    if graph := jsonld.get("@graph"):
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
            with Path("data", slug_for_url(url) + f"_ldjson{i}.json").open("w") as f:
                f.write(jsonld_str)
            try:
                jsonld = json.loads(jsonld_str)
            except Exception as e:
                error(f"parsing jsonld from {url}: {e.__class__.__name__}: {e}")
            else:
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


async def worker(site_queue: asyncio.Queue[Site]):
    while True:
        site = await site_queue.get()

        hj = None
        try:
            hj = await get_human_json(site.url)
        except Exception as e:
            error(f"getting human.json from {site}: {e.__class__.__name__}: {e}")
            # if "some erroring url" in url:
            #     import traceback
            #     print(traceback.format_exc(), file=sys.stderr)

        if hj is not None:
            try:
                print(f"Got {len(hj['vouches'])} from {site}")
                for vouch in hj["vouches"]:
                    vurl = fix_url(vouch["url"])
                    if vurl in sites:
                        sites[vurl].vouchers.append(site.url)
                    else:
                        sites[vurl] = vsite = Site(vurl)
                        vsite.vouchers.append(site.url)
                        await site_queue.put(vsite)
            except Exception as e:
                error(f"reading human.json: {e}")

        site_queue.task_done()


async def main(start_url: str, n_workers: int):
    site_queue: asyncio.Queue[Site] = asyncio.Queue()

    sites[start_url] = site = Site(start_url)
    await site_queue.put(site)

    workers = [asyncio.create_task(worker(site_queue)) for _ in range(n_workers)]
    await site_queue.join()
    for w in workers:
        w.cancel()

    print(f"\n\nFound {len(sites)} sites:")
    for url, site in sorted(sites.items()):
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
    asyncio.run(main("https://nedbatchelder.com", 20))

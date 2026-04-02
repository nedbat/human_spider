# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "aiohttp",
#   "beautifulsoup4",
#   "demjson3",
#   "listparser",
# ]
# ///

import asyncio
import collections
import sys
import urllib.parse
from pathlib import Path
from typing import Iterable, Iterator

import demjson3
import listparser

from myhttp import fix_url, root_for_url, slug_for_url, Req, Resp
from myjson import fix_json
from parse_wander import parse_wander


class Site:
    def __init__(self, url: str) -> None:
        self.url = url
        self.vouchers: set[str] = set()
        self.author = ""
        self.human_json: str | None = None
        self.robots_txt: bool = False
        self.wander_js: bool = False
        self.fediverse_creator: str | None = None
        self.rss: set[str] = set()
        self.blogroll: set[str] = set()

    def __str__(self) -> str:
        return self.url.rstrip("/")

    def __lt__(self, other: "Site") -> bool:
        return self.url < other.url

    def print(self) -> None:
        print(self.url)
        if self.vouchers:
            print(f"    {len(self.vouchers)} human vouchers")
        if self.author:
            print(f"    Author: {self.author}")
        if self.fediverse_creator:
            print(f"    fediverse creator: {self.fediverse_creator}")
        if self.human_json:
            print(f"    human.json: {self.human_json}")
        if self.robots_txt:
            print("    has robots.txt")
        if self.wander_js:
            print("    has wander.js")
        for rss in self.rss:
            print(f"    rss: {rss}")
        for roll in self.blogroll:
            print(f"    blogroll: {roll}")


class Sites:
    def __init__(self) -> None:
        self.all: list[Site] = []
        self.by_url: dict[str, Site] = {}

    def for_url(self, url: str) -> tuple[Site, bool]:
        """Get a site for a URL, and whether it is new or not."""
        site = self.by_url.get(url)
        if site is None:
            site = Site(url)
            self.all.append(site)
            self.by_url[url] = site
            is_new = True
        else:
            is_new = False
        return site, is_new

    def __len__(self) -> int:
        return len(self.all)

    def __iter__(self) -> Iterator[Site]:
        return iter(self.all)


class Crawler:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Site] = asyncio.Queue()
        self.sites = Sites()
        self.people: dict[str, list[str]] = collections.defaultdict(list)
        self.human_jsons: dict[str, int] = {}
        self.wander_consoles: set[str] = set()
        self.wander_pages: set[str] = set()

    async def site_for_url(self, url: str) -> Site:
        site, is_new = self.sites.for_url(url)
        if is_new:
            await self.queue.put(site)
        return site

    def extract_facts_from_jsonld(self, site: Site, jsonld: dict | list) -> None:
        match jsonld:
            case list():
                for jld in jsonld:
                    self.extract_facts_from_jsonld(site, jld)

            case dict():
                if at_type := jsonld.get("@type"):
                    if at_type == "Person":
                        name = jsonld.get("name", "")
                        if name:
                            self.people[name].append(f"Person on {site}")
                    author = jsonld.get("author", {})
                    if isinstance(author, dict):
                        author = author.get("name", "")
                    if isinstance(author, str) and author:
                        self.people[author].append(f"{at_type} author on {site}")
                if graph := jsonld.get("@graph"):
                    for subld in graph:
                        self.extract_facts_from_jsonld(site, subld)

    async def read_robots_txt(self, site: Site) -> None:
        req = Req(
            "/robots.txt",
            base=site.url,
            fail_ok=True,
            ok_content_types=["text/plain"],
        )
        resp = await req.get()
        if resp is not None:
            site.robots_txt = True
            resp.save(dirname="data")

    def read_meta_tags(self, site: Site, resp: Resp) -> None:
        for item in resp.soup().find_all("meta", {"name": "author", "content": True}):
            if author := item["content"]:
                site.author = author
                self.people[author].append(f"Author of {site}")
        for item in resp.soup().find_all(
            "meta", {"name": "fediverse:creator", "content": True}
        ):
            if creator := item["content"]:
                site.fediverse_creator = creator

    def read_rss_links(self, site: Site, resp: Resp) -> None:
        for item in resp.soup().find_all(
            "link", {"rel": "alternate", "type": "application/rss+xml", "href": True}
        ):
            if href := item["href"]:
                if "/comments/" not in href:
                    href = urllib.parse.urljoin(site.url, href)
                    site.rss.add(href)

    async def read_blogrolls(self, site: Site, resp: Resp) -> None:
        for item in resp.soup().find_all(
            "link", {"rel": "blogroll", "type": "text/xml", "href": True}
        ):
            if href := item["href"]:
                req = Req(href, base=site.url, fail_ok=True)
                site.blogroll.add(req.url)
                roll_resp = await req.get()
                if roll_resp:
                    opml = listparser.parse(roll_resp.text())
                    for feed in opml.get("feeds", ()):
                        url = feed.get("url")
                        if url is not None:
                            await self.site_for_url(root_for_url(url))

    def read_jsonld(self, site: Site, resp: Resp) -> None:
        for i, item in enumerate(
            resp.soup().find_all("script", {"type": "application/ld+json"})
        ):
            jsonld_str = item.string
            filename = slug_for_url(site.url) + f"_ldjson_{i:03d}.json"
            with Path("data", filename).open("w") as f:
                f.write(jsonld_str)
            try:
                jsonld = demjson3.decode(fix_json(jsonld_str))
            except Exception as e:
                error(f"parsing jsonld from {site}: {e.__class__.__name__}: {e}")
            else:
                self.extract_facts_from_jsonld(site, jsonld)

    async def read_wanderjs(self, site: Site) -> None:
        for relative in ["/wander/wander.js", "/wander.js"]:
            req = Req(
                relative,
                base=site.url,
                fail_ok=True,
                ok_content_types=["text/javascript", "application/javascript"],
            )
            resp = await req.get()
            if resp is not None:
                break
        if resp is not None:
            site.wander_js = True
            resp.save(dirname="data")
            wander_data = parse_wander(resp.text())
            for console in wander_data["consoles"]:
                console = fix_url(console)
                self.wander_consoles.add(console)
                await self.site_for_url(root_for_url(console))
            for page in wander_data["pages"]:
                page = fix_url(page)
                self.wander_pages.add(page)
                await self.site_for_url(root_for_url(page))

    async def get_site_data(self, site: Site) -> None:
        await self.read_robots_txt(site)

        # fail_ok=True because bots might be forbidden
        page_resp = await Req(site.url, fail_ok=True).get()
        if page_resp is not None:
            self.read_meta_tags(site, page_resp)
            self.read_rss_links(site, page_resp)
            await self.read_blogrolls(site, page_resp)
            self.read_jsonld(site, page_resp)
            site.url = page_resp.url
            self.sites.by_url[site.url] = site

        await self.read_wanderjs(site)

        guessed = False
        hjurl = ""
        if page_resp is not None:
            for item in page_resp.soup().find_all(
                "link", {"rel": "human-json", "href": True}
            ):
                hjurl = item["href"]
                break
        if not hjurl:
            hjurl = "/human.json"
            guessed = True

        req = Req(hjurl, base=site.url)
        if guessed:
            req.ok_errors = {403, 404, 406, 410}
        if (resp := await req.get()) is None:
            return None

        resp.save(dirname="data")
        if b"<html" in resp.content:
            if not guessed:
                error(f"{hjurl} served HTML")
            return None

        site.human_json = hjurl
        hj = resp.json()
        self.human_jsons[site.url] = len(hj.get("vouches", []))

        try:
            for vouch in hj.get("vouches", []):
                vurl = fix_url(vouch["url"])
                vsite = await self.site_for_url(vurl)
                vsite.vouchers.add(site.url)
        except Exception as e:
            error(f"reading human.json from {resp.url}: {e}")

    async def worker(self) -> None:
        while True:
            site = await self.queue.get()

            try:
                await self.get_site_data(site)
            except Exception as e:
                error(f"processing {site}: {e.__class__.__name__}: {e}")
                # if 1:#"some erroring url" in url:
                #     import traceback
                #     print(traceback.format_exc(), file=sys.stderr)

            self.queue.task_done()

    async def reporter(self) -> None:
        while True:
            if self.queue.qsize():
                print(
                    f"### {self.queue.qsize()} sites remaining, {len(self.sites)} total",
                    file=sys.stderr,
                )
            await asyncio.sleep(5)

    async def main(self, start_urls: Iterable[str], n_workers: int) -> None:
        for url in start_urls:
            await self.site_for_url(url)

        # req = Req("https://indieblog.page/export")
        # resp = await req.get()
        # if resp is not None:
        #     for feed in resp.json():
        #         await self.site_for_url(feed["homepage"])

        workers = []
        workers += [asyncio.create_task(self.reporter())]
        workers += [asyncio.create_task(self.worker()) for _ in range(n_workers)]
        await self.queue.join()
        for w in workers:
            w.cancel()

        print(f"\n\nFound {len(self.sites)} sites:")
        for site in sorted(self.sites):
            site.print()

        print(f"\nFound {len(self.people)} people:")
        for name, person in sorted(self.people.items()):
            print(name)
            for fact in sorted(set(person)):
                print(f"    {fact}")

        print(f"\nFound {len(self.human_jsons)} human.json files:")
        print("\n".join(f"{n:4d}: {u}" for u, n in sorted(self.human_jsons.items())))
        print(f"{sum(self.human_jsons.values()):4d}  total")

        print(f"\nFound {len(self.wander_consoles)} wander consoles:")
        print("\n".join(sorted(self.wander_consoles)))
        print(f"\nFound {len(self.wander_pages)} wander pages:")
        print("\n".join(sorted(self.wander_pages)))

        creators = {s.fediverse_creator for s in self.sites}
        print(f"\nFound {len(creators)} fediverse creators")

        rsses = sum(len(s.rss) for s in self.sites)
        print(f"\nFound {rsses} rss feeds")

        rolls = sum(len(s.blogroll) for s in self.sites)
        print(f"\nFound {rolls} blogrolls")


def error(msg: str) -> None:
    print(f"** Error {msg}")
    print(f"** Error {msg}", file=sys.stderr)


if __name__ == "__main__":
    urls = [
        "https://nedbatchelder.com",
        "https://susam.net",
    ]
    asyncio.run(Crawler().main(urls, 20))

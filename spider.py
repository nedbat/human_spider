import asyncio
import collections
import json
import sys
import time
import urllib.parse

from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Callable, Iterator

import demjson3
import listparser

from myhttp import fix_url, root_for_url, slug_for_url, Req, Resp, ONE_PER, TryLater
from myjson import fix_json
from logs import fetch_log
from parse_wander import parse_wander
from report import error, print_both


class Site:
    def __init__(self, url: str) -> None:
        self.url = url
        self.urls: set[str] = {url}
        self.vouchers: set[str] = set()
        self.author = ""
        self.human_json: str | None = None
        self.wander_js: bool = False
        self.fediverse_creator: str | None = None
        self.feeds: set[str] = set()
        self.blogroll: set[str] = set()
        self.relme: set[str] = set()
        self.webmention: set[str] = set()

    def __str__(self) -> str:
        return self.url.rstrip("/")

    def __repr__(self) -> str:
        return f"Site({str(self)!r})"

    def __lt__(self, other: "Site") -> bool:
        return self.url < other.url

    def print(self) -> None:
        print(self.url)
        if len(self.urls) > 1:
            print("    urls:")
            for u in sorted(self.urls):
                print(f"        {u}")
        if self.vouchers:
            print(f"    {len(self.vouchers)} human vouchers")
            for v in self.vouchers:
                print(f"        {v}")
        if self.author:
            print(f"    Author: {self.author}")
        if self.fediverse_creator:
            print(f"    fediverse creator: {self.fediverse_creator}")
        if self.human_json:
            print(f"    human.json: {self.human_json}")
        if self.wander_js:
            print("    has wander.js")
        for feed in self.feeds:
            print(f"    feed: {feed}")
        for roll in self.blogroll:
            print(f"    blogroll: {roll}")
        for me in self.relme:
            print(f"    rel=me: {me}")
        for w in self.webmention:
            print(f"    webmention: {w}")


class Sites:
    def __init__(self) -> None:
        self.all: list[Site] = []
        self.by_url: dict[str, Site] = {}

    def for_url(self, url: str) -> tuple[Site, bool]:
        """Get a site for a URL, and whether it is new or not."""
        site = self.by_url.get(url)
        if site is None:
            site = Site(url)
            print(f"URL {url} is site {nice_id(site)}")
            self.all.append(site)
            self.by_url[url] = site
            is_new = True
        else:
            is_new = False
        return site, is_new

    def rename_site(self, site: Site, new_url: str) -> bool:
        """A site actually has a different name.

        Returns True if processing should continue on this site,
        or False if there's another site already for it.
        """
        new_site = self.by_url.get(new_url)
        if new_site is not None:
            new_site.urls.add(site.url)
            return False

        site.url = new_url
        site.urls.add(new_url)
        self.by_url[new_url] = site
        return True

    def __len__(self) -> int:
        return len(self.all)

    def __iter__(self) -> Iterator[Site]:
        return iter(self.all)


type WorkFn = Callable[..., Coroutine[Any, Any, None]]


class WorkItem:
    def __init__(self, fn: WorkFn, **kwargs: Any) -> None:
        self.fn = fn
        self.kwargs = kwargs
        self.retries = 0
        self.total_delay = 0.0

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.kwargs.items())
        return f"{self.fn.__name__}({args})"


def nice_seconds(s: int) -> str:
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    w, d = divmod(d, 7)
    parts = zip([w, d, h, m, s], "wdhms")
    return "".join(f"{n}{u}" for n, u in parts if n) or "0s"


def nice_id(obj: Any) -> str:
    import string

    alphabet = string.ascii_letters
    nid = ""
    i = id(obj)
    while i:
        i, digit = divmod(i, len(alphabet))
        nid += alphabet[digit]
    return nid


class Crawler:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        self.waiting: set[asyncio.Task] = set()
        self.start = 0.0

        self.sites = Sites()
        self.people: dict[str, list[str]] = collections.defaultdict(list)
        self.human_jsons: dict[str, int] = {}
        self.wander_consoles: set[str] = set()
        self.wander_pages: set[str] = set()

    # Generic async working

    async def worker(self) -> None:
        while True:
            work = await self.queue.get()
            retrying = False
            try:
                await work.fn(**work.kwargs)
                if work.retries > 10:
                    print_both(
                        f"** Success after {work.retries}, {work.total_delay:.3f}s: {work}"
                    )
            except TryLater as tle:
                if work.retries >= 20:
                    error(f"retried {work} {work.retries} times, last {tle.delay:.3f}s")
                    fetch_log.info("Quit  %s", work)
                else:
                    delay = tle.delay
                    work.retries += 1
                    work.total_delay += delay
                    if work.retries > 10:
                        print_both(
                            f"** Retrying {work}: {work.retries}, {delay:.3f}s: {tle.reason}"
                        )
                    self.do_later(work, delay, mark_done=True)
                    retrying = True
                    continue
            except Exception as e:
                error(f"in {work}", e)
                # if 1:#"some erroring url" in url:
                #     import traceback
                #     print(traceback.format_exc(), file=sys.stderr)
            finally:
                if not retrying:
                    self.queue.task_done()

    async def queue_work(self, fn: WorkFn, *, delay: float = 0, **kwargs: Any) -> None:
        work = WorkItem(fn, **kwargs)
        if delay:
            self.do_later(work, delay)
        else:
            await self.queue.put(work)

    def do_later(self, work: WorkItem, delay: float, mark_done: bool = False) -> None:
        task = asyncio.create_task(self.wait_then_queue(work, delay))
        self.waiting.add(task)

        def done_callback(task):
            self.waiting.discard(task)
            if mark_done:
                self.queue.task_done()

        task.add_done_callback(done_callback)

    async def wait_then_queue(self, work: WorkItem, after: float) -> None:
        await asyncio.sleep(after)
        await self.queue.put(work)

    async def reporter(self) -> None:
        while True:
            if self.queue.qsize() or self.waiting:
                print(
                    "### "
                    + f"{nice_seconds(int(time.time() - self.start))}: "
                    + f"{self.queue.qsize()} to do, "
                    + f"{len(self.waiting)} waiting, "
                    + f"{len(self.sites)} sites",
                    file=sys.stderr,
                )
            await asyncio.sleep(5)

    async def run_workers(self, n_workers: int) -> None:
        self.start = time.time()
        workers = []
        workers += [asyncio.create_task(self.reporter())]
        workers += [asyncio.create_task(self.worker()) for _ in range(n_workers)]
        while True:
            await self.queue.join()
            if not self.waiting:
                break
            await asyncio.wait(self.waiting)
        for w in workers:
            w.cancel()

    # Specifics of blog scraping.

    async def site_for_url(self, url: str) -> Site:
        site, is_new = self.sites.for_url(root_for_url(url))
        if is_new:
            await self.queue_work(self.get_site_data, site=site)
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

    def read_meta_tags(self, site: Site, resp: Resp) -> None:
        for item in resp.soup().find_all("meta", {"name": "author", "content": True}):
            if author := item["content"].strip():
                site.author = author
                self.people[author].append(f"Author of {site}")
        for item in resp.soup().find_all(
            "meta", {"name": "fediverse:creator", "content": True}
        ):
            if creator := item["content"].strip():
                site.fediverse_creator = creator

    def read_feed_links(self, site: Site, resp: Resp) -> None:
        mimetypes = [
            "application/atom+xml",
            "application/rss+xml",
        ]
        for mimetype in mimetypes:
            for item in resp.soup().find_all(
                "link", {"rel": "alternate", "type": mimetype, "href": True}
            ):
                if href := item["href"].strip():
                    if "/comments/" not in href:
                        href = urllib.parse.urljoin(site.url, href)
                        site.feeds.add(href)

    async def read_blogrolls(self, site: Site, resp: Resp) -> None:
        """Read the blogrolls from a site."""
        for item in resp.soup().find_all(
            "link", {"rel": "blogroll", "type": "text/xml", "href": True}
        ):
            if href := item["href"].strip():
                # These are most likely same-site URLs, so wait a bit.
                await self.queue_work(
                    self.read_one_blogroll, delay=1.1 * ONE_PER, site=site, href=href
                )

    async def read_one_blogroll(self, site: Site, href: str) -> None:
        req = Req(href, base=site.url, fail_ok=True)
        site.blogroll.add(req.url)
        roll_resp = await req.get()
        if roll_resp:
            opml = listparser.parse(roll_resp.text())
            for feed in opml.get("feeds", ()):
                url = feed.get("url")
                if url is not None:
                    await self.site_for_url(url)

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
                error(f"parsing jsonld from {site}", e)
            else:
                self.extract_facts_from_jsonld(site, jsonld)

    def read_relme(self, site: Site, resp: Resp) -> None:
        # <a href="https://bsky.app/profile/nedbat.com" rel="me">bluesky</a>
        for tag in resp.soup().find_all("a", {"rel": "me", "href": True}):
            if href := tag["href"].strip():
                site.relme.add(href)

    def read_webmention(self, site: Site, resp: Resp) -> None:
        # <link rel="webmention" href="https://webmention.io/nedbatchelder.com/webmention">
        for link in resp.soup().find_all("link", {"rel": "webmention", "href": True}):
            if href := link["href"].strip():
                site.webmention.add(href)

    async def read_wanderjs(self, console_url: str) -> None:
        site = await self.site_for_url(console_url)
        if site.wander_js:
            return
        req = Req(
            "wander.js",
            base=console_url.rstrip("/") + "/",
            fail_ok=True,
            ok_content_types=["text/javascript", "application/javascript"],
        )
        resp = await req.get()
        if resp is not None:
            site.wander_js = True
            wander_data = parse_wander(resp.text())
            for console in wander_data["consoles"]:
                console = fix_url(console)
                self.wander_consoles.add(console)
                await self.queue_work(self.read_wanderjs, console_url=console)
            for page in wander_data["pages"]:
                page = fix_url(page)
                self.wander_pages.add(page)
                await self.site_for_url(page)

    async def get_site_data(self, site: Site) -> None:
        # fail_ok=True because bots might be forbidden
        page_resp = await Req(site.url, fail_ok=True).get()
        if page_resp is None:
            return

        new_url = root_for_url(page_resp.url)
        if new_url != site.url:
            print(f"Changing {site.url} to {new_url} for site {nice_id(site)}")
            if not self.sites.rename_site(site, new_url):
                return

        self.read_meta_tags(site, page_resp)
        self.read_feed_links(site, page_resp)
        self.read_jsonld(site, page_resp)
        self.read_relme(site, page_resp)
        self.read_webmention(site, page_resp)
        await self.read_blogrolls(site, page_resp)

        hjurl = ""
        if page_resp is not None:
            for item in page_resp.soup().find_all(
                "link", {"rel": "human-json", "href": True}
            ):
                hjurl = item["href"]
                break
        if hjurl:
            await self.queue_work(
                self.read_human_json,
                delay=2.25 * ONE_PER,
                site=site,
                hjurl=hjurl,
            )

    async def read_human_json(self, site: Site, hjurl: str) -> None:
        req = Req(hjurl, base=site.url)
        if (resp := await req.get()) is None:
            error(f"human.json: {hjurl} returned error")
            return None

        if b"<html" in resp.content:
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
            error(f"reading human.json from {resp.url}", e)

    # One-off blog sources.

    async def load_indieblog(self) -> None:
        resp = await Req("https://indieblog.page/export").get()
        if resp is not None:
            for feed in resp.json():
                await self.site_for_url(feed["homepage"])

    async def load_blogroll_org(self) -> None:
        resp = await Req("https://blogroll.org/").get()
        if resp is not None:
            for entry in resp.soup().find_all(
                "a", {"class": "entry-main", "href": True}
            ):
                await self.site_for_url(entry["href"])

    async def load_a_website_is_a_room(self) -> None:
        url = "https://docs.google.com/spreadsheets/d/1KjiqdG8EmGd8oPVSNwRcFedNQPG1A45ycoRpYvGzKIg/gviz/tq?gid=0&tq=select%20B%20order%20by%20D%20desc&tqx=responseHandler:gimmedata"
        resp = await Req(url).get()
        if resp is not None:
            text = resp.text()
            json_text = text[text.find("{") : text.rfind("}") + 1]
            for row in json.loads(json_text)["table"]["rows"]:
                await self.site_for_url(row["c"][0]["v"])

    async def load_noai_webring(self) -> None:
        resp = await Req("https://baccyflap.com/noai/webring.json").get()
        if resp is not None:
            for site in resp.json():
                await self.site_for_url(site["url"])

    async def load_ooh_directory(self) -> None:
        for line in Path("ooh_directory.txt").open():
            await self.site_for_url(line.strip())

    # Main code

    def print_results(self) -> None:
        print(f"\n\nFound {len(self.sites)} sites ({len(self.sites.by_url)} by url):")
        for site in sorted(self.sites):
            site.print()

        human_sites = [s for s in self.sites if s.vouchers]
        print(f"\n\nFound {len(human_sites)} human sites:")
        for site in sorted(human_sites):
            site.print()

        human_feeds = [s for s in human_sites if s.feeds]
        print(f"\n\nFound {len(human_feeds)} human sites with feeds:")
        for site in sorted(human_feeds):
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

        feeds = sum(len(s.feeds) for s in self.sites)
        print(f"\nFound {feeds} feeds")

        rolls = sum(len(s.blogroll) for s in self.sites)
        print(f"\nFound {rolls} blogrolls")

    async def main(self, n_workers: int) -> None:
        await self.queue_work(
            self.get_site_data,
            site=await self.site_for_url("https://nedbatchelder.com"),
        )
        await self.queue_work(
            self.read_wanderjs,
            console_url="https://susam.net/wander",
        )
        await self.queue_work(self.load_indieblog)
        await self.queue_work(self.load_blogroll_org)
        await self.queue_work(self.load_a_website_is_a_room)
        await self.queue_work(self.load_noai_webring)
        await self.queue_work(self.load_ooh_directory)

        await self.run_workers(n_workers)
        self.print_results()


if __name__ == "__main__":
    asyncio.run(Crawler().main(40))

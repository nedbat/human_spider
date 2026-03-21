# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "beautifulsoup4",
#   "requests",
# ]
# ///

import json
import mimetypes
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

import requests
from bs4 import BeautifulSoup


START = "https://sethmlarson.dev"

people = set()
human_jsons = {}


@dataclass
class Resp:
    resp: Any

    def soup(self):
        return BeautifulSoup(self.resp.content, "html.parser")

    @property
    def url(self) -> str:
        return self.resp.url

    @property
    def content(self) -> bytes:
        return self.resp.content

    def json(self) -> Any:
        return self.resp.json()

    def save(self, *, dirname: str = "") -> None:
        filename = re.sub(r"[^\w]", "_", self.resp.url.partition("://")[-1])
        content_type = (
            self.resp.headers.get("content-type", "").partition(";")[0].strip()
        )
        ext = mimetypes.guess_extension(content_type) or ".dat"
        with Path(dirname, f"{filename}{ext}").open("wb") as f:
            f.write(self.resp.content)


@dataclass
class Req:
    url: str
    base: str = ""
    fail_ok: bool = False
    ok_errors: Collection[int] = ()
    reason: str = ""

    def get(self) -> Resp | None:
        if self.base:
            url = urllib.parse.urljoin(self.base, self.url)
        else:
            url = self.url
        resp = requests.get(url, timeout=10, allow_redirects=True)
        if resp.status_code != 200 and self.fail_ok:
            return None
        if resp.status_code in self.ok_errors:
            return None
        resp.raise_for_status()
        return Resp(resp)


def get_human_json(url: str) -> dict | None:
    # fail_ok=True because bots might be forbidden
    page_resp = Req(url, reason="main page", fail_ok=True).get()
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
    if (resp := req.get()) is None:
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


def main():
    domains_to_do = {START}
    domains_done = set()
    while domains_to_do:
        domain = domains_to_do.pop()
        domains_done.add(domain)
        try:
            hj = get_human_json(domain)
            if hj is None:
                continue
        except Exception as e:
            error(f"getting human.json from {domain}: {e}")
            continue

        try:
            print(f"Got {len(hj['vouches'])} from {domain}")
            for vouch in hj["vouches"]:
                url = vouch["url"].strip("/")
                if url not in domains_done:
                    domains_to_do.add(url)
        except Exception as e:
            error(f"reading human.json: {e}")

    print(f"\n\nFound {len(domains_done)} domains:")
    print("\n".join(sorted(domains_done)))

    print(f"\nFound {len(people)} people:")
    print("\n".join(sorted(people)))

    print(f"\nFound {len(human_jsons)} human.json files:")
    print("\n".join(f"{n:4d}: {u}" for u, n in sorted(human_jsons.items())))
    print(f"{sum(human_jsons.values()):4d}  total")


main()

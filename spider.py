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

import requests
from bs4 import BeautifulSoup


START = "https://sethmlarson.dev"

domains_done = set()
people = set()
human_jsons = {}


def get_human_json(domain: str) -> dict | None:
    resp = requests.get(domain, timeout=10)
    soup = BeautifulSoup(resp.content, "html.parser")
    for item in soup.find_all("meta", {"name": "author", "content": True}):
        if author := item["content"]:
            print(f"{domain} author: {author!r}")
            people.add(author)

    for item in soup.find_all("script", {"type": "application/ld+json"}):
        jsonld_str = item.string
        jsonld = json.loads(jsonld_str)
        at_type = jsonld.get("@type")
        if at_type:
            msg = f"{domain} has {len(jsonld_str)} chars of {at_type!r} JSON-LD"
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

    guessed = False
    for item in soup.find_all("link", {"rel": "human-json", "href": True}):
        hjurl = item["href"]
        print(f"{domain} points to {hjurl}")
        break
    else:
        hjurl = "/human.json"
        guessed = True

    hjurl = urllib.parse.urljoin(resp.url, hjurl)
    resp = requests.get(hjurl, timeout=10, allow_redirects=True)
    if resp.status_code in {403, 404, 406, 410} and guessed:
        return None
    resp.raise_for_status()

    filename = re.sub(r"[^\w]", "_", resp.url.partition("://")[-1])
    content_type = resp.headers.get("content-type", "application/json").partition(";")[0].strip()
    ext = mimetypes.guess_extension(content_type) or ".dat"
    with open(f"data/{filename}{ext}", "wb") as f:
        f.write(resp.content)
    if b"<html" in resp.content:
        if not guessed:
            error(f"{hjurl} served HTML")
        return None

    hj = resp.json()
    human_jsons[hjurl] = len(hj.get("vouches", []))
    return hj


def error(msg):
    print(f"** Error {msg}")
    print(f"** Error {msg}", file=sys.stderr)


def main():
    domains_to_do = {START}
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

import sqlite3
from urllib.parse import urlsplit

con = sqlite3.connect("/dwn/places.sqlite")
cur = con.cursor()
res = cur.execute("SELECT url FROM moz_places where visit_count > 2")
hosts = set(urlsplit(r[0]).hostname for r in res.fetchall())
hosts.remove(None)
print(len(hosts))

print("\n".join(sorted(hosts)))

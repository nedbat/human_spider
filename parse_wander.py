import json
import re
import sys
from pathlib import Path


TOKENS = r"""(?xm)
    (?P<name>\w+) |
    (?P<str>['"][^'"]+['"]) |
    (?P<punct>[{}[\]]) |
    (?P<comment>//.*$)
    """


def parse_wander(text: str) -> dict[str, list[str]]:
    data: dict[str, list[str]] | None = None
    items = None
    name = None
    for m in re.finditer(TOKENS, text):
        kind, value = next((k, v) for k, v in m.groupdict().items() if v is not None)
        match (kind, value):
            case "punct", "{":
                assert data is None, "parsing wander: unexpected {"
                data = {}

            case "name", id:
                name = id

            case "punct", "[":
                assert data is not None, "parsing wander: [ with no {"
                assert name is not None, "parsing wander: [ with no name"
                assert items is None, "parsing wander: [ inside list"
                items = data[name] = []

            case "str", text:
                assert items is not None, "parsing wander: string outside list"
                items.append(text[1:-1])

            case "punct", "]":
                assert items is not None, "parsing wander: ] outside list"
                items = None

    assert data is not None, "parsing wander: no data"
    return data


if __name__ == "__main__":
    text = Path(sys.argv[1]).read_text()
    data = parse_wander(text)
    print(json.dumps(data, indent=4))

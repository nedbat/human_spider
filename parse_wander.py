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


def parse_wander(text: str) -> dict:
    data: dict[str, list[str]] | None = None
    items = None
    name = None
    for m in re.finditer(TOKENS, text):
        kind, value = next((k, v) for k, v in m.groupdict().items() if v is not None)
        match (kind, value):
            case "punct", "{":
                assert data is None
                data = {}

            case "name", id:
                name = id

            case "punct", "[":
                assert data is not None
                assert name is not None
                assert items is None
                items = data[name] = []

            case "str", text:
                assert items is not None
                items.append(text[1:-1])

            case "punct", "]":
                assert items is not None
                items = None

    assert data is not None
    return data


if __name__ == "__main__":
    text = Path(sys.argv[1]).read_text()
    data = parse_wander(text)
    print(json.dumps(data, indent=4))

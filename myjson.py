import re


def fix_json(jstr: str) -> str:
    """Correct some JSON errors that Schema.org accepts.

    Strings containing newlines get joined together with \\n
    Scrub control characters. Tabs become spaces.

    Ex: https://validator.schema.org/#url=https%3A%2F%2Fforkingmad.blog

    """
    jstr = re.sub(r"[\x00-\x08\x0E-\x1F]", "", jstr)
    jstr = re.sub(r"[\x09\x0D]", " ", jstr)
    lines = jstr.splitlines(keepends=False)
    fixed = []
    partial = ""
    for line in lines:
        partial += line
        quotes = partial.count('"') - partial.count(r"\"")
        if quotes % 2 == 0:
            fixed.append(partial)
            partial = ""
        else:
            partial += r"\n"
    if partial:
        fixed.append(partial)
    return "\n".join(fixed)

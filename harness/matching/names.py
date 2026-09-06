import re
import unicodedata

_PREFIX = {"ny ": "new york ", "la ": "los angeles ", "sf ": "san francisco ", "nyc ": "new york "}
_ST = re.compile(r"\bst\.?(?=\s|$)")


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower()
    s = s.replace("'", "")
    s = s.replace("&", " and ")
    s = re.sub(r"\s+", " ", s).strip()
    for k, v in _PREFIX.items():
        if s.startswith(k):
            s = v + s[len(k):]
            break
    tokens = s.split(" ")
    if len(tokens) > 1:
        tokens = [tokens[0]] + [("state" if _ST.fullmatch(t) else t) for t in tokens[1:]]
    s = " ".join(tokens)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

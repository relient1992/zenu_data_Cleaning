"""Build index.html from index.src.html: embed the guides (sections), field-name hints and Zenu dictionary.
Re-run after editing a guide in reference/guides, then republish."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = r"C:\Users\Daryl\Desktop\Web 2\Zenu-Migration-Tool"
sys.path.insert(0, PROJECT)
from daris import general_flow  # noqa: E402
from daris.references import library  # noqa: E402

GUIDE_FILES = [
    (os.path.join(HERE, "GUIDE.md"), "DARVIS Guide"),
    (os.path.join(PROJECT, "reference", "guides", "Contact Cleaning - Name Splitting.md"), "Name Splitting guide"),
    (os.path.join(PROJECT, "reference", "guides", "Contact Cleaning - Phones and Emails.md"), "Phones and Emails guide"),
    (os.path.join(PROJECT, "reference", "guides", "General Field Names.md"), "General Field Names guide"),
]


def sections(path, source):
    text = open(path, encoding="utf-8").read()
    out, heading, buf = [], source, []
    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            if "".join(buf).strip():
                out.append({"source": source, "heading": heading, "text": "\n".join(buf).strip()[:3500]})
            heading, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        out.append({"source": source, "heading": heading, "text": "\n".join(buf).strip()[:3500]})
    return out


guides = []
for path, source in GUIDE_FILES:
    guides += sections(path, source)
dic = {k: {"type": v.get("type", ""), "desc": v.get("desc", ""), "accepted": v.get("accepted", [])[:40],
           "examples": [str(x) for x in v.get("examples", [])[:3]]} for k, v in library().dictionary.items()}
for k, v in dic.items():
    acc = f" Accepted values: {', '.join(v['accepted'])}." if v["accepted"] else ""
    guides.append({"source": "Zenu dictionary", "heading": k.replace("_", " ") + f" ({k})",
                   "text": f"{k} ({v['type']}): {v['desc']}.{acc}"})
hints = general_flow.guide_hints()

src = open(os.path.join(HERE, "index.src.html"), encoding="utf-8").read()
j = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
for key, val in (("__GUIDE__", j(hints)), ("__GUIDES__", j(guides)), ("__DICT__", j(dic))):
    assert src.count(key) == 1, key
    src = src.replace(key, val)
open(os.path.join(HERE, "index.html"), "w", encoding="utf-8").write(src)
print(f"built index.html: {len(guides)} guide sections, {len(dic)} Zenu fields, {len(hints)} role name lists, "
      f"{len(src.encode('utf-8')) / 1024:.0f} KB")

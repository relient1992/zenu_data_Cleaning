"""Every example phrase in reference/guides/DARIS Commands.md does what the guide says.

Run from the project folder:   python -m unittest tests.test_commands -v
Change a command -> update the guide -> update this list.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daris.assistant import classify  # noqa: E402

FOUND = {"matches": [{"suburb": "X"}], "close": []}
NOT_FOUND = {"matches": [], "close": []}


def lookup(text):
    return NOT_FOUND if "import" in text.lower() else FOUND


GUIDE = {
    # Files
    "show files": ("card", "files"), "what's in the files": ("card", "files", True),
    "analyse the files": ("card", "files"), "inspect": ("card", "files"),
    # Merge
    "merge the files": "merge", "consolidate": "merge", "combine them": "merge", "reconcile": "merge",
    "process the files": "merge", "map the files": "merge", "run it": "merge", "go ahead": "merge",
    "do it": "merge", "can you merge them please": "merge",
    # Clean contacts
    "clean the contacts": "clean", "clean these up": "clean", "tidy the data": "clean",
    "split the names": "clean", "format the phones": "clean", "format the numbers": "clean",
    "split the address and suburb": "clean", "separate the suburbs": "clean", "fix the addresses": "clean",
    "parse the mailing address": "clean", "can you split the address and suburb as well please": "clean",
    "could you clean these contacts?": "clean", "please separate the suburbs": "clean",
    "help me clean the contacts": "clean",
    "show cleaning results": ("card", "cleaning"), "view the cleaning": ("card", "cleaning"),
    # Agentbox / General handlers
    "clean agentbox contacts": "agentbox", "agentbox": "agentbox", "clean the agentbox files": "agentbox",
    "run agentbox": "agentbox", "can you clean the agent box contacts please": "agentbox",
    "general cleaning": "general", "run general cleaning": "general", "clean it as general": "general",
    "clean with general": "general", "use the general handler": "general",
    "how does agentbox cleaning work?": "ask", "in general how do we split couples?": "ask",
    # Review
    "show mapping": ("card", "mapping"), "columns": ("card", "mapping"), "headers": ("card", "mapping"),
    "suggestions": ("card", "mapping"),
    "show duplicates": ("card", "duplicates"), "same email": ("card", "duplicates"),
    "same phone": ("card", "duplicates"), "ID clashes": ("card", "duplicates"),
    "show issues": ("card", "issues"), "problems": ("card", "issues"), "errors": ("card", "issues"),
    "warnings": ("card", "issues"),
    # Teach
    "map Cell Ph to contact_mobile": "teach", "treat Cell Ph as contact_mobile": "teach",
    "set Cell Ph to contact_mobile": "teach", "map Cell Ph -> contact_mobile": "teach",
    "ignore Fav Colour": "teach", "don't map Fav Colour": "teach", "never map Fav Colour": "teach",
    "keep Fav Colour as-is": "teach", "keep Fav Colour unmapped": "teach",
    # Suburbs
    "postcode of Tooradin": "suburb", "postcode for Richmond VIC": "suburb", "state of Mount Coolum": "suburb",
    "check suburb Mt Coolum": "suburb", "look up suburb Tooradin": "suburb", "verify suburb Tooradin": "suburb",
    "is Toradin a real suburb?": "suburb",
    # Reference library
    "show references": "reference", "show guides": "reference", "list lookups": "reference",
    "show the dictionary": "reference", "what references do you have": "reference",
    # Help
    "help": "help", "what can you do": "help", "commands": "help",
    # Questions -> AI
    "is it safe to import?": "ask", "how do we split couples?": "ask",
    "what happens to a second fax number?": "ask", "what titles does Zenu accept?": "ask",
    "can you translate the notes to French?": "ask", "what is the state of the import?": "ask",
    # Questions that also show a card
    "which columns need review?": ("card", "mapping", True),
    "why are there duplicates?": ("card", "duplicates", True),
}


class GuideExamples(unittest.TestCase):
    def test_every_guide_phrase(self):
        for phrase, want in GUIDE.items():
            with self.subTest(phrase=phrase):
                got = classify(phrase, lookup)
                if isinstance(want, tuple):
                    self.assertEqual((got["kind"], got.get("card")), ("card", want[1]))
                    self.assertEqual(got["question"], len(want) > 2)
                else:
                    self.assertEqual(got["kind"], want)

    def test_teach_extracts_header_and_field(self):
        self.assertEqual(classify("map Cell Ph to contact_mobile"),
                         {"kind": "teach", "header": "Cell Ph", "target": "contact_mobile"})
        self.assertEqual(classify("keep Fav Colour as-is"), {"kind": "teach", "header": "Fav Colour", "target": ""})
        self.assertEqual(classify("map the files to zenu")["kind"], "merge")   # not a field name


if __name__ == "__main__":
    unittest.main()

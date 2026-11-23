"""Couples in separate rows linked by a spouse / partner contact ID column (e.g. spouse_contact_id)."""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daris.general_cleaning import GeneralCleaner  # noqa: E402

ROWS = [  # id, first, surname, spouse_contact_id
    ("11518458", "Nithun", "Deenadayalan", "11518460"),
    ("11518460", "Hemalatha", "Nithun", "11518458"),
    ("11518487", "Sophie", "Theodore", "11518490"),
    ("11518490", "Tony", "Theodore", ""),            # one-sided: still linked
    ("11518505", "Alana", "Elphick", "99999999"),    # spouse not in the file
    ("11518507", "Paul", "Anderson", "11518458"),    # points at someone linked to another person
    ("11518600", "Glen & Leanne", "Brindley", "11518602"),   # already a couple in one row
    ("11518602", "Leanne", "Brindley", "11518600"),
    ("11518611", "Neelam", "Rani", "11518611"),      # points at itself: ignored
]
COLS = ["CONTACT_IDENTIFIER", "CONTACT_PARTNER_IDENTIFIER", "CONTACT_PARTNERSHIP_ID", "CONTACT_PARTNERSHIP_TYPE"]


def run(tag="09112026"):
    df = pd.DataFrame(ROWS, columns=["id", "first_name", "surname", "spouse_contact_id"], dtype=str)
    m = {"id": "id", "first": "first_name", "last": "surname", "spouse_id": "spouse_contact_id"}
    r = GeneralCleaner(m, id_tag=tag).run(df)
    return r, {row["CONTACT_IDENTIFIER"]: row for row in r["summary"][COLS].to_dict("records")}


class TestSpouseLinks(unittest.TestCase):
    def test_mutual_pair(self):
        _r, s = run()
        a, b = s["11518458_09112026_c"], s["11518460_09112026_c"]
        self.assertEqual(a["CONTACT_PARTNER_IDENTIFIER"], "11518460_09112026_c")
        self.assertEqual(b["CONTACT_PARTNER_IDENTIFIER"], "11518458_09112026_c")
        self.assertEqual(a["CONTACT_PARTNERSHIP_ID"], "11518458_09112026_c_11518460_09112026_c_r")
        self.assertEqual(b["CONTACT_PARTNERSHIP_ID"], a["CONTACT_PARTNERSHIP_ID"])
        self.assertEqual((a["CONTACT_PARTNERSHIP_TYPE"], b["CONTACT_PARTNERSHIP_TYPE"]), ("Partner", "Partner"))

    def test_one_sided_link(self):
        _r, s = run()
        self.assertEqual(s["11518490_09112026_c"]["CONTACT_PARTNER_IDENTIFIER"], "11518487_09112026_c")

    def test_left_unlinked(self):
        r, s = run()
        for cid in ("11518505_09112026_c", "11518507_09112026_c", "11518611_09112026_c", "11518602_09112026_c"):
            self.assertEqual(s[cid]["CONTACT_PARTNER_IDENTIFIER"], "", cid)
        self.assertEqual(s["11518600_09112026_c2"]["CONTACT_PARTNER_IDENTIFIER"], "11518600_09112026_c1")   # split couple untouched
        st = r["stats"]
        self.assertEqual((st["couples_linked_from_spouse_id"], st["spouse_id_not_in_file"], st["spouse_id_conflicts"],
                          st["spouse_id_row_already_split"]), (2, 1, 1, 2))      # both rows of the split couple point at each other
        self.assertTrue(any("couple(s) linked from the spouse ID" in d for _s, d in r["issues"]))

    def test_without_id_tag(self):
        _r, s = run(tag="")
        self.assertEqual(s["11518458_c"]["CONTACT_PARTNER_IDENTIFIER"], "11518460_c")


if __name__ == "__main__":
    unittest.main()

"""is_contactable = 1: a permission whose own cell is blank becomes allowed (unsubscribe -> FALSE)."""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daris import general_flow  # noqa: E402
from daris.general_cleaning import GeneralCleaner, propose  # noqa: E402

COLS = ["id", "first_name", "surname", "is_allow_text", "is_allow_email", "unsubscribe_newsletter_date", "is_contactable"]
ROWS = [
    ("1", "Ann", "Lee", "", "", "", "1"),                      # all blank + contactable -> allowed everywhere
    ("2", "Bob", "Ray", "0", "", "2025-10-27", "1"),           # filled cells win; blank email -> allowed
    ("3", "Cy", "Dee", "", "", "", "0"),                       # not contactable -> the usual empty-cell setting
    ("4", "Di", "Fox", "1", "1", "", ""),
]
OUT = ["CONTACT_ALLOW_CONTACT_VIA_TEXT", "CONTACT_ALLOW_CONTACT_VIA_EMAIL", "CONTACT_ALLOW_CONTACT_VIA_LETTER",
       "CONTACT_ALLOW_CONTACT_VIA_PHONE", "CONTACT_UNSUBSCRIBE_FROM_MARKETING"]
MAP = {"id": "id", "first": "first_name", "last": "surname", "do_not_sms": "is_allow_text", "do_not_email": "is_allow_email",
       "subscribed": "unsubscribe_newsletter_date", "contactable": "is_contactable"}
MODES = {"do_not_sms": {"meaning": "allowed", "blank": "no"}, "do_not_email": {"meaning": "allowed", "blank": "no"},
         "subscribed": {"meaning": "filled_blocked", "blank": "no"}}


def run(mapping=MAP):
    df = pd.DataFrame(ROWS, columns=COLS, dtype=str)
    return GeneralCleaner(mapping, flag_modes=MODES).run(df)


class TestContactable(unittest.TestCase):
    def test_blank_permissions_become_allowed(self):
        r = run()
        self.assertEqual(r["summary"][OUT].values.tolist(), [
            ["TRUE", "TRUE", "TRUE", "TRUE", "FALSE"],     # letter / phone aren't in the export: filled too
            ["FALSE", "TRUE", "TRUE", "TRUE", "TRUE"],     # text 0 stays not allowed; unsubscribe date stays unsubscribed
            ["FALSE", "FALSE", "", "", "FALSE"],
            ["TRUE", "TRUE", "", "", "FALSE"],
        ])
        self.assertEqual(r["stats"]["permissions_from_contactable"], 8)          # row 1: 5 cells, row 2: email, letter, phone
        self.assertTrue(any("because the contact is contactable" in d for _s, d in r["issues"]))
        self.assertEqual(r["settings"]["contactable"], "1 / Yes = contactable: blank permissions become allowed")

    def test_without_contactable_nothing_changes(self):
        m = {k: v for k, v in MAP.items() if k != "contactable"}
        r = run(m)
        self.assertEqual(r["summary"][OUT].values.tolist()[0], ["FALSE", "FALSE", "", "", "FALSE"])
        self.assertEqual(r["stats"]["permissions_from_contactable"], 0)

    def test_mapped_by_name(self):
        out, _how = propose(COLS, {}, hints=general_flow.guide_hints())
        self.assertEqual(out["contactable"], "is_contactable")


if __name__ == "__main__":
    unittest.main()

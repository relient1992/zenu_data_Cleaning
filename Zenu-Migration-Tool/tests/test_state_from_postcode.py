"""A blank state is filled from the postcode (AU suburb list; the suburb breaks ties; Australia Post ranges as fallback)."""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daris.address_cleaning import AddressSplitter, range_state  # noqa: E402
from daris.general_cleaning import GeneralCleaner  # noqa: E402
from daris.references import library  # noqa: E402

LIB = library()
SPLIT = AddressSplitter(LIB.suburbs, LIB.spelling)


class TestLookup(unittest.TestCase):
    def test_one_state(self):
        self.assertEqual(SPLIT.state_for_postcode("3030")[:2], ("VIC", "list"))
        self.assertEqual(SPLIT.state_for_postcode("800")[:2], ("NT", "list"))          # leading zero lost in Excel

    def test_border_postcode_needs_the_suburb(self):
        self.assertEqual(SPLIT.state_for_postcode("2620")[:2], ("", "ambiguous"))
        self.assertEqual(SPLIT.state_for_postcode("2620", "Hume")[0], "ACT")
        self.assertEqual(SPLIT.state_for_postcode("2620", "Queanbeyan")[0], "NSW")

    def test_not_a_postcode(self):
        self.assertEqual(SPLIT.state_for_postcode("abcd"), ("", "", ""))
        self.assertEqual(SPLIT.state_for_postcode("30300"), ("", "", ""))

    def test_ranges(self):
        self.assertEqual([range_state(p) for p in ("0200", "0850", "2601", "2650", "4001", "6100", "7250", "8001", "6798")],
                         ["ACT", "NT", "ACT", "NSW", "QLD", "WA", "TAS", "VIC", ""])      # 6798 Christmas Island: outside WA's range


class TestCleaner(unittest.TestCase):
    def run_rows(self, rows, **kw):
        df = pd.DataFrame(rows, columns=["ID", "First", "Last", "Street", "Suburb", "State", "Postcode", "Country"], dtype=str)
        m = {"id": "ID", "first": "First", "last": "Last", "street": "Street", "suburb": "Suburb", "state": "State",
             "postcode": "Postcode", "country": "Country"}
        return GeneralCleaner(m, address_splitter=SPLIT, **kw).run(df)

    def test_fills_blank_state_only(self):
        r = self.run_rows([["1", "A", "B", "", "", "", "3030", ""],           # no street: still filled
                           ["2", "A", "B", "1 Main St", "Hume", "", "2620", "Australia"],
                           ["3", "A", "B", "", "", "QLD", "3030", ""],        # a state that's there is never changed
                           ["4", "A", "B", "", "", "", "2620", ""],           # border postcode, no suburb
                           ["5", "A", "B", "", "", "", "6011", "New Zealand"]])
        self.assertEqual(r["summary"]["CONTACT_MAILING_ADDRESS_STATE"].tolist(), ["VIC", "ACT", "QLD", "", ""])
        self.assertEqual(r["stats"]["state_from_postcode"], 2)
        self.assertEqual(r["stats"]["state_postcode_ambiguous"], 1)
        self.assertIn("state VIC filled from postcode 3030", r["address_cleaning"]["DARVIS address note"].iat[0])
        self.assertTrue(any("filled from the postcode" in d for _s, d in r["issues"]))

    def test_can_be_switched_off(self):
        r = self.run_rows([["1", "A", "B", "", "", "", "3030", ""]], fill_state_from_postcode=False)
        self.assertEqual(r["summary"]["CONTACT_MAILING_ADDRESS_STATE"].iat[0], "")


if __name__ == "__main__":
    unittest.main()

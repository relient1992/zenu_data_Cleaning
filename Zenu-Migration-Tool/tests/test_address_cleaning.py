"""Address / suburb splitting rules (daris/address_cleaning.py).

Uses a small built-in suburb list so the tests don't depend on reference/lookups.
Run from the project folder:   python -m unittest tests.test_address_cleaning -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daris.address_cleaning import AddressSplitter, norm_place  # noqa: E402

ROWS = [("Tooradin", "VIC", "3980"), ("Point Cook", "VIC", "3030"), ("St Kilda", "VIC", "3182"),
        ("St Kilda East", "VIC", "3183"), ("Richmond", "VIC", "3121"), ("Richmond", "NSW", "2753"),
        ("Richmond", "NSW", "2755"), ("Richmond", "QLD", "4822"), ("Mount Coolum", "QLD", "4573"),
        ("Darwin", "NT", "0800"), ("Ocean Grove", "VIC", "3226"), ("East Melbourne", "VIC", "3002"),
        ("Melbourne", "VIC", "3000"), ("Rochedale South", "QLD", "4123")]
SUBURBS = {}
for s, st, pc in ROWS:
    SUBURBS.setdefault(norm_place(s), []).append((s, st, pc))
SPELLING = {norm_place("Mt Coolum"): "Mount Coolum", norm_place("Toradin"): "Tooradin"}


class AddressSplitting(unittest.TestCase):
    def setUp(self):
        self.a = AddressSplitter(SUBURBS, SPELLING)

    def check(self, args, line1, suburb, state, postcode, flag=None):
        r = self.a.clean(*args)
        self.assertEqual((r["line1"], r["suburb"], r["state"], r["postcode"]), (line1, suburb, state, postcode))
        if flag:
            self.assertIn(flag, r["flags"])
        return r

    def test_splits_with_a_clear_boundary(self):
        # state / postcode only when the address itself contains them
        self.check(("12 Smith St, Tooradin",), "12 Smith St", "Tooradin", "", "", "split")
        self.check(("5 Bay Rd Point Cook VIC 3030",), "5 Bay Rd", "Point Cook", "VIC", "3030", "postcode_from_address")
        self.check(("5 Bay Rd Point Cook, Victoria",), "5 Bay Rd", "Point Cook", "VIC", "", "state_from_address")
        self.check(("Unit 3/45 King Road Point Cook 3030",), "Unit 3/45 King Road", "Point Cook", "", "3030")
        self.check(("PO Box 123 Tooradin",), "PO Box 123", "Tooradin", "", "")
        self.check(("10 Smith St, Darwin NT",), "10 Smith St", "Darwin", "NT", "")
        self.check(("9 Gavia Court\nROCHEDALE SOUTH QLD 4123",), "9 Gavia Court", "ROCHEDALE SOUTH", "QLD", "4123")

    def test_longest_suburb_wins(self):
        self.check(("12 St Kilda Rd St Kilda East",), "12 St Kilda Rd", "St Kilda East", "", "")
        self.check(("1 Collins St East Melbourne",), "1 Collins St", "East Melbourne", "", "")

    def test_no_boundary_no_split(self):
        # 'Ocean Grove' is a suburb and a plausible street name - without a separator, leave it
        r = self.check(("12 Ocean Grove",), "12 Ocean Grove", "", "", "")
        self.assertNotIn("split", r["flags"])
        self.check(("Tooradin",), "Tooradin", "", "", "")            # nothing left for the street

    def test_not_a_known_suburb(self):
        self.check(("12 Smith St, Nowhereville",), "12 Smith St, Nowhereville", "", "", "")

    def test_spelling_fixes(self):
        self.check(("4 Beach Pde, Mt Coolum",), "4 Beach Pde", "Mount Coolum", "", "", "spelling_fixed")
        self.check(("", "Mt Coolum"), "", "Mount Coolum", "", "", "spelling_fixed")

    def test_nothing_is_invented_from_the_list(self):
        for args in (("10 Smith St, Richmond",), ("", "Tooradin"), ("10 Smith St, Richmond VIC",)):
            r = self.a.clean(*args)
            self.assertFalse(r["flags"] & {"state_filled", "postcode_filled", "ambiguous_state"}, args)
        self.check(("10 Smith St, Richmond VIC",), "10 Smith St", "Richmond", "VIC", "")

    def test_fill_from_list_option(self):
        a = AddressSplitter(SUBURBS, SPELLING, fill_from_list=True)
        r = a.clean("12 Smith St, Tooradin")
        self.assertEqual((r["state"], r["postcode"]), ("VIC", "3980"))
        self.assertIn("ambiguous_state", a.clean("10 Smith St, Richmond")["flags"])

    def test_existing_columns_are_never_overwritten(self):
        self.check(("12 Smith St, Tooradin", "Point Cook"), "12 Smith St, Tooradin", "Point Cook", "", "", "conflict")
        self.check(("12 Smith St, Tooradin", "Tooradin", "", ""), "12 Smith St", "Tooradin", "", "")
        self.check(("", "Tooradin", "Victoria", "3980"), "", "Tooradin", "Victoria", "3980")

    def test_mismatches_are_flagged(self):
        self.check(("", "Tooradin", "NSW", ""), "", "Tooradin", "NSW", "", "state_mismatch")
        self.check(("", "Tooradin", "VIC", "3000"), "", "Tooradin", "VIC", "3000", "postcode_mismatch")
        r = self.check(("", "Toorradin"), "", "Toorradin", "", "", "unknown_suburb")
        self.assertIn("did you mean Tooradin", r["notes"][0])


if __name__ == "__main__":
    unittest.main()

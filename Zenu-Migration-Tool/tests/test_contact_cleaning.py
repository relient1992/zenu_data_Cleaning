"""Golden tests: every worked example in reference/guides/Contact Cleaning - *.md.

Run from the project folder:   python -m unittest tests.test_contact_cleaning -v
If a rule in a macro changes, update the guide, then the expected values here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daris.contact_cleaning import ContactDetailsCleaner, is_valid_email, split_names  # noqa: E402


class NameSplittingGuide(unittest.TestCase):
    # (C title, D first, E last) -> (people, note)   - guide section 4 + rule examples
    CASES = [
        (("Mr", "John", "Smith"), ([("Mr", "John", "Smith")], "")),
        (("mrs.", "Jane", "Lee"), ([("Mrs", "Jane", "Lee")], "")),
        (("Mr", "John and Mary", "Smith"), ([("Mr", "John", "Smith"), ("", "Mary", "Smith")], "")),
        (("", "Frank/Merlyn", "Armer"), ([("", "Frank", "Armer"), ("", "Merlyn", "Armer")], "")),
        (("", "Brian & Liljana", "Armfield/Incao"), ([("", "Brian", "Armfield"), ("", "Liljana", "Incao")], "")),
        (("", "John", "Smith & Jones"), ([("", "John", "Smith"), ("", "", "Jones")], "")),
        (("", "Kate Griffin & Nick Griffin", ""),
         ([("", "Kate", "Griffin"), ("", "Nick", "Griffin")], "no surname in raw data")),
        (("Mrs", "Dr Sofija", "Petrovic"), ([("Dr", "Sofija", "Petrovic")], "")),
        (("Sarah", "", "Lee"), ([("", "Sarah", "Lee")], "")),
        (("Mr & Mrs", "Tom & Ann", "Brown"),
         ([("", "Tom", "Brown"), ("", "Ann", "Brown")], "invalid title 'Mr & Mrs' ignored")),
        (("", "Megan SEA TEMPLE", "Lee"), ([("", "Megan", "Lee")], "label 'SEA TEMPLE' removed")),
        (("Dr", "N/A", "-"), ([], "title only, no name")),
        (("", "A, B, C, D, E, F", "Ng"),
         ([("", x, "Ng") for x in "ABCDE"], "6 people found, first 5 kept")),
        # Step 3 label table
        (("", "JOHN SMITH", ""), ([("", "JOHN", "SMITH")], "")),
        (("", "John A", ""), ([("", "John", "A")], "")),
        (("", "Mary O'NEIL", "Lee"), ([("", "Mary", "Lee")], "label 'O'NEIL' removed")),
        # Open question 2: the label check runs before splitting, so a capitalised partner goes
        (("", "John & MARY", "SMITH"), ([("", "John", "SMITH")], "label 'MARY' removed")),
        (("", "JOHN & MARY", "SMITH"), ([("", "JOHN", "SMITH"), ("", "MARY", "SMITH")], "")),
        # Step 4 separators
        (("", "Andrew", "Lee"), ([("", "Andrew", "Lee")], "")),
        (("", "Ann AND Bob", "Lee"), ([("", "Ann", "Lee"), ("", "Bob", "Lee")], "")),
        (("", "Mary-Jane", "Lee"), ([("", "Mary-Jane", "Lee")], "")),
        (("MR", "  John\xa0 ", "Smith"), ([("Mr", "John", "Smith")], "")),
    ]

    def test_guide_examples(self):
        for (c, d, e), expected in self.CASES:
            with self.subTest(title=c, first=d, last=e):
                self.assertEqual(split_names(c, d, e), expected)

    def test_labels_can_be_switched_off(self):
        self.assertEqual(split_names("", "Megan SEA TEMPLE", "Lee", {"strip_caps_labels": False}),
                         ([("", "Megan SEA TEMPLE", "Lee")], ""))


class PhonesAndEmailsGuide(unittest.TestCase):
    def clean(self, email="", mobile="", work="", home="", fax=""):
        return ContactDetailsCleaner().clean(email, mobile, work, home, fax)

    def assertSlots(self, out, **expected):
        for col in ("G", "J", "L", "N", "O", "U", "W", "X", "Y", "Z"):
            self.assertEqual(out[col], expected.get(col, ""), f"column {col}")

    # Guide section 7 - worked phone examples
    def test_worked_examples(self):
        cases = [
            (dict(mobile="0412345678"), dict(G="0412 345 678")),
            (dict(mobile="412 345 678"), dict(G="0412 345 678")),
            (dict(mobile="+61 412 345 678"), dict(G="+61 412 345 678")),
            (dict(mobile="0412 345 678 / 0498 765 432"), dict(G="0412 345 678 | 0498 765 432")),
            (dict(mobile="(03) 9876-5432"), dict(J="03 9876 5432")),
            (dict(work="02 9999 0000 (work)", home="0398765432"), dict(J="03 9876 5432", L="02 9999 0000")),
            (dict(mobile="0412 345 678", home="0412345678"), dict(G="0412 345 678")),
            (dict(mobile="0412 345 678 ext 2"), dict(W="0412 345 678 ext 2")),
            (dict(mobile="+44 (20) 7946-0958"), dict(G="+44 20 79460958")),
            (dict(mobile="98765432"), dict(J="9876 5432")),
            (dict(mobile="john@x.com"), dict(O="john@x.com")),
            (dict(fax="03 9111 2222, 03 9111 3333"), dict(N="03 9111 2222", J="03 9111 3333")),
        ]
        for raw, expected in cases:
            with self.subTest(**raw):
                self.assertSlots(self.clean(**raw), **expected)

    # Guide section 4 - classification table
    def test_classification_table(self):
        c = ContactDetailsCleaner()
        cases = {
            "0412345678": ("M", "0412 345 678"),
            "+61 0412 345 678": ("M", "+61 412 345 678"),
            "61412345678": ("M", "+61 412 345 678"),
            "412345678": ("M", "0412 345 678"),
            "+64 21 123 4567": ("M", "+64 21 123 4567"),
            "021 123 4567": ("M", "021 123 4567"),
            "0231234567": ("L", "02 3123 4567"),           # 023-029: Sydney, not NZ
            "298765432": ("L", "02 9876 5432"),
            "+61 2 9876 5432": ("L", "+61 2 9876 5432"),
            "1300 123 456": ("L", "1300 123 456"),
            "131234": ("L", "13 12 34"),
            "98765432": ("L", "9876 5432"),
            "0011 44 20 7946 0958": ("N", "+44 20 7946 0958"),
            "+44 (20) 7946-0958": ("N", "+44 20 79460958"),
            "0000000000": ("I", "0000000000"),
            "0412 345 678 ext 2": ("I", "0412 345 678 ext 2"),
            "04-12+345": ("I", "04-12+345"),
            "+61 12345": ("I", "+61 12345"),
            "1234567": ("I", "1234567"),
        }
        for raw, (kind, fmt) in cases.items():
            with self.subTest(raw=raw):
                got = c.classify(raw)
                self.assertEqual((got[0], got[1]), (kind, fmt))

    def test_bracket_text_removed_area_code_kept(self):
        self.assertSlots(self.clean(mobile="0412 345 678 (mob)"), G="0412 345 678")
        self.assertSlots(self.clean(home="(07) 3123 4567"), J="07 3123 4567")

    def test_landline_routing_quirks(self):
        # second home landline fills WorkLandline (guide section 5)
        self.assertSlots(self.clean(home="03 9111 2222; 03 9111 3333"), J="03 9111 2222", L="03 9111 3333")
        # international from Home is a landline, from Mobile a mobile
        self.assertSlots(self.clean(home="+44 20 7946 0958"), J="+44 20 7946 0958")

    def test_invalid_columns_by_source(self):
        out = self.clean(mobile="abc", home="123", work="x1", fax="0000 0000")
        self.assertEqual((out["W"], out["X"], out["Y"], out["Z"]), ("abc", "123", "x1", "0000 0000"))

    def test_dash_splits_only_long_cells(self):
        self.assertSlots(self.clean(mobile="0412 345 678 - 0498 765 432"), G="0412 345 678 | 0498 765 432")
        self.assertSlots(self.clean(mobile="0412-345-678"), G="0412 345 678")

    # Guide section 6 - emails
    def test_email_examples(self):
        cases = [
            ("john@x.com", "john@x.com", ""),
            ("JOHN@x.com; john@x.com", "JOHN@x.com", ""),
            ("john@x.com, mary@y.com.au", "john@x.com | mary@y.com.au", ""),
            ("mailto:<sam@z.net>", "sam@z.net", ""),
            ("john@x", "", "john@x"),
            ("John Smith <john@x.com>", "john@x.com", "John | Smith"),
        ]
        for raw, o, u in cases:
            with self.subTest(raw=raw):
                out = self.clean(email=raw)
                self.assertEqual((out["O"], out["U"]), (o, u))

    def test_invalid_email_in_phone_cell(self):
        # Macro quirk: no digits left -> the cell is blanked, nothing reaches W/U.
        # DARIS keeps the macro's output but reports the removed text.
        out = self.clean(mobile="john@x")
        self.assertEqual((out["O"], out["W"], out["dropped"]), ("", "", ["john@x"]))
        # with digits it stays and is rejected as an invalid phone
        out = self.clean(mobile="john1@x")
        self.assertEqual((out["W"], out["dropped"]), ("john1@x", []))
        # a bare label is cleared too (the intended case)
        self.assertEqual(self.clean(mobile="Email: a@b.com")["O"], "a@b.com")

    def test_email_validity_rules(self):
        for e in ("a@b.co", "o'neil@x.com.au", "a.b+c%d@x-y.org"):
            self.assertTrue(is_valid_email(e), e)
        for e in ("a@b.c", "@abc.com", "a@@b.com", "a..b@x.com", ".a@x.com", "a@x", "a@-x.com",
                  "a@x.c0m", "ab@x.c"):
            self.assertFalse(is_valid_email(e), e)

    def test_every_phone_value_is_accounted_for(self):
        c = ContactDetailsCleaner()
        c.clean(mobile="0412345678 / 0412 345 678 / junk", home="(03) 9876-5432", fax="98765432")
        rec = c.reconciliation()
        self.assertTrue(rec["phones_ok"], rec)
        self.assertEqual(rec["phone_parts_in"], 5)


if __name__ == "__main__":
    unittest.main()

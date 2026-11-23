"""The second person's first name typed into the surname column: first 'Munny', surname '& Brawna Kumar'."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daris.contact_cleaning import split_names  # noqa: E402


def people(first, last, title=""):
    return split_names(title, first, last)[0]


class TestPartnerInSurname(unittest.TestCase):
    def test_shared_surname(self):
        self.assertEqual(people("Munny", "& Brawna Kumar"), [("", "Munny", "Kumar"), ("", "Brawna", "Kumar")])
        self.assertEqual(people("Gilroy", "& Irene Dias"), [("", "Gilroy", "Dias"), ("", "Irene", "Dias")])

    def test_no_space_after_ampersand(self):
        self.assertEqual(people("Fabian", "&myschelle Bhandari"), [("", "Fabian", "Bhandari"), ("", "myschelle", "Bhandari")])

    def test_first_name_only(self):
        p, note = split_names("", "Finlae", "& Stephen")
        self.assertEqual(p, [("", "Finlae", ""), ("", "Stephen", "")])
        self.assertIn("second person's first name was in the surname column", note)

    def test_other_separators_and_titles(self):
        self.assertEqual(people("John", "and Mary Smith"), [("", "John", "Smith"), ("", "Mary", "Smith")])
        self.assertEqual(people("Mrs Ann", "& Mr Bob Lee"), [("Mrs", "Ann", "Lee"), ("Mr", "Bob", "Lee")])
        self.assertEqual(people("Ann", "& Mary Anne Smith"), [("", "Ann", "Smith"), ("", "Mary Anne", "Smith")])

    def test_unchanged_cases(self):
        self.assertEqual(people("Jo", "Anderson"), [("", "Jo", "Anderson")])            # 'and' inside a word
        self.assertEqual(people("Ann", "andrew Smith"), [("", "Ann", "andrew Smith")])
        self.assertEqual(people("John & Mary", "Smith"), [("", "John", "Smith"), ("", "Mary", "Smith")])
        self.assertEqual(people("", "& Bob Lee"), [("", "", "Bob Lee")])                  # no first name: not this rule


if __name__ == "__main__":
    unittest.main()

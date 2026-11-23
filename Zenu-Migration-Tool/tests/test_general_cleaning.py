"""General cleaning (mirrors 'Contact Details Cleaning - General.xlsb') and its DARIS review flow.
Synthetic contacts only. Run: python -m unittest tests.test_general_cleaning"""
import io
import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daris import general_flow  # noqa: E402
import _offline  # noqa: E402,F401  (no AI in chat tests)
from daris.assistant import DarisAssistant, DarisSession  # noqa: E402
from daris.general_cleaning import GeneralCleaner, name_blocks, propose_mapping  # noqa: E402
from daris.references import library  # noqa: E402

CSV = """ID,First Name,Surname,Mobile,Email,Home Phone,Address,Suburb,Postcode,Notes
1,Daryl & Kim,Roallos,0412 345 678,daryl@example.com,07 3333 4444,"9 Gavia Court ROCHEDALE SOUTH",,,vip
2,John,Smith,12345,bad@,,,,,
3,Mr John and Mrs Jane,Citizen,0400111222,j@example.com,,12 Main St,Tooradin,3980,
"""
MAPPING = {"id": "ID", "first": "First Name", "last": "Surname", "mobile": "Mobile", "email": "Email",
           "home": "Home Phone", "street": "Address", "suburb": "Suburb", "postcode": "Postcode", "notes": "Notes"}


def frame(csv=CSV):
    return pd.read_csv(io.StringIO(csv), dtype=str).fillna("")


def run(df=None, mapping=MAPPING, tag=""):
    return GeneralCleaner(mapping, id_tag=tag).run(frame() if df is None else df)


class TestIdentifiers(unittest.TestCase):
    def test_users_example_without_tag(self):
        s = run()["summary"]
        rows = list(zip(s["CONTACT_IDENTIFIER"], s["CONTACT_FIRST_NAME"], s["CONTACT_SURNAME"]))
        self.assertIn(("1_c1", "Daryl", "Roallos"), rows)
        self.assertIn(("1_c2", "Kim", "Roallos"), rows)

    def test_single_person_is_c(self):
        s = run(tag="04112026")["summary"]
        self.assertIn("2_04112026_c", list(s["CONTACT_IDENTIFIER"]))

    def test_partner_and_partnership_ids(self):
        s = run(tag="04112026")["summary"].set_index("CONTACT_IDENTIFIER")
        self.assertEqual(s.loc["1_04112026_c1", "CONTACT_PARTNER_IDENTIFIER"], "1_04112026_c2")
        self.assertEqual(s.loc["1_04112026_c2", "CONTACT_PARTNER_IDENTIFIER"], "1_04112026_c1")
        self.assertEqual(s.loc["1_04112026_c2", "CONTACT_PARTNERSHIP_ID"], "1_04112026_c1_1_04112026_c2_r")
        self.assertEqual(s.loc["3_04112026_c1", "CONTACT_PARTNERSHIP_TYPE"], "Husband")
        self.assertEqual(s.loc["3_04112026_c2", "CONTACT_PARTNERSHIP_TYPE"], "Wife")
        self.assertEqual(s.loc["1_04112026_c1", "CONTACT_PARTNERSHIP_TYPE"], "Partner")

    def test_name_blocks_c_vs_c1(self):
        blocks, _note = name_blocks("7", "", "Ann", "Lee")
        self.assertEqual(blocks[0]["id"], "7_c")
        blocks, _note = name_blocks("7", "", "Ann & Bob", "Lee")
        self.assertEqual([b["id"] for b in blocks[:2]], ["7_c1", "7_c2"])


class TestSummaryOrder(unittest.TestCase):
    def test_split_people_at_the_bottom(self):
        r = run()
        ids = list(r["summary"]["CONTACT_IDENTIFIER"])
        self.assertEqual(ids, ["1_c1", "2_c", "3_c1", "1_c2", "3_c2"])
        st = r["stats"]
        self.assertEqual((st["main_rows"], st["split_rows"], st["total_rows"]), (3, 2, 5))

    def test_every_source_row_has_one_main_row(self):
        r = run()
        self.assertEqual(list(r["summary"]["Raw ORIG CONTACT_IDENTIFIER"][:3]), ["1", "2", "3"])


class TestPhonesAndPartners(unittest.TestCase):
    def test_partner_falls_back_to_main_mobile_and_email(self):
        s = run()["summary"].set_index("CONTACT_IDENTIFIER")
        self.assertEqual(s.loc["1_c2", "CONTACT_MOBILE"], "0412 345 678")
        self.assertEqual(s.loc["1_c2", "CONTACT_EMAIL_ADDRESS"], "daryl@example.com")
        self.assertEqual(s.loc["1_c2", "CONTACT_PHONE_HOME"], "07 3333 4444")

    def test_partner_takes_own_mobile2(self):
        df = frame()
        df["Partner Mobile"] = ["0499 000 111", "", ""]
        s = run(df, {**MAPPING, "mobile2": "Partner Mobile"})["summary"].set_index("CONTACT_IDENTIFIER")
        self.assertEqual(s.loc["1_c2", "CONTACT_MOBILE"], "0499 000 111")
        self.assertEqual(s.loc["1_c1", "CONTACT_MOBILE"], "0412 345 678")

    def test_invalid_number_goes_to_notes(self):
        r = run()
        s = r["summary"].set_index("CONTACT_IDENTIFIER")
        self.assertEqual(s.loc["2_c", "CONTACT_MOBILE"], "")
        self.assertIn("12345", s.loc["2_c", "CONTACT_NOTES"])
        self.assertTrue(r["stats"]["phones_reconciled"])

    def test_nan_is_a_name(self):
        df = frame()
        df.loc[1, "First Name"] = "Nan"
        s = run(df)["summary"].set_index("CONTACT_IDENTIFIER")
        self.assertEqual(s.loc["2_c", "CONTACT_FIRST_NAME"], "Nan")


class TestSharedCells(unittest.TestCase):
    """Several mobiles / emails in one cell of a split record: one each, in order."""
    CSV = """ID,First Name,Surname,Mobile,Email
3,Mr John and Mrs Jane,Citizen,"0400111222, 0400333444","j@example.com; jane@example.com"
4,Ann & Bob,Lee,"0411000001, 0411000002, 0411000003",ann@example.com
5,Sam,Solo,"0422000001, 0422000002",
"""
    M = {"id": "ID", "first": "First Name", "last": "Surname", "mobile": "Mobile", "email": "Email"}

    def summary(self, **kw):
        r = GeneralCleaner(self.M, **kw).run(frame(self.CSV))
        return r, r["summary"].set_index("CONTACT_IDENTIFIER")

    def test_second_person_gets_second_value(self):
        r, s = self.summary()
        self.assertEqual(s.loc["3_c1", "CONTACT_MOBILE"], "0400 111 222")
        self.assertEqual(s.loc["3_c2", "CONTACT_MOBILE"], "0400 333 444")
        self.assertEqual(s.loc["3_c1", "CONTACT_EMAIL_ADDRESS"], "j@example.com")
        self.assertEqual(s.loc["3_c2", "CONTACT_EMAIL_ADDRESS"], "jane@example.com")
        self.assertEqual((r["stats"]["own_mobile_from_shared_cell"], r["stats"]["own_email_from_shared_cell"]), (2, 1))

    def test_leftovers_stay_with_person_one(self):
        _r, s = self.summary()
        self.assertEqual(s.loc["4_c1", "CONTACT_MOBILE"], "0411 000 001 | 0411 000 003")
        self.assertEqual(s.loc["4_c2", "CONTACT_MOBILE"], "0411 000 002")
        self.assertEqual(s.loc["4_c2", "CONTACT_EMAIL_ADDRESS"], "ann@example.com")     # one email: shared

    def test_single_person_keeps_both(self):
        _r, s = self.summary()
        self.assertEqual(s.loc["5_c", "CONTACT_MOBILE"], "0422 000 001 | 0422 000 002")

    def test_workbook_rule_when_switched_off(self):
        _r, s = self.summary(split_multi_values=False)
        self.assertEqual(s.loc["3_c2", "CONTACT_MOBILE"], "0400 111 222 | 0400 333 444")

    def test_contact_details_tab_unchanged(self):
        r, _s = self.summary()
        self.assertEqual(r["contact_details"]["Mobile"].iat[0], "0400 111 222 | 0400 333 444")


class TestMappingProposal(unittest.TestCase):
    def test_role_hints_without_zenu_field(self):
        m = propose_mapping(["Partner First Name", "Do Not SMS", "Email 2"], {})
        self.assertEqual((m["first2"], m["do_not_sms"], m["email2"]), ("Partner First Name", "Do Not SMS", "Email 2"))

    def test_a_column_is_used_once(self):
        m = propose_mapping(["Mobile"], {"contact_mobile": "Mobile", "contact_phone_work": "Mobile"})
        self.assertEqual([r for r, c in m.items() if c], ["mobile"])


class TestSessionFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.s = DarisSession.create(self.tmp.name)
        self.s.add_files([("contacts.csv", io.BytesIO(CSV.encode()))])

    def tearDown(self):
        self.tmp.cleanup()

    def test_review_card_proposes_before_running(self):
        cards, _issues, _notes = general_flow.review_cards(self.s, "general")
        self.assertEqual(len(cards), 1)
        c = cards[0]
        for role, col in MAPPING.items():
            self.assertEqual(c["mapping"][role], col, role)
        self.assertEqual(os.listdir(self.s.out_dir), [])           # nothing written yet
        self.assertRegex(c["id_tag"], r"^\d{8}$")

    def test_chat_clean_shows_review_card(self):
        evs = list(DarisAssistant(self.s).handle("clean the contacts"))
        self.assertEqual([e["card"]["kind"] for e in evs if e["type"] == "card"], ["general_mapping"])
        self.assertEqual(os.listdir(self.s.out_dir), [])

    def test_run_with_confirmed_mapping(self):
        evs = list(DarisAssistant(self.s).run_general("contacts.csv", "csv", MAPPING, "04112026"))
        card = next(e["card"] for e in evs if e["type"] == "card")
        self.assertEqual(card["kind"], "general_result")
        path = os.path.join(self.s.out_dir, card["output_file"])
        book = pd.read_excel(path, sheet_name=None, dtype=str)
        self.assertEqual(list(book)[:4], ["Summary", "Name SPlitting", "Contact Details", "Address Cleaning"])
        self.assertEqual(list(book["Summary"]["CONTACT_IDENTIFIER"])[-1], "3_04112026_c2")

    def test_run_rejects_a_column_used_twice(self):
        bad = {**MAPPING, "work": "Mobile"}
        evs = list(DarisAssistant(self.s).run_general("contacts.csv", "csv", bad, ""))
        self.assertTrue(any(e["type"] == "error" and "only be used once" in e["text"] for e in evs))

    def test_run_rejects_unknown_column(self):
        evs = list(DarisAssistant(self.s).run_general("contacts.csv", "csv", {"first": "Nope"}, ""))
        self.assertTrue(any(e["type"] == "error" and "aren't in the file" in e["text"] for e in evs))

    def test_merge_is_switched_off(self):
        evs = list(DarisAssistant(self.s).handle("merge the files"))
        text = " ".join(e.get("text", "") for e in evs)
        self.assertIn("switched off", text)
        self.assertEqual(os.listdir(self.s.out_dir), [])


class TestReferences(unittest.TestCase):
    def test_macro_workbooks_are_not_read(self):
        for f in library().files:
            if f["file"].lower().endswith((".xlsb", ".xlsm")):
                self.assertEqual(f["status"], "listed", f["file"])


if __name__ == "__main__":
    unittest.main()

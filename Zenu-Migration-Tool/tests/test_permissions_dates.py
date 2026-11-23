"""Permission columns (what 1 / Yes means, empty cells, opt-out dates) and date formats (date only / with time)."""
import io
import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daris import general_flow  # noqa: E402
import _offline  # noqa: E402,F401
from daris.assistant import DarisSession  # noqa: E402
from daris.general_cleaning import GeneralCleaner, format_date, suggest_flag_mode  # noqa: E402

CSV = """contact_id,first_name,last_name,is_allow_text,is_allow_email,unsubscribe_newsletter_date,date_created
1,Ann,Lee,1,1,,{ts '2024-07-16 09:42:03'}
2,Bob,Ray,1,0,{ts '2025-10-27 15:28:21'},{ts '2023-01-05 17:00:00'}
3,Cy,Dee,,1,,2024-02-29
4,Di,Fox,maybe,1,{ts '2025-10-27 11:50:34'},not a date
"""
OUT = ["CONTACT_ALLOW_CONTACT_VIA_TEXT", "CONTACT_ALLOW_CONTACT_VIA_EMAIL", "CONTACT_UNSUBSCRIBE_FROM_MARKETING", "CONTACT_DATE_CREATED"]


class TestDates(unittest.TestCase):
    def test_formats(self):
        ts = "{ts '2024-07-16 09:42:03'}"
        self.assertEqual(format_date(ts), "16/07/2024")
        self.assertEqual(format_date(ts, "datetime"), "16/07/2024 09:42")
        self.assertEqual(format_date(ts, "datetime_s"), "16/07/2024 09:42:03")
        self.assertEqual(format_date("16/07/2024 9:42 PM", "datetime"), "16/07/2024 21:42")
        self.assertEqual(format_date("2024-07-16", "datetime"), "16/07/2024 00:00")
        self.assertEqual(format_date("16 Jul 2024"), "16/07/2024")
        self.assertEqual(format_date("31/02/2024"), "31/02/2024")         # not a real date: kept
        self.assertEqual(format_date("not a date", "datetime"), "not a date")


class TestPermissionGuess(unittest.TestCase):
    def test_guess_from_header(self):
        g = lambda h, s=(): suggest_flag_mode("do_not_sms", h, s)["meaning"]
        self.assertEqual(g("is_allow_text"), "allowed")
        self.assertEqual(g("Do Not SMS"), "blocked")
        self.assertEqual(g("unsubscribe_newsletter_date"), "filled_blocked")
        self.assertEqual(g("SMS opt-in date"), "filled_allowed")
        self.assertEqual(g("Status", ["{ts '2025-10-27 15:28:21'}"]), "filled_allowed")

    def test_old_rule_when_no_setting(self):
        df = pd.DataFrame({"ID": ["1", "2", "3"], "First": ["A"] * 3, "Last": ["B"] * 3, "DNS": ["1", "0", ""]}, dtype=str)
        s = GeneralCleaner({"id": "ID", "first": "First", "last": "Last", "do_not_sms": "DNS"}).run(df)["summary"]
        self.assertEqual(s["CONTACT_ALLOW_CONTACT_VIA_TEXT"].tolist(), ["FALSE", "TRUE", "FALSE"])


class TestFlowSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.s = DarisSession.create(self.tmp.name)
        self.s.add_files([("perm.csv", io.BytesIO(CSV.encode()))])

    def tearDown(self):
        self.tmp.cleanup()

    def test_card_guesses_and_counts(self):
        c = general_flow.review_cards(self.s, "general")[0][0]
        self.assertEqual((c["mapping"]["do_not_sms"], c["mapping"]["subscribed"]), ("is_allow_text", "unsubscribe_newsletter_date"))
        self.assertEqual(c["flag_modes"]["do_not_sms"]["meaning"], "allowed")
        self.assertEqual(c["flag_modes"]["subscribed"]["meaning"], "filled_blocked")
        col = {x["name"]: x for x in c["columns"]}
        self.assertEqual(col["is_allow_text"]["flags"], {"yes": 2, "no": 0, "empty": 1, "other": 1})
        self.assertEqual(col["date_created"]["dates"]["datetime"], "16/07/2024 09:42")
        self.assertEqual(col["date_created"]["dates"]["unreadable"], 1)

    def run_flow(self, **kw):
        c = general_flow.review_cards(self.s, "general")[0][0]
        st = general_flow.run(self.s, c["file"], c["sheet"], c["mapping"], **kw)
        out = os.path.join(self.s.out_dir, st["output_file"])
        return st, pd.read_excel(out, sheet_name=None, dtype=str, keep_default_na=False)

    def test_guessed_settings_by_default(self):
        st, wb = self.run_flow()
        rows = wb["Summary"][OUT].values.tolist()
        self.assertEqual(rows[:4], [["TRUE", "TRUE", "FALSE", "16/07/2024"], ["TRUE", "FALSE", "TRUE", "05/01/2023"],
                                    ["FALSE", "TRUE", "FALSE", "29/02/2024"], ["FALSE", "TRUE", "TRUE", "not a date"]])
        self.assertTrue(any("weren't yes/no" in i["detail"] for i in st["issues"]))
        self.assertTrue(any("couldn't be read as dates" in i["detail"] for i in st["issues"]))
        setting = dict(zip(wb["Column Mapping"]["Role"], wb["Column Mapping"]["Setting"]))
        self.assertEqual(setting["Newsletter / marketing (unsubscribe)"], "filled in = unsubscribed")

    def test_user_settings(self):
        st, wb = self.run_flow(flag_modes={"do_not_sms": {"meaning": "allowed", "blank": "yes"}},
                               date_modes={"created": "datetime"})
        s = wb["Summary"]
        self.assertEqual(s["CONTACT_ALLOW_CONTACT_VIA_TEXT"].tolist()[:4], ["TRUE", "TRUE", "TRUE", "FALSE"])
        self.assertEqual(s["CONTACT_DATE_CREATED"].tolist()[:3], ["16/07/2024 09:42", "05/01/2023 17:00", "29/02/2024 00:00"])
        self.assertEqual(st["settings"]["do_not_sms"], "1 / Yes = allowed, empty = allowed")


if __name__ == "__main__":
    unittest.main()

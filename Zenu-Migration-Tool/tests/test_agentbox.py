"""Agentbox handler: the 4 Agentbox CSVs -> Contact Type / Tags, Team member 1, decoded notes.
Synthetic data only. Run: python -m unittest tests.test_agentbox"""
import base64
import io
import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daris import agentbox, general_flow  # noqa: E402
import _offline  # noqa: E402,F401  (no AI in chat tests)
from daris.assistant import DarisAssistant, DarisSession  # noqa: E402

B64 = "[base64]" + base64.b64encode("Called re: café on Smith St\nkeen buyer".encode("utf-8")).decode()

CONTACT = (
    "contact_id,type,title,salutation,first_name,last_name,company_name,job_title,legal_name,address_to,email,"
    "home_phone,work_phone,mobile,fax,website_url,street_address,suburb,state,postcode,country,postal_address,"
    "postal_suburb,postal_state,postal_postcode,postal_country,preferred_contact_method,source,anniversary_date,"
    "comments,do_not_call,do_not_sms,do_not_email,do_not_mail,status,date_created,last_modified,"
    "subscribed_newsletters\n"
    f'101,person,Mr,Dear Daryl,Daryl & Kim,Roallos,,,,,daryl@example.com,,,0412 345 678,,,9 Gavia Court,'
    f'Rochedale South,QLD,4123,Australia,,,,,,,Website,,"{B64}",false,false,false,false,active,'
    f'2024-01-31 10:00:00,2024-02-01 09:00:00,true\n'
    "102,person,Ms,,Jane,Citizen,,,,,jane@example.com,,,0400 111 222,,,,,,,,,,,,,,,,plain old note,"
    "false,true,false,false,active,2023-05-01,2023-05-02,false\n"
)
CATEGORY = ("contact_id,category_name,agent_id\n101,buyer,1\n101,Hot Lead,1\n101, VIP ,1\n101,BUYER,1\n"
            "102,Tenant,2\n999,Vendor,2\n")
CXA = "contact_id,agent_id,role\n101,2,full_access\n101,1,primary_owner\n102,2,full_access\n"
AGENT = "agent_id,office_id,first_name,last_name\n1,9,Alex,Agent\n2,9,Sam,Seller\n"


class TestRules(unittest.TestCase):
    def test_type_tags_split(self):
        self.assertEqual(agentbox.split_type_tags("buyer,Hot Lead, VIP ,BUYER"), ("Buyer,VIP", "Hot Lead"))
        self.assertEqual(agentbox.split_type_tags(""), ("", ""))

    def test_base64_comment(self):
        self.assertEqual(agentbox.decode_comment(B64), ("Called re: café on Smith St\nkeen buyer", "decoded"))
        self.assertEqual(agentbox.decode_comment("plain old note"), ("plain old note", "plain"))
        self.assertEqual(agentbox.decode_comment(""), ("", "empty"))

    def test_primary_owner_first(self):
        df = pd.read_csv(io.StringIO(CXA), dtype=str)
        self.assertEqual(agentbox.team_member_1(df), {"101": "1", "102": "2"})

    def test_detects_each_export(self):
        for text, kind in ((CONTACT, "contact"), (CATEGORY, "contact_category"), (CXA, "contact_x_agent"),
                           (AGENT, "agent"), ("ID,First Name,Surname\n", None)):
            self.assertEqual(agentbox.kind_of(text.splitlines()[0].split(",")), kind)


class TestSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.s = DarisSession.create(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, *names):
        files = {"contact.csv": CONTACT, "contact_category.csv": CATEGORY, "contact_x_agent.csv": CXA,
                 "agent.csv": AGENT}
        self.s.add_files([(n, io.BytesIO(files[n].encode("utf-8"))) for n in names])

    def test_agentbox_card_uses_fixed_headers_and_links(self):
        self.upload("contact.csv", "contact_category.csv", "contact_x_agent.csv", "agent.csv")
        cards, _issues, _notes = general_flow.review_cards(self.s, "agentbox")
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual((c["profile"], c["file"]), ("agentbox", "contact.csv"))
        self.assertEqual(c["mapping"]["mobile"], "mobile")
        self.assertEqual(c["mapping"]["street"], "street_address")
        self.assertEqual(c["mapping"]["type"], agentbox.TYPE_COL)
        self.assertEqual(c["mapping"]["team1"], agentbox.TEAM_COL)
        self.assertEqual(c["mapping"]["notes"], agentbox.NOTES_COL)
        self.assertTrue(all(link["file"] for link in c["linked"]))
        self.assertEqual(os.listdir(self.s.out_dir), [])

    def test_clean_the_contacts_picks_agentbox(self):
        self.upload("contact.csv", "contact_category.csv")
        evs = list(DarisAssistant(self.s).handle("clean the contacts"))
        cards = [e["card"] for e in evs if e["type"] == "card"]
        self.assertEqual([c["profile"] for c in cards], ["agentbox"])
        text = " ".join(e.get("text", "") for e in evs)
        self.assertIn("Agentbox", text)
        self.assertIn("contact_x_agent.csv isn't uploaded", text)

    def test_general_skips_agentbox_lookup_files(self):
        self.upload("contact.csv", "contact_category.csv", "contact_x_agent.csv", "agent.csv")
        evs = list(DarisAssistant(self.s).handle("general cleaning"))
        cards = [e["card"] for e in evs if e["type"] == "card"]
        self.assertEqual([(c["profile"], c["file"]) for c in cards], [("general", "contact.csv")])

    def test_agentbox_without_contact_csv(self):
        self.upload("agent.csv")
        evs = list(DarisAssistant(self.s).handle("clean agentbox contacts"))
        self.assertFalse([e for e in evs if e["type"] == "card"])
        self.assertIn("contact.csv", " ".join(e.get("text", "") for e in evs))

    def test_run_fills_agentbox_columns_with_tagged_ids(self):
        self.upload("contact.csv", "contact_category.csv", "contact_x_agent.csv", "agent.csv")
        card = general_flow.review_cards(self.s, "agentbox")[0][0]
        evs = list(DarisAssistant(self.s).run_general("contact.csv", card["sheet"], card["mapping"],
                                                      "05112026", False, "agentbox"))
        result = next(e["card"] for e in evs if e["type"] == "card")
        self.assertTrue(result["output_file"].startswith("DARIS_Agentbox_contact_"))
        s = pd.read_excel(os.path.join(self.s.out_dir, result["output_file"]), sheet_name="Summary",
                          dtype=str).fillna("").set_index("CONTACT_IDENTIFIER")
        main = s.loc["101_05112026_c1"]
        self.assertEqual(main["CONTACT_TYPE"], "Buyer,VIP")
        self.assertEqual(main["CONTACT_TAGS"], "Hot Lead")
        self.assertEqual(main["CONTACT_TEAM_MEMBER_1"], "Alex Agent")
        self.assertIn("café on Smith St", main["CONTACT_NOTES"])
        self.assertEqual(main["CONTACT_ENQUIRY_SOURCE"], "Website")
        self.assertEqual(main["CONTACT_NAME_ON_LETTERS"], "Dear Daryl")
        self.assertEqual(main["CONTACT_DATE_CREATED"], "31/01/2024")
        partner = s.loc["101_05112026_c2"]                         # split person, at the bottom
        self.assertEqual((partner["CONTACT_FIRST_NAME"], partner["CONTACT_TYPE"]), ("Kim", "Buyer,VIP"))
        self.assertEqual(list(s.index)[-1], "101_05112026_c2")
        jane = s.loc["102_05112026_c"]
        self.assertEqual((jane["CONTACT_TYPE"], jane["CONTACT_TEAM_MEMBER_1"]), ("Tenant", "Sam Seller"))
        self.assertEqual(jane["CONTACT_NOTES"], "plain old note")
        self.assertEqual(jane["CONTACT_ALLOW_CONTACT_VIA_TEXT"], "FALSE")    # do_not_sms = true
        self.assertEqual(result["stats"]["Agentbox: with type"], 2)


if __name__ == "__main__":
    unittest.main()

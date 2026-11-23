"""General cleaning maps CRMs it has never seen: field-names guide, learned roles, AI fill-in.
Synthetic data, a temporary memory database and a fake AI. Run: python -m unittest tests.test_field_mapping"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daris import general_flow  # noqa: E402
import _offline  # noqa: E402,F401  (no AI in chat tests)
from daris.assistant import DarisAssistant, DarisSession  # noqa: E402
from daris.memory import MappingMemory, RoleMemory  # noqa: E402

# An export from a CRM DARIS has no rules for
CSV = """Client Ref,Given Name,Family Name,Cell,AH Phone,BH Phone,E-mail Address,Partner Given Name,Partner Cell,Residential Address,Town,Post Code,SMS Opt Out,Lead Source,Consultant,Mood,Secret Code
C1,Daryl,Roallos,0412 345 678,07 3333 4444,07 3000 0000,daryl@example.com,Kim,0499 000 111,9 Gavia Court,Rochedale South,4123,Yes,Website,Alex Agent,happy,x1
C2,John,Smith,0400 111 222,,,john@example.com,,,12 Main St,Tooradin,3980,No,Signboard,Sam Seller,calm,x2
"""


class FakeLLM:
    available, protect, label = True, False, "Fake AI"

    def __init__(self, picks):
        self.picks, self.calls = picks, 0

    def safe_samples(self, values):
        return list(values)

    def generate(self, prompt, **kw):
        self.calls += 1
        return {"picks": self.picks}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        db = os.path.join(self.tmp.name, "memory.db")
        self._saved = general_flow.RoleMemory, general_flow.MappingMemory
        general_flow.RoleMemory = lambda: RoleMemory(db)
        general_flow.MappingMemory = lambda: MappingMemory(db)
        self.db = db
        self.s = DarisSession.create(os.path.join(self.tmp.name, "sessions"))
        self.s.add_files([("crmx.csv", io.BytesIO(CSV.encode()))])

    def tearDown(self):
        general_flow.RoleMemory, general_flow.MappingMemory = self._saved
        self.tmp.cleanup()

    def card(self):
        cards, _issues, _notes = general_flow.review_cards(self.s, "general")
        return cards[0]


class TestGuideNames(Base):
    def test_guide_maps_unfamiliar_headers(self):
        m = self.card()["mapping"]
        expect = {"id": "Client Ref", "first": "Given Name", "last": "Family Name", "mobile": "Cell",
                  "home": "AH Phone", "work": "BH Phone", "street": "Residential Address", "suburb": "Town",
                  "postcode": "Post Code", "do_not_sms": "SMS Opt Out", "source": "Lead Source",
                  "team1": "Consultant"}
        for role, col in expect.items():
            self.assertEqual(m[role], col, role)

    def test_editing_the_guide_is_picked_up(self):
        guide = os.path.join(self.tmp.name, "guide.md")
        saved = general_flow.guide_path
        general_flow.guide_path = lambda: guide
        try:
            with open(guide, "w", encoding="utf-8") as f:
                f.write("| `mood` | x | y |\n| `mobile2` | Partner mobile | Other Half Cell · OH mob |\n")
            self.assertEqual(general_flow.guide_hints(), {"mobile2": ["Other Half Cell", "OH mob"]})
            os.utime(guide, (1, 1))                       # a changed file is re-read
            with open(guide, "a", encoding="utf-8") as f:
                f.write("| `fax` | Fax | Facsimile No |\n")
            self.assertIn("fax", general_flow.guide_hints())
        finally:
            general_flow.guide_path = saved
            general_flow._guide_cache.update(mtime=None, hints={})


class TestLearning(Base):
    def test_confirmed_choices_are_learned(self):
        c = self.card()
        self.assertEqual(c["mapping"]["first2"], "")
        mapping = {**c["mapping"], "first2": "Partner Given Name", "mobile2": "Partner Cell"}
        list(DarisAssistant(self.s).run_general("crmx.csv", c["sheet"], mapping, "", True, "general"))
        self.assertEqual(RoleMemory(self.db).lookup_many(["Partner Given Name"]), {"Partner Given Name": "first2"})
        again = self.card()
        self.assertEqual(again["mapping"]["first2"], "Partner Given Name")
        row = next(r for g in again["groups"] for r in g["roles"] if r["role"] == "first2")
        self.assertEqual(row["method"], "learned")

    def test_nothing_learned_without_remember(self):
        c = self.card()
        list(DarisAssistant(self.s).run_general("crmx.csv", c["sheet"], c["mapping"], "", False, "general"))
        self.assertEqual(RoleMemory(self.db).count(), 0)


class TestAIFill(Base):
    def test_ai_fills_empty_roles_and_is_checked(self):
        c = self.card()
        llm = FakeLLM([{"column": "Partner Given Name", "role": "first2"},
                       {"column": "Partner Cell", "role": "mobile2"},
                       {"column": "Mood", "role": "email2"},          # values aren't emails: rejected
                       {"column": "Secret Code", "role": "first2"}])  # role already used: rejected
        filled = general_flow.ai_fill(c, llm, {})
        self.assertEqual(filled, 2)
        self.assertEqual((c["mapping"]["first2"], c["mapping"]["mobile2"]), ("Partner Given Name", "Partner Cell"))
        self.assertEqual(c["mapping"]["email2"], "")
        rows = {r["role"]: r for g in c["groups"] for r in g["roles"]}
        self.assertEqual(rows["first2"]["method"], "ai")
        self.assertNotIn("Partner Cell", c["unused"])
        self.assertIn("marked **AI**", general_flow.describe(c))

    def test_answers_are_cached(self):
        cache, llm = {}, FakeLLM([{"column": "Partner Cell", "role": "mobile2"}])
        general_flow.ai_fill(self.card(), llm, cache)
        general_flow.ai_fill(self.card(), llm, cache)
        self.assertEqual(llm.calls, 1)

    def test_agentbox_cards_are_left_alone(self):
        c = self.card()
        c["profile"] = "agentbox"
        self.assertEqual(general_flow.ai_fill(c, FakeLLM([{"column": "Partner Cell", "role": "mobile2"}]), {}), 0)

    def test_no_ai_no_change(self):
        c = self.card()
        llm = FakeLLM([])
        llm.available = False
        self.assertEqual(general_flow.ai_fill(c, llm, {}), 0)


if __name__ == "__main__":
    unittest.main()

import difflib
import re

# =====================================================================
# ADDRESS / SUBURB SPLITTING  (uses reference/lookups AU suburb list)
# ---------------------------------------------------------------------
# '12 Smith St, Tooradin'          -> line1 '12 Smith St', suburb Tooradin
# '5 Bay Rd Point Cook VIC 3030'   -> line1 '5 Bay Rd',    suburb Point Cook, VIC, 3030
# Careful by design - a wrong split corrupts an address:
#   * the tail must be a real suburb from the list (spelling fixes applied)
#   * AND something must mark where the street ends: a comma, a state or
#     postcode after the suburb, a street type before it ('St', 'Rd', ...),
#     or a PO Box. '12 Ocean Grove' is left alone.
#   * state / postcode come from the address text or the existing columns
#     (fill_from_list=False). The list is used to find and check the suburb;
#     values in a column are never overwritten.
#   * a BLANK state can be filled from the postcode (state_for_postcode, used by
#     GeneralCleaner): suburb list first, the suburb breaks ties, Australia Post
#     ranges as the fallback.
# =====================================================================

STATE_CODES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")
STATE_NAMES = {"NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD",
               "SOUTH AUSTRALIA": "SA", "WESTERN AUSTRALIA": "WA", "TASMANIA": "TAS",
               "NORTHERN TERRITORY": "NT", "AUSTRALIAN CAPITAL TERRITORY": "ACT"}
STREET_TYPES = set("""ST STREET RD ROAD AVE AV AVENUE DR DRIVE CT CRT COURT CRES CRESCENT CR PL PLACE
PDE PARADE TCE TERRACE HWY HIGHWAY LN LANE WAY WY CL CLOSE BVD BLVD BOULEVARD CCT CIRCUIT GR GROVE GVE
SQ SQUARE ESP ESPLANADE ROW RISE LOOP MEWS WALK TRK TRACK TRL TRAIL LINK VISTA VIEW PKWY PARKWAY COVE
GLADE GREEN GRN HTS HEIGHTS HILL BRAE CHASE RIDGE RETREAT RUN STRIP QUAY GDNS GARDENS MALL PROM
PROMENADE ALLEY ARCADE CNR CORNER ENT ENTRANCE FWY FREEWAY BYPASS END GATE GLEN RAMBLE REACH VALE""".split())
_PO_BOX = re.compile(r"\b(?:P\.?\s?O\.?|GPO)\s*BOX\s*\d+$|\bLOCKED\s+BAG\s*\d+$", re.I)
MAX_SUBURB_WORDS = 5
# Australia Post postcode ranges: the fallback when a postcode isn't in the suburb list (e.g. PO Box postcodes)
POSTCODE_RANGES = (("ACT", 200, 299), ("NT", 800, 999), ("NSW", 1000, 2599), ("ACT", 2600, 2618), ("NSW", 2619, 2899),
                   ("ACT", 2900, 2920), ("NSW", 2921, 2999), ("VIC", 3000, 3999), ("QLD", 4000, 4999), ("SA", 5000, 5999),
                   ("WA", 6000, 6797), ("WA", 6800, 6999), ("TAS", 7000, 7999), ("VIC", 8000, 8999), ("QLD", 9000, 9999))
AU_COUNTRY = {"", "AUSTRALIA", "AU", "AUS"}


def range_state(pc4: str) -> str:
    n = int(pc4)
    return next((st for st, lo, hi in POSTCODE_RANGES if lo <= n <= hi), "")


def norm_place(s) -> str:
    s = re.sub(r"[^A-Z0-9 '\-]", " ", str(s).upper())
    return re.sub(r"\s+", " ", s).strip()


class AddressSplitter:
    """suburbs: NORMALISED NAME -> [(Suburb, STATE, postcode)]; spelling: NORMALISED WRONG -> right."""

    def __init__(self, suburbs: dict, spelling: dict = None, fill_from_list: bool = False):
        """fill_from_list=True would also fill a missing state / postcode from the list when
        there is exactly one answer. Off: only values present in the input are used."""
        self.suburbs = suburbs
        self.spelling = spelling or {}
        self.fill_from_list = fill_from_list
        self._keys = list(suburbs)
        self._pc = None                              # postcode -> {states}, built on first use

    def _key(self, name):
        """Normalised suburb key with spelling fixes applied -> (key, fixed_name or None)."""
        key = norm_place(name)
        fixed = self.spelling.get(key)
        return (norm_place(fixed), fixed) if fixed else (key, None)

    def state_for_postcode(self, postcode, suburb=""):
        """A blank state from the postcode -> (state, how, note). how: 'list' (suburb list), 'range'
        (Australia Post ranges, postcode not in the list), 'ambiguous' (several states, the suburb doesn't
        settle it) or '' (not an AU postcode)."""
        s = str(postcode or "").strip()
        if not re.fullmatch(r"\d{3,4}", s):
            return "", "", ""
        pc = s.zfill(4)
        if self._pc is None:
            idx = {}
            for entries in self.suburbs.values():
                for e in entries:
                    idx.setdefault(str(e[2]).zfill(4), set()).add(e[1])
            self._pc = idx
        states = self._pc.get(pc)
        if states:
            if len(states) == 1:
                st = next(iter(states))
                return st, "list", f"state {st} filled from postcode {s}"
            if suburb:
                key, _fixed = self._key(suburb)
                match = {e[1] for e in self.suburbs.get(key, []) if str(e[2]).zfill(4) == pc}
                if len(match) == 1:
                    st = match.pop()
                    return st, "list", f"state {st} filled from postcode {s} and suburb {suburb}"
            return "", "ambiguous", f"postcode {s} is in more than one state ({', '.join(sorted(states))}) - state left blank"
        st = range_state(pc)
        if st:
            return st, "range", f"state {st} filled from postcode {s} (Australia Post postcode range)"
        return "", "", ""

    def _parse(self, line1):
        """Find 'street, SUBURB [STATE] [POSTCODE]' in one address line, or None."""
        tokens = re.findall(r"[^\s,]+|,", re.sub(r"\s+", " ", str(line1)).strip(" ,"))
        state = pc = None
        while tokens and tokens[-1] == ",":
            tokens.pop()
        if tokens and re.fullmatch(r"\d{4}", tokens[-1]):
            pc = tokens.pop()
        while tokens and tokens[-1] == ",":
            tokens.pop()
        if tokens and tokens[-1].upper().strip(".") in STATE_CODES:
            state = tokens.pop().upper().strip(".")
        else:
            for n in (3, 2, 1):
                tail = " ".join(tokens[-n:]).upper()
                if len(tokens) > n and tail in STATE_NAMES:
                    state = STATE_NAMES[tail]
                    del tokens[-n:]
                    break
        while tokens and tokens[-1] == ",":
            tokens.pop()

        for k in range(min(MAX_SUBURB_WORDS, len(tokens) - 1), 0, -1):
            cand = tokens[-k:]
            if "," in cand:
                continue
            key, fixed = self._key(" ".join(cand))
            if key not in self.suburbs:
                continue
            head = tokens[:-k]
            comma = bool(head) and head[-1] == ","
            while head and head[-1] == ",":
                head.pop()
            if not head:
                continue
            street = " ".join(head).replace(" ,", ",")
            last = head[-1].upper().strip(".")
            if comma or state or pc or last in STREET_TYPES or _PO_BOX.search(street):
                return {"street": street, "key": key, "fixed": fixed, "text": " ".join(cand),
                        "state": state, "postcode": pc}
        return None

    def clean(self, line1="", suburb="", state="", postcode=""):
        """-> dict(line1, suburb, state, postcode, notes[list], flags[set])."""
        line1, suburb, state, postcode = (str(v or "").strip() for v in (line1, suburb, state, postcode))
        notes, flags = [], set()

        if suburb:                                   # existing suburb column: spelling fix
            key, fixed = self._key(suburb)
            if fixed:
                notes.append(f"suburb spelling fixed: '{suburb}' -> '{fixed}'")
                suburb = fixed
                flags.add("spelling_fixed")

        parsed = self._parse(line1) if line1 else None
        if parsed:
            # The suburb as written in the address (spelling-fixed if needed), not the list's version
            name = parsed["fixed"] or parsed["text"]
            if not suburb or norm_place(suburb) == parsed["key"]:
                line1 = parsed["street"]
                if not suburb:
                    suburb = name
                if parsed["fixed"]:
                    notes.append(f"suburb spelling fixed to '{parsed['fixed']}'")
                    flags.add("spelling_fixed")
                if not state and parsed["state"]:
                    state = parsed["state"]
                    flags.add("state_from_address")
                if not postcode and parsed["postcode"]:
                    postcode = parsed["postcode"]
                    flags.add("postcode_from_address")
                flags.add("split")
            else:
                notes.append(f"address ends with '{name}' but the suburb column says '{suburb}' - left as is")
                flags.add("conflict")

        if suburb:                                   # check against the list (fill only if enabled)
            entries = self.suburbs.get(norm_place(suburb))
            if not entries:
                close = difflib.get_close_matches(norm_place(suburb), self._keys, n=1, cutoff=0.82)
                hint = f" (did you mean {self.suburbs[close[0]][0][0]}?)" if close else ""
                notes.append(f"suburb '{suburb}' not in the AU suburb list{hint}")
                flags.add("unknown_suburb")
            else:
                st = state.upper().strip(".")
                st = STATE_NAMES.get(st, st)          # 'Victoria' in a state column counts as VIC
                state_ok = [e for e in entries if not st or e[1] == st]
                if st and not state_ok:
                    notes.append(f"'{suburb}' is not in {st} per the suburb list "
                                 f"(found: {', '.join(sorted({e[1] for e in entries}))})")
                    flags.add("state_mismatch")
                elif not state and self.fill_from_list:
                    states = sorted({e[1] for e in entries})
                    if len(states) == 1:
                        state = states[0]
                        flags.add("state_filled")
                    else:
                        notes.append(f"'{suburb}' exists in several states ({', '.join(states)}) - state left blank")
                        flags.add("ambiguous_state")
                if state_ok or not st:
                    want = st or state                # the state given, or the one just filled
                    pcs = sorted({e[2] for e in entries if not want or e[1] == want})
                    if not postcode and len(pcs) == 1 and self.fill_from_list:
                        postcode = pcs[0]
                        flags.add("postcode_filled")
                    elif postcode and pcs and postcode.zfill(4) not in pcs:
                        notes.append(f"postcode {postcode} doesn't match {suburb} {state} "
                                     f"(list: {', '.join(pcs[:4])})")
                        flags.add("postcode_mismatch")
        return {"line1": line1, "suburb": suburb, "state": state, "postcode": postcode,
                "notes": notes, "flags": flags}

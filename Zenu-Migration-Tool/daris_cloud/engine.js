/* =====================================================================
 * DARVIS ENGINE - JavaScript port of DARVIS's Python cleaning engine
 * ---------------------------------------------------------------------
 *   splitNames / ContactDetailsCleaner  <- contact_cleaning.py (the two macros)
 *   AddressSplitter                     <- address_cleaning.py
 *   nameBlocks / GeneralCleaner         <- general_cleaning.py (the General.xlsb tabs)
 *   agentbox.*                          <- agentbox.py (the 4 Agentbox CSVs)
 * Rule-for-rule ports, quirks included; checked cell-by-cell against the
 * Python engine. Runs in the browser (window.DarisEngine) and in Node.
 * ===================================================================== */
(function (root) {
  "use strict";

  // ---------- small helpers that mirror Python string behaviour ----------
  const stripSp = (s) => s.replace(/^ +| +$/g, "");                // str.strip(" ")
  const collapse = (s) => s.replace(/ {2,}/g, " ");
  const xlTrim = (s) => stripSp(collapse(s));                        // WorksheetFunction.Trim
  const digitsOf = (s) => s.replace(/[^0-9]/g, "");
  const append = (t, v, sep) => (t ? t + sep + v : v);
  const str = (v) => (v === null || v === undefined ? "" : String(v));
  const hasOwn = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  const dict = () => Object.create(null);
  const counter = () => new Proxy({}, { get: (o, k) => (k in o ? o[k] : 0) });

  function cell(v) {
    if (v === null || v === undefined || (typeof v === "number" && isNaN(v))) return "";
    const s = String(v);
    return /^-?\d+\.0$/.test(s) ? s.slice(0, -2) : s;
  }

  function normalizeHeader(h) {
    let s = str(h).replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
    return s.replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
  }

  // =====================================================================
  // NAME SPLITTING  (Contact_name_splitting.bas)
  // =====================================================================
  const NAME_TITLES = Object.assign(Object.create(null), { mr: "Mr", ms: "Ms", miss: "Miss", mrs: "Mrs", dr: "Dr" });
  const NAME_PLACEHOLDERS = new Set(["n/a", "na", "none", "-", "--", "+"]);
  const MAX_PEOPLE = 5;
  const NAME_DEFAULTS = { strip_caps_labels: true };

  function cleanName(s) {
    s = str(s).replace(/ /g, " ").replace(/\t/g, " ");
    s = stripSp(collapse(s));
    return NAME_PLACEHOLDERS.has(s.toLowerCase()) ? "" : s;
  }
  function normTitle(s) {
    s = stripSp(s).toLowerCase();
    if (s.endsWith(".")) s = s.slice(0, -1);
    return NAME_TITLES[s] || "";
  }
  function takeTitle(s) {
    if (!s) return [s, ""];
    const sp = s.indexOf(" ");
    const word = sp < 0 ? s : s.slice(0, sp);
    const title = normTitle(word);
    if (!title) return [s, ""];
    return [sp < 0 ? "" : stripSp(s.slice(sp + 1)), title];
  }
  function stripCapsLabel(s) {
    if (!s) return [s, ""];
    const w = s.split(" ");
    let k = w.length;
    for (let i = w.length - 1; i > 0; i--) {
      if (/^[A-Z'\-]{2,}$/.test(w[i])) k = i; else break;
    }
    if (k > w.length - 1) return [s, ""];
    let mixed = false;
    for (let i = 0; i < k; i++) if (w[i] !== w[i].toUpperCase()) mixed = true;
    if (!mixed) return [s, ""];
    return [w.slice(0, k).join(" "), w.slice(k).join(" ")];
  }
  function splitParts(s) {
    if (!s) return [];
    s = (" " + s + " ").replace(/ and /gi, "|");
    for (const sep of [",", "&", "/", "+", ";"]) s = s.split(sep).join("|");
    return s.split("|").map(cleanName).filter(Boolean);
  }

  /** First 'Munny', surname '& Brawna Kumar' -> first 'Munny & Brawna', surname 'Kumar' (shared). Surname '& Stephen'
   *  (one word) -> first 'Finlae & Stephen', no surname. Only when the first name holds one person and the rest one. */
  function partnerInSurname(d, e, notes) {
    const m = d && e ? e.match(/^(?:&|\+|\/|and(?=\s))\s*(.*)$/i) : null;
    if (!m || splitParts(d).length !== 1) return [d, e];
    const rest = cleanName(m[1]);
    if (!rest || splitParts(rest).length !== 1) return [d, e];
    const words = rest.split(" ");
    notes.push("second person's first name was in the surname column");
    if (words.length === 1) return [`${d} & ${rest}`, ""];
    return [`${d} & ${words.slice(0, -1).join(" ")}`, words[words.length - 1]];
  }

  function splitNames(title, first, last, options) {
    const opt = Object.assign({}, NAME_DEFAULTS, options || {});
    const notes = [];
    const c = cleanName(title);
    let d = cleanName(first), e = cleanName(last);
    const titleC = normTitle(c);
    if (c && !titleC) {
      if (!d && /^[A-Za-z'\-]+$/.test(c)) d = c;
      else notes.push(`invalid title '${c}' ignored`);
    }
    if (opt.strip_caps_labels) {
      let label;
      [d, label] = stripCapsLabel(d);
      if (label) notes.push(`label '${label}' removed`);
      [e, label] = stripCapsLabel(e);
      if (label) notes.push(`label '${label}' removed`);
    }
    [d, e] = partnerInSurname(d, e, notes);
    const dp = splitParts(d), ep = splitParts(e);
    const nd = dp.length, ne = ep.length;
    let total = Math.max(nd, ne);
    if (total === 0) {
      if (titleC) notes.push("title only, no name");
      return [[], notes.join("; ")];
    }
    if (total > MAX_PEOPLE) {
      notes.push(`${total} people found, first ${MAX_PEOPLE} kept`);
      total = MAX_PEOPLE;
    }
    const people = [];
    for (let i = 0; i < total; i++) {
      let fn = i < nd ? dp[i] : "";
      let sn = ne === 1 ? ep[0] : i < ne ? ep[i] : "";
      let tt;
      [fn, tt] = takeTitle(fn);
      if (!tt) [sn, tt] = takeTitle(sn);
      if (!sn && fn.includes(" ")) {
        const sp = fn.lastIndexOf(" ");
        sn = fn.slice(sp + 1);
        fn = stripSp(fn.slice(0, sp));
      }
      if (i === 0 && !tt) tt = titleC;
      if (fn || sn) people.push([tt, fn, sn]);
    }
    if (people.length > 1 && ne === 0) notes.push("no surname in raw data");
    return [people, notes.join("; ")];
  }

  // =====================================================================
  // PHONES & EMAILS  (Clean_contactdetails.bas)
  // =====================================================================
  const DETAIL_DEFAULTS = {
    dedupe_in_row: true, multi_sep: " | ", intl_min: 8, intl_max: 15, intl_require_plus: false,
    keep_extras_after_merge: false, merge_extra_emails_to_o: true,
  };
  const SLOTS = ["G", "H", "I", "J", "K", "L", "M", "N"];
  const INVALID_COLS = { MOB: "W", HOME: "X", WORK: "Y", FAX: "Z" };
  const MOBILE_TARGETS = { MOB: [["G", "I", "K", "M"], "M"], HOME: [["G", "I"], "I"], WORK: [["G", "K"], "K"], FAX: [["G", "M"], "M"] };
  const LANDLINE_TARGETS = { MOB: [["J", "L"], "H"], HOME: [["J", "L"], "H"], WORK: [["L", "J"], "H"], FAX: [["N"], "H"] };
  const EMAIL_WRAP = "<>()[]\"'.";

  function isValidEmail(e) {
    if (e.length < 6) return false;
    const at = e.indexOf("@");
    if (at < 1 || e.slice(at + 1).includes("@") || e.includes("..")) return false;
    const local = e.slice(0, at), domain = e.slice(at + 1);
    if (local.startsWith(".") || local.endsWith(".")) return false;
    if (!domain.includes(".") || ".-".includes(domain[0]) || ".-".includes(domain[domain.length - 1])) return false;
    if (!/^[A-Za-z0-9._%+'\-]+$/.test(local) || !/^[A-Za-z0-9.\-]+$/.test(domain)) return false;
    return /^[A-Za-z]{2,}$/.test(domain.slice(domain.lastIndexOf(".") + 1));
  }
  function stripEmailWrap(e) {
    if (e.slice(0, 7).toLowerCase() === "mailto:") e = e.slice(7);
    while (e && EMAIL_WRAP.includes(e[0])) e = e.slice(1);
    while (e && EMAIL_WRAP.includes(e[e.length - 1])) e = e.slice(0, -1);
    return e;
  }
  function stripTextParens(s) {
    let a = s.indexOf("(");
    while (a >= 0) {
      const b = s.indexOf(")", a + 1);
      if (b < 0) break;
      if (/[A-Za-z]/.test(s.slice(a + 1, b))) {
        s = s.slice(0, a) + " " + s.slice(b + 1);
        a = s.indexOf("(", a);
      } else a = s.indexOf("(", b + 1);
    }
    return s;
  }
  function cleanPhoneText(s) {
    let out = "";
    for (const ch of s) {
      if (/^[0-9A-Za-z+]$/.test(ch)) out += ch;
      else if (ch === " " || ch === "\t" || ch === " ") out += " ";
    }
    out = stripSp(collapse(out));
    if (out.startsWith("+ ")) out = "+" + out.slice(2);
    return out;
  }
  function dropLeadingDigits(s, n) {
    let count = 0;
    for (let i = 0; i < s.length; i++) {
      if (s[i] >= "0" && s[i] <= "9" && ++count === n) return stripSp(s.slice(i + 1));
    }
    return "";
  }

  class ContactDetailsCleaner {
    constructor(options) {
      this.opt = Object.assign({}, DETAIL_DEFAULTS, options || {});
      this.count = counter();
    }
    classify(part) {
      const o = this.opt;
      let s = cleanPhoneText(stripTextParens(part));
      const bad = ["I", stripSp(part.replace(/ /g, " ")), "", ""];
      if (!s || s.slice(1).includes("+") || /[A-Za-z]/.test(s)) return bad;
      let hasPlus = s.startsWith("+");
      let d = digitsOf(s);
      if (!d || d === d[0].repeat(d.length)) return bad;
      if (!hasPlus) {
        const pre = d.startsWith("0011") && d.length > 4 ? 4 : d.startsWith("00") && d.length >= 11 ? 2 : 0;
        if (pre) { d = d.slice(pre); s = "+" + dropLeadingDigits(s, pre); hasPlus = true; }
      }
      let au61 = false;
      if (d.startsWith("610") && d.length === 12) { d = "0" + d.slice(3); au61 = true; }
      else if (d.startsWith("61") && d.length === 11) { d = "0" + d.slice(2); au61 = true; }
      else if (!hasPlus && d.length === 9 && "23478".includes(d[0])) d = "0" + d;

      if (d.length === 10 && d.startsWith("04")) {
        const fmt = au61 ? `+61 ${d.slice(1, 4)} ${d.slice(4, 7)} ${d.slice(7, 10)}` : `${d.slice(0, 4)} ${d.slice(4, 7)} ${d.slice(7, 10)}`;
        return ["M", fmt, d, ""];
      }
      if (hasPlus && !au61 && d.startsWith("64") && d.length === 11) return ["M", s, "+" + d, "NZ"];
      if (!hasPlus && !au61 && d.startsWith("02") && (d.length === 9 || d.length === 11 || (d.length === 10 && "012".includes(d[2]))))
        return ["M", s, "+64" + d.slice(1), "NZ"];
      if (d.length === 10 && ["02", "03", "07", "08"].includes(d.slice(0, 2))) {
        const fmt = au61 ? `+61 ${d[1]} ${d.slice(2, 6)} ${d.slice(6, 10)}` : `${d.slice(0, 2)} ${d.slice(2, 6)} ${d.slice(6, 10)}`;
        return ["L", fmt, d, ""];
      }
      if (d.length === 10 && ["1300", "1800"].includes(d.slice(0, 4)) && !au61) return ["L", `${d.slice(0, 4)} ${d.slice(4, 7)} ${d.slice(7, 10)}`, d, ""];
      if (d.length === 6 && d.startsWith("13") && !hasPlus) return ["L", `13 ${d.slice(2, 4)} ${d.slice(4, 6)}`, d, ""];
      if (d.length === 8 && "23456789".includes(d[0]) && !hasPlus && !au61) return ["L", `${d.slice(0, 4)} ${d.slice(4, 8)}`, d, ""];
      if (au61) return bad;
      if (o.intl_min <= d.length && d.length <= o.intl_max && (hasPlus || !o.intl_require_plus)) return ["N", s, hasPlus ? "+" + d : d, "INTL"];
      return bad;
    }
    splitPhones(s) {
      s = s.replace(/ /g, " ").replace(/\t/g, " ");
      s = s.split("\r\n").join(",").split("\r").join(",").split("\n").join(",");
      for (const sep of [";", "/", "&", "|"]) s = s.split(sep).join(",");
      s = s.replace(/ and /gi, ",").replace(/ or /gi, ",");
      if (digitsOf(s).length > this.opt.intl_max) s = s.split("-").join(",");
      return s.split(",");
    }
    extractEmails(raw, found, dropped) {
      if (!raw.includes("@")) return raw;
      let s = raw;
      for (const p of ["\r\n", "\r", "\n", "\t", " ", ",", ";", "/", "|", "&"]) s = s.split(p).join(" ");
      for (const token of s.split(" ")) {
        if (token.includes("@")) {
          const e = stripEmailWrap(stripSp(token));
          if (isValidEmail(e)) {
            found.push(e);
            raw = raw.split(token).join(" ");
            this.count.emails_moved_from_phones += 1;
          }
        }
      }
      raw = stripSp(raw);
      if (!digitsOf(raw)) {
        if (raw && dropped) { dropped.push(raw); this.count.text_dropped_from_phones += 1; }
        return "";
      }
      return raw;
    }
    place(slots, targets, overflow, value) {
      for (const t of targets) if (!slots[t]) { slots[t] = value; return; }
      slots[overflow] = append(slots[overflow], value, this.opt.multi_sep);
    }
    process(raw, src, onlyKind, slots, invalid, seen) {
      if (!raw) return;
      const sep = this.opt.multi_sep;
      for (const part of this.splitPhones(raw)) {
        if (!stripSp(part)) continue;
        let [kind, fmt, key, tag] = this.classify(part);
        if (kind === "N") kind = src === "MOB" ? "M" : "L";
        if (kind === "I") {
          if (onlyKind !== "L") {
            invalid[INVALID_COLS[src]] = append(invalid[INVALID_COLS[src]], fmt, sep);
            this.count.invalid_numbers += 1;
          }
          continue;
        }
        if (onlyKind && kind !== onlyKind) continue;
        if (this.opt.dedupe_in_row && seen.has(key)) { this.count.duplicates_skipped += 1; continue; }
        seen.add(key);
        this.count.numbers_placed += 1;
        if (tag === "NZ") this.count.nz_mobiles += 1;
        else if (tag === "INTL") this.count.international += 1;
        const [targets, overflow] = (kind === "M" ? MOBILE_TARGETS : LANDLINE_TARGETS)[src];
        this.place(slots, targets, overflow, fmt);
      }
    }
    mergeExtras(slots) {
      const sep = this.opt.multi_sep, keep = this.opt.keep_extras_after_merge;
      for (const idx of ["I", "K", "M"]) {
        if (slots[idx]) { slots.G = append(slots.G, slots[idx], sep); if (!keep) slots[idx] = ""; }
      }
      if (slots.H) { slots.J = append(slots.J, slots.H, sep); if (!keep) slots.H = ""; }
    }
    emails(raw) {
      let primary = "", extra = "";
      const seen = new Set(), sep = this.opt.multi_sep;
      if (!raw) return [primary, extra];
      let s = raw.replace(/ /g, " ");
      s = s.split("\r\n").join(",").split("\r").join(",").split("\n").join(",");
      for (const p of [";", " ", "/", "|", "\t"]) s = s.split(p).join(",");
      for (const token of s.split(",")) {
        const e = stripEmailWrap(stripSp(token));
        if (!e) continue;
        if (seen.has(e.toLowerCase())) { this.count.emails_duplicate_skipped += 1; continue; }
        seen.add(e.toLowerCase());
        if (isValidEmail(e) && (!primary || this.opt.merge_extra_emails_to_o)) {
          primary = append(primary, e, sep); this.count.emails_valid += 1;
        } else { extra = append(extra, e, sep); this.count.emails_invalid_or_extra += 1; }
      }
      return [primary, extra];
    }
    phoneParts(raw) {
      return raw ? this.splitPhones(raw).filter((p) => stripSp(p)).length : 0;
    }
    clean(email = "", mobile = "", work = "", home = "", fax = "") {
      const text = (v) => stripSp(str(v));
      const found = [], dropped = [];
      const rawMob = this.extractEmails(text(mobile), found, dropped);
      const rawHome = this.extractEmails(text(home), found, dropped);
      const rawWork = this.extractEmails(text(work), found, dropped);
      const rawFax = this.extractEmails(text(fax), found, dropped);
      this.count.phone_parts_in += [rawMob, rawHome, rawWork, rawFax].reduce((a, r) => a + this.phoneParts(r), 0);
      const slots = {}; SLOTS.forEach((k) => (slots[k] = ""));
      const invalid = { W: "", X: "", Y: "", Z: "" };
      const seen = new Set();
      this.process(rawMob, "MOB", "M", slots, invalid, seen);
      this.process(rawHome, "HOME", "", slots, invalid, seen);
      this.process(rawWork, "WORK", "", slots, invalid, seen);
      this.process(rawFax, "FAX", "", slots, invalid, seen);
      this.process(rawMob, "MOB", "L", slots, invalid, seen);
      this.mergeExtras(slots);
      const [primary, extra] = this.emails(text(email) + "," + found.join(","));
      this.count.rows += 1;
      return Object.assign({}, slots, { O: primary, U: extra }, invalid, { dropped });
    }
    reconciliation() {
      const c = this.count;
      const accounted = c.numbers_placed + c.duplicates_skipped + c.invalid_numbers;
      return { phone_parts_in: c.phone_parts_in, phone_parts_accounted: accounted, phones_ok: c.phone_parts_in === accounted };
    }
  }

  // =====================================================================
  // ADDRESS / SUBURB SPLITTING  (address_cleaning.py)
  // =====================================================================
  const STATE_CODES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"];
  const STATE_NAMES = Object.assign(Object.create(null), { "NEW SOUTH WALES": "NSW", VICTORIA: "VIC", QUEENSLAND: "QLD", "SOUTH AUSTRALIA": "SA",
    "WESTERN AUSTRALIA": "WA", TASMANIA: "TAS", "NORTHERN TERRITORY": "NT", "AUSTRALIAN CAPITAL TERRITORY": "ACT" });
  const STREET_TYPES = new Set(("ST STREET RD ROAD AVE AV AVENUE DR DRIVE CT CRT COURT CRES CRESCENT CR PL PLACE " +
    "PDE PARADE TCE TERRACE HWY HIGHWAY LN LANE WAY WY CL CLOSE BVD BLVD BOULEVARD CCT CIRCUIT GR GROVE GVE " +
    "SQ SQUARE ESP ESPLANADE ROW RISE LOOP MEWS WALK TRK TRACK TRL TRAIL LINK VISTA VIEW PKWY PARKWAY COVE " +
    "GLADE GREEN GRN HTS HEIGHTS HILL BRAE CHASE RIDGE RETREAT RUN STRIP QUAY GDNS GARDENS MALL PROM " +
    "PROMENADE ALLEY ARCADE CNR CORNER ENT ENTRANCE FWY FREEWAY BYPASS END GATE GLEN RAMBLE REACH VALE").split(" "));
  const PO_BOX = /\b(?:P\.?\s?O\.?|GPO)\s*BOX\s*\d+$|\bLOCKED\s+BAG\s*\d+$/i;
  const MAX_SUBURB_WORDS = 5;
  const stripDots = (s) => s.replace(/^\.+|\.+$/g, "");

  function normPlace(s) {
    return str(s).toUpperCase().replace(/[^A-Z0-9 '\-]/g, " ").replace(/\s+/g, " ").trim();
  }

  // difflib.get_close_matches(word, keys, n=1, cutoff) - used only for a "did you mean" hint
  function seqRatio(a, b) {
    const b2j = new Map();
    for (let j = 0; j < b.length; j++) { if (!b2j.has(b[j])) b2j.set(b[j], []); b2j.get(b[j]).push(j); }
    let matched = 0;
    const queue = [[0, a.length, 0, b.length]];
    while (queue.length) {
      const [alo, ahi, blo, bhi] = queue.pop();
      let besti = alo, bestj = blo, bestsize = 0, j2len = new Map();
      for (let i = alo; i < ahi; i++) {
        const next = new Map();
        for (const j of b2j.get(a[i]) || []) {
          if (j < blo) continue;
          if (j >= bhi) break;
          const k = (j2len.get(j - 1) || 0) + 1;
          next.set(j, k);
          if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
        }
        j2len = next;
      }
      if (bestsize) {
        matched += bestsize;
        if (alo < besti && blo < bestj) queue.push([alo, besti, blo, bestj]);
        if (besti + bestsize < ahi && bestj + bestsize < bhi) queue.push([besti + bestsize, ahi, bestj + bestsize, bhi]);
      }
    }
    const t = a.length + b.length;
    return t ? (2 * matched) / t : 1;
  }
  function quickRatio(a, b) {
    const avail = new Map();
    for (const ch of b) avail.set(ch, (avail.get(ch) || 0) + 1);
    let m = 0;
    for (const ch of a) { const n = avail.get(ch) || 0; if (n > 0) { m++; avail.set(ch, n - 1); } }
    return (2 * m) / (a.length + b.length || 1);
  }
  function closeMatch(word, keys, cutoff) {
    let best = null;
    for (const x of keys) {
      const la = x.length, lb = word.length;
      if ((2 * Math.min(la, lb)) / (la + lb || 1) < cutoff) continue;
      if (quickRatio(x, word) < cutoff) continue;
      const r = seqRatio(x, word);
      if (r >= cutoff && (!best || r > best[0] || (r === best[0] && x > best[1]))) best = [r, x];
    }
    return best ? best[1] : null;
  }

  // Australia Post postcode ranges: the fallback when a postcode isn't in the suburb list (e.g. PO Box postcodes)
  const POSTCODE_RANGES = [["ACT", 200, 299], ["NT", 800, 999], ["NSW", 1000, 2599], ["ACT", 2600, 2618], ["NSW", 2619, 2899],
    ["ACT", 2900, 2920], ["NSW", 2921, 2999], ["VIC", 3000, 3999], ["QLD", 4000, 4999], ["SA", 5000, 5999],
    ["WA", 6000, 6797], ["WA", 6800, 6999], ["TAS", 7000, 7999], ["VIC", 8000, 8999], ["QLD", 9000, 9999]];
  const rangeState = (pc4) => { const n = +pc4; const r = POSTCODE_RANGES.find(([, lo, hi]) => lo <= n && n <= hi); return r ? r[0] : ""; };

  class AddressSplitter {
    constructor(suburbs, spelling, fillFromList = false) {
      this.suburbs = Object.assign(dict(), suburbs || {});
      this.spelling = Object.assign(dict(), spelling || {});
      this.fillFromList = fillFromList;
      this.keys = Object.keys(this.suburbs);
      this.closeCache = new Map();                     // unknown suburb -> "did you mean" key
      this.pcIndex = null;                             // postcode -> Set(states), built on first use
    }
    /** A blank state from the postcode -> [state, how, note]; how: list | range | ambiguous | "" (see address_cleaning.py). */
    stateForPostcode(postcode, suburb = "") {
      const s = str(postcode).trim();
      if (!/^\d{3,4}$/.test(s)) return ["", "", ""];
      const pc = s.padStart(4, "0");
      if (!this.pcIndex) {
        this.pcIndex = new Map();
        for (const entries of Object.values(this.suburbs)) for (const e of entries) {
          const k = String(e[2]).padStart(4, "0");
          if (!this.pcIndex.has(k)) this.pcIndex.set(k, new Set());
          this.pcIndex.get(k).add(e[1]);
        }
      }
      const states = this.pcIndex.get(pc);
      if (states) {
        if (states.size === 1) { const st = [...states][0]; return [st, "list", `state ${st} filled from postcode ${s}`]; }
        if (suburb) {
          const [key] = this.key(suburb);
          const match = new Set((this.suburbs[key] || []).filter((e) => String(e[2]).padStart(4, "0") === pc).map((e) => e[1]));
          if (match.size === 1) { const st = [...match][0]; return [st, "list", `state ${st} filled from postcode ${s} and suburb ${suburb}`]; }
        }
        return ["", "ambiguous", `postcode ${s} is in more than one state (${[...states].sort().join(", ")}) - state left blank`];
      }
      const st = rangeState(pc);
      return st ? [st, "range", `state ${st} filled from postcode ${s} (Australia Post postcode range)`] : ["", "", ""];
    }
    key(name) {
      const k = normPlace(name);
      const fixed = this.spelling[k];
      return fixed ? [normPlace(fixed), fixed] : [k, null];
    }
    parse(line1) {
      const tokens = str(line1).replace(/\s+/g, " ").trim().replace(/^[ ,]+|[ ,]+$/g, "").match(/[^\s,]+|,/g) || [];
      let state = null, pc = null;
      const popCommas = () => { while (tokens.length && tokens[tokens.length - 1] === ",") tokens.pop(); };
      popCommas();
      if (tokens.length && /^\d{4}$/.test(tokens[tokens.length - 1])) pc = tokens.pop();
      popCommas();
      if (tokens.length && STATE_CODES.includes(stripDots(tokens[tokens.length - 1].toUpperCase()))) {
        state = stripDots(tokens.pop().toUpperCase());
      } else {
        for (const n of [3, 2, 1]) {
          const tail = tokens.slice(-n).join(" ").toUpperCase();
          if (tokens.length > n && STATE_NAMES[tail]) { state = STATE_NAMES[tail]; tokens.splice(tokens.length - n, n); break; }
        }
      }
      popCommas();
      for (let k = Math.min(MAX_SUBURB_WORDS, tokens.length - 1); k > 0; k--) {
        const cand = tokens.slice(-k);
        if (cand.includes(",")) continue;
        const [key, fixed] = this.key(cand.join(" "));
        if (!(key in this.suburbs)) continue;
        const head = tokens.slice(0, tokens.length - k);
        const comma = head.length > 0 && head[head.length - 1] === ",";
        while (head.length && head[head.length - 1] === ",") head.pop();
        if (!head.length) continue;
        const street = head.join(" ").split(" ,").join(",");
        const last = stripDots(head[head.length - 1].toUpperCase());
        if (comma || state || pc || STREET_TYPES.has(last) || PO_BOX.test(street))
          return { street, key, fixed, text: cand.join(" "), state, postcode: pc };
      }
      return null;
    }
    clean(line1 = "", suburb = "", state = "", postcode = "") {
      [line1, suburb, state, postcode] = [line1, suburb, state, postcode].map((v) => str(v).trim());
      const notes = [], flags = new Set();
      if (suburb) {
        const [, fixed] = this.key(suburb);
        if (fixed) { notes.push(`suburb spelling fixed: '${suburb}' -> '${fixed}'`); suburb = fixed; flags.add("spelling_fixed"); }
      }
      const parsed = line1 ? this.parse(line1) : null;
      if (parsed) {
        const name = parsed.fixed || parsed.text;
        if (!suburb || normPlace(suburb) === parsed.key) {
          line1 = parsed.street;
          if (!suburb) suburb = name;
          if (parsed.fixed) { notes.push(`suburb spelling fixed to '${parsed.fixed}'`); flags.add("spelling_fixed"); }
          if (!state && parsed.state) { state = parsed.state; flags.add("state_from_address"); }
          if (!postcode && parsed.postcode) { postcode = parsed.postcode; flags.add("postcode_from_address"); }
          flags.add("split");
        } else {
          notes.push(`address ends with '${name}' but the suburb column says '${suburb}' - left as is`);
          flags.add("conflict");
        }
      }
      if (suburb) {
        const entries = this.suburbs[normPlace(suburb)];
        if (!entries) {
          const ns = normPlace(suburb);
          if (!this.closeCache.has(ns)) this.closeCache.set(ns, closeMatch(ns, this.keys, 0.82));
          const close = this.closeCache.get(ns);
          const hint = close ? ` (did you mean ${this.suburbs[close][0][0]}?)` : "";
          notes.push(`suburb '${suburb}' not in the AU suburb list${hint}`);
          flags.add("unknown_suburb");
        } else {
          let st = stripDots(state.toUpperCase());
          st = STATE_NAMES[st] || st;
          const stateOk = entries.filter((e) => !st || e[1] === st);
          if (st && !stateOk.length) {
            notes.push(`'${suburb}' is not in ${st} per the suburb list (found: ${[...new Set(entries.map((e) => e[1]))].sort().join(", ")})`);
            flags.add("state_mismatch");
          } else if (!state && this.fillFromList) {
            const states = [...new Set(entries.map((e) => e[1]))].sort();
            if (states.length === 1) { state = states[0]; flags.add("state_filled"); }
            else { notes.push(`'${suburb}' exists in several states (${states.join(", ")}) - state left blank`); flags.add("ambiguous_state"); }
          }
          if (stateOk.length || !st) {
            const want = st || state;
            const pcs = [...new Set(entries.filter((e) => !want || e[1] === want).map((e) => e[2]))].sort();
            if (!postcode && pcs.length === 1 && this.fillFromList) { postcode = pcs[0]; flags.add("postcode_filled"); }
            else if (postcode && pcs.length && !pcs.includes(postcode.padStart(4, "0"))) {
              notes.push(`postcode ${postcode} doesn't match ${suburb} ${state} (list: ${pcs.slice(0, 4).join(", ")})`);
              flags.add("postcode_mismatch");
            }
          }
        }
      }
      return { line1, suburb, state, postcode, notes, flags };
    }
  }

  // =====================================================================
  // GENERAL CLEANING  (general_cleaning.py - the General.xlsb tabs)
  // =====================================================================
  const ROLES = {
    id: ["Contact ID", "contact_identifier"], spouse_id: ["Spouse / partner's contact ID", null], company: ["Company name", "contact_company_name"],
    title: ["Title", "contact_title"], first: ["First name", "contact_first_name"], last: ["Last name", "contact_surname"],
    title2: ["Second person - title", null], first2: ["Second person - first name", null], last2: ["Second person - last name", null],
    email: ["Email", "contact_email_address"], mobile: ["Mobile", "contact_mobile"], work: ["Work phone", "contact_phone_work"],
    home: ["Home phone", "contact_phone_home"], fax: ["Fax", "contact_fax"], mobile2: ["Second person - mobile", null],
    email2: ["Second person - email", null], street: ["Address line 1 / street", "contact_mailing_address_line1"],
    street2: ["Address line 2", "contact_mailing_address_line2"], suburb: ["Suburb", "contact_mailing_address_suburb"],
    postcode: ["Postcode", "contact_mailing_address_postcode"], state: ["State", "contact_mailing_address_state"],
    country: ["Country", "contact_mailing_address_country"], salutation: ["Name on letters / salutation", "contact_name_on_letters"],
    legal: ["Legal name", "contact_legal_name"], position: ["Company position / job title", "contact_company_position"],
    type: ["Contact type", "contact_type"], tags: ["Contact tags", "contact_tags"], source: ["Enquiry source", "contact_enquiry_source"],
    notes: ["Notes / comments", "contact_notes"], team1: ["Team member 1", "contact_team_member_1"],
    created: ["Date created", "contact_date_created"], modified: ["Date modified", "contact_date_modified"],
    do_not_sms: ["Text / SMS permission", null], do_not_email: ["Email permission", null],
    do_not_mail: ["Letter / mail permission", null], do_not_call: ["Phone permission", null],
    subscribed: ["Newsletter / marketing (unsubscribe)", null], contactable: ["Contactable (fills blank permissions)", null],
    // the rest of the Zenu contact fields: carried across as they are (trimmed; dates as dd/mm/yyyy)
    zenu_id: ["Zenu contact ID (existing Zenu record)", "zenu_contact_id"],
    subscriptions: ["Subscriptions", "contact_subscriptions"],
    town_city: ["Town / city", "contact_mailing_address_town_city"],
    children: ["Children's names", "contact_childrens_names"],
    rating: ["Rating", "contact_rating"],
    last_contact: ["Last contact date", "contact_last_contact_date"],
    team2: ["Team member 2", "contact_team_member_2"],
    team3: ["Team member 3", "contact_team_member_3"],
    team4: ["Team member 4", "contact_team_member_4"],
    team5: ["Team member 5", "contact_team_member_5"],
    team6: ["Team member 6", "contact_team_member_6"],
    team7: ["Team member 7", "contact_team_member_7"],
    team8: ["Team member 8", "contact_team_member_8"],
    team9: ["Team member 9", "contact_team_member_9"],
    team10: ["Team member 10", "contact_team_member_10"],
    team11: ["Team member 11", "contact_team_member_11"],
    team12: ["Team member 12", "contact_team_member_12"],
    team13: ["Team member 13", "contact_team_member_13"],
    team14: ["Team member 14", "contact_team_member_14"],
    team15: ["Team member 15", "contact_team_member_15"],
    sale_type: ["Sale type", "contact_sale_type"],
    solicitor_id: ["Zenu solicitor ID", "zenu_solicitor_id"],
    company_id: ["Zenu company ID", "zenu_company_id"],
  };
  const ROLE_HINTS = {
    title2: ["title2", "title 2", "partner title", "second title"],
    first2: ["firstname2", "first name 2", "first2", "partner first name", "second first name"],
    last2: ["surname2", "lastname2", "last name 2", "surname 2", "partner surname", "partner last name"],
    mobile2: ["mobile2", "mobile 2", "partner mobile", "second mobile"],
    email2: ["email2", "email 2", "partner email", "second email"],
    do_not_sms: ["do not sms", "donotsms", "no sms"], do_not_email: ["do not email", "donotemail", "no email"],
    do_not_mail: ["do not mail", "donotmail", "no mail"], do_not_call: ["do not call", "donotcall", "no call"],
    subscribed: ["subscribed newsletters", "subscribed newsletter", "newsletter"],
  };
  // the Zenu contact fields in import order, between the workbook's helper columns
  const SUMMARY_COLUMNS = [
    "Indicator",
    "Contact Count",
    "Raw ORIG CONTACT_IDENTIFIER",
    "CONTACT_IDENTIFIER",
    "ZENU_CONTACT_ID",
    "CONTACT_TITLE",
    "CONTACT_FIRST_NAME",
    "CONTACT_SURNAME",
    "CONTACT_IS_COMPANY",
    "CONTACT_COMPANY_NAME",
    "CONTACT_NAME_ON_LETTERS",
    "CONTACT_LEGAL_NAME",
    "CONTACT_COMPANY_POSITION",
    "CONTACT_TYPE",
    "CONTACT_TAGS",
    "CONTACT_SUBSCRIPTIONS",
    "CONTACT_MAILING_ADDRESS_LINE1",
    "CONTACT_MAILING_ADDRESS_LINE2",
    "CONTACT_MAILING_ADDRESS_SUBURB",
    "CONTACT_MAILING_ADDRESS_TOWN_CITY",
    "CONTACT_MAILING_ADDRESS_STATE",
    "CONTACT_MAILING_ADDRESS_COUNTRY",
    "CONTACT_MAILING_ADDRESS_POSTCODE",
    "CONTACT_EMAIL_ADDRESS",
    "CONTACT_MOBILE",
    "CONTACT_PHONE_WORK",
    "CONTACT_PHONE_HOME",
    "CONTACT_FAX",
    "CONTACT_ALLOW_CONTACT_VIA_TEXT",
    "CONTACT_ALLOW_CONTACT_VIA_EMAIL",
    "CONTACT_ALLOW_CONTACT_VIA_LETTER",
    "CONTACT_ALLOW_CONTACT_VIA_PHONE",
    "CONTACT_UNSUBSCRIBE_FROM_MARKETING",
    "CONTACT_CHILDRENS_NAMES",
    "CONTACT_ENQUIRY_SOURCE",
    "CONTACT_RATING",
    "CONTACT_PARTNER_IDENTIFIER",
    "CONTACT_PARTNERSHIP_ID",
    "CONTACT_PARTNERSHIP_TYPE",
    "CONTACT_LAST_CONTACT_DATE",
    "CONTACT_DATE_CREATED",
    "CONTACT_DATE_MODIFIED",
    "CONTACT_TEAM_MEMBER_1",
    "CONTACT_TEAM_MEMBER_2",
    "CONTACT_TEAM_MEMBER_3",
    "CONTACT_TEAM_MEMBER_4",
    "CONTACT_TEAM_MEMBER_5",
    "CONTACT_TEAM_MEMBER_6",
    "CONTACT_TEAM_MEMBER_7",
    "CONTACT_TEAM_MEMBER_8",
    "CONTACT_TEAM_MEMBER_9",
    "CONTACT_TEAM_MEMBER_10",
    "CONTACT_TEAM_MEMBER_11",
    "CONTACT_TEAM_MEMBER_12",
    "CONTACT_TEAM_MEMBER_13",
    "CONTACT_TEAM_MEMBER_14",
    "CONTACT_TEAM_MEMBER_15",
    "CONTACT_SALE_TYPE",
    "ZENU_SOLICITOR_ID",
    "ZENU_COMPANY_ID",
    "CONTACT_NOTES",
    "Contact Name",
  ];
  const TRUE_WORDS = new Set(["true", "1", "yes", "y"]);
  const FALSE_WORDS = new Set(["false", "0", "no", "n"]);

  function excelTrimClean(v) {
    let s = str(v).split("\r\n").join(" ").split("\n").join(" ").split("\r").join(" ");
    s = s.replace(/[\x00-\x1f]/g, "");
    return stripSp(collapse(s));
  }
  function relationship(a, b) {
    a = a.toLowerCase(); b = b.toLowerCase();
    if (a === "mr" && b === "mrs") return "Husband";
    if (a === "mrs" && b === "mr") return "Wife";
    return "Partner";
  }
  function contactNo(id) {
    const p = id.lastIndexOf("_c");
    if (p < 0) return 1;
    const tail = id.slice(p + 2);
    return /^\d+$/.test(tail) ? parseInt(tail, 10) : 1;
  }
  const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, sept: 9, oct: 10, nov: 11, dec: 12 };
  const pad2 = (n) => String(n).padStart(2, "0");
  function validDate(y, m, d) {
    const t = new Date(Date.UTC(y, m - 1, d));
    return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
  }
  /** Zenu wants DD/MM/YYYY with no time. Unparseable text is kept as it is. */
  // ---- dates: the same reader as general_cleaning.py ----
  const DATE_MODES = { date: "Date only (dd/mm/yyyy)", datetime: "Date and time (dd/mm/yyyy hh:mm)", datetime_s: "Date and time with seconds (dd/mm/yyyy hh:mm:ss)" };
  const DATE_ROLES = ["created", "modified", "last_contact"];
  /** {y, m, d, H, M, S} or null. ISO, dd/mm/yyyy (+ time, AM/PM), 16 Jul 2024, Jul 16 2024 and ODBC stamps like {ts '2024-07-16 09:42:03'}. */
  function parseDate(v) {
    let s = String(v ?? "").trim();
    const o = s.match(/^\{\s*(?:ts|d|t)\s*'([^']*)'\s*\}$/i);
    if (o) s = o[1].trim();
    let y, mo, d, t = [], m;
    if ((m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*([AP]M)?(?:Z|[+-]\d{2}:?\d{2})?)?$/i))) { y = +m[1]; mo = +m[2]; d = +m[3]; t = m.slice(4, 8); }
    else if ((m = s.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s?([AP]M))?)?$/i))) {
      y = +m[3]; if (m[3].length === 2) y += y < 69 ? 2000 : 1900; mo = +m[2]; d = +m[1]; t = m.slice(4, 8);
    }
    else if ((m = s.match(/^(\d{1,2})[ -]([A-Za-z]{3,9})\.?[ -]+(\d{4})$/)) && hasOwn(MONTHS, m[2].slice(0, 3).toLowerCase())) { y = +m[3]; mo = MONTHS[m[2].slice(0, 3).toLowerCase()]; d = +m[1]; }
    else if ((m = s.match(/^([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})$/)) && hasOwn(MONTHS, m[1].slice(0, 3).toLowerCase())) { y = +m[3]; mo = MONTHS[m[1].slice(0, 3).toLowerCase()]; d = +m[2]; }
    else return null;
    if (!validDate(y, mo, d)) return null;
    let h = +(t[0] || 0), mi = +(t[1] || 0), sec = +(t[2] || 0);
    const ap = (t[3] || "").toUpperCase();
    if (ap === "PM" && h < 12) h += 12;
    if (ap === "AM" && h === 12) h = 0;
    if (h > 23 || mi > 59 || sec > 59) return null;
    return { y, m: mo, d, H: h, M: mi, S: sec };
  }
  /** A date for Zenu: dd/mm/yyyy, or with the time (00:00 when the source has none). Text that isn't a date is kept. */
  function formatDate(v, mode = "date") {
    const s = String(v ?? "").trim();
    if (!s) return "";
    if (!hasOwn(DATE_MODES, mode)) mode = "date";
    if (mode === "date" && /^\d{1,2}\/\d{1,2}\/\d{4}$/.test(s)) return s;
    const p = parseDate(s);
    if (!p) return s;
    let out = `${pad2(p.d)}/${pad2(p.m)}/${p.y}`;
    if (mode === "datetime") out += ` ${pad2(p.H)}:${pad2(p.M)}`;
    else if (mode === "datetime_s") out += ` ${pad2(p.H)}:${pad2(p.M)}:${pad2(p.S)}`;
    return out;
  }
  const dateDdmmyyyy = (v) => formatDate(v, "date");

  // ---- permissions: what the column's values mean ----
  const FLAG_ROLES = ["do_not_sms", "do_not_email", "do_not_mail", "do_not_call", "subscribed"];
  const FLAG_OUT = { do_not_sms: "CONTACT_ALLOW_CONTACT_VIA_TEXT", do_not_email: "CONTACT_ALLOW_CONTACT_VIA_EMAIL",
    do_not_mail: "CONTACT_ALLOW_CONTACT_VIA_LETTER", do_not_call: "CONTACT_ALLOW_CONTACT_VIA_PHONE", subscribed: "CONTACT_UNSUBSCRIBE_FROM_MARKETING" };
  const FLAG_MEANINGS = ["allowed", "blocked", "filled_blocked", "filled_allowed"];
  const FLAG_BLANKS = ["no", "yes", "empty"];
  const legacyFlagMode = (role) => ({ meaning: role === "subscribed" ? "allowed" : "blocked", blank: "no" });
  function suggestFlagMode(role, header, samples = []) {
    const h = String(header || "").replace(/[_\-.]+/g, " ");
    const neg = /(do\s*not|don'?t|opt\s*out|optout|unsub|block|exclude|suppress|\bno\s*(sms|text|e?mail|post|letter|call|phone)\b|\bdnc\b)/i.test(h);
    const vals = samples.filter(Boolean);
    const dateish = /(date|time|when|\bat$|\bon$)/i.test(h) || (vals.length > 0 && vals.every((s) => /^(\{\s*ts|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})/i.test(String(s))));
    return { meaning: dateish ? (neg ? "filled_blocked" : "filled_allowed") : (neg ? "blocked" : "allowed"), blank: "no" };
  }
  function cleanFlagMode(mode, role) {
    const base = legacyFlagMode(role), m = mode && typeof mode === "object" ? mode : {};
    return { meaning: FLAG_MEANINGS.includes(m.meaning) ? m.meaning : base.meaning, blank: FLAG_BLANKS.includes(m.blank) ? m.blank : base.blank };
  }
  /** -> [true | false | null (leave empty), recognised?] */
  function flagAllowed(value, mode) {
    const v = String(value ?? "").trim().toLowerCase();
    if (mode.meaning === "filled_blocked") return [!v, true];
    if (mode.meaning === "filled_allowed") return [!!v, true];
    if (!v) return [mode.blank === "yes" ? true : mode.blank === "empty" ? null : false, true];
    if (TRUE_WORDS.has(v)) return [mode.meaning === "allowed", true];
    if (FALSE_WORDS.has(v)) return [mode.meaning !== "allowed", true];
    return [false, false];
  }
  function flagModeText(role, mode) {
    const sub = role === "subscribed", [yes, no] = sub ? ["subscribed", "unsubscribed"] : ["allowed", "do not contact"];
    let text = { allowed: `1 / Yes = ${yes}`, blocked: `1 / Yes = ${no}`, filled_blocked: `filled in = ${no}`, filled_allowed: `filled in = ${yes}` }[mode.meaning];
    if (mode.meaning === "allowed" || mode.meaning === "blocked")
      text += ", empty = " + { no: sub ? "unsubscribed" : "not allowed", yes: sub ? "subscribed" : "allowed", empty: "left empty" }[mode.blank];
    return text;
  }
  const isContactable = (v) => TRUE_WORDS.has(String(v ?? "").trim().toLowerCase());
  const flagText = (role, allowed) => (allowed === null ? "" : role === "subscribed" ? (allowed ? "FALSE" : "TRUE") : (allowed ? "TRUE" : "FALSE"));

  function nameBlocks(rawId, title, first, last, title2 = "", first2 = "", last2 = "", nameOptions) {
    const [people, note] = splitNames(title, first, last, nameOptions);
    const names = people.concat(Array(5).fill(["", "", ""])).slice(0, 5);
    let second = [], note2 = "";
    if (title2 || first2 || last2) [second, note2] = splitNames(title2, first2, last2, nameOptions);
    const seconds = second.concat(Array(2).fill(["", "", ""])).slice(0, 2);
    const has = names.map(([, f, s]) => !!(f || s));
    const hasSn = seconds.map(([, f, s]) => !!(f || s));
    const a = rawId;
    const iId = has[1] || hasSn[0] ? `${a}_c1` : `${a}_c`;
    const ids = [iId].concat([1, 2, 3, 4].map((k) => (has[k] ? `${a}_c${k + 1}` : "")));
    const h = has.slice(0, 4).filter(Boolean).length;
    const ax = !hasSn[0] ? "" : !has[1] ? `${a}_c2` : `${a}_c${h + 1}`;
    const be = !hasSn[1] ? "" : ax ? `${a}_c${parseInt(ax[ax.length - 1], 10) + 1}` : "";
    const pId = ids[1];
    const joined = !pId && !ax ? "" : `${iId}_` + [ids[1], ids[2], ids[3], ids[4], ax, be].filter(Boolean).map((x) => x + "_").join("") + "r";
    const blocks = [];
    const mTitle = names[0][0];
    let partner, ctype;
    if (!pId && !ax) { partner = ""; ctype = ""; }
    else {
      partner = pId ? pId : iId.endsWith("c1") && ax ? ax : pId;
      ctype = pId ? relationship(mTitle, names[1][0]) : ax ? relationship(mTitle, seconds[0][0]) : "Partner";
    }
    blocks.push({ id: iId, partner, partnership: joined, type: ctype, title: names[0][0], first: names[0][1], surname: names[0][2] });
    for (let k = 1; k < 5; k++) {
      if (ids[k]) {
        blocks.push({ id: ids[k], partner: iId, partnership: joined, type: k === 1 ? relationship(names[1][0], mTitle) : "Partner",
          title: names[k][0], first: names[k][1], surname: names[k][2] });
      } else blocks.push({ id: "", partner: "", partnership: "", type: "", title: names[k][0], first: names[k][1], surname: names[k][2] });
    }
    [ax, be].forEach((sid, k) => {
      if (sid) {
        blocks.push({ id: sid, partner: `${a}_c1`, partnership: joined,
          type: k === 0 && sid[sid.length - 1] === "2" ? relationship(seconds[0][0], mTitle) : "Partner",
          title: seconds[k][0], first: seconds[k][1], surname: seconds[k][2] });
      } else blocks.push({ id: "", partner: "", partnership: "", type: "", title: seconds[k][0], first: seconds[k][1], surname: seconds[k][2] });
    });
    return [blocks, [note, note2].filter(Boolean).join("; ")];
  }

  function emailOkay(e) {
    if (!e || !e.trim()) return "BLANK";
    if ([..."&(,;"].some((ch) => e.includes(ch)) || e.trim().includes(" ") || e.split("@").length - 1 > 1) return "NO";
    return "YES";
  }
  function invalidNumbersText(r) {
    const parts = [r.W ? `Invalid Mobile: ${r.W} ` : "", r.X ? `Invalid Home: ${r.X} ` : "",
      r.Y ? `Invalid Work: ${r.Y} ` : "", r.Z ? `Invalid Fax: ${r.Z} ` : ""];
    return xlTrim(parts.join(""));
  }
  function extraNotes(r, split) {
    const bits = [r.AA ? "Invalid Numbers:" + r.AA : ""];
    if (!split) bits.push(r.H ? "*MOBILE: " + r.H : "");
    bits.push(r.I ? "*HOME: " + r.I : "", r.K ? "*WORK: " + r.K : "", r.M ? "*FAX: " + r.M : "", r.U ? "EMAIL: " + r.U : "");
    return xlTrim(bits.join(" "));
  }

  /** role -> column and role -> how it was found: learned > guide > field name > similar. */
  function propose(columns, opts = {}) {
    const learned = opts.learned || {}, hints = opts.hints || {};
    const names = {};
    for (const role of Object.keys(ROLES)) {
      const set = new Set((ROLE_HINTS[role] || []).map(normalizeHeader));
      for (const h of hints[role] || []) set.add(normalizeHeader(h));
      const field = ROLES[role][1];
      if (field) { set.add(normalizeHeader(field)); set.add(normalizeHeader(field.replace(/^contact_/, ""))); }
      names[role] = set;
    }
    const norm = dict(); columns.forEach((c) => (norm[c] = normalizeHeader(c)));
    const out = {}, how = {}, used = new Set();
    Object.keys(ROLES).forEach((r) => { out[r] = ""; how[r] = ""; });
    const take = (role, col, label) => {
      if (col && col in norm && !out[role] && !used.has(col)) { out[role] = col; how[role] = label; used.add(col); }
    };
    for (const [col, role] of Object.entries(learned)) if (hasOwn(ROLES, role)) take(role, col, "learned");
    for (const role of Object.keys(ROLES)) take(role, columns.find((c) => !used.has(c) && names[role].has(norm[c])) || "", "guide");
    for (const role of Object.keys(ROLES)) {
      const multi = [...names[role]].filter((h) => h.includes(" "));
      take(role, columns.find((c) => !used.has(c) && multi.some((h) => ` ${norm[c]} `.includes(` ${h} `))) || "", "similar");
    }
    return { mapping: out, how };
  }

  class GeneralCleaner {
    constructor(mapping, opts = {}) {
      this.mapping = {};
      for (const [r, c] of Object.entries(mapping || {})) if (hasOwn(ROLES, r)) this.mapping[r] = c || "";
      this.idTag = (opts.idTag || "").trim();
      this.splitter = opts.addressSplitter || null;
      this.splitMultiValues = opts.splitMultiValues !== false;
      this.fillStateFromPostcode = opts.fillStateFromPostcode !== false;
      this.nameOptions = opts.nameOptions; this.detailOptions = opts.detailOptions;
      // flagModes: role -> {meaning, blank} (missing = the workbook's rule); dateModes: role -> date | datetime | datetime_s
      const fm = opts.flagModes || {}, dmo = opts.dateModes || {};
      this.flagModes = Object.fromEntries(FLAG_ROLES.map((r) => [r, fm[r] ? cleanFlagMode(fm[r], r) : legacyFlagMode(r)]));
      this.dateModes = Object.fromEntries(DATE_ROLES.map((r) => [r, hasOwn(DATE_MODES, dmo[r]) ? dmo[r] : "date"]));
      this.issues = [];
    }
    /** frame: {columns: [...], rows: [{column: value}]} */
    run(frame) {
      const rowsIn = frame.rows, n = rowsIn.length, M = this.mapping;
      const col = {};
      for (const role of Object.keys(ROLES)) {
        const c = M[role];
        col[role] = c && frame.columns.includes(c) ? rowsIn.map((r) => cell(hasOwn(r, c) ? r[c] : "")) : Array(n).fill("");
      }
      // ---------- Transfer: raw IDs ----------
      const baseIds = M.id ? col.id : Array.from({ length: n }, (_, i) => String(i + 1));
      const rawIds = baseIds.map((b) => (b ? (this.idTag ? `${b}_${this.idTag}` : b) : ""));
      const idCount = new Map();
      rawIds.filter(Boolean).forEach((x) => idCount.set(x, (idCount.get(x) || 0) + 1));
      const dupes = [...idCount].filter(([, c]) => c > 1).map(([k]) => k);
      if (dupes.length) this.issues.push(["FAIL", `${dupes.length} contact ID(s) appear more than once (e.g. ${dupes.slice(0, 5).join(", ")}) - each raw ID must be unique or rows will share their details.`]);
      if (!M.id) this.issues.push(["INFO", "No ID column mapped - rows were numbered 1, 2, 3 ... as IDs."]);

      // ---------- Name SPlitting ----------
      const blocksAll = [], nameNotes = [], companyFlags = [];
      for (let i = 0; i < n; i++) {
        const [blocks, note] = nameBlocks(rawIds[i], col.title[i], col.first[i], col.last[i], col.title2[i], col.first2[i], col.last2[i], this.nameOptions);
        blocksAll.push(blocks); nameNotes.push(note); companyFlags.push(col.company[i] ? "TRUE" : "FALSE");
      }
      // ---------- Spouse links: a column holding the partner's contact ID (another row) - see general_cleaning.py ----------
      const spouse = { linked: 0, not_found: 0, conflict: 0, split: 0 };
      if (M.spouse_id) {
        const rowOf = new Map();
        baseIds.forEach((b, i) => { if (b && !dupes.includes(rawIds[i]) && !rowOf.has(b.trim())) rowOf.set(b.trim(), i); });
        const linked = new Set();
        const single = (x) => !blocksAll[x][0].partner && !blocksAll[x].slice(1).some((blk) => blk.id);
        for (let i = 0; i < n; i++) {
          const s = col.spouse_id[i].trim();
          if (!s || !rawIds[i] || s === baseIds[i].trim() || linked.has(i)) continue;
          const j = rowOf.get(s);
          if (j === undefined) { spouse.not_found++; continue; }
          const back = col.spouse_id[j].trim();
          if ((back && back !== baseIds[i].trim()) || linked.has(j)) { spouse.conflict++; continue; }
          if (!(single(i) && single(j))) { spouse.split++; continue; }
          const [lo, hi] = i < j ? [i, j] : [j, i];
          const joined = `${blocksAll[lo][0].id}_${blocksAll[hi][0].id}_r`;
          for (const [x, y] of [[i, j], [j, i]]) {
            Object.assign(blocksAll[x][0], { partner: blocksAll[y][0].id, partnership: joined, type: "Partner" });
            nameNotes[x] = [nameNotes[x], `partner ${blocksAll[y][0].id} linked from the spouse ID`].filter(Boolean).join("; ");
          }
          linked.add(i); linked.add(j);
          spouse.linked++;
        }
      }
      // ---------- Summary order (FillSummary) ----------
      const people = [], srcNo = Array(n).fill(0);
      let ctr = 0;
      for (let i = 0; i < n; i++) if (rawIds[i]) srcNo[i] = ++ctr;
      for (let i = 0; i < n; i++) if (rawIds[i]) people.push([i, 0]);
      const nMain = people.length;
      for (let i = 0; i < n; i++) {
        if (!rawIds[i]) continue;
        for (let b = 1; b < 7; b++) {
          const blk = blocksAll[i][b];
          if (blk.id && (blk.first || blk.surname)) people.push([i, b]);
        }
      }
      const perRow = Array(n).fill(0);
      people.forEach(([i]) => perRow[i]++);

      // ---------- Contact Details ----------
      const cleaner = new ContactDetailsCleaner(this.detailOptions);
      const second = new ContactDetailsCleaner(this.detailOptions);
      const details = [];
      for (let i = 0; i < n; i++) {
        const r = cleaner.clean(col.email[i], col.mobile[i], col.work[i], col.home[i], col.fax[i]);
        r.AA = invalidNumbersText(r);
        r.V = extraNotes(r, perRow[i] > 1);
        r.AB = emailOkay(r.O);
        r["Mobile 2"] = col.mobile2[i] ? second.clean("", col.mobile2[i]).G : "";
        r.Email2 = col.email2[i] ? col.email2[i].split(/[,;\s|/]+/).find((e) => isValidEmail(e)) || "" : "";
        r.AL = emailOkay(r.Email2);
        details.push(r);
      }
      // ---------- Address Cleaning ----------
      const addresses = [], addressNotes = [];
      let splitCount = 0;
      const pcState = { list: 0, range: 0, ambiguous: 0 };
      for (let i = 0; i < n; i++) {
        const rawPc = col.postcode[i];
        const a = { line1: excelTrimClean(col.street[i]), line2: excelTrimClean(col.street2[i]),
          suburb: excelTrimClean(col.suburb[i]), state: excelTrimClean(col.state[i]), country: excelTrimClean(col.country[i]),
          postcode: ["", "0"].includes(rawPc.trim()) ? "" : rawPc.trim(), note: "" };
        if (this.splitter && col.street[i]) {
          const res = this.splitter.clean(col.street[i], a.suburb, a.state, a.postcode);
          if (res.flags.has("split")) splitCount++;
          Object.assign(a, { line1: excelTrimClean(res.line1), suburb: excelTrimClean(res.suburb), state: res.state, postcode: res.postcode });
          a.note = res.notes.join("; ");
        }
        if (this.fillStateFromPostcode && this.splitter && !a.state && a.postcode && ["", "AUSTRALIA", "AU", "AUS"].includes(a.country.toUpperCase().replace(/[^A-Z]/g, ""))) {
          const [st, how, note] = this.splitter.stateForPostcode(a.postcode, a.suburb);
          if (st) a.state = st;
          pcState[how] = (pcState[how] || 0) + 1;
          if (note) a.note = [a.note, note].filter(Boolean).join("; ");
        }
        addresses.push(a); addressNotes.push(a.note);
      }
      // ---------- Summary rows ----------
      const flagVals = {}, flagOdd = {}, dateOdd = {};
      // is_contactable = 1: a permission whose own cell is blank (or that isn't in the export) becomes allowed
      const contactable = M.contactable ? col.contactable.map(isContactable) : Array(n).fill(false);
      let contactFills = 0;
      for (const role of FLAG_ROLES) {
        const mapped = !!M[role];
        if (!mapped && !contactable.some(Boolean)) continue;
        flagOdd[role] = 0;
        flagVals[role] = Array.from({ length: n }, (_, i) => {
          if (contactable[i] && (!mapped || !col[role][i].trim())) { contactFills++; return flagText(role, true); }
          if (!mapped) return "";
          const [allowed, known] = flagAllowed(col[role][i], this.flagModes[role]); if (!known) flagOdd[role]++; return flagText(role, allowed);
        });
      }
      const flagAllow = (role, i) => (flagVals[role] ? flagVals[role][i] : "");
      for (const role of DATE_ROLES) dateOdd[role] = col[role].filter((v) => v.trim() && !parseDate(v.trim())).length;
      const sep = cleaner.opt.multi_sep;
      const rank = Array(n).fill(0), givenOwn = { mobile: 0, email: 0 };
      const ownValue = (joined, k, here, kind) => {
        const parts = joined ? joined.split(sep).filter((x) => x.trim()) : [];
        if (!this.splitMultiValues || here < 2 || parts.length < 2) return joined;
        if (k === 0) return [parts[0]].concat(parts.slice(here)).join(sep);
        if (k < parts.length) { givenOwn[kind]++; return parts[k]; }
        return parts[0];
      };
      const summary = [];
      for (const [i, b] of people) {
        const blk = blocksAll[i][b], d = details[i], a = addresses[i];
        const count = contactNo(blk.id);
        const k = rank[i]++;
        const mobile = (count === 2 ? d["Mobile 2"] : "") || ownValue(d.G, k, perRow[i], "mobile");
        const email = (count === 2 ? d.Email2 : "") || ownValue(d.O, k, perRow[i], "email");
        const company = col.company[i];
        const contactName = companyFlags[i] === "TRUE" ? company : [blk.first, blk.surname].filter(Boolean).join(" ");
        const notes = [col.notes[i].trim(), d.V].filter(Boolean).join(" ");
        const row = {
          Indicator: srcNo[i], "Contact Count": count, "Raw ORIG CONTACT_IDENTIFIER": rawIds[i],
          CONTACT_IDENTIFIER: blk.id, CONTACT_PARTNER_IDENTIFIER: blk.partner, CONTACT_PARTNERSHIP_ID: blk.partnership,
          CONTACT_PARTNERSHIP_TYPE: blk.type, CONTACT_TITLE: blk.title, CONTACT_FIRST_NAME: blk.first, CONTACT_SURNAME: blk.surname,
          CONTACT_IS_COMPANY: companyFlags[i], CONTACT_COMPANY_NAME: company, CONTACT_MOBILE: mobile,
          CONTACT_PHONE_HOME: d.J, CONTACT_PHONE_WORK: d.L, CONTACT_FAX: d.N, CONTACT_EMAIL_ADDRESS: email,
          CONTACT_ALLOW_CONTACT_VIA_TEXT: flagAllow("do_not_sms", i), CONTACT_ALLOW_CONTACT_VIA_EMAIL: flagAllow("do_not_email", i),
          CONTACT_ALLOW_CONTACT_VIA_LETTER: flagAllow("do_not_mail", i), CONTACT_ALLOW_CONTACT_VIA_PHONE: flagAllow("do_not_call", i),
          CONTACT_UNSUBSCRIBE_FROM_MARKETING: flagAllow("subscribed", i),
          CONTACT_MAILING_ADDRESS_LINE1: a.line1, CONTACT_MAILING_ADDRESS_LINE2: a.line2, CONTACT_MAILING_ADDRESS_SUBURB: a.suburb,
          CONTACT_MAILING_ADDRESS_STATE: a.state, CONTACT_MAILING_ADDRESS_COUNTRY: a.country, CONTACT_MAILING_ADDRESS_POSTCODE: a.postcode,
          CONTACT_TYPE: col.type[i].trim(), CONTACT_ENQUIRY_SOURCE: col.source[i].trim(), CONTACT_NAME_ON_LETTERS: col.salutation[i].trim(),
          CONTACT_LEGAL_NAME: col.legal[i].trim(), CONTACT_TAGS: col.tags[i].trim(), CONTACT_NOTES: notes,
          CONTACT_TEAM_MEMBER_1: col.team1[i].trim(), CONTACT_DATE_MODIFIED: formatDate(col.modified[i].trim(), this.dateModes.modified),
          CONTACT_DATE_CREATED: formatDate(col.created[i].trim(), this.dateModes.created), "Contact Name": contactName, CONTACT_COMPANY_POSITION: col.position[i].trim(),
          ZENU_CONTACT_ID: b === 0 ? col.zenu_id[i].trim() : "",
          CONTACT_SUBSCRIPTIONS: col.subscriptions[i].trim(),
          CONTACT_MAILING_ADDRESS_TOWN_CITY: col.town_city[i].trim(),
          CONTACT_CHILDRENS_NAMES: col.children[i].trim(),
          CONTACT_RATING: col.rating[i].trim(),
          CONTACT_LAST_CONTACT_DATE: formatDate(col.last_contact[i].trim(), this.dateModes.last_contact),
          CONTACT_TEAM_MEMBER_2: col.team2[i].trim(),
          CONTACT_TEAM_MEMBER_3: col.team3[i].trim(),
          CONTACT_TEAM_MEMBER_4: col.team4[i].trim(),
          CONTACT_TEAM_MEMBER_5: col.team5[i].trim(),
          CONTACT_TEAM_MEMBER_6: col.team6[i].trim(),
          CONTACT_TEAM_MEMBER_7: col.team7[i].trim(),
          CONTACT_TEAM_MEMBER_8: col.team8[i].trim(),
          CONTACT_TEAM_MEMBER_9: col.team9[i].trim(),
          CONTACT_TEAM_MEMBER_10: col.team10[i].trim(),
          CONTACT_TEAM_MEMBER_11: col.team11[i].trim(),
          CONTACT_TEAM_MEMBER_12: col.team12[i].trim(),
          CONTACT_TEAM_MEMBER_13: col.team13[i].trim(),
          CONTACT_TEAM_MEMBER_14: col.team14[i].trim(),
          CONTACT_TEAM_MEMBER_15: col.team15[i].trim(),
          CONTACT_SALE_TYPE: col.sale_type[i].trim(),
          ZENU_SOLICITOR_ID: col.solicitor_id[i].trim(),
          ZENU_COMPANY_ID: col.company_id[i].trim(),
        };
        const full = {};
        SUMMARY_COLUMNS.forEach((c) => (full[c] = c in row ? row[c] : ""));
        summary.push(full);
      }
      // ---------- working tabs ----------
      const nameSplitting = [], contactDetails = [], addressCleaning = [];
      const labels = ["Contact 1", "Contact 2", "Contact 3", "Contact 4", "Contact 5", "Second Name 1", "Second Name 2"];
      const keys = [["id", "ID"], ["partner", "Partner ID"], ["partnership", "Partnership ID"], ["type", "Type"], ["title", "Title"], ["first", "First"], ["surname", "Surname"]];
      for (let i = 0; i < n; i++) {
        const r = { ID: rawIds[i], CONTACT_COMPANY_NAME: col.company[i], Title: col.title[i], "First Name": col.first[i],
          "Last Name": col.last[i], CONTACT_IS_COMPANY: companyFlags[i] };
        labels.forEach((label, k) => keys.forEach(([key, nm]) => (r[`${label} ${nm}`] = blocksAll[i][k][key])));
        r["Review notes"] = nameNotes[i];
        nameSplitting.push(r);
        const d = details[i];
        contactDetails.push({ "Raw ORIG CONTACT_IDENTIFIER": rawIds[i], "Raw CONTACT_EMAIL_ADDRESS": col.email[i],
          "Raw CONTACT_MOBILE": col.mobile[i], "Raw CONTACT_PHONE_WORK": col.work[i], "Raw CONTACT_PHONE_HOME": col.home[i],
          "Raw CONTACT_FAX": col.fax[i], Mobile: d.G, "Mobile Landline": d.H, "Home Mobile": d.I, HomeLandline: d.J,
          "Work Mobile": d.K, WorkLandline: d.L, "Fax Mobile": d.M, "Fax Landline": d.N, "Email Address": d.O,
          "Invalid Email and extra Email": d.U, "Extra Notes": d.V, "Invalid Mobile - Get Raw mobile": d.W,
          "Invalid Home - get raw home": d.X, "Invalid Work - get raw work": d.Y, "Invalid Fax": d.Z, "Invalid Numbers": d.AA,
          "Email Okay?": d.AB, Email2: d.Email2, "Mobile 2": d["Mobile 2"], "Removed from phone cells (no digits)": d.dropped.join(", ") });
        const a = addresses[i];
        addressCleaning.push({ "Raw ORIG CONTACT_IDENTIFIER": rawIds[i], "Raw street": col.street[i], "Raw suburb": col.suburb[i],
          "Raw postcode": col.postcode[i], "Raw state": col.state[i], "Raw country": col.country[i],
          CONTACT_MAILING_ADDRESS_LINE1: a.line1, CONTACT_MAILING_ADDRESS_LINE2: a.line2, CONTACT_MAILING_ADDRESS_SUBURB: a.suburb,
          CONTACT_MAILING_ADDRESS_STATE: a.state, CONTACT_MAILING_ADDRESS_COUNTRY: a.country, CONTACT_MAILING_ADDRESS_POSTCODE: a.postcode,
          "DARVIS address note": a.note });
      }
      // ---------- checks + counts ----------
      const c = cleaner.count, rec = cleaner.reconciliation();
      const cnt = (key) => details.filter((d) => d[key]).length;
      const stats = {
        source_rows: n, source_contacts: ctr, main_rows: nMain, split_rows: people.length - nMain, total_rows: people.length,
        records_split: perRow.filter((x) => x > 1).length, name_notes: nameNotes.filter(Boolean).length,
        "Mobile (G)": cnt("G"), "Home Landline (J)": cnt("J"), "Work Landline (L)": cnt("L"), "Fax (N)": cnt("N"),
        "Email (O)": cnt("O"), "Invalid/extra email (U)": cnt("U"), "NZ mobiles placed": c.nz_mobiles,
        "International numbers placed": c.international, "Invalid numbers (W:Z)": c.invalid_numbers,
        "Emails moved from phone columns": c.emails_moved_from_phones, phone_values_in: rec.phone_parts_in,
        phones_reconciled: rec.phones_ok, text_dropped_from_phones: c.text_dropped_from_phones,
        email_not_okay: details.filter((d) => d.AB === "NO").length, addresses_split: splitCount,
        address_notes: addressNotes.filter(Boolean).length,
        state_from_postcode: pcState.list + pcState.range, state_postcode_ambiguous: pcState.ambiguous,
        couples_linked_from_spouse_id: spouse.linked, spouse_id_not_in_file: spouse.not_found,
        spouse_id_conflicts: spouse.conflict, spouse_id_row_already_split: spouse.split,
        permissions_from_contactable: contactFills,
        titles_dr: people.filter(([i, b]) => blocksAll[i][b].title === "Dr").length,
        own_mobile_from_shared_cell: givenOwn.mobile, own_email_from_shared_cell: givenOwn.email,
      };
      if (spouse.linked) this.issues.push(["INFO", `${spouse.linked} couple(s) linked from the spouse ID column: each gets the other as CONTACT_PARTNER_IDENTIFIER, a shared CONTACT_PARTNERSHIP_ID and type Partner.`]);
      if (spouse.not_found) this.issues.push(["WARN", `${spouse.not_found} spouse ID(s) aren't a contact ID in this file - left unlinked.`]);
      if (spouse.conflict) this.issues.push(["WARN", `${spouse.conflict} spouse ID(s) point at a contact whose own spouse ID is someone else (or who is already linked) - left unlinked.`]);
      if (spouse.split) this.issues.push(["INFO", `${spouse.split} spouse ID(s) not used: the row (or its spouse's row) already splits into several people, who are linked to each other.`]);
      if (pcState.list || pcState.range) this.issues.push(["INFO", `${pcState.list + pcState.range} blank state(s) filled from the postcode (${pcState.list} from the suburb list, ${pcState.range} from Australia Post postcode ranges).`]);
      if (pcState.ambiguous) this.issues.push(["WARN", `${pcState.ambiguous} blank state(s) left blank: the postcode is in more than one state and the suburb doesn't settle it - see Address Cleaning.`]);
      if (contactFills) this.issues.push(["INFO", `${contactFills} permission cell(s) were blank and set to allowed because the contact is contactable ('${M.contactable}' = 1).`]);
      for (const role of FLAG_ROLES) if (flagOdd[role]) this.issues.push(["WARN", `${flagOdd[role]} value(s) in '${M[role]}' (${ROLES[role][0]}) weren't yes/no values and were treated as not allowed.`]);
      for (const role of DATE_ROLES) if (dateOdd[role]) this.issues.push(["WARN", `${dateOdd[role]} value(s) in '${M[role]}' (${ROLES[role][0]}) couldn't be read as dates and were left as they are.`]);
      if (!rec.phones_ok) this.issues.push(["FAIL", `Phone values don't reconcile (${rec.phone_parts_in} in, ${rec.phone_parts_accounted} accounted for).`]);
      if (stats["Invalid numbers (W:Z)"]) this.issues.push(["WARN", `${stats["Invalid numbers (W:Z)"]} invalid phone value(s) - kept in the Invalid columns and added to CONTACT_NOTES.`]);
      if (stats.text_dropped_from_phones) this.issues.push(["WARN", `${stats.text_dropped_from_phones} value(s) with no digits were removed from phone cells by the macro rule - see Contact Details.`]);
      if (stats.email_not_okay) this.issues.push(["WARN", `${stats.email_not_okay} record(s) fail 'Email Okay?' (several emails or odd characters in CONTACT_EMAIL_ADDRESS).`]);
      if (stats.titles_dr) this.issues.push(["INFO", `${stats.titles_dr} contact(s) titled Dr (allowed by the Contacts Mapping, not in Zenu's documented list).`]);
      if (givenOwn.mobile || givenOwn.email) this.issues.push(["INFO", `Split people given their own value from a shared cell: ${givenOwn.mobile} mobile(s), ${givenOwn.email} email(s) (person 1 gets the first value, person 2 the second ...).`]);
      if (stats.split_rows) this.issues.push(["INFO", `${stats.records_split} record(s) split into ${stats.split_rows} extra people - rows ${nMain + 2}-${people.length + 1} of the Summary.`]);
      const settings = {};
      FLAG_ROLES.forEach((r) => { if (M[r]) settings[r] = flagModeText(r, this.flagModes[r]); });
      if (M.contactable) settings.contactable = "1 / Yes = contactable: blank permissions become allowed";
      DATE_ROLES.forEach((r) => { if (M[r]) settings[r] = DATE_MODES[this.dateModes[r]]; });
      return { settings, summary, nameSplitting, contactDetails, addressCleaning, stats, issues: this.issues, rowOf: people.map(([i]) => i) };
    }
  }

  // =====================================================================
  // AGENTBOX  (agentbox.py - the 4 Agentbox CSVs)
  // =====================================================================
  const TYPE_LIST = ("Buyer|Vendor|Bidder|Past Vendor|Investor|Web Enquiry|Prospective Rental Provider|Rental Provider|" +
    "Website Signup|Purchaser|Owner|Prospective Vendor|Prospective Landlord|Enquiry|Unknown|Landlord|Accountant|Auctioneer|" +
    "Business Buyer|Solicitor|Buyers Agent|Conjunctional Agent|Developer|Managing Agent|Owner Absentee|Absentee Owner|" +
    "Owner Occupier|Occupier|Past Buyer|Past Landlord|Past Owner|Past Tenant|Prospective Buyer|Prospective Tenant|Referrer|" +
    "Strata Manager|Supplier|Tradesperson|Valuer|Vendor Solicitor|Tenant|Potential Buyer|Potential Seller|Past Vendors|" +
    "Developers|VIP|Past Purchaser|Renter|Potential Vendor|Prospective Buyers|Owners|Tenants|Lessor|Lessee|Sellers").split("|");
  const TYPES = dict();
  TYPE_LIST.forEach((t) => { if (!(t.toLowerCase() in TYPES)) TYPES[t.toLowerCase()] = t; });
  const TYPE_COL = "Contact Type (from contact_category.csv)";
  const TAGS_COL = "Contact Tags (from contact_category.csv)";
  const TEAM_COL = "Team member 1 (from contact_x_agent.csv + agent.csv)";
  const NOTES_COL = "comments (Base64 decoded)";
  const CONTACT_MAPPING = { id: "contact_id", title: "title", first: "first_name", last: "last_name", company: "company_name",
    email: "email", home: "home_phone", work: "work_phone", mobile: "mobile", fax: "fax", street: "street_address", suburb: "suburb",
    state: "state", postcode: "postcode", country: "country", salutation: "salutation", legal: "legal_name", position: "job_title",
    source: "source", created: "date_created", modified: "last_modified", do_not_call: "do_not_call", do_not_sms: "do_not_sms",
    do_not_email: "do_not_email", do_not_mail: "do_not_mail", subscribed: "subscribed_newsletters" };
  const AB_FILES = { contact: "contact.csv", contact_category: "contact_category.csv", contact_x_agent: "contact_x_agent.csv", agent: "agent.csv" };

  const hmap = (headers) => { const m = dict(); headers.forEach((h) => { const k = String(h).trim().toLowerCase(); if (!(k in m)) m[k] = h; }); return m; };
  const abKey = (v) => { const s = String(v).trim(); return /^-?\d+\.0$/.test(s) ? s.slice(0, -2) : s; };
  const hasAll = (h, keys) => keys.every((k) => k in h);

  function kindOf(headers) {
    const h = hmap(headers);
    if (hasAll(h, ["contact_id", "first_name", "last_name"]) && ["street_address", "mobile", "email"].some((k) => k in h)) return "contact";
    if (hasAll(h, ["contact_id", "category_name"])) return "contact_category";
    if (hasAll(h, ["contact_id", "agent_id", "role"])) return "contact_x_agent";
    if (hasAll(h, ["agent_id", "first_name", "last_name"]) && !("contact_id" in h)) return "agent";
    return null;
  }
  function splitTypeTags(raw) {
    const types = [], tags = [], seenT = new Set(), seenG = new Set();
    for (const part of str(raw).split(",")) {
      const s = stripSp(collapse(part.replace(/ /g, " ")));
      if (!s) continue;
      const k = s.toLowerCase();
      if (k in TYPES) { if (!seenT.has(k)) { seenT.add(k); types.push(TYPES[k]); } }
      else if (!seenG.has(k)) { seenG.add(k); tags.push(s); }
    }
    return [types.join(","), tags.join(",")];
  }
  function b64bytes(b64) {
    if (typeof atob === "function") { const bin = atob(b64); const out = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out; }
    return Uint8Array.from(Buffer.from(b64, "base64"));
  }
  function decodeComment(text) {
    const s = str(text);
    if (!s.trim()) return ["", "empty"];
    if (!s.includes("[base64]")) return [s, "plain"];
    let b64 = s.split("[base64]").join("").replace(/\s+/g, "").replace(/[^A-Za-z0-9+/=]/g, "").replace(/=+$/, "");
    b64 += "=".repeat((4 - (b64.length % 4)) % 4);
    let bytes;
    try { bytes = b64bytes(b64); } catch (e) { return [s, "bad"]; }
    try { return [new TextDecoder("utf-8", { fatal: true }).decode(bytes), "decoded"]; }
    catch (e) { return [new TextDecoder("windows-1252").decode(bytes), "decoded"]; }
  }
  function categoriesByContact(frame) {
    const h = hmap(frame.columns), out = new Map();
    for (const r of frame.rows) {
      const k = abKey(r[h.contact_id]);
      if (!k) continue;
      const c = String(r[h.category_name] ?? "").trim();
      const prev = out.has(k) ? out.get(k) : "";
      out.set(k, prev);
      if (c) out.set(k, prev ? `${prev},${c}` : c);
    }
    return out;
  }
  function agentNames(frame) {
    const h = hmap(frame.columns), out = new Map();
    for (const r of frame.rows) {
      const a = abKey(r[h.agent_id]);
      if (a) out.set(a, [String(r[h.first_name] ?? "").trim(), String(r[h.last_name] ?? "").trim()].filter(Boolean).join(" "));
    }
    return out;
  }
  function teamMember1(frame) {
    const h = hmap(frame.columns), rows = new Map();
    for (const r of frame.rows) {
      const k = abKey(r[h.contact_id]);
      if (!k) continue;
      if (!rows.has(k)) rows.set(k, []);
      rows.get(k).push([String(r[h.role] ?? "").trim() !== "primary_owner", abKey(r[h.agent_id])]);
    }
    const out = new Map();
    for (const [k, v] of rows) out.set(k, (v.find((x) => !x[0]) || v[0])[1]);
    return out;
  }
  /** found: {kind: frame}. -> {frame, mapping, info} - the contact frame plus derived Agentbox columns. */
  function buildAgentbox(found) {
    const contact = found.contact;
    const h = hmap(contact.columns);
    const ids = contact.rows.map((r) => abKey(r[h.contact_id]));
    const info = { files: {}, counts: {}, extra_columns: [] };
    Object.keys(AB_FILES).forEach((k) => (info.files[k] = found[k] ? found[k].name : ""));
    const mapping = {};
    for (const [role, c] of Object.entries(CONTACT_MAPPING)) if (c in h) mapping[role] = h[c];
    const rows = contact.rows.map((r) => Object.assign({}, r));
    const columns = contact.columns.slice();
    const add = (name, values) => { columns.push(name); rows.forEach((r, i) => (r[name] = values[i])); info.extra_columns.push(name); };
    if (found.contact_category) {
      const cats = categoriesByContact(found.contact_category);
      const split = ids.map((i) => splitTypeTags(cats.get(i) || ""));
      add(TYPE_COL, split.map((x) => x[0])); add(TAGS_COL, split.map((x) => x[1]));
      Object.assign(mapping, { type: TYPE_COL, tags: TAGS_COL });
      const idSet = new Set(ids);
      Object.assign(info.counts, { with_type: split.filter((x) => x[0]).length, with_tags: split.filter((x) => x[1]).length,
        category_contacts_not_in_contact_csv: [...cats.keys()].filter((k) => !idSet.has(k)).length });
    }
    if (found.contact_x_agent) {
      const first = teamMember1(found.contact_x_agent);
      const names = found.agent ? agentNames(found.agent) : new Map();
      const team = ids.map((i) => names.get(first.get(i) || "") || "");
      add(TEAM_COL, team);
      mapping.team1 = TEAM_COL;
      const missing = new Set(ids.filter((i) => first.get(i) && !names.has(first.get(i))).map((i) => first.get(i)));
      Object.assign(info.counts, { with_team_member: team.filter(Boolean).length, agents_not_in_agent_csv: missing.size });
    }
    if ("comments" in h) {
      const dec = contact.rows.map((r) => decodeComment(r[h.comments]));
      add(NOTES_COL, dec.map((x) => x[0]));
      mapping.notes = NOTES_COL;
      const hows = dec.map((x) => x[1]);
      Object.assign(info.counts, { comments_decoded: hows.filter((x) => x === "decoded").length,
        comments_plain_text: hows.filter((x) => x === "plain").length, comments_not_decodable: hows.filter((x) => x === "bad").length });
    }
    return { frame: { columns, rows }, mapping, info };
  }
  function linkNotes(info) {
    const f = info.files, c = info.counts, lines = [];
    if (!f.contact_category) lines.push(["WARN", "contact_category.csv isn't uploaded - CONTACT_TYPE and CONTACT_TAGS will be blank."]);
    if (!f.contact_x_agent) lines.push(["WARN", "contact_x_agent.csv isn't uploaded - CONTACT_TEAM_MEMBER_1 will be blank."]);
    else if (!f.agent) lines.push(["WARN", "agent.csv isn't uploaded - agent IDs can't be turned into names, so CONTACT_TEAM_MEMBER_1 will be blank."]);
    if (c.agents_not_in_agent_csv) lines.push(["WARN", `${c.agents_not_in_agent_csv} agent ID(s) in contact_x_agent.csv aren't in agent.csv - those contacts get no team member.`]);
    if (c.comments_not_decodable) lines.push(["WARN", `${c.comments_not_decodable} comment(s) marked [base64] couldn't be decoded - kept as they are.`]);
    if (c.category_contacts_not_in_contact_csv) lines.push(["INFO", `${c.category_contacts_not_in_contact_csv} contact ID(s) in contact_category.csv aren't in contact.csv (ignored).`]);
    return lines;
  }

  // =====================================================================
  // INGEST HELPERS (browser): rows of cells -> frame
  // =====================================================================
  function looksLikeData(v) {
    const s = String(v).trim();
    return /@/.test(s) || /^[+(]?\d[\d\s()+\-./]{4,}$/.test(s) || /^\d+(\.\d+)?$/.test(s);
  }
  /** A sheet's rows (arrays) -> {columns, rows, headerRow, headerless}. */
  function toFrame(grid) {
    const rows = grid.filter((r) => r && r.some((v) => String(v ?? "").trim() !== ""));
    if (!rows.length) return null;
    const width = rows.reduce((w, r) => Math.max(w, r.length), 0);       // not Math.max(...): that overflows the stack past ~120,000 rows
    let hrow = 0, best = -1;
    for (let i = 0; i < Math.min(rows.length, 10); i++) {
      const cells = rows[i].map((v) => String(v ?? "").trim());
      const filled = cells.filter(Boolean);
      const score = filled.filter((v) => !looksLikeData(v)).length * 2 - filled.filter(looksLikeData).length * 3 + (filled.length >= width * 0.5 ? 2 : 0);
      if (score > best) { best = score; hrow = i; }
    }
    const head = rows[hrow].map((v) => String(v ?? "").trim());
    const headerless = head.filter(Boolean).filter(looksLikeData).length >= Math.max(2, head.filter(Boolean).length / 2);
    const seen = {};
    const columns = Array.from({ length: width }, (_, i) => {
      let h = headerless ? "" : head[i] || "";
      h = h || `Column_${i + 1}`;
      const k = h.toLowerCase(); seen[k] = (seen[k] || 0) + 1;
      return seen[k] === 1 ? h : `${h} (${seen[k]})`;
    });
    const body = rows.slice(headerless ? hrow : hrow + 1).map((r) => {
      const o = {}; columns.forEach((c, i) => (o[c] = r[i] === undefined || r[i] === null ? "" : String(r[i]))); return o;
    });
    const keep = columns.filter((c) => !(c.startsWith("Column_") && body.every((r) => !String(r[c]).trim())));
    return { columns: keep, rows: body, headerRow: headerless ? 0 : hrow + 1, headerless };
  }

  // =====================================================================
  // IMPORT PREP  (main.py /api/import-prep - the Import Prep .xlsm "Internal Output")
  //   input sheets -> template ("Compare To") column order -> special characters removed
  //   -> CSV per sheet, split at 15,000 rows. Cells are typed: string | number | boolean |
  //   null | {date: [y, m, d, H, M]} (an Excel date cell).
  // =====================================================================
  const IP_SKIP_SHEETS = new Set(["document history", "instructions", "summary"]);
  const IP_HEADER_TOKENS = new Set(["contact_identifier", "property_identifier", "contact_id", "property_id",
    "zenu_contact_id", "zenu_property_id"]);
  const IP_MAX_ROWS = 15000;
  const IP_DISALLOWED = /[^A-Za-z0-9 ,\-.:;{}\[\]_&'\\/<>%+=@#!$^*()?\r\n]/g;
  const isBlank = (v) => v === null || v === undefined || (typeof v === "number" && isNaN(v));

  function ipText(v) {                       // Python str() of a header / token cell
    if (typeof v === "boolean") return v ? "True" : "False";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) + (Object.is(v, -0) ? "" : "") : String(v);
    if (v && v.date) return v.text || "";
    return String(v);
  }
  function importPrepStringify(v, headerLower) {
    if (isBlank(v)) return "";
    if (v && typeof v === "object" && v.date) {
      const [y, m, d, H, M] = v.date;
      const day = `${pad2(d)}/${pad2(m)}/${y}`;
      return headerLower === "inspection_start_date" || headerLower === "inspection_end_date" ? `${day} ${pad2(H)}:${pad2(M)}` : day;
    }
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(v);
    if (typeof v === "boolean") return v ? "True" : "False";
    return String(v);
  }
  function importPrepClean(text) {
    let token = "";
    for (const ch of text) { const c = ch.codePointAt(0); if (c >= 32 && c <= 126) token += ch; }
    const cleaned = token.replace(IP_DISALLOWED, (m) => (m === '"' ? "'" : ""));
    return [cleaned, cleaned !== text];
  }
  function importPrepTemplateHeaders(grid) {
    const row0 = (grid && grid[0]) || [];
    const headers = row0.map((c) => (isBlank(c) ? "" : ipText(c).trim()));
    while (headers.length && headers[headers.length - 1] === "") headers.pop();
    return headers;
  }
  function importPrepFindHeaderRow(grid) {
    for (let r = 0; r < grid.length; r++) {
      if ((grid[r] || []).some((x) => !isBlank(x) && IP_HEADER_TOKENS.has(ipText(x).trim().toLowerCase()))) return r;
    }
    return 0;
  }
  function importPrepBuildSheet(grid, templateHeaders) {
    const hr = importPrepFindHeaderRow(grid);
    const colIndex = new Map();
    (grid[hr] || []).forEach((h, idx) => {
      if (isBlank(h)) return;
      const k = ipText(h).trim().toLowerCase();
      if (!colIndex.has(k)) colIndex.set(k, idx);
    });
    const matched = [];
    templateHeaders.forEach((th, pos) => {
      const idx = colIndex.get(th.trim().toLowerCase());
      if (idx !== undefined) matched.push([pos, idx, th.trim().toLowerCase()]);
    });
    const rows = [templateHeaders.slice()];
    let changed = 0;
    for (let r = hr + 1; r < grid.length; r++) {
      const drow = grid[r] || [];
      const out = templateHeaders.map(() => "");
      for (const [pos, idx, hl] of matched) {
        const raw = idx < drow.length ? drow[idx] : "";
        const [cleaned, was] = importPrepClean(importPrepStringify(raw, hl));
        if (was) changed++;
        out[pos] = cleaned;
      }
      rows.push(out);
    }
    while (rows.length > 1 && rows[rows.length - 1].every((c) => c === "")) rows.pop();
    return { rows, changed, matched: matched.length, headerRow: hr + 1 };
  }
  function importPrepCsvName(sheet) {
    const name = String(sheet).trim().replace(/ /g, "_").replace(/[^A-Za-z0-9._-]/g, "_");
    return (name || "output").toLowerCase() + ".csv";
  }
  /** Python csv.writer (QUOTE_MINIMAL, \r\n) */
  function toCsv(rows) {
    const q = (f) => {
      f = String(f);
      return /[",\r\n]/.test(f) ? '"' + f.replace(/"/g, '""') + '"' : f;
    };
    return rows.map((r) => (r.length === 1 && r[0] === "" ? '""' : r.map(q).join(","))).join("\r\n") + "\r\n";
  }
  /** sheets: [{sheet, grid}], template: grid -> {outputs: [{name, rows}], report, totals, perSheet} */
  function importPrep(sheets, templateGrid, names) {
    const templateHeaders = importPrepTemplateHeaders(templateGrid);
    if (!templateHeaders.length) throw new Error("The Compare To template has no header row (row 1 is empty).");
    const outputs = [], perSheet = [];
    const report = ["Import Prep report", `Input file: ${names.input}`, `Template: ${names.template}`, "Mode: remove special characters", ""];
    let totalChanged = 0, totalRows = 0;
    for (const { sheet, grid } of sheets) {
      if (IP_SKIP_SHEETS.has(String(sheet).trim().toLowerCase())) continue;
      if (!grid || !grid.length) continue;
      const { rows, changed, matched, headerRow } = importPrepBuildSheet(grid, templateHeaders);
      const dataRows = rows.length - 1;
      if (dataRows <= 0) continue;
      totalChanged += changed; totalRows += dataRows;
      const base = importPrepCsvName(sheet).slice(0, -4);
      const chunks = [];
      if (dataRows <= IP_MAX_ROWS) chunks.push(rows);
      else for (let s = 1; s < rows.length; s += IP_MAX_ROWS) chunks.push([rows[0]].concat(rows.slice(s, s + IP_MAX_ROWS)));
      chunks.forEach((ch, i) => outputs.push({ name: chunks.length === 1 ? base + ".csv" : `${base}_${i + 1}.csv`, rows: ch }));
      perSheet.push({ sheet, dataRows, changed, matched, headerRow, files: chunks.length });
      report.push(`${sheet}: ${dataRows} data rows, ${changed} cells cleaned` + (chunks.length > 1 ? `, split into ${chunks.length} files` : ""));
    }
    if (!outputs.length) throw new Error("No data rows were produced. Check that the input's headers match the template and that a header row was detected.");
    report.push("", `TOTAL: ${totalRows} data rows, ${totalChanged} cells cleaned across ${outputs.length} file(s).`);
    return { outputs, report: report.join("\r\n"), totals: { rows: totalRows, changed: totalChanged, files: outputs.length }, perSheet, templateHeaders };
  }

  // =====================================================================
  // AI PICK CHECKS (general_flow.ai_fill) - what Claude is told, and a sanity check on its answers
  // =====================================================================
  const ROLE_HELP = {
    id: "the record's unique ID / reference number",
    spouse_id: "the CONTACT ID of this contact's spouse / partner - another row in the same file (not a name)",
    company: "the business the CONTACT belongs to (Pty Ltd, Trust, firm names) - never a staff member",
    title: "Mr / Mrs / Ms / Dr ...", first: "first (given) names", last: "surnames",
    title2: "the SECOND person's title", first2: "the SECOND person's first name", last2: "the SECOND person's surname",
    email: "the main email address", mobile: "the main mobile number", home: "a home / after-hours phone",
    work: "a work / business phone", fax: "a fax number", mobile2: "the SECOND person's mobile",
    email2: "the SECOND person's email", street: "street address (number + street)", street2: "a second address line",
    suburb: "suburb / town", state: "state (QLD, NSW ...)", postcode: "4-digit postcode", country: "country",
    salutation: "how letters are addressed ('Dear John')", legal: "full legal name", position: "job title / position",
    type: "contact type / category", tags: "tags / labels / groups", source: "how the lead came in (enquiry source)",
    notes: "free-text notes / comments",
    team1: "the agency's agent / rep / consultant (a staff person's name, repeated across many contacts) who looks after the contact",
    created: "date the record was created", modified: "date it was last changed", do_not_sms: "SMS / text permission: an allow flag, a do-not flag or an opt-out date",
    do_not_email: "email permission: an allow flag, a do-not flag or an opt-out date", do_not_mail: "letter / mail permission: an allow flag, a do-not flag or an opt-out date",
    do_not_call: "phone permission: an allow flag, a do-not flag or an opt-out date",
    subscribed: "newsletter / marketing: subscribed flag, unsubscribed flag or an unsubscribe date",
    contactable: "an overall contactable flag (1 / yes = the contact may be contacted)",
    zenu_id: "an existing Zenu contact ID (only when the export came from Zenu)",
    subscriptions: "the mailing lists / subscriptions the contact is on",
    town_city: "town or city when the export has it separate from the suburb",
    children: "names of the contact's children",
    rating: "a contact rating / grade (Hot, Warm, A, B, 1-5 ...)",
    last_contact: "date the contact was last contacted",
    team2: "another agent / staff member who looks after the contact (team member 2)",
    team3: "another agent / staff member who looks after the contact (team member 3)",
    team4: "another agent / staff member who looks after the contact (team member 4)",
    team5: "another agent / staff member who looks after the contact (team member 5)",
    team6: "another agent / staff member who looks after the contact (team member 6)",
    team7: "another agent / staff member who looks after the contact (team member 7)",
    team8: "another agent / staff member who looks after the contact (team member 8)",
    team9: "another agent / staff member who looks after the contact (team member 9)",
    team10: "another agent / staff member who looks after the contact (team member 10)",
    team11: "another agent / staff member who looks after the contact (team member 11)",
    team12: "another agent / staff member who looks after the contact (team member 12)",
    team13: "another agent / staff member who looks after the contact (team member 13)",
    team14: "another agent / staff member who looks after the contact (team member 14)",
    team15: "another agent / staff member who looks after the contact (team member 15)",
    sale_type: "the sale type the contact is interested in (e.g. Private Treaty, Auction)",
    solicitor_id: "the Zenu ID of the contact's solicitor",
    company_id: "the Zenu ID of the contact's company"
  };
  const FLAG_RE = /^(true|false|yes|no|y|n|0|1|-1)$/i;
  function aiFits(role, samples) {
    const vals = samples.filter(Boolean);
    if (!vals.length) return false;
    const dg = (v) => digitsOf(v).length;
    if (role === "email" || role === "email2") return vals.every((v) => v.includes("@"));
    if (["mobile", "home", "work", "fax", "mobile2"].includes(role)) return vals.every((v) => dg(v) >= 6 && !v.includes("@"));
    if (role === "postcode") return vals.every((v) => /^\d{3,4}$/.test(v.trim()));
    if (role === "created" || role === "modified") return vals.every((v) => dg(v) >= 4);
    if (role.startsWith("do_not_") || role === "subscribed") return vals.every((v) => FLAG_RE.test(v.trim()));
    if (["first", "last", "first2", "last2", "title", "title2", "suburb", "state", "country"].includes(role))
      return vals.every((v) => !v.includes("@") && dg(v) <= 1);
    if (role === "salutation") return vals.every((v) => dg(v) === 0 && (v.split(/\s+/).length >= 2 || /^[A-Z]/.test(v)));
    if (role === "street" || role === "street2") return vals.some((v) => /[A-Za-z]/.test(v));
    return true;
  }
  /** A "column name" that is really a value (a file with no header row the detector missed):
   *  an email, a phone-like number or a long digit run. Never sent to Claude or saved to memory. */
  function isDataLike(h) {
    const s = String(h || "").trim();
    if (!s) return false;
    if (s.includes("@")) return true;
    if (digitsOf(s).length >= 6) return true;
    return /^[+(]?\d[\d\s()+\-./]{4,}$/.test(s);
  }
  /** 'Daryl Roallos' -> 'Aaaaa Aaaaaaa': what Claude sees when real values are kept private. */
  function shape(v) {
    return String(v).replace(/[0-9]/g, "9").replace(/[A-Z]/g, "A").replace(/[a-z]/g, "a").slice(0, 40);
  }

  // =====================================================================
  // REA MATCHING (Agentbox): the office's REA listings feed matched to the old CRM's listings
  //   inputs: listing_sale, listing_lease, property (old CRM), Contact ID (Zenu ID map), REA data
  //   REA source_id = old listing's rea_unique_id (text and numbers compared alike, unlike VLOOKUP).
  //   In scope: every REA row except Appraisal and Property records.
  // =====================================================================
  const REA_OUT_OF_SCOPE = new Set(["appraisal", "property"]);
  const REA_KINDS = {
    sale: "Listing Sale", lease: "Listing Lease", props: "Properties", contacts: "Contact ID", rea: "Office REA data",
  };
  /** Which REA-matching input a table is, from its columns (and file name as a tie-break), or "". */
  /** Source CRMs for REA matching. ready = built; the others show as coming later. slots = the input files, in order:
   *  [kind, label, hint, need]. Add a CRM here (and its reaKind / reaMatch handling) to switch it on. */
  const REA_CRMS = {
    agentbox: { label: "Agentbox", ready: true, keyNote: "REA source_id = Agentbox rea_unique_id (Listing Sale first, then Listing Lease)",
      slots: [
        ["rea", "Office REA data", "listings.csv from the REA export", "required"],
        ["sale", "Listing Sale", "listing_sale.csv", "sale or lease"],
        ["lease", "Listing Lease", "listing_lease.csv", "sale or lease"],
        ["props", "Properties", "property.csv · for addresses", "for addresses"],
        ["contacts", "Contact ID", "Zenu ID map · copied into the workbook", "optional"],
      ] },
    rex: { label: "Rex", ready: false, slots: [] },
    vaultre: { label: "VaultRE", ready: false, slots: [] },
    eagle: { label: "Eagle", ready: false, slots: [] },
  };
  /** Which REA-matching input a table is for this CRM, from its columns (file name as a tie-break), or "". */
  function reaKind(columns, name, crm = "agentbox") {
    if (crm !== "agentbox") return "";
    const norm = (x) => String(x).replace(/^\uFEFF/, "").trim().toLowerCase().replace(/[\s-]+/g, "_");
    const cols = new Set(columns.map(norm));
    const has = (c) => cols.has(c);
    const n = String(name || "").toLowerCase();
    if (has("source_contact_id") && has("contact_id")) return "contacts";
    if (has("source_id") && has("listing_status")) return "rea";
    if (has("rea_unique_id") && (has("settled_date") || has("sold_price") || has("contract_date"))) return "sale";
    if (has("rea_unique_id") && (has("leased_date") || has("listed_rent") || has("lease_start_date"))) return "lease";
    if (has("rea_unique_id")) return /lease/.test(n) ? "lease" : /sale/.test(n) ? "sale" : "";
    if (has("property_id") && has("street_name") && has("suburb") && !has("listing_id")) return "props";
    return "";
  }
  const reaKey = (v) => {
    let s = String(v ?? "").trim().toLowerCase();
    if (/^\d+\.0+$/.test(s)) s = s.replace(/\.0+$/, "");
    return s;
  };
  const reaText = (v) => String(v ?? "").trim();
  const reaJoin = (sep, parts) => parts.map(reaText).filter(Boolean).join(sep);
  const reaDate = (v) => { const s = reaText(v); return s ? formatDate(s, "date") : ""; };
  /** A row's value by column name, ignoring case and spaces (rea_unique_id = REA_UNIQUE_ID). */
  function reaGetter(columns) {
    const map = {};
    columns.forEach((c) => { const k = String(c).replace(/^\uFEFF/, "").trim().toLowerCase().replace(/[\s-]+/g, "_"); if (!(k in map)) map[k] = c; });
    return (row, col) => { const c = map[col]; return c === undefined ? "" : row[c]; };
  }
  const REA_UNIT_WORDS = new Set(["level", "lvl", "lv", "unit", "suite", "shop", "lot", "rear", "of", "ground", "floor", "apartment", "apt", "villa", "townhouse", "flat", "cnr", "corner"]);
  /** street number | first word of the street | suburb, from unit + number + street name together, however the
   *  CRM split them ("8E" / "30-34 Churchhill" = "8E 30-34" / "Churchhill Avenue"). The number is the last one
   *  before the street's name ("REAR OF 241" -> 241, "A9, 1-9" -> 1-9), so units and words before it don't count. */
  function reaStreetKey(unitNumStreet, suburb) {
    const words = reaText(unitNumStreet).toLowerCase().replace(/[\\"`‘’]/g, "").replace(/\s*[-‐-―]\s*/g, "-")
      .split(/[\s,/]+/).filter(Boolean);
    const at = words.findIndex((w) => /^[a-z][a-z']+$/.test(w) && !REA_UNIT_WORDS.has(w));
    if (at < 0) return "";
    // a range counts by its first number: 22 = 22-28
    const nums = words.slice(0, at).join(" ").match(/\d+[a-z]?(?:-\d+[a-z]?)?/g);
    const b = reaText(suburb).toLowerCase().replace(/[^a-z]/g, "");
    return b ? `${nums ? nums[nums.length - 1].split("-")[0] : ""}|${words[at].replace(/'/g, "")}|${b}` : "";
  }
  /**
   * Run the matching. frames: {sale, lease, props, contacts, rea} each {columns, rows} (sale or lease may be missing,
   * contacts is only carried through). Returns the sheets of the "REA Feed - <client> - Matching Results" workbook.
   */
  function reaMatch(frames, opts = {}) {
    const crm = REA_CRMS[opts.crm || "agentbox"];
    if (!crm) throw new Error(`Unknown CRM '${opts.crm}'.`);
    if (!crm.ready) throw new Error(`REA matching for ${crm.label} isn't built yet. Only Agentbox works for now.`);
    const client = reaText(opts.client) || "Old Office";
    if (!frames.rea) throw new Error("Load the office's REA data (listings.csv from the REA export).");
    if (!frames.sale && !frames.lease) throw new Error("Load listing_sale and/or listing_lease from the old CRM export.");
    const R = reaGetter(frames.rea.columns);
    // old listings by REA unique id: sale first, then lease (as the VLOOKUPs did); the first listing wins a shared id
    const byKey = new Map(), shared = { sale: 0, lease: 0 };
    for (const kind of ["sale", "lease"]) {
      const f = frames[kind];
      if (!f) continue;
      const G = reaGetter(f.columns);
      for (const row of f.rows) {
        const k = reaKey(G(row, "rea_unique_id"));
        if (!k) continue;
        if (byKey.has(k)) { shared[kind]++; continue; }
        byKey.set(k, { kind, row, G });
      }
    }
    const props = new Map();
    if (frames.props) {
      const P = reaGetter(frames.props.columns);
      for (const row of frames.props.rows) {
        const id = reaKey(P(row, "property_id"));
        if (!id || props.has(id)) continue;
        props.set(id, {
          address: reaJoin(" ", [reaJoin(", ", [reaJoin(" ", ["lot_num", "level_num", "unit_num", "street_num", "street_name", "street_type"].map((c) => P(row, c))), P(row, "suburb"), P(row, "state")]), P(row, "postcode")]),
          key: reaStreetKey(reaJoin(" ", [P(row, "unit_num"), P(row, "street_num"), P(row, "street_name")]), P(row, "suburb")),
        });
      }
    }
    const C = client;
    const matched = [], settled = [], unable = [], outOfScope = {};
    let different = 0, noSettledDate = 0, blankSource = 0;
    for (const row of frames.rea.rows) {
      const status = reaText(R(row, "listing_status"));
      if (REA_OUT_OF_SCOPE.has(status.toLowerCase())) { outOfScope[status] = (outOfScope[status] || 0) + 1; continue; }
      const address = reaJoin(" ", [reaJoin(", ", [reaJoin(" ", [R(row, "unit_number"), R(row, "street_number"), R(row, "street_name")]), R(row, "suburb"), R(row, "state")]), R(row, "postcode")]);
      const listed = reaDate(R(row, "date_listed")), contract = reaDate(R(row, "contract_date")), leased = reaDate(R(row, "leased_date"));
      const key = reaKey(R(row, "source_id"));
      if (!key) blankSource++;
      const hit = key ? byKey.get(key) : null;
      if (!hit) {
        unable.push({
          ID: reaText(R(row, "id")), SOURCE_ID: reaText(R(row, "source_id")), LISTING_STATUS: status, SALE_METHOD: reaText(R(row, "sale_method")),
          "Full Address": address, UNIT_NUMBER: reaText(R(row, "unit_number")), STREET_NUMBER: reaText(R(row, "street_number")),
          STREET_NAME: reaText(R(row, "street_name")), SUBURB: reaText(R(row, "suburb")), POSTCODE: reaText(R(row, "postcode")), STATE: reaText(R(row, "state")),
          "Extracted Listed Date": listed, "Extracted Contract Date": contract, "Extracted Leased Date": leased,
        });
        continue;
      }
      const G = hit.G, old = hit.row;
      const pid = reaText(G(old, "property_id")), prop = props.get(reaKey(pid));
      const reaStreet = reaStreetKey(reaJoin(" ", [R(row, "unit_number"), R(row, "street_number"), R(row, "street_name")]), R(row, "suburb"));
      const check = !prop ? (frames.props ? "No property in " + C : "") : prop.key && reaStreet && prop.key === reaStreet ? "Same" : "Different";
      if (check === "Different") different++;
      const oldStatus = reaText(G(old, "status"));
      const m = {
        Indicator: "Matched", "REA ID": reaText(R(row, "id")), "REA SOURCE_ID": reaText(R(row, "source_id")), "REA LISTING_STATUS": status,
        "REA SALE_METHOD": reaText(R(row, "sale_method")), "REA Full Address": address,
        "REA Extracted Listed Date": listed, "REA Extracted Contract Date": contract, "REA Extracted Leased Date": leased,
        [`${C} Listing ID`]: reaText(G(old, "listing_id")), [`${C} Property ID`]: pid, [`${C} REA ID`]: reaText(G(old, "rea_unique_id")),
        [`${C} Status`]: oldStatus, [`${C} Sale/Lease`]: reaText(G(old, "listing_type")) || hit.kind,
        [`${C} Property Address`]: prop ? prop.address : "",
        [`${C} Listed Date`]: reaDate(G(old, "listed_date")),
        [`${C} Contract Date`]: hit.kind === "sale" ? reaDate(G(old, "contract_date")) : "",
        [`${C} Leased Date`]: hit.kind === "lease" ? reaDate(G(old, "leased_date")) : "",
        "Address Check": check,
      };
      matched.push(m);
      if (status.toLowerCase() === "sold" && oldStatus.toLowerCase() === "settled") {
        const sd = hit.kind === "sale" ? reaDate(G(old, "settled_date")) : "";
        if (!sd) noSettledDate++;
        settled.push({
          "REA ID": m["REA ID"], "REA SOURCE_ID": m["REA SOURCE_ID"], "REA LISTING_STATUS": status,
          [`${C} Listing ID`]: m[`${C} Listing ID`], [`${C} Property ID`]: pid, [`${C} REA ID`]: m[`${C} REA ID`],
          [`${C} Status`]: oldStatus, "Settled Date": sd,
        });
      }
    }
    const summary = [
      { "Sheet Name": "Matched", "# of items": matched.length,
        Description: `A confirmed match between your old office and REA Extract can be found for the in-scope listings that have come through from REA. Matched based on REA ID.` +
          (different ? `\n\nPlease note that there are ${different} listings that matched with REA ID but have a different address. Please highlight anything you would like to remove from the list.` : ""),
        "Action Needed": "Once approved. We will proceed to attach the corresponding data like vendor/purchaser to these REA listings." },
      { "Sheet Name": "Sold to Settled", "# of items": settled.length,
        Description: `Out of the matched Sold properties, these are the ones that are in 'Settled' status in your old database\n\nWe will be updating these to 'Settled' status in Zenu.`,
        "Action Needed": "Please let us know if you want to keep these properties as Sold." +
          (noSettledDate ? `\n${noSettledDate} listings have NO settlement date. Please fill these in if you want them to be updated.` : "") },
      { "Sheet Name": "Unable to match", "# of items": unable.length,
        Description: "Unable to find address in Old office extract.",
        "Action Needed": `No action needed from you. Please note that we will NOT be able to attach any old ${C} data to these.` },
    ];
    const reaRows = frames.rea.rows.length, inScope = matched.length + unable.length;
    return {
      client: C, summary, matched, settled, unable,
      stats: { reaRows, inScope, outOfScope, matched: matched.length, settled: settled.length, unable: unable.length,
        differentAddress: different, noSettledDate, blankSourceId: blankSource, oldListings: byKey.size, sharedReaIds: shared.sale + shared.lease },
      columns: {
        matched: ["Indicator", "REA ID", "REA SOURCE_ID", "REA LISTING_STATUS", "REA SALE_METHOD", "REA Full Address", "REA Extracted Listed Date", "REA Extracted Contract Date", "REA Extracted Leased Date",
          `${C} Listing ID`, `${C} Property ID`, `${C} REA ID`, `${C} Status`, `${C} Sale/Lease`, `${C} Property Address`, `${C} Listed Date`, `${C} Contract Date`, `${C} Leased Date`, "Address Check"],
        settled: ["REA ID", "REA SOURCE_ID", "REA LISTING_STATUS", `${C} Listing ID`, `${C} Property ID`, `${C} REA ID`, `${C} Status`, "Settled Date"],
        unable: ["ID", "SOURCE_ID", "LISTING_STATUS", "SALE_METHOD", "Full Address", "UNIT_NUMBER", "STREET_NUMBER", "STREET_NAME", "SUBURB", "POSTCODE", "STATE", "Extracted Listed Date", "Extracted Contract Date", "Extracted Leased Date"],
        summary: ["Sheet Name", "# of items", "Description", "Action Needed"],
      },
    };
  }

  /** An input table with its full address added (Properties: "Full Address" after postcode; REA data: "full add"
   *  after state), as the matching results workbook shows them. */
  function reaWithAddress(frame, kind) {
    const G = reaGetter(frame.columns);
    const isRea = kind === "rea";
    const name = isRea ? "full add" : "Full Address";
    if (frame.columns.some((c) => String(c).trim().toLowerCase() === name.toLowerCase())) return frame;
    const after = frame.columns.findIndex((c) => String(c).trim().toLowerCase() === (isRea ? "state" : "postcode"));
    const columns = frame.columns.slice();
    columns.splice(after < 0 ? columns.length : after + 1, 0, name);
    const rows = frame.rows.map((row) => ({ ...row, [name]: isRea
      ? reaJoin(" ", [reaJoin(", ", [reaJoin(" ", [G(row, "unit_number"), G(row, "street_number"), G(row, "street_name")]), G(row, "suburb"), G(row, "state")]), G(row, "postcode")])
      : reaJoin(" ", [reaJoin(", ", [reaJoin(" ", ["lot_num", "level_num", "unit_num", "street_num", "street_name", "street_type"].map((c) => G(row, c))), G(row, "suburb"), G(row, "state")]), G(row, "postcode")]) }));
    return { columns, rows };
  }

  const api = {
    cell, normalizeHeader, splitNames, ContactDetailsCleaner, isValidEmail, AddressSplitter, normPlace, closeMatch,
    ROLES, ROLE_HINTS, SUMMARY_COLUMNS, excelTrimClean, contactNo, dateDdmmyyyy, parseDate, formatDate, DATE_MODES, DATE_ROLES,
    FLAG_ROLES, FLAG_OUT, flagModeText, isContactable, suggestFlagMode, legacyFlagMode, cleanFlagMode, flagAllowed, flagText, nameBlocks, emailOkay, propose, GeneralCleaner,
    agentbox: { TYPE_LIST, TYPE_COL, TAGS_COL, TEAM_COL, NOTES_COL, CONTACT_MAPPING, FILES: AB_FILES, kindOf, splitTypeTags,
      decodeComment, categoriesByContact, agentNames, teamMember1, build: buildAgentbox, linkNotes },
    toFrame, ROLE_HELP, aiFits, shape, isDataLike,
    importPrep: { run: importPrep, toCsv, clean: importPrepClean, stringify: importPrepStringify, MAX_ROWS: IP_MAX_ROWS },
    reaMatch: { run: reaMatch, kind: reaKind, KINDS: REA_KINDS, CRMS: REA_CRMS, withAddress: reaWithAddress },
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DarisEngine = api;
})(typeof window !== "undefined" ? window : globalThis);

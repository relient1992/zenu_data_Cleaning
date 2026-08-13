/*
 * Zenu Smart Matcher — pure mapping logic, no DOM.
 * Loaded by smart_mapper.html in the browser, and by test_matcher.js in Node.
 *
 * Given client column headers + sample values, scores each against the Zenu
 * unified schema (window.ZENU_FIELDS) and returns a unique column -> field map.
 */
(function (root) {
  'use strict';

  /* ------------------------------------------------------------------
   * Alias dictionary: Zenu field -> phrases other CRMs commonly use.
   * Carries the matcher through headers that share no words with ours
   * (e.g. "Cell" -> contact_mobile, "Beds" -> property_bedrooms).
   * ------------------------------------------------------------------ */
  var ALIASES = {
    contact_identifier: ['contact id', 'client id', 'customer id', 'record id', 'unique id', 'external id', 'crm id', 'source id', 'id',
                         'cust no', 'cust id', 'customer no', 'customer number', 'client no', 'client number', 'account no',
                         'account number', 'reference', 'ref', 'contact ref', 'client ref', 'contact no', 'contact number'],
    contact_title: ['salutation', 'prefix', 'honorific', 'title', 'ttl'],
    contact_first_name: ['first name', 'fname', 'given name', 'givenname', 'forename', 'christian name', 'firstname',
                         'given names', 'givennames', 'first names', 'f name', 'first'],
    contact_surname: ['last name', 'lname', 'family name', 'lastname', 'surname', 'l name', 'last', 'sname'],
    contact_is_company: ['is company', 'is business', 'company flag', 'is organisation', 'is organization', 'business flag'],
    contact_company_name: ['company', 'company name', 'business name', 'organisation', 'organization', 'trading name', 'employer'],
    contact_name_on_letters: ['name on letters', 'letter name', 'addressee', 'salutation line', 'mail name'],
    contact_legal_name: ['legal name', 'full legal name', 'registered name'],
    contact_company_position: ['position', 'job title', 'role at company', 'occupation', 'designation'],
    contact_type: ['contact type', 'client type', 'record type', 'contact category'],
    contact_tags: ['tags', 'labels', 'keywords', 'groups', 'tag list'],
    contact_subscriptions: ['subscriptions', 'mailing lists', 'subscribed lists', 'newsletters'],
    contact_mailing_address_line1: ['address', 'address 1', 'address line 1', 'street address', 'postal address', 'mailing address', 'addr1', 'address1',
                                    'residential address', 'home address', 'addr', 'addr line 1', 'street 1'],
    contact_mailing_address_line2: ['address 2', 'address line 2', 'addr2', 'address2', 'addr line 2', 'street 2'],
    contact_mailing_address_suburb: ['suburb', 'locality', 'mailing suburb', 'postal suburb', 'sub'],
    contact_mailing_address_town_city: ['city', 'town', 'town city', 'mailing city', 'postal city', 'post town', 'city town'],
    contact_mailing_address_state: ['state', 'region', 'province', 'mailing state', 'postal state', 'st'],
    contact_mailing_address_country: ['country', 'mailing country', 'postal country'],
    contact_mailing_address_postcode: ['postcode', 'post code', 'zip', 'zip code', 'zipcode', 'postal code', 'pcode', 'p code', 'post cd'],
    contact_email_address: ['email', 'e mail', 'email address', 'primary email', 'email 1', 'emailaddress',
                            'email addr', 'primary email addr', 'e mail address', 'main email'],
    contact_mobile: ['mobile', 'cell', 'cell phone', 'cellphone', 'mobile phone', 'mob', 'mobile number', 'sms', 'mobile ph', 'mob ph'],
    contact_phone_work: ['work phone', 'business phone', 'office phone', 'phone work', 'telephone work', 'work number', 'phone business', 'work ph', 'ph work', 'bus ph', 'business ph', 'office ph', 'work tel'],
    contact_phone_home: ['home phone', 'phone home', 'landline', 'private phone', 'home number', 'phone', 'telephone', 'home ph', 'ph home', 'ph', 'tel', 'phone number'],
    contact_fax: ['fax', 'facsimile', 'fax number', 'fax no'],
    contact_allow_contact_via_text: ['allow sms', 'sms opt in', 'can text', 'consent sms'],
    contact_allow_contact_via_email: ['allow email', 'email opt in', 'can email', 'consent email'],
    contact_allow_contact_via_letter: ['allow mail', 'allow letter', 'can post', 'consent mail'],
    contact_allow_contact_via_phone: ['allow phone', 'phone opt in', 'can call', 'consent phone'],
    contact_unsubscribe_from_marketing: ['unsubscribe', 'opt out', 'do not market', 'marketing opt out', 'do not contact', 'no marketing',
                                         'opt out flag', 'unsubscribed', 'dnc', 'do not mail'],
    contact_childrens_names: ['children', 'childrens names', 'kids', 'dependants'],
    contact_enquiry_source: ['source', 'lead source', 'enquiry source', 'referral source', 'origin', 'how did you hear', 'marketing source'],
    contact_rating: ['rating', 'grade', 'priority', 'star rating', 'score', 'classification'],
    contact_partner_identifier: ['partner id', 'spouse id', 'linked contact', 'related contact'],
    contact_partnership_type: ['partnership type', 'relationship', 'relationship type', 'spouse type'],
    contact_last_contact_date: ['last contact', 'last contacted', 'last activity', 'last touch', 'last call'],
    contact_date_created: ['created', 'date created', 'created date', 'created at', 'entry date', 'date added', 'added on',
                           'date of entry', 'date entered', 'creation date', 'added date'],
    contact_date_modified: ['modified', 'updated', 'last modified', 'date modified', 'updated at', 'last updated'],
    contact_criteria_sale_method: ['preferred sale method', 'criteria sale method', 'buying method'],
    contact_criteria_category: ['criteria category', 'looking for category'],
    contact_criteria_property_type: ['criteria property type', 'wanted property type', 'looking for type'],
    contact_criteria_price_from: ['price from', 'min price', 'budget from', 'budget min', 'price min', 'minimum price'],
    contact_criteria_price_to: ['price to', 'max price', 'budget to', 'budget max', 'price max', 'maximum price', 'budget'],
    contact_criteria_bedrooms: ['criteria bedrooms', 'wanted bedrooms', 'min bedrooms', 'bedrooms required'],
    contact_criteria_bathrooms: ['criteria bathrooms', 'wanted bathrooms', 'min bathrooms'],
    contact_criteria_carspaces: ['criteria carspaces', 'wanted carspaces', 'min carspaces', 'parking required'],
    contact_criteria_land_from: ['land from', 'min land', 'land size from'],
    contact_criteria_land_to: ['land to', 'max land', 'land size to'],
    contact_criteria_suburbs: ['criteria suburbs', 'wanted suburbs', 'preferred suburbs', 'areas of interest', 'search suburbs'],
    contact_team_member_1: ['agent', 'primary agent', 'assigned to', 'owner', 'salesperson', 'consultant', 'team member', 'account manager', 'agent name', 'responsible'],
    contact_team_member_2: ['secondary agent', 'agent 2', 'co agent', 'second agent'],
    contact_sale_type: ['sale type'],
    contact_notes: ['notes', 'note', 'comments', 'remarks', 'memo', 'note text', 'comment'],
    contact_note_created_date: ['note date', 'note created', 'comment date'],
    contact_note_team_member: ['note author', 'note by', 'note agent', 'comment by'],

    property_identifier: ['property id', 'listing id', 'property reference', 'listing reference', 'property ref'],
    property_unit_number: ['unit', 'unit number', 'apartment', 'apt', 'flat', 'flat number', 'unit no'],
    property_street_number: ['street number', 'street no', 'house number', 'st number', 'streetnumber'],
    property_street_name: ['street', 'street name', 'road', 'streetname', 'road name'],
    property_suburb: ['suburb', 'locality', 'property suburb'],
    property_postcode: ['postcode', 'post code', 'zip', 'zip code', 'postal code', 'property postcode'],
    property_state: ['state', 'region', 'province', 'property state'],
    property_country: ['country', 'property country'],
    property_full_address: ['full address', 'address', 'property address', 'display address', 'complete address', 'street address'],
    property_building_name: ['building', 'building name', 'complex', 'complex name', 'estate name'],
    property_sale_method: ['sale method', 'method of sale', 'listing method', 'auction or private'],
    property_category: ['property category', 'listing category', 'class'],
    property_type: ['property type', 'dwelling type', 'house type', 'dwelling'],
    property_bedrooms: ['bedrooms', 'beds', 'bed', 'br', 'no of bedrooms', 'number of bedrooms', 'bedroom'],
    property_bathrooms: ['bathrooms', 'baths', 'bath', 'ba', 'no of bathrooms', 'number of bathrooms', 'bathroom'],
    property_toilets: ['toilets', 'wc', 'powder rooms', 'ensuites'],
    property_total_rooms: ['total rooms', 'rooms', 'number of rooms'],
    property_garages: ['garage', 'garages', 'car spaces', 'carspaces', 'garage spaces', 'cars', 'parking spaces', 'parking'],
    property_carports: ['carport', 'carports', 'covered parking'],
    property_open_parking_spaces: ['open parking', 'open spaces', 'uncovered parking', 'off street parking'],
    property_living_area_m2: ['living area', 'floor area', 'building area', 'internal area', 'house size', 'floor size', 'living size'],
    property_land_size_m2: ['land size', 'land area', 'land', 'block size', 'lot size', 'site area', 'sqm', 'land sqm'],
    property_modified_date: ['property modified', 'listing updated', 'last modified'],
    property_appraisal_date: ['appraisal date', 'appraised', 'valuation date', 'cma date'],
    property_timeline_status: ['status', 'property status', 'listing status', 'timeline status', 'stage', 'state of sale'],
    property_team_member_1: ['agent', 'primary agent', 'listing agent', 'assigned to', 'salesperson', 'consultant', 'team member', 'agent name', 'lead agent'],
    property_team_member_2: ['secondary agent', 'agent 2', 'co agent', 'second agent'],
    property_is_occupied_by_owner: ['owner occupied', 'occupied by owner', 'is owner occupier'],
    property_appraisal_source: ['appraisal source', 'valuation source'],
    property_search_price: ['search price', 'advertised price', 'display price', 'asking price', 'list price', 'listed price'],
    property_vendor_price: ['vendor price', 'vendor expectation', 'reserve price', 'vendor asking'],
    property_estimated_commission: ['estimated commission', 'est commission', 'expected commission'],
    property_pipeline_rating: ['pipeline rating', 'pipeline', 'likelihood', 'probability'],
    property_contract_date: ['contract date', 'exchange date', 'date of contract', 'under contract date'],
    property_unconditional_date: ['unconditional date', 'unconditional', 'finance approved date'],
    property_settlement_date: ['settlement date', 'settlement', 'settled', 'settled date', 'completion date'],
    property_sold_price: ['sold price', 'sale price', 'sold for', 'sold amount', 'final price', 'purchase price'],
    property_sale_team_member: ['selling agent', 'sold by', 'sale agent'],
    property_sale_gross_commission: ['gross commission', 'gci', 'commission', 'total commission'],
    property_year_built: ['year built', 'built', 'construction year', 'built year'],
    property_council_name: ['council', 'council name', 'lga', 'local government', 'municipality'],
    property_council_zoning: ['zoning', 'zone', 'council zoning', 'land zoning'],
    property_lot_number: ['lot', 'lot number', 'lot no'],
    property_title_number: ['title number', 'title ref', 'title reference', 'volume folio', 'certificate of title'],
    property_rent_per_week: ['rent', 'rent per week', 'weekly rent', 'rent pw', 'rental amount', 'rent amount'],
    property_last_listed_price: ['last listed price', 'previous list price'],
    property_current_price: ['current price', 'price'],
    property_last_listed_date: ['last listed date', 'previously listed', 'last listing date'],
    property_last_sold_date: ['last sold date', 'previous sale date', 'last sale date'],
    property_last_sold_price: ['last sold price', 'previous sale price', 'last sale price'],
    property_last_listed_by_agency: ['last listed by agency', 'previous agency', 'other agency'],
    property_last_listed_by_agent: ['last listed by agent', 'previous agent'],
    property_last_rent_pw: ['last rent', 'previous rent', 'last weekly rent'],
    property_notes: ['notes', 'note', 'comments', 'remarks', 'memo', 'property notes'],
    property_note_created_date: ['note date', 'note created'],
    property_note_team_member: ['note author', 'note by'],

    task_identifier: ['task id', 'activity id'],
    task_subject: ['subject', 'task', 'task name', 'activity', 'task subject'],
    task_notes: ['task notes', 'task details'],
    task_status: ['task status', 'completed'],
    task_team_member_1: ['assigned to', 'task owner', 'task agent'],
    task_date_due: ['due date', 'due', 'date due', 'deadline', 'follow up date'],

    enquiry_identifier: ['enquiry id', 'inquiry id', 'lead id'],
    enquiry_notes: ['enquiry notes', 'inquiry notes', 'enquiry details', 'message'],
    enquiry_status: ['enquiry status', 'inquiry status', 'lead status'],
    enquiry_source: ['enquiry source', 'inquiry source', 'portal', 'channel'],
    enquiry_team_member_1: ['enquiry agent', 'handled by'],
    enquiry_date_created: ['enquiry date', 'inquiry date', 'date of enquiry', 'lead date'],

    inspection_identifier: ['inspection id', 'viewing id', 'open home id'],
    inspection_notes: ['inspection notes', 'viewing notes', 'feedback'],
    inspection_start_date: ['inspection date', 'inspection start', 'viewing date', 'open home start', 'start time'],
    inspection_end_date: ['inspection end', 'open home end', 'end time'],
    inspection_is_private: ['private inspection', 'is private', 'private viewing'],
    inspection_team_member_1: ['inspection agent', 'conducted by'],
    inspection_is_interested: ['interested', 'buyer interested', 'interest level'],
    inspection_feedback_price: ['feedback price', 'buyer feedback price', 'indicated price'],

    offer_identifier: ['offer id'],
    offer_price: ['offer price', 'offer amount', 'offered', 'offer'],
    offer_terms: ['offer terms', 'terms', 'conditions'],
    offer_date: ['offer date', 'date of offer', 'date offered'],
    offer_team_member_1: ['offer agent', 'received by']
  };

  var STOP = { the: 1, a: 1, an: 1, of: 1, and: 1, or: 1, to: 1, in: 1, on: 1, for: 1, no: 1, nbr: 1, num: 1 };

  var PREFIXES = ['contact_mailing_address_', 'contact_criteria_', 'contact_', 'property_', 'listing_',
                  'task_', 'enquiry_', 'inspection_', 'offer_', 'zenu_'];

  var THRESHOLD = 42;

  /* ---------------- text helpers ---------------- */
  function norm(s) { return String(s == null ? '' : s).toLowerCase().replace(/[^a-z0-9]/g, ''); }

  function tokenize(s) {
    return String(s == null ? '' : s)
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')   // camelCase -> camel Case
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter(function (t) { return t && !STOP[t]; });
  }

  function dice(a, b) {
    if (!a.length || !b.length) return 0;
    var i, setB = {}, ua = {}, ub = {}, seen = {}, hit = 0;
    for (i = 0; i < b.length; i++) { setB[b[i]] = 1; ub[b[i]] = 1; }
    for (i = 0; i < a.length; i++) {
      ua[a[i]] = 1;
      if (setB[a[i]] && !seen[a[i]]) { hit++; seen[a[i]] = 1; }
    }
    return (2 * hit) / (Object.keys(ua).length + Object.keys(ub).length);
  }

  function trigrams(s) {
    var p = '  ' + s + ' ', out = [], i;
    for (i = 0; i < p.length - 2; i++) out.push(p.substr(i, 3));
    return out;
  }

  function trigramSim(a, b) {
    if (!a || !b) return 0;
    if (a === b) return 1;
    return dice(trigrams(a), trigrams(b));
  }

  /* ---------------- value shape detection ---------------- */
  var RE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  var RE_PHONE = /^[+()\d][\d\s()+\-.]{5,}$/;
  var RE_INT = /^-?\d+$/;
  var RE_NUM = /^-?\d+(\.\d+)?$/;
  var RE_MONEY = /^[$€£]?\s?-?[\d,]+(\.\d{1,2})?$/;
  var RE_BOOL = /^(true|false|yes|no|y|n)$/i;
  var RE_DATE = /^(\d{1,4}[\/\-.]\d{1,2}[\/\-.]\d{1,4})([ T]\d{1,2}:\d{2})?/;

  function detectShape(values) {
    var vals = (values || []).filter(function (v) { return v !== '' && v != null; });
    if (!vals.length) return { kind: 'empty' };
    var n = vals.length, c = { email: 0, phone: 0, int: 0, num: 0, money: 0, bool: 0, date: 0, postcode: 0 };
    vals.forEach(function (raw) {
      var isDate = Object.prototype.toString.call(raw) === '[object Date]';
      var v = String(raw).trim();
      if (RE_EMAIL.test(v)) c.email++;
      if (RE_PHONE.test(v) && v.replace(/\D/g, '').length >= 6) c.phone++;
      if (RE_INT.test(v)) {
        c.int++;
        var iv = parseInt(v, 10);
        if (v.length === 4 && iv >= 800 && iv <= 9999) c.postcode++;
      }
      if (RE_NUM.test(v)) c.num++;
      if (RE_MONEY.test(v) && /[,$€£]|\d{4,}/.test(v)) c.money++;
      if (RE_BOOL.test(v)) c.bool++;
      if (isDate || RE_DATE.test(v)) c.date++;
    });
    var f = function (k) { return c[k] / n; };
    if (f('email') > 0.7) return { kind: 'email' };
    if (f('bool') > 0.85) return { kind: 'bool' };
    if (f('date') > 0.7) return { kind: 'date' };
    if (f('phone') > 0.7 && f('int') < 0.95) return { kind: 'phone' };
    if (f('postcode') > 0.7) return { kind: 'postcode' };
    if (f('money') > 0.7 && f('int') < 0.6) return { kind: 'money' };
    if (f('int') > 0.85) return { kind: 'int' };
    if (f('num') > 0.85) return { kind: 'num' };
    return { kind: 'text' };
  }

  // Nudge candidates whose declared type agrees with the sampled values.
  function typeDelta(shape, field) {
    var n = field.name, t = field.type, k = shape.kind;
    var isPhoneField = /(mobile|phone|fax)/.test(n);
    var isEmailField = /email/.test(n);
    var isPostField = /postcode/.test(n);
    var isMoneyField = /(price|commission|amount|rent|recognition)/.test(n);

    if (k === 'email') return isEmailField ? 12 : (t === 'date' || t === 'integer' ? -26 : -6);
    if (k === 'phone') return isPhoneField ? 12 : (t === 'date' ? -18 : -4);
    if (k === 'postcode') return isPostField ? 14 : (t === 'date' ? -14 : 0);
    if (k === 'bool') return t === 'true/false' ? 13 : -12;
    if (k === 'date') return t === 'date' ? 10 : (t === 'integer' ? -14 : -3);
    if (k === 'money') return isMoneyField ? 10 : (t === 'date' ? -14 : 0);
    if (k === 'int') return t === 'integer' ? 6 : (t === 'date' ? -12 : (isEmailField ? -14 : 0));
    if (k === 'num') return t === 'integer' ? 4 : 0;
    if (k === 'text') return t === 'text' ? 3 : (t === 'integer' ? -5 : (t === 'true/false' ? -8 : 0));
    return 0;
  }

  /* ---------------- field preparation ---------------- */
  function prepareFields(fields) {
    fields.forEach(function (f) {
      if (f._norm) return;   // already prepared
      f._norm = norm(f.name);
      f._tokens = tokenize(f.name);
      var base = f.name;
      for (var i = 0; i < PREFIXES.length; i++) {
        if (base.indexOf(PREFIXES[i]) === 0) { base = base.slice(PREFIXES[i].length); break; }
      }
      f._base = base;
      f._baseNorm = norm(base);
      f._baseTokens = tokenize(base);
      f._aliasNorm = {};
      f._aliasTokens = [];
      (ALIASES[f.name] || []).forEach(function (a) {
        f._aliasNorm[norm(a)] = 1;
        f._aliasTokens.push(tokenize(a));
      });
    });
    return fields;
  }

  /* Enrich raw {header, values} into scoring-ready columns. */
  function prepareColumns(raw) {
    return raw.map(function (c, i) {
      var vals = (c.values || []).filter(function (v) { return v !== '' && v != null; }).slice(0, 60);
      var samples = [], seen = {};
      for (var k = 0; k < vals.length && samples.length < 3; k++) {
        var sv = String(vals[k]).trim();
        if (sv && !seen[sv]) { seen[sv] = 1; samples.push(sv); }
      }
      return {
        header: c.header, index: i,
        norm: norm(c.header), tokens: tokenize(c.header),
        values: vals, samples: samples, shape: detectShape(vals)
      };
    });
  }

  /* ---------------- scoring ---------------- */
  function scoreField(col, field, bias) {
    var base = 0, why = '';
    if (col.norm === field._norm) { base = 100; why = 'exact field name'; }
    else if (col.norm === field._baseNorm) { base = 96; why = 'exact name match'; }
    else if (field._aliasNorm[col.norm]) { base = 93; why = 'known CRM alias'; }
    else {
      var i, tok = Math.max(dice(col.tokens, field._baseTokens), dice(col.tokens, field._tokens));
      for (i = 0; i < field._aliasTokens.length; i++) {
        tok = Math.max(tok, dice(col.tokens, field._aliasTokens[i]));
      }
      var tri = Math.max(trigramSim(col.norm, field._baseNorm), trigramSim(col.norm, field._norm));
      base = 89 * (0.7 * tok + 0.3 * tri);
      why = tok >= 0.85 ? 'token match' : (tok >= 0.5 ? 'partial token match' : 'fuzzy similarity');
    }

    var score = base;
    var td = typeDelta(col.shape, field);
    // a type penalty must never undo a near-certain name match
    score += (base >= 93 && td < 0) ? 0 : td;
    if (td > 0 && base < 93) why += ' + value type';

    // Clamp BEFORE the entity bias, otherwise two equally-named fields from
    // different entities (contact_..._postcode vs property_postcode) both
    // saturate at 100 and the bias can no longer break the tie.
    if (score > 100) score = 100;
    if (bias) score += (field.group === bias) ? 5 : -7;
    if (score > 100) score = 100;
    if (score < 0) score = 0;
    return { score: score, why: why };
  }

  function rank(col, fields, bias, limit) {
    var out = [], i, r;
    for (i = 0; i < fields.length; i++) {
      r = scoreField(col, fields[i], bias);
      if (r.score >= 25) out.push({ field: fields[i], score: r.score, why: r.why });
    }
    out.sort(function (a, b) { return b.score - a.score; });
    return out.slice(0, limit || 10);
  }

  var ENTITY_HINTS = [
    ['contact', ['contact', 'client', 'customer', 'people', 'owner', 'vendor', 'buyer', 'lead']],
    ['property', ['propert', 'listing', 'address', 'sale', 'sold', 'appraisal']],
    ['task', ['task', 'activity', 'todo']],
    ['enquiry', ['enquir', 'inquir']],
    ['inspection', ['inspect', 'openhome', 'viewing']],
    ['offer', ['offer']]
  ];

  function inferEntity(cols, fields, hint) {
    var tally = {}, h = norm(hint || '');
    cols.forEach(function (c) {
      var top = rank(c, fields, null, 1)[0];
      if (top && top.score >= 55) tally[top.field.group] = (tally[top.field.group] || 0) + top.score;
    });
    ENTITY_HINTS.forEach(function (pair) {
      pair[1].forEach(function (kw) {
        if (h.indexOf(kw) !== -1) tally[pair[0]] = (tally[pair[0]] || 0) + 120;
      });
    });
    var best = null;
    Object.keys(tally).forEach(function (g) { if (!best || tally[g] > tally[best]) best = g; });
    return best || 'contact';
  }

  /* Greedy unique assignment: the strongest (column, field) pairs win first, so
     two client columns never claim the same Zenu field. */
  function autoMap(cols, fields, entityMode, hint) {
    prepareFields(fields);
    var bias = (!entityMode || entityMode === 'auto') ? inferEntity(cols, fields, hint) : entityMode;

    var pairs = [];
    cols.forEach(function (c, ci) {
      c.candidates = rank(c, fields, bias, 10);
      c.candidates.forEach(function (cand, rankIdx) {
        pairs.push({
          ci: ci, name: cand.field.name, score: cand.score, why: cand.why,
          sameEntity: cand.field.group === bias,
          isTop: rankIdx === 0
        });
      });
    });
    pairs.sort(function (a, b) { return b.score - a.score; });

    var usedField = {}, doneCol = {}, map = {};
    pairs.forEach(function (p) {
      if (doneCol[p.ci] || usedField[p.name] || p.score < THRESHOLD) return;
      // Block cross-entity spillover: once a column's own best match is taken,
      // don't hand it a different entity's field (e.g. a second "Comments"
      // column in a contact file must not land on property_notes). A column
      // whose *top* match is another entity is still allowed, so genuinely
      // mixed contact+property exports keep working.
      if (!p.sameEntity && !p.isTop) return;
      doneCol[p.ci] = 1;
      usedField[p.name] = 1;
      map[p.ci] = { name: p.name, score: p.score, why: p.why, manual: false };
    });
    return { entity: bias, map: map };
  }

  var API = {
    ALIASES: ALIASES,
    THRESHOLD: THRESHOLD,
    norm: norm,
    tokenize: tokenize,
    detectShape: detectShape,
    prepareFields: prepareFields,
    prepareColumns: prepareColumns,
    scoreField: scoreField,
    rank: rank,
    inferEntity: inferEntity,
    autoMap: autoMap
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = API;
  else root.ZenuMatcher = API;
})(typeof self !== 'undefined' ? self : this);

/* DARVIS CRM preview: draws an import.json (import.schema.json v1) as contact and listing pages laid out like the CRM,
   so you can see where each value lands before importing. A simulation only: it reads the JSON and changes nothing.
   window.DarisCrmPreview.create(container) -> { load(doc, label), clear() } */
(function (root) {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const has = (v) => v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && !v.length);
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  /** 2026-11-13 -> Fri, 13 Nov 26 (and the time, when there is one) */
  function day(v) {
    const m = String(v || "").match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
    if (!m) return v ? String(v) : "";
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    let t = `${DAYS[d.getUTCDay()]}, ${m[3]} ${MON[+m[2] - 1]} ${m[1].slice(2)}`;
    if (m[4] !== undefined) { const h = +m[4]; t += ` ${h % 12 || 12}:${m[5]}${h < 12 ? "am" : "pm"}`; }
    return t;
  }
  const money = (n) => (typeof n === "number" ? "$" + n.toLocaleString("en-AU", { maximumFractionDigits: 2 }) : "");
  /** "Daryl Roallos" -> "D.Roa", as the CRM signs activity */
  const staffShort = (s) => { const p = String(s || "").trim().split(/\s+/); return p.length > 1 ? `${p[0][0]}.${p[p.length - 1].slice(0, 3)}` : String(s || ""); };
  const initials = (s) => String(s || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

  // ------------------------------------------------------------------ index the document
  function buildIndex(doc) {
    const X = { contacts: [], listings: [], C: new Map(), L: new Map(), vendorOf: new Map(), buyerOf: new Map(), solOf: new Map(),
      actC: new Map(), actL: new Map(), rel: new Map(), members: new Map(), counts: {} };
    const push = (m, k, v) => { k = String(k); let a = m.get(k); if (!a) m.set(k, (a = [])); a.push(v); };
    for (const kind of ["create", "update"]) {
      ((doc.contacts || {})[kind] || []).forEach((rec, i) => { const x = { rec, kind, id: String(rec.contact_id), path: `contacts.${kind}[${i}]` }; X.contacts.push(x); X.C.set(x.id, x); });
      ((doc.listings || {})[kind] || []).forEach((rec, i) => { const x = { rec, kind, id: String(rec.listing_id), path: `listings.${kind}[${i}]` }; X.listings.push(x); X.L.set(x.id, x); });
    }
    for (const l of X.listings) {
      (l.rec.vendor_contact_ids || []).forEach((id, i) => push(X.vendorOf, id, { l, path: `${l.path}.vendor_contact_ids[${i}]` }));
      const ct = l.rec.contract || {};
      (ct.buyer_contact_ids || []).forEach((id, i) => push(X.buyerOf, id, { l, path: `${l.path}.contract.buyer_contact_ids[${i}]` }));
      if (has(l.rec.vendor_solicitor_contact_id)) push(X.solOf, l.rec.vendor_solicitor_contact_id, { l, role: "Vendor's solicitor", path: `${l.path}.vendor_solicitor_contact_id` });
      if (has(ct.buyer_solicitor_contact_id)) push(X.solOf, ct.buyer_solicitor_contact_id, { l, role: "Buyer's solicitor", path: `${l.path}.contract.buyer_solicitor_contact_id` });
    }
    for (const [sec, type] of [["tasks", "Task"], ["enquiries", "Enquiry"], ["inspections", "Inspection"], ["offers", "Offer"]]) {
      const list = ((doc[sec] || {}).create || []);
      X.counts[sec] = list.length;
      list.forEach((r, i) => {
        const a = { type, r, path: `${sec}.create[${i}]` };
        if (has(r.contact_id)) push(X.actC, r.contact_id, a);
        if (has(r.listing_id)) push(X.actL, r.listing_id, a);
      });
    }
    for (const c of X.contacts) {
      (c.rec.relationships || []).forEach((r, i) => {
        push(X.rel, c.id, { id: String(r.contact_id), type: r.type, path: `${c.path}.relationships[${i}]` });
        push(X.rel, r.contact_id, { id: c.id, type: r.type, other: true, path: `${c.path}.relationships[${i}]` });   // Zenu adds the other side
      });
      if (has(c.rec.company_contact_id)) push(X.members, c.rec.company_contact_id, c);
    }
    return X;
  }
  function contactName(X, id) {
    const c = X.C.get(String(id));
    if (!c) return `Existing contact #${id}`;
    const r = c.rec;
    if (r.is_company && r.company_name) return r.company_name;
    const n = [r.first_name, r.last_name].filter(has).join(" ");
    return n || r.company_name || (c.kind === "update" ? `Contact #${r.contact_id}` : r.identifier);
  }
  function listingAddress(X, id, short) {
    const l = X.L.get(String(id));
    if (!l) return `Existing listing #${id}`;
    const r = l.rec;
    const street = [r.unit_number ? `${r.unit_number}/${r.street_number || ""}` : r.street_number, r.street_name].filter(has).join(" ");
    if (!street && !r.suburb) return l.kind === "update" ? `Listing #${r.listing_id} (${r.identifier})` : r.identifier;
    if (short) return [street, r.suburb].filter(has).join(", ");
    return [street, r.suburb, [r.state, r.post_code].filter(has).join(" ")].filter(has).join(", ");
  }

  // ------------------------------------------------------------------ the view
  function create(host) {
    const S = { doc: null, X: null, label: "", list: "contacts", q: "", show: "all", page: 0, view: null, keys: false, act: "All", actPage: 0 };
    host.classList.add("sim-host");
    host.innerHTML = `<div class="sim-empty">Convert files on the JSON Import tab and press <strong>Preview in CRM view</strong>, or load an import .json here.</div>`;

    // a value with its JSON key (shown with "Show JSON keys")
    const K = (path) => (S.keys && path ? `<span class="sim-key">${esc(path)}</span>` : "");
    const val = (v, path, fmt) => `${has(v) ? esc(fmt ? fmt(v) : Array.isArray(v) ? v.join(", ") : v) : `<span class="sim-dash">--</span>`}${K(path)}`;
    const row = (label, v, path, fmt) => `<div class="sim-row"><span class="sim-l">${esc(label)}</span><span class="sim-v">${val(v, path, fmt)}</span></div>`;
    const rowHtml = (label, html, path) => `<div class="sim-row"><span class="sim-l">${esc(label)}</span><span class="sim-v">${html || `<span class="sim-dash">--</span>`}${K(path)}</span></div>`;
    const cLink = (id) => S.X.C.has(String(id)) ? `<a href="#" class="sim-a" data-sim-go="contact:${esc(id)}">${esc(contactName(S.X, id))}</a>` : `<span class="sim-ext" title="Already in Zenu: not in this file">${esc(contactName(S.X, id))}</span>`;
    const lLink = (id, short) => S.X.L.has(String(id)) ? `<a href="#" class="sim-a" data-sim-go="listing:${esc(id)}">${esc(listingAddress(S.X, id, short))}</a>` : `<span class="sim-ext" title="Already in Zenu: not in this file">${esc(listingAddress(S.X, id, short))}</span>`;
    const card = (title, body, extra = "") => `<section class="sim-card"><div class="sim-card-h"><span>${title}</span>${extra}</div><div class="sim-card-b">${body}</div></section>`;
    const updBanner = (x, what) => x.kind === "update" ? `<div class="sim-upd"><strong>Update</strong> to an existing Zenu ${what} (#${esc(x.rec[what === "contact" ? "contact_id" : "listing_id"])}). Only the values shown are sent; anything shown as -- stays as it is in Zenu. Lists (notes, tags, vendors…) are added to.</div>` : "";

    function load(doc, label) {
      S.doc = doc; S.X = buildIndex(doc); S.label = label || ""; S.q = ""; S.page = 0;
      S.list = S.X.contacts.length ? "contacts" : "listings";
      const first = S.list === "contacts" ? S.X.contacts[0] : S.X.listings[0];
      S.view = first ? { type: S.list === "contacts" ? "contact" : "listing", id: first.id, tab: "details" } : null;
      S.act = "All"; S.actPage = 0;
      render();
    }
    function clear() { S.doc = null; S.X = null; host.innerHTML = `<div class="sim-empty">No JSON loaded.</div>`; }

    // ---------------- records rail
    const noAddr = (x) => x.kind === "create" && !x.rec.street_name && !x.rec.suburb;
    const linked = (x) => [S.X.vendorOf, S.X.buyerOf, S.X.solOf, S.X.actC, S.X.rel, S.X.members].some((m) => m.has(x.id)) || has(x.rec.company_contact_id);
    const SHOW = {
      contacts: [["all", "All contacts", () => true], ["create", "New", (x) => x.kind === "create"], ["update", "Updates", (x) => x.kind === "update"],
        ["linked", "Linked to listings or people", (x) => linked(x)], ["alone", "Not linked to anything", (x) => !linked(x)]],
      listings: [["all", "All listings", () => true], ["create", "New", (x) => x.kind === "create"], ["update", "Updates", (x) => x.kind === "update"],
        ["noaddr", "New with no address", noAddr], ["contract", "With a contract", (x) => !!x.rec.contract], ["vendors", "With vendors", (x) => has(x.rec.vendor_contact_ids)]],
    };
    function railHtml() {
      const X = S.X, q = S.q.trim().toLowerCase();
      const all = S.list === "contacts" ? X.contacts : X.listings;
      const name = (x) => (S.list === "contacts" ? contactName(X, x.id) : listingAddress(X, x.id));
      const keep = SHOW[S.list].find((o) => o[0] === S.show) || SHOW[S.list][0];
      const hits = all.filter((x) => keep[2](x) && (!q || `${name(x)} ${x.rec.identifier} ${x.id} ${x.rec.email || ""} ${x.rec.mobile_phone || ""}`.toLowerCase().includes(q)));
      const per = 60, pages = Math.max(1, Math.ceil(hits.length / per));
      S.page = Math.min(S.page, pages - 1);
      const shown = hits.slice(S.page * per, (S.page + 1) * per);
      const cur = S.view && S.view.id;
      return `<div class="sim-rail-h">
          <div class="sim-seg"><button type="button" data-sim-list="contacts" aria-pressed="${S.list === "contacts"}">Contacts <b>${X.contacts.length.toLocaleString()}</b></button><button type="button" data-sim-list="listings" aria-pressed="${S.list === "listings"}">Listings <b>${X.listings.length.toLocaleString()}</b></button></div>
          <select class="sim-find" id="simShow" aria-label="Show">${SHOW[S.list].map(([k, label, f]) => `<option value="${k}"${S.show === k ? " selected" : ""}>${label} (${all.filter(f).length.toLocaleString()})</option>`).join("")}</select>
          <input type="search" class="sim-find" id="simFind" placeholder="Name, address, identifier, email…" value="${esc(S.q)}" aria-label="Find a record"></div>
        <div class="sim-rail-list">${shown.map((x) => `<button type="button" class="sim-rec${(S.view && S.view.type === (S.list === "contacts" ? "contact" : "listing") && cur === x.id) ? " on" : ""}" data-sim-go="${S.list === "contacts" ? "contact" : "listing"}:${esc(x.id)}">
            <span class="sim-rec-n">${esc(name(x))}</span><span class="sim-rec-m">${x.kind === "update" ? `<i class="sim-tag upd">UPDATE</i>` : `<i class="sim-tag new">NEW</i>`}${esc(x.rec.identifier)}${S.list === "listings" && x.rec.status ? ` · ${esc(x.rec.status)}` : ""}</span></button>`).join("") || `<p class="sim-none">No match.</p>`}</div>
        <div class="sim-rail-f"><span>${hits.length.toLocaleString()} record${hits.length === 1 ? "" : "s"}</span>
          <span><button type="button" data-sim-page="-1"${S.page ? "" : " disabled"}>Prev</button> ${S.page + 1}/${pages} <button type="button" data-sim-page="1"${S.page < pages - 1 ? "" : " disabled"}>Next</button></span></div>`;
    }

    // ---------------- activity list (notes, inspections, enquiries, offers, tasks)
    function activities(notes, notesPath, acts, side) {
      const out = [];
      (notes || []).forEach((n, i) => out.push({ type: "Note", date: n.created_date || "", staff: n.staff_name, path: `${notesPath}[${i}]`, html: `${esc(n.text).replace(/\n/g, "<br>")}` }));
      for (const a of acts || []) {
        const r = a.r, other = side === "contact" ? (has(r.listing_id) ? `<div class="sim-act-sub">${lLink(r.listing_id, false)}</div>` : "") : (has(r.contact_id) ? `<div class="sim-act-sub">${cLink(r.contact_id)}</div>` : "");
        let body = "", date = "";
        if (a.type === "Inspection") { date = r.start_date; body = `${r.is_private ? "Private inspection" : "Open home"}${r.end_date ? ` until ${esc(day(r.end_date).split(" ").slice(-1)[0])}` : ""}${has(r.interest) ? ` · Interested: <b>${esc(r.interest)}</b>` : ""}${has(r.feedback_price) ? ` · Feedback price ${esc(money(r.feedback_price))}` : ""}${r.notes ? `<br>${esc(r.notes)}` : ""}`; }
        else if (a.type === "Enquiry") { date = r.created_date; body = `${esc(r.source || "Enquiry")}${r.status ? ` · ${esc(r.status)}` : ""}${r.notes ? `<br>${esc(r.notes)}` : ""}`; }
        else if (a.type === "Offer") { date = r.created_date; body = `Amount - <b>${esc(money(r.price))}</b>${r.terms ? `<br>Terms - ${esc(r.terms)}` : ""}`; }
        else { date = r.due_date; body = `<b>${esc(r.subject)}</b> · ${esc(r.status || "Active")}${r.due_date ? ` · due ${esc(day(r.due_date))}` : ""}${r.notes ? `<br>${esc(r.notes)}` : ""}`; }
        out.push({ type: a.type, date: date || "", staff: r.staff_name, path: a.path, html: other + body });
      }
      out.sort((a, b) => String(b.date).localeCompare(String(a.date)));
      return out;
    }
    function activityCard(items) {
      const tabs = ["All", "Note", "Inspection", "Enquiry", "Offer", "Task"];
      const list = S.act === "All" ? items : items.filter((i) => i.type === S.act);
      const per = 5, pages = Math.max(1, Math.ceil(list.length / per));
      S.actPage = Math.min(S.actPage, pages - 1);
      const shown = list.slice(S.actPage * per, (S.actPage + 1) * per);
      const ic = { Note: "▤", Inspection: "⌂", Enquiry: "?", Offer: "$", Task: "✓" };
      return `<section class="sim-card"><div class="sim-card-h"><span>Activity</span></div>
        <div class="sim-tabs">${tabs.map((t) => `<button type="button" data-sim-act="${t}" aria-pressed="${S.act === t}">${t}${t === "All" ? "" : ` <small>${items.filter((i) => i.type === t).length}</small>`}</button>`).join("")}</div>
        <div class="sim-acts">${shown.map((i) => `<div class="sim-act"><span class="sim-act-i" aria-hidden="true">${ic[i.type]}</span><div class="sim-act-t"><div class="sim-act-top"><b>${i.type}</b><span>${esc(day(i.date) || "No date")}${i.staff ? ` - ${esc(staffShort(i.staff))}` : ""}</span></div>${i.html}${K(i.path)}</div></div>`).join("") || `<p class="sim-none">Nothing here in this file.</p>`}</div>
        <div class="sim-pager"><button type="button" data-sim-actpage="-1"${S.actPage ? "" : " disabled"}>PREV</button><button type="button" data-sim-actpage="1"${S.actPage < pages - 1 ? "" : " disabled"}>NEXT</button></div></section>`;
    }

    // ---------------- contact
    function contactPage(x) {
      const X = S.X, r = x.rec, p = x.path, name = contactName(X, x.id);
      const rels = X.rel.get(x.id) || [];
      const crumbs = [`<span class="on">1. ${esc(name)}</span>`].concat(rels.map((o, i) => `<a href="#" data-sim-go="contact:${esc(o.id)}">${i + 2}. ${esc(contactName(X, o.id))}</a>`));
      const phones = (r.extra_phones || []);
      const tab = S.view.tab;
      const head = `<div class="sim-top"><span class="sim-search">Search</span></div>
        <div class="sim-head"><button type="button" class="sim-back" data-sim-back aria-label="Back">←</button><div><div class="sim-title">${esc(name)}</div><div class="sim-sub">Contacts${r.is_company ? " · Company" : ""}</div></div></div>
        <div class="sim-pagetabs"><button type="button" data-sim-tab="details" aria-pressed="${tab === "details"}">DETAILS</button><button type="button" data-sim-tab="docs" aria-pressed="${tab === "docs"}">DOCS</button><button type="button" data-sim-tab="activity" aria-pressed="${tab === "activity"}">ACTIVITY</button></div>
        <div class="sim-body">${updBanner(x, "contact")}<div class="sim-crumbs">${crumbs.join("")}</div>`;
      if (tab === "docs") return head + `<div class="sim-card sim-pad">Documents aren't part of the JSON import.</div></div>`;
      if (tab === "activity") return head + contactSnapshot(x) + `</div>`;
      const pref = [["Text", r.allows_text], ["Email", r.allows_email], ["Phone", r.allows_phone], ["Letter", r.allows_letter]];
      const prefTxt = pref.some(([, v]) => v !== undefined) ? pref.filter(([, v]) => v === true).map(([k]) => k).join(", ") + (pref.some(([, v]) => v === false) ? ` (not: ${pref.filter(([, v]) => v === false).map(([k]) => k).join(", ")})` : "") : "";
      const addr = [r.address_line_1, r.address_line_2, [r.suburb, r.state, r.post_code].filter(has).join(" "), r.country].filter(has);
      const details = `<div class="sim-chiprow"><span class="sim-namechip">${esc(name.toUpperCase())}</span>${r.title ? `<span class="sim-small">${esc(r.title)}</span>` : ""}</div>${S.keys ? `<div class="sim-keyline">${K(`${p}.title / first_name / last_name / company_name`)}</div>` : ""}
          ${row("Identifier", r.identifier, `${p}.identifier`)}
          ${row("Mobile", r.mobile_phone, `${p}.mobile_phone`)}${row("Email", r.email, `${p}.email`)}
          ${row("Work Ph", r.work_phone, `${p}.work_phone`)}${row("Home Ph", r.home_phone, `${p}.home_phone`)}${has(r.fax) ? row("Fax", r.fax, `${p}.fax`) : ""}
          ${has(r.extra_emails) ? row("Other emails", r.extra_emails, `${p}.extra_emails`) : ""}
          ${phones.length ? rowHtml("Other phones", phones.map((ph) => `${esc(ph.number)} <small>(${esc(ph.label)})</small>`).join("<br>"), `${p}.extra_phones`) : ""}
          <hr>${rowHtml("Rating", has(r.rating) ? `<span class="sim-rate ${esc(String(r.rating).toLowerCase())}">${esc(r.rating)}</span>` : "", `${p}.rating`)}
          <hr>${rowHtml("Mailing Address", addr.map(esc).join("<br>"), `${p}.address_line_1 … post_code`)}${has(r.town_city) ? row("Town / city", r.town_city, `${p}.town_city`) : ""}
          <hr>${row("Contact Type", r.types, `${p}.types`)}${row("Contact Tags", r.tags, `${p}.tags`)}
          ${has(r.company_contact_id) ? rowHtml("Company", cLink(r.company_contact_id), `${p}.company_contact_id`) : ""}${has(r.company_position) ? row("Position", r.company_position, `${p}.company_position`) : ""}
          ${(X.members.get(x.id) || []).length ? rowHtml("People", X.members.get(x.id).map((m) => cLink(m.id)).join("<br>"), `contacts.*.company_contact_id = ${x.id}`) : ""}
          <hr>${row("Enquiry Source", r.source, `${p}.source`)}${row("Name on letters", r.name_on_letters, `${p}.name_on_letters`)}${has(r.legal_name) ? row("Legal name", r.legal_name, `${p}.legal_name`) : ""}
          <hr>${row("Preferences", prefTxt, `${p}.allows_text / allows_email / allows_phone / allows_letter`)}
          ${rowHtml("E-Marketing", r.is_unsubscribed === true ? `Unsubscribed${r.unsubscribed_date ? ` ${esc(day(r.unsubscribed_date))}` : ""}` : r.is_unsubscribed === false ? "Subscribed" : "", `${p}.is_unsubscribed`)}
          ${row("Subscriptions", has(r.subscription_ids) ? r.subscription_ids.map((s) => `List #${s}`) : "", `${p}.subscription_ids`)}
          <hr>${row("Staff", r.primary_staff_name, `${p}.primary_staff_name`)}${row("Other staff", r.secondary_staff_names, `${p}.secondary_staff_names`)}
          ${row("Created", r.created_date, `${p}.created_date`, day)}${row("Last contacted", r.last_contacted_date, `${p}.last_contacted_date`, day)}`;
      const owned = (X.vendorOf.get(x.id) || []).map((o) => ({ ...o, role: "Owner" })).concat((X.buyerOf.get(x.id) || []).map((o) => ({ ...o, role: "Purchaser" })), (X.solOf.get(x.id) || []));
      const props = owned.length ? owned.map((o) => { const lr = o.l.rec; return `<div class="sim-prop"><span class="sim-av">${esc(o.role[0])}</span><div>${lLink(o.l.id, true)}<div class="sim-small">${esc(o.role)} · ${esc(lr.status || (o.l.kind === "update" ? "Existing listing" : ""))}${lr.appraisal && lr.appraisal.date ? ` - ${esc(day(lr.appraisal.date))}` : ""}${lr.lister_staff_names ? ` - ${esc(staffShort(lr.lister_staff_names[0]))}` : ""}</div>${K(o.path)}</div></div>`; }).join("") : `<p class="sim-none">Not linked to a listing in this file.</p>`;
      const rel = rels.length ? rels.map((o) => `<div class="sim-row"><span class="sim-l">${esc(o.type)}${o.other ? " (added both ways)" : ""}</span><span class="sim-v">${cLink(o.id)}${K(o.path)}</span></div>`).join("") : `<p class="sim-none">No relationships.</p>`;
      const crit = (r.criteria || []).map((c, i) => `<div class="sim-crit"><b>${esc([c.sale_method, c.category].filter(has).join(" · ") || "Requirement")}</b>
          ${[["Types", c.property_types], ["Price", has(c.min_price) || has(c.max_price) ? `${money(c.min_price) || "any"} – ${money(c.max_price) || "any"}` : ""], ["Bed", c.min_bedrooms], ["Bath", c.min_bathrooms], ["Car", c.min_car_spaces],
            ["Land", has(c.min_land_size) || has(c.max_land_size) ? `${c.min_land_size ?? "any"} – ${c.max_land_size ?? "any"} ${c.land_size_unit || "sqm"}` : ""], ["Suburbs", c.suburbs]].filter(([, v]) => has(v)).map(([k, v]) => `<span><small>${k}</small> ${esc(Array.isArray(v) ? v.join(", ") : v)}${k === "Bed" || k === "Bath" || k === "Car" ? "+" : ""}</span>`).join("")}${K(`${p}.criteria[${i}]`)}</div>`).join("");
      return head + `<div class="sim-grid c2"><div>${card("CONTACT DETAILS", details, `<span class="sim-muted">EDIT ⋮</span>`)}</div>
          <div>${activityCard(activities(r.notes, `${p}.notes`, X.actC.get(x.id), "contact"))}
            ${card("PROPERTIES (CURRENT)", props)}${card("RELATIONSHIPS", rel)}${crit ? card("REQUIREMENTS", crit) : ""}</div></div></div>`;
    }
    function contactSnapshot(x) {
      const X = S.X, acts = X.actC.get(x.id) || [];
      const by = new Map();
      const get = (lid) => { let g = by.get(lid); if (!g) by.set(lid, (g = { lid, Offer: 0, Enquiry: 0, Inspection: 0, Task: 0, interested: false, role: "" })); return g; };
      for (const a of acts) if (has(a.r.listing_id)) { const g = get(String(a.r.listing_id)); g[a.type]++; if (a.type === "Inspection" && a.r.interest === "Yes") g.interested = true; }
      (X.vendorOf.get(x.id) || []).forEach((o) => (get(o.l.id).role = "Owner"));
      (X.buyerOf.get(x.id) || []).forEach((o) => (get(o.l.id).role = "Purchaser"));
      const rows = [...by.values()];
      const sum = (k) => rows.reduce((n, g) => n + g[k], 0);
      const cell = (n) => (n ? `<b>${n}</b>` : "--");
      return `<section class="sim-card"><div class="sim-card-h"><span>SNAPSHOT</span></div><div class="sim-card-b">
        <div class="sim-tiles"><div class="on">ALL</div><div>OFFERS<br><b>${sum("Offer")} MADE</b></div><div>DOCUMENTS<br><b>not imported</b></div><div>ENQUIRIES<br><b>${sum("Enquiry")}</b></div><div>INTERESTED<br><b>${rows.filter((g) => g.interested).length}</b></div><div>INSPECTIONS<br><b>${sum("Inspection")}</b></div></div>
        <table class="sim-table"><thead><tr><th></th><th>ROLE</th><th>INTERESTED</th><th>OFFERS</th><th>ENQUIRIES</th><th>INSPECTIONS</th><th>TASKS</th></tr></thead><tbody>
        ${rows.map((g) => `<tr><td>${lLink(g.lid, false)}</td><td>${esc(g.role || "--")}</td><td>${g.interested ? "YES" : "--"}</td><td>${cell(g.Offer)}</td><td>${cell(g.Enquiry)}</td><td>${cell(g.Inspection)}</td><td>${cell(g.Task)}</td></tr>`).join("") || `<tr><td colspan="7" class="sim-none">No listing activity in this file.</td></tr>`}</tbody></table></div></section>`;
    }

    // ---------------- listing
    function listingPage(x) {
      const X = S.X, r = x.rec, p = x.path, addr = listingAddress(X, x.id), tab = S.view.tab;
      const ct = r.contract;
      const head = `<div class="sim-top"><span class="sim-search">Search</span></div>
        <div class="sim-lhead"><button type="button" class="sim-back" data-sim-back aria-label="Back">←</button><span class="sim-muted">Listings /</span> <span>${esc(addr)}</span>
          <span class="sim-ltabs"><button type="button" data-sim-tab="details" aria-pressed="${tab === "details"}">Details</button><button type="button" data-sim-tab="docs" aria-pressed="${tab === "docs"}">Image/Docs</button><button type="button" data-sim-tab="activity" aria-pressed="${tab === "activity"}">Activity</button></span></div>
        <div class="sim-body">${updBanner(x, "listing")}${x.kind === "create" && !r.street_name && !r.suburb ? `<div class="sim-upd bad"><strong>No address.</strong> This listing only appears in vendor, buyer, note or activity rows, so it would be created with just its identifier (${esc(r.identifier)}). Check that its appraisal / prospect / listing row is in the files, or that it should be an existing Zenu listing (zenu_property_id).</div>` : ""}`;
      if (tab === "docs") return head + `<div class="sim-card sim-pad">Images and documents aren't part of the JSON import.</div></div>`;
      if (tab === "activity") return head + listingSnapshot(x) + `</div>`;
      const cars = [["G", r.garages], ["C", r.carports], ["O", r.open_parking_spaces]].filter(([, v]) => has(v));
      const carTotal = cars.reduce((n, [, v]) => n + v, 0);
      const left = `<section class="sim-card"><div class="sim-card-b">
          <div class="sim-ltitle">${esc(addr)}</div>${K(`${p}.unit_number / street_number / street_name / suburb / state / post_code`)}
          <div class="sim-status">${esc((r.status || (x.kind === "update" ? "Existing (unchanged)" : "")).toUpperCase())} <span>▾</span></div>${K(`${p}.status`)}
          <div class="sim-chips"><span>${esc([r.category, r.sale_method].filter(has).join(" ") || "Category / method not set")}</span></div>${K(`${p}.category + sale_method`)}
          <div class="sim-photo"><span>NO PHOTOS IN THE IMPORT</span><div class="sim-bbc">Bed ${esc(r.bedrooms ?? "-")}  Bath ${esc(r.bathrooms ?? "-")}  Car ${cars.length ? carTotal : "-"}</div></div></div></section>`;
      const summary = card("Summary", `${row("Team", r.lister_staff_names, `${p}.lister_staff_names`)}<hr>
          ${row("Bedrooms", r.bedrooms, `${p}.bedrooms`)}${row("Bathrooms", r.bathrooms, `${p}.bathrooms`)}${has(r.toilets) ? row("Toilets", r.toilets, `${p}.toilets`) : ""}
          ${rowHtml("Car Spaces", cars.length ? `${carTotal} <small>(${cars.map(([k, v]) => `${{ G: "garage", C: "carport", O: "open" }[k]} ${v}`).join(", ")})</small>` : "", `${p}.garages / carports / open_parking_spaces`)}
          ${row("Land", has(r.land_size) ? `${r.land_size} ${r.land_size_unit || "sqm"}` : "", `${p}.land_size + land_size_unit`)}${has(r.living_area) ? row("Living area", `${r.living_area} sqm`, `${p}.living_area`) : ""}<hr>
          ${row("Method", r.sale_method, `${p}.sale_method`)}${row("Category", r.category, `${p}.category`)}${row("Property Type", r.property_type, `${p}.property_type`)}
          ${has(r.year_built) ? row("Year built", r.year_built, `${p}.year_built`) : ""}${has(r.building_name) ? row("Building", r.building_name, `${p}.building_name`) : ""}<hr>
          ${row("Search Price", r.search_price, `${p}.search_price`, money)}${has(r.weekly_rent) ? row("Rent per week", r.weekly_rent, `${p}.weekly_rent`, money) : ""}
          ${has(r.is_owner_occupied) ? row("Owner occupied", r.is_owner_occupied ? "Yes" : "No", `${p}.is_owner_occupied`) : ""}
          ${[["Council", "council_name"], ["Zoning", "council_zoning"], ["Lot", "lot_number"], ["Title", "title_number"]].filter(([, k]) => has(r[k])).map(([l, k]) => row(l, r[k], `${p}.${k}`)).join("")}
          ${row("Identifier", r.identifier, `${p}.identifier`)}${has(r.modified_date) ? row("Modified", r.modified_date, `${p}.modified_date`, day) : ""}`);
      const ap = r.appraisal, pr = r.prospect;
      const stage = ap ? card("Appraisal", `${row("Date", ap.date, `${p}.appraisal.date`, day)}${row("Source", ap.source, `${p}.appraisal.source`)}${row("Motivation", ap.motivation, `${p}.appraisal.motivation`)}${row("Price", ap.price, `${p}.appraisal.price`, money)}${row("Staff", ap.staff_name, `${p}.appraisal.staff_name`)}`)
        : pr ? card("Prospect", `${row("Pipeline rating", pr.pipeline_rating, `${p}.prospect.pipeline_rating`)}${row("Current price", pr.current_price, `${p}.prospect.current_price`, money)}${row("Last listed", pr.last_listed_date, `${p}.prospect.last_listed_date`, day)}${row("Last listed price", pr.last_listed_price, `${p}.prospect.last_listed_price`, money)}${row("Last listed agency", pr.last_listed_agency, `${p}.prospect.last_listed_agency`)}${row("Last listed agent", pr.last_listed_agent, `${p}.prospect.last_listed_agent`)}${row("Last sold", pr.last_sold_date, `${p}.prospect.last_sold_date`, day)}${row("Last sold price", pr.last_sold_price, `${p}.prospect.last_sold_price`, money)}`) : "";
      const acts = X.actL.get(x.id) || [];
      const offers = acts.filter((a) => a.type === "Offer");
      const offerCard = card(`Offers - ${offers.length}`, offers.map((a) => `<div class="sim-act"><span class="sim-act-i">$</span><div class="sim-act-t"><div class="sim-act-top"><b>Offer</b><span>${esc(day(a.r.created_date) || "No date")}${a.r.staff_name ? ` - ${esc(staffShort(a.r.staff_name))}` : ""}</span></div>Contact - ${cLink(a.r.contact_id)}<br>Amount - ${esc(money(a.r.price))}${a.r.terms ? `<br>Terms - ${esc(a.r.terms)}` : ""}${K(a.path)}</div></div>`).join("") || `<p class="sim-none">No offers in this file.</p>`);
      const contract = ct ? card("Contract Details", `<div class="sim-contract"><div>${(ct.buyer_contact_ids || []).map(cLink).join(", ") || `<span class="sim-dash">No buyer</span>`}${K(`${p}.contract.buyer_contact_ids`)}</div>
          <b>${esc(ct.status || (ct.settlement_date ? "SETTLED" : x.kind === "update" ? "" : "SOLD")).toUpperCase()}</b></div>
          ${row("Contract date", ct.contract_date, `${p}.contract.contract_date`, day)}${row("Unconditional", ct.unconditional_date, `${p}.contract.unconditional_date`, day)}${row("Settlement", ct.settlement_date, `${p}.contract.settlement_date`, day)}
          ${row("Price", ct.sold_price, `${p}.contract.sold_price`, money)}${row("Commission (ex GST)", ct.gross_commission, `${p}.contract.gross_commission`, money)}
          ${rowHtml("Sale team", (ct.staff || []).map((s) => `${esc(s.staff_name)} <small>(${esc(s.role)}${has(s.share_percentage) ? ` ${s.share_percentage}%` : ""}${has(s.share_amount) ? ` ${money(s.share_amount)}` : ""})</small>`).join("<br>"), `${p}.contract.staff`)}`) : "";
      const vendors = r.vendor_contact_ids || [];
      const person = (id, path) => { const c = X.C.get(String(id)), cr = c ? c.rec : {}; return `<div class="sim-owner"><span class="sim-av">${esc(initials(contactName(X, id)))}</span><div>${cLink(id)}${c ? `<div class="sim-mini"><span>Mobile</span>${esc(cr.mobile_phone || "--")}<span>Email</span>${esc(cr.email || "--")}<span>Home</span>${esc(cr.home_phone || "--")}${cr.company_name && !cr.is_company ? `<span>Company</span>${esc(cr.company_name)}` : ""}</div>` : `<div class="sim-small">Already in Zenu: details not in this file</div>`}${K(path)}</div></div>`; };
      const owners = card(`Owners - ${vendors.length}`, vendors.map((id, i) => person(id, `${p}.vendor_contact_ids[${i}]`)).join("") || `<p class="sim-none">No vendors in this file.</p>`);
      const vsol = card("Owner Solicitor", has(r.vendor_solicitor_contact_id) ? person(r.vendor_solicitor_contact_id, `${p}.vendor_solicitor_contact_id`) : `<p class="sim-none">--</p>`);
      const bsol = ct && has(ct.buyer_solicitor_contact_id) ? card("Buyer Solicitor", person(ct.buyer_solicitor_contact_id, `${p}.contract.buyer_solicitor_contact_id`)) : "";
      return head + `<div class="sim-grid c3"><div>${left}${summary}${stage}</div><div>${activityCard(activities(r.notes, `${p}.notes`, acts.filter((a) => a.type !== "Offer"), "listing"))}${offerCard}${contract}</div><div>${owners}${vsol}${bsol}</div></div></div>`;
    }
    function listingSnapshot(x) {
      const X = S.X, r = x.rec, acts = X.actL.get(x.id) || [];
      const by = new Map();
      const get = (cid) => { cid = String(cid); let g = by.get(cid); if (!g) by.set(cid, (g = { cid, Offer: 0, Enquiry: 0, Inspection: 0, Task: 0, interested: false, role: "", last: "" })); return g; };
      for (const a of acts) if (has(a.r.contact_id)) {
        const g = get(a.r.contact_id); g[a.type]++;
        if (a.type === "Inspection" && a.r.interest === "Yes") g.interested = true;
        const d = a.r.created_date || a.r.start_date || a.r.due_date || ""; if (d > g.last) g.last = d;
      }
      (r.vendor_contact_ids || []).forEach((id) => (get(id).role = "Owner"));
      ((r.contract || {}).buyer_contact_ids || []).forEach((id) => (get(id).role = "Purchaser"));
      const rows = [...by.values()].sort((a, b) => b.last.localeCompare(a.last));
      const n = (t) => acts.filter((a) => a.type === t).length;
      const cell = (v) => (v ? `<b>${v}</b>` : "--");
      return `<div class="sim-stats"><div><small>Enquiries</small><b>${n("Enquiry")}</b></div><div><small>Offers</small><b>${n("Offer")}</b></div><div><small>Interested</small><b>${rows.filter((g) => g.interested).length}</b></div><div><small>Inspections</small><b>${n("Inspection")}</b></div><div><small>Notes</small><b>${(r.notes || []).length}</b></div><div><small>Tasks</small><b>${n("Task")}</b></div></div>
        <section class="sim-card"><div class="sim-card-h"><span>Snapshot</span><span class="sim-muted">${rows.length} contact${rows.length === 1 ? "" : "s"}</span></div><div class="sim-card-b">
        <table class="sim-table"><thead><tr><th></th><th>ROLE</th><th>OFFERS</th><th>INTERESTED</th><th>ENQUIRIES</th><th>INSPECTIONS</th><th>TASKS</th></tr></thead><tbody>
        ${rows.map((g) => { const c = X.C.get(g.cid); return `<tr><td><span class="sim-av sm">${esc(initials(contactName(X, g.cid)))}</span>${cLink(g.cid)}${c && c.rec.mobile_phone ? `<div class="sim-small">☎ ${esc(c.rec.mobile_phone)}</div>` : ""}${g.last ? `<div class="sim-small">Active ${esc(day(g.last))}</div>` : ""}</td><td>${esc(g.role || "--")}</td><td>${cell(g.Offer)}</td><td>${g.interested ? "YES" : "--"}</td><td>${cell(g.Enquiry)}</td><td>${cell(g.Inspection)}</td><td>${cell(g.Task)}</td></tr>`; }).join("") || `<tr><td colspan="7" class="sim-none">No contacts linked to this listing in this file.</td></tr>`}</tbody></table></div></section>`;
    }

    // ---------------- frame
    function render() {
      if (!S.X) return;
      const X = S.X, v = S.view;
      const x = v ? (v.type === "contact" ? X.C.get(v.id) : X.L.get(v.id)) : null;
      const totals = `${X.contacts.length.toLocaleString()} contacts · ${X.listings.length.toLocaleString()} listings · ${(X.counts.tasks || 0).toLocaleString()} tasks · ${(X.counts.enquiries || 0).toLocaleString()} enquiries · ${(X.counts.inspections || 0).toLocaleString()} inspections · ${(X.counts.offers || 0).toLocaleString()} offers`;
      host.innerHTML = `<div class="sim-bar"><span class="sim-badge">SIMULATION</span><span>${esc(S.label)}${S.doc.office_id ? ` · office ${esc(S.doc.office_id)}` : ""} · ${esc(totals)}</span>
          <label class="sim-toggle"><input type="checkbox" id="simKeys"${S.keys ? " checked" : ""}> Show JSON keys</label></div>
        <div class="sim-wrap"><aside class="sim-rail">${railHtml()}</aside>
          <div class="sim-screen" id="simScreen">${x ? (v.type === "contact" ? contactPage(x) : listingPage(x)) : `<div class="sim-empty">This JSON has no contacts or listings.</div>`}</div></div>`;
    }
    const hist = [];
    function go(type, id) {
      if (S.view) hist.push(S.view);
      S.view = { type, id: String(id), tab: "details" }; S.act = "All"; S.actPage = 0;
      render();
      const sc = host.querySelector("#simScreen"); if (sc) sc.scrollTop = 0;
    }
    host.addEventListener("click", (e) => {
      const t = e.target, a = t.closest("[data-sim-go],[data-sim-list],[data-sim-page],[data-sim-tab],[data-sim-act],[data-sim-actpage],[data-sim-back]");
      if (!a || !S.X) return;
      e.preventDefault();
      if (a.dataset.simGo) { const i = a.dataset.simGo.indexOf(":"); go(a.dataset.simGo.slice(0, i), a.dataset.simGo.slice(i + 1)); }
      else if (a.dataset.simList) { S.list = a.dataset.simList; S.page = 0; S.show = "all"; render(); }
      else if (a.dataset.simPage) { S.page += +a.dataset.simPage; render(); }
      else if (a.dataset.simTab) { S.view.tab = a.dataset.simTab; render(); }
      else if (a.dataset.simAct) { S.act = a.dataset.simAct; S.actPage = 0; render(); }
      else if (a.dataset.simActpage) { S.actPage += +a.dataset.simActpage; render(); }
      else if (a.hasAttribute("data-sim-back")) { if (hist.length) { S.view = hist.pop(); render(); } }
    });
    let ft;
    host.addEventListener("input", (e) => {
      if (e.target.id !== "simFind") return;
      clearTimeout(ft);
      ft = setTimeout(() => { S.q = e.target.value; S.page = 0; render(); const f = host.querySelector("#simFind"); if (f) { f.focus(); f.setSelectionRange(f.value.length, f.value.length); } }, 200);
    });
    host.addEventListener("change", (e) => {
      if (e.target.id === "simKeys") { S.keys = e.target.checked; render(); }
      if (e.target.id === "simShow") { S.show = e.target.value; S.page = 0; render(); }
    });
    return { load, clear, go, state: S };
  }

  const api = { create, buildIndex, day, staffShort };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DarisCrmPreview = api;
})(typeof window !== "undefined" ? window : globalThis);

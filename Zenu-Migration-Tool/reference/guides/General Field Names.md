# General Field Names

General cleaning uses the same name splitting, phone / email cleaning and address cleaning as
Agentbox. What changes between CRMs is **what the columns are called**. This guide tells DARIS
which header names mean which role, so it can map an export it has never seen.

How DARIS maps a General export (first match wins):
1. **Learned** — what you confirmed on an earlier run (tick *Remember my changes*).
2. **This guide** — the header names below (case, spaces and punctuation are ignored).
3. **Matcher** — Zenu field aliases and close spellings.
4. **AI** — for roles still empty, the AI reads the remaining columns' sample values and
   suggests one. AI picks are marked **AI** on the review card: check them before you Run.

**Add a name:** put it in the right row, separated by ` · `. Saved changes are picked up on the
next "general cleaning" — no restart. Keep the first column (the role key) as it is.

| Role key | Role | Header names other CRMs use |
|---|---|---|
| `id` | Contact ID | contact id · contactid · client id · person id · people id · record id · customer id · id · ref · reference · contact ref · uid · unique id |
| `spouse_id` | Spouse / partner's contact ID | spouse contact id · spouse id · spouse contactid · partner contact id · partner contactid · spouse contact · linked contact id · related contact id · spouse ref |
| `company` | Company name | company · company name · organisation · organization · business name · trading name · firm |
| `title` | Title | title · salutation title · prefix · name title · honorific |
| `first` | First name | first name · firstname · given name · given names · forename · first names · name first |
| `last` | Last name | last name · lastname · surname · family name · name last |
| `title2` | Second person - title | title 2 · partner title · spouse title · second title · contact 2 title |
| `first2` | Second person - first name | first name 2 · partner first name · spouse first name · partner name · spouse name · second first name · contact 2 first name |
| `last2` | Second person - last name | last name 2 · surname 2 · partner surname · spouse surname · partner last name · contact 2 surname |
| `email` | Email | email · email address · e mail · primary email · email 1 · email1 · personal email |
| `mobile` | Mobile | mobile · mobile phone · mobile number · cell · cell phone · mob · mobile 1 · mobile1 · primary mobile · phone mobile |
| `home` | Home phone | home phone · home · phone home · home number · ph home · after hours · ah phone · home ph |
| `work` | Work phone | work phone · business phone · office phone · phone work · bh phone · business hours · work ph · direct phone |
| `fax` | Fax | fax · fax number · facsimile · fax phone |
| `mobile2` | Second person - mobile | mobile 2 · mobile2 · partner mobile · spouse mobile · second mobile · secondary mobile · contact 2 mobile |
| `email2` | Second person - email | email 2 · email2 · partner email · spouse email · secondary email · second email · contact 2 email |
| `street` | Address line 1 / street | address · street · street address · address 1 · address line 1 · addr1 · postal address · mailing address · residential address · property address |
| `street2` | Address line 2 | address 2 · address line 2 · addr2 · street 2 |
| `suburb` | Suburb | suburb · town · city · locality · postal suburb · mailing suburb |
| `state` | State | state · state code · region · province · postal state |
| `postcode` | Postcode | postcode · post code · postal code · zip · zip code · pcode |
| `country` | Country | country · country name · nation |
| `salutation` | Name on letters / salutation | salutation · dear · letter name · name on letters · addressee · address to · greeting |
| `legal` | Legal name | legal name · full legal name · registered name |
| `position` | Company position / job title | job title · position · role · occupation · designation |
| `type` | Contact type | contact type · type · category · contact category · classification · client type |
| `tags` | Contact tags | tags · tag · labels · groups · interests · keywords |
| `source` | Enquiry source | source · enquiry source · lead source · how heard · referral source · origin · marketing source |
| `notes` | Notes / comments | notes · note · comments · comment · remarks · memo · description · details |
| `team1` | Team member 1 | agent · agent name · consultant · owner · assigned to · salesperson · sales agent · team member · listing agent · account manager |
| `created` | Date created | date created · created · created date · created on · date added · added · entered · created at |
| `modified` | Date modified | date modified · modified · last modified · updated · last updated · modified date · updated at · changed |
| `do_not_sms` | Text / SMS permission | do not sms · no sms · sms opt out · opt out sms · unsubscribed sms · sms blocked · allow text · is allow text · allow sms · sms allowed · sms opt in · can sms · text permission |
| `do_not_email` | Email permission | do not email · no email · email opt out · opt out email · unsubscribed email · email blocked · allow email · is allow email · email allowed · email opt in · can email · email permission |
| `do_not_mail` | Letter / mail permission | do not mail · no mail · no post · mail opt out · opt out mail · do not post · allow letter · is allow letter · allow mail · allow post · mail opt in · letter permission |
| `do_not_call` | Phone permission | do not call · no call · dnc · do not phone · call opt out · opt out call · allow phone · is allow phone · allow call · call allowed · phone opt in · phone permission |
| `subscribed` | Newsletter / marketing (unsubscribe) | subscribed · newsletter · subscribed newsletters · marketing opt in · email marketing · opt in · unsubscribe newsletter date · unsubscribe date · unsubscribed date · newsletter unsubscribed · unsubscribe from marketing |
| `contactable` | Contactable (fills blank permissions) | is contactable · contactable · can contact · ok to contact · okay to contact · contact allowed · allow contact · contactable flag |
| `zenu_id` | Zenu contact ID (existing Zenu record) | zenu contact id · zenu id · zenu contact · existing zenu id |
| `subscriptions` | Subscriptions | subscriptions · subscription · mailing lists · mailing list · lists subscribed |
| `town_city` | Town / city | town city · town or city · mailing town city · postal town city |
| `children` | Children's names | childrens names · children's names · children · children names · kids · kids names · child names |
| `rating` | Rating | rating · contact rating · grade · lead rating · priority · temperature |
| `last_contact` | Last contact date | last contact date · last contact · last contacted · date last contacted · last contacted date · last activity |
| `team2` | Team member 2 | team member 2 · agent 2 · consultant 2 · second agent · secondary agent · agent 2 name |
| `team3` | Team member 3 | team member 3 · agent 3 · consultant 3 |
| `team4` | Team member 4 | team member 4 · agent 4 · consultant 4 |
| `team5` | Team member 5 | team member 5 · agent 5 · consultant 5 |
| `team6` | Team member 6 | team member 6 · agent 6 · consultant 6 |
| `team7` | Team member 7 | team member 7 · agent 7 · consultant 7 |
| `team8` | Team member 8 | team member 8 · agent 8 · consultant 8 |
| `team9` | Team member 9 | team member 9 · agent 9 · consultant 9 |
| `team10` | Team member 10 | team member 10 · agent 10 · consultant 10 |
| `team11` | Team member 11 | team member 11 · agent 11 · consultant 11 |
| `team12` | Team member 12 | team member 12 · agent 12 · consultant 12 |
| `team13` | Team member 13 | team member 13 · agent 13 · consultant 13 |
| `team14` | Team member 14 | team member 14 · agent 14 · consultant 14 |
| `team15` | Team member 15 | team member 15 · agent 15 · consultant 15 |
| `sale_type` | Sale type | sale type · contact sale type · method of sale |
| `solicitor_id` | Zenu solicitor ID | zenu solicitor id · solicitor id · solicitor zenu id |
| `company_id` | Zenu company ID | zenu company id · company zenu id |

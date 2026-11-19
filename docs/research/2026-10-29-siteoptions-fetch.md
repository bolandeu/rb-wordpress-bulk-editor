# siteoptions through `wp-bulk options --fetch` (marketer T415)

Date: 2026-10-29. Question from the marketer Architect: can the company data
of a site come from siteoptions instead of the `company` tab of a Google sheet.
Sources: the wp-bulk code, two fetches of ost-term.ru (15.05.2026: log
`/Users/bolandau/Work/_sandbox/wp-bulk-edit/projects/ost/logs/options_fetch_20260515_181653.log`;
29.10.2026: marketer's `ost.json`). No secret values are written here.

## 1. Canonical key list / schema

- wp-bulk has none. It is a transport: `GET /wp-json/siteoptions/v1/options`
  → the dict as is → tab `options` as `{key, value}` records
  (`src/rb_wordpress_bulk_editor/wordpress/options.py`, `options` in `src/rb_wordpress_bulk_editor/cli/tool_commands.py`).
  No key list, types, groups or secret flags in the wp-bulk code or docs.
- The siteoptions plugin is not part of this project and its source is not
  in `~/Work`. The schema belongs to the plugin and its owner.

## 2. Mapping sheet keys ↔ site keys

- No mapping in wp-bulk, nor anywhere in `~/Work` except marketer's own
  sheet loader (`windmill/f/sheets/company.py`). wp-bulk passes the site's
  names 1:1 in both directions.
- The sheet naming comes from the static-site generator, not from WordPress:
  sitegen's config schema `/Users/bolandau/Work/_sandbox/sitegen/bulk_editor/schema.py`
  has `legal.companyName`, `legal.companyFullName`, `legal.inn`, `legal.ogrn`,
  `legal.legalAddress`, `legal.phone`, `legal.workHours`, `legal.email`,
  `newsletter.unisender.*`, `meta.jsonld.address.*`. The `company` tab took
  these names.
- `egalAddress` is a typo of sitegen's `legalAddress`: it is in
  `marketer/docs/user/company.md:18` and `marketer/directus/migration/steps.py:226`.
- Source of truth: for the site's names, the plugin. For the platform's own
  names, the platform decides; wp-bulk carries either.

## 3. Groups (by key name only: there is no schema to confirm them)

| Group | Keys |
|---|---|
| Legal entity | `fullname`, `name`, `inn`, `ogrn`, `kpp`, `bik`, `rs`, `ks`, `juraddress`, `postaladdress` |
| Contacts, location | `phone`, `email`, `working_hours`, `address`, `address_city`, `address_country`, `address_postal`, `address_region`, `address_street`, `latitude`, `longitude`, `metro` |
| Profiles, maps, reviews | `vk`, `ok`, `telegram`, `whatsapp`, `max`, `instagram`, `tiktok`, `rutube`, `youtube`, `tenchat`, `dzen`, `yandex`, `yandex_uslugi`, `google_maps`, `2gis`, `flamp`, `zoon`, `yell`, `otzovik`, `irecommend`, `profi_ru`, `avito`, `tbank` |
| Site / integration config | `contacts_page` (int, a page ID), `phone_mapping`, `ym_counter`, `vk_pixel`, `calltouch_counter_id`, `smtp_host`, `smtp_port`, `smtp_secure`, `smtp_from`, `smtp_from_name`, `smtp_username`, `b24_custom_fields`, `b24_excluded_forms`, `quizle_b24_lead_title`, `quizle_mail_subject`, `tg_chat_id`, `legal_consent_enabled` (bool), `unisender_enabled` (bool) |
| Secrets | `smtp_password`, `b24_webhook` (the URL holds the token), `tg_bot_token` |

## 4. All keys or only the set ones

- wp-bulk returns every key the endpoint returns: no filter, empty values
  kept. On 15.05.2026, 17 of 58 keys were empty.
- The endpoint returns its whole key list, empty ones too, sorted by key.
  Values are strings; the newer keys are typed (`contacts_page` int,
  `legal_consent_enabled` and `unisender_enabled` bool).
- The list follows the plugin version, not the site: ost-term.ru had 58 keys
  on 15.05.2026 and 66 on 29.10.2026. Added: `b24_excluded_forms`,
  `calltouch_counter_id`, `contacts_page`, `instagram`,
  `legal_consent_enabled`, `metro`, `postaladdress`, `unisender_enabled`;
  none removed. Do not hard-code the count; expect new keys after a plugin
  update.
- Values come raw: on 15.05.2026 `inn` had a trailing space. Trim on the
  reader's side.
- The way back (`wp-bulk options` without `--fetch`) skips records with an
  empty key or value: an empty cell does not erase a setting.

## Bug found

- `options` writes option values into the log file: `OptionsEndpoint.get` /
  `update` log the whole dict at DEBUG, the provider's `post` / `put` log
  "full data", and the file sink is always DEBUG. So `smtp_password`,
  `b24_webhook` and `tg_bot_token` land in `<project>/logs/options_*.log` on
  every run. The log of 15.05.2026 above holds `b24_webhook` in plain text.
  Fix: log key names only.
- Not a bug, by design: the `--fetch` output (JSON and the tab) carries the
  secrets in plain text, because wp-bulk is the transport. Whoever reads it
  decides what to keep.

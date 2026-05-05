# Translation Backlog

Tracker for translations to add beyond the current bundled set.
Bundled today: KJV, WEB (English) + YOR / BMYO (Yoruba).

## Free tier candidates (public domain or open-licensed)

### English

| Code | Name                          | License        | Notes |
| ---- | ----------------------------- | -------------- | ----- |
| BSB  | Berean Standard Bible         | Public domain  | Modern, very high quality. Strong candidate to add next. |
| ASV  | American Standard 1901        | Public domain  | Scholarly, near-literal. |
| YLT  | Young's Literal 1898          | Public domain  | Word-for-word, study aid. |
| BBE  | Bible in Basic English 1949   | Public domain  | Simple vocabulary, ESL-friendly. |
| OEB  | Open English Bible            | CC0            | Modern, dedicated to public domain. |
| LSV  | Literal Standard Version 2020 | CC BY-SA       | Modern literal translation. |
| WMB  | World Messianic Bible         | Public domain  | WEB variant with Hebraic terms. |
| DRA  | Douay-Rheims 1899             | Public domain  | Catholic. |
| ERV  | English Revised 1885          | Public domain  | Predecessor to ASV. |

### Yoruba

| Code        | Name                              | License        | Notes |
| ----------- | --------------------------------- | -------------- | ----- |
| YOR1884     | Crowther 1884 (original)          | Public domain  | Foundational. Clean text harder to source -- Bible Society of Nigeria's 1960 revision (BYCV) is what most digital "Crowther" texts actually are. |

## Paid-tier / commercial (require API.Bible or per-user keys)

### English

NIV, ESV, NLT, NASB, NKJV, MSG, CSB, AMP, NRSV.

### Yoruba

| Code | Name                                            | Holder                    |
| ---- | ----------------------------------------------- | ------------------------- |
| BYCV | Bible Society of Nigeria 1960 Crowther revision | Bible Society of Nigeria  |
|      | Yoruba Living Bible                             | (commercial)              |
|      | Yoruba Easy Reading Version                     | (commercial)              |

## Ingestion plan when adding new translations

For each new translation:

1. Verify license, locate USFM source (eBible.org first choice).
2. Add to `app/bible/ingest.py` `TRANSLATIONS` dict.
3. Update `LICENSES.md` with copyright/attribution string.
4. Run `python scripts/download_bibles.py` and
   `python scripts/ingest_bibles.py --only NEW_CODE`.
5. Spot-check 5 well-known verses across translations.
6. Add to README translation table.

For non-USFM sources (some BSB editions ship as JSON), write a thin
adapter in `app/bible/sources/` that emits the same `Verse` tuples our
ingest expects.

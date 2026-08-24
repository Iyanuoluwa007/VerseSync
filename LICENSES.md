# License Notices

## Scope

VerseSync has two kinds of content under two different licenses.

| What | License | Where |
| ---- | ------- | ----- |
| **Source code** (everything in this repository) | MIT | [`LICENSE`](LICENSE) |
| **Bible translations** (downloaded at install time) | Varies per translation, see below | Not in this repository |

The MIT license in `LICENSE` covers the **source code only**. It does
not, and cannot, grant you any rights over the Bible texts. Those are
separately licensed by their respective copyright holders, and one of
them (Yoruba) is copyleft with a trademark condition.

No Bible text is committed to this repository. `scripts/download_bibles.py`
fetches each translation from eBible.org at install time, so the text
travels from its source under its own license rather than being
redistributed by this project.

If you fork VerseSync, the MIT terms are all you need for the code. If
you redistribute an installation that includes the ingested database,
the notices below apply to you as well.

---

## Bundled Bible translations

The three translations VerseSync installs, and their licensing status.

---

## King James Version (KJV)

- **Code**: `KJV`
- **Language**: English
- **Status**: Public Domain
- **Source**: <https://ebible.org/Scriptures/eng-kjv_usfm.zip>

The King James Version of 1611 is in the public domain in the United
States and most jurisdictions worldwide. In the United Kingdom, Crown
Letters Patent grant exclusive printing rights to specific publishers,
but these have no effect on electronic distribution outside the UK and
no effect on import/distribution elsewhere in the world.

---

## World English Bible (WEB)

- **Code**: `WEB`
- **Language**: English
- **Status**: Public Domain
- **Source**: <https://ebible.org/Scriptures/eng-web_usfm.zip>
- **Trademark notice**: "World English Bible" is a trademark of eBible.org.

The text of the World English Bible is dedicated to the public domain.
The trademark may only be used to refer to faithful, unmodified copies
of the WEB text. VerseSync ships the WEB text unmodified.

---

## Bíbélì Mímọ́ ní Èdè Yorùbá Òde-Òní (Open Yoruba Contemporary Bible)

- **Code**: `YOR`
- **Language**: Yoruba
- **Copyright**: © 2009, 2017 Biblica, Inc.
- **License**: [Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/)
- **Source**: <https://ebible.org/Scriptures/yor_usfm.zip>
- **Upstream**: <https://www.biblica.com> and <https://open.bible>

> Biblica® ní oore ọ̀fẹ́ láti lo Bíbélì Mímọ́ ní Èdè Yorùbá Òde-Òní™
> Ẹ̀tọ́ àdàkọ © 2009, 2017 Biblica, Inc.
>
> Biblica® Open Yoruba Contemporary Bible™
> Copyright © 2009, 2017 by Biblica, Inc.

"Biblica" is a trademark registered in the United States Patent and
Trademark Office by Biblica, Inc. Used with permission. The Biblica®
trademark is preserved intact in this distribution as required by the
license.

This work is made available under the **Creative Commons
Attribution-ShareAlike 4.0 International License (CC BY-SA 4.0)**.
Under the terms of this license you may copy and redistribute the
unmodified work as long as you keep the Biblica® trademark intact.
If you modify the text and create a derivative work, you must remove
the Biblica® trademark and clearly indicate your changes.

VerseSync ships the Yoruba Bible text **unmodified**. The full
license text is available at the URL above.

---

## A note on copyrighted translations not bundled

The following translations are commonly requested but cannot be
bundled in a free, redistributable application due to commercial
licensing restrictions:

- **NIV** — Biblica, Inc.
- **ESV** — Crossway Bibles
- **NLT** — Tyndale House Publishers
- **MSG** — NavPress
- **NASB** — The Lockman Foundation
- **NKJV** — Thomas Nelson, Inc.

VerseSync supports these via per-user API.Bible accounts in a future
release; users supply their own API key and accept the upstream
license terms.

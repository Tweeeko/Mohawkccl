# CCL Mohawk Finder

GitHub Actions monitor for the CCL Follow the Mohawk treasure hunt. Sends candidate and confirmed £1 PC links via Telegram. No automated purchasing.

Setup: add repository Actions secret `TELEGRAM_BOT_TOKEN`, then use Actions > Mohawk Hunt > Run workflow. Keep the token secret.

Monitoring is best effort: public pages only, robots.txt respected, no guarantee of finding a prize first.


## Catalogue-first upgrade (November 2026)

The scanner keeps the existing HTML, JavaScript, OCR, logo reference, Gemini and Telegram systems. New capabilities:

- Persistent SQLite catalogue index with URL, title, price, first/last seen and last checked.
- Bulk extraction of product cards from category pages, including £1 listings with unrelated product names.
- Product change events, high-priority revisit queues, cross-category time allocation and clue-link graph.
- A separate **Mohawk Catalogue Baseline** workflow scheduled for **11:45am UK time**, which builds a baseline before the 1pm hunt. It uses dry-run notifications and no Chromium.
- Main **Mohawk Hunt** workflow scheduled for 12:55pm, active 1pm–3pm. It restores the latest saved state, runs tests and sends 15-minute status reports.
- Rendered £1 price checks, a maximum of two Chromium visual scans per cycle, and bounded Gemini requests.

**Verification:** The repository tests and GitHub Actions runs must pass before treating this as deployed. A green test suite does not establish live CCL coverage. Review the logs and Telegram heartbeat for unique URLs checked, errors, access restrictions and Gemini calls.

**Safety:** Only publicly accessible pages allowed by robots.txt are fetched. CAPTCHA or Cloudflare challenges are not bypassed. No checkout or purchase actions occur.

**Limitations:** CCL's full catalogue may exceed the number of pages that can be checked in two hours at a respectful request rate. The logo reference is compact and matching may miss altered or partial logos. The bot does not guarantee finding the prize.

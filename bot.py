"""CCL Mohawk public-page clue monitor. No checkout or access-control bypass."""
import os, re, time, json, hashlib, logging, sqlite3
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from intelligence import inspect_html, inspect_javascript, load_promotions

BASE = "https://www.cclonline.com/"
UA = "CCLMohawkMonitor/1.0 (personal clue monitoring)"
CLUE = re.compile(r"mohawk|follow the mohawk|treasure hunt|next clue|first clue|£\s*1\s*(?:gaming\s*)?pc", re.I)
PC = re.compile(r"gaming\s+pc|desktop\s+pc|prebuilt\s+pc|gaming\s+desktop|computer\s+system", re.I)
BAD = re.compile(r"/(?:basket|cart|checkout|account|login|register|wishlist)(?:/|$)", re.I)
log = logging.getLogger("mohawk")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "9228500165")

def safe_url(href, parent=BASE):
    try:
        p = urlsplit(urljoin(parent, href))
        if p.scheme != "https" or p.hostname not in ("cclonline.com", "www.cclonline.com") or BAD.search(p.path):
            return None
        return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))
    except ValueError:
        return None

def notify(message):
    if os.getenv("DRY_RUN", "0") == "1":
        log.warning("DRY RUN: %s", message)
        return True
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("Missing TELEGRAM_BOT_TOKEN secret")
        return False
    try:
        response = requests.post("https://api.telegram.org/bot" + token + "/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True}, timeout=15)
        response.raise_for_status()
        if not response.json().get("ok"):
            raise ValueError("Telegram API returned unsuccessful response")
        return True
    except (requests.RequestException, ValueError):
        log.exception("Telegram alert failed")
        return False

def products(soup, page_url):
    def walk(obj):
        if isinstance(obj, list):
            for v in obj: yield from walk(v)
        elif isinstance(obj, dict):
            typ = obj.get("@type", "")
            if typ == "Product" or (isinstance(typ, list) and "Product" in typ):
                yield obj
            for v in obj.values():
                if isinstance(v, (dict, list)): yield from walk(v)
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            for item in walk(json.loads(tag.string or tag.get_text())):
                name = str(item.get("name", ""))
                if not PC.search(name): continue
                offers = item.get("offers", [])
                if isinstance(offers, dict): offers = [offers]
                for offer in offers:
                    if not isinstance(offer, dict): continue
                    try: price = float(str(offer.get("price", "")).replace(",", "."))
                    except ValueError: continue
                    if price == 1 and str(offer.get("priceCurrency", "GBP")).upper() == "GBP":
                        yield name, safe_url(item.get("url") or page_url, page_url) or page_url
        except (ValueError, TypeError):
            continue

class Finder:
    def __init__(self):
        self.db = sqlite3.connect("state.sqlite3")
        self.db.execute("create table if not exists seen (kind text, value text, primary key(kind,value))")
        self.db.execute("create table if not exists snapshots (url text primary key, digest text)")
        self.db.commit()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})
        self.robots = RobotFileParser()
        self.robots.set_url(urljoin(BASE, "robots.txt"))
        self.allowed = False
        try:
            response = self.session.get(self.robots.url, timeout=15)
            response.raise_for_status()
            self.robots.parse(response.text.splitlines())
            self.allowed = True
        except requests.RequestException:
            log.error("Cannot retrieve robots.txt; scanning disabled")
        self.queue = [(BASE, 100), (urljoin(BASE, "all-deals/"), 80)]
        self.last_request = 0.0
        self.blocked = False
        self.promotions = load_promotions()
        self.scanned_scripts = set()

    def alert_once(self, kind, key, message):
        if self.db.execute("select 1 from seen where kind=? and value=?", (kind, key)).fetchone():
            return
        if notify(message):
            self.db.execute("insert or ignore into seen values (?,?)", (kind, key))
            self.db.commit()

    def fetch(self, url):
        if not self.allowed or not self.robots.can_fetch(UA, url):
            return None
        delay = max(2.0, float(os.getenv("REQUEST_DELAY_SECONDS", "3")))
        wait = self.last_request + delay - time.monotonic()
        if wait > 0: time.sleep(wait)
        self.last_request = time.monotonic()
        try:
            response = self.session.get(url, timeout=18, allow_redirects=False)
            if response.status_code in (403, 429, 503):
                self.blocked = True
                self.alert_once("blocked", url, "CCL access restriction (HTTP " + str(response.status_code) + "). Check manually: " + url)
                return None
            if response.status_code != 200: return None
            if "text/html" not in response.headers.get("content-type", ""): return None
            if len(response.content) > 2_000_000: return None
            page = response.text
            if re.search(r"cf-chl-|cf-turnstile|checking your browser|verify you are human|captcha", page[:12000], re.I):
                self.blocked = True
                self.alert_once("blocked", url, "CCL human verification detected. Open manually: " + url)
                return None
            return page
        except requests.RequestException:
            log.exception("Fetch failed: %s", url)
            return None

    def cycle(self):
        todo = sorted(set(self.queue), key=lambda x: -x[1])
        self.queue = []
        limit = max(1, min(50, int(os.getenv("MAX_PAGES_PER_CYCLE", "25"))))
        for url, priority in todo[:limit]:
            if self.blocked: break
            html = self.fetch(url)
            if html is None: continue
            source_digest = hashlib.sha256(html.encode()).hexdigest()
            prior_source = self.db.execute("select digest from snapshots where url=?", ("source:" + url,)).fetchone()
            source_changed = prior_source is None or prior_source[0] != source_digest
            self.db.execute("insert or replace into snapshots values (?,?)", ("source:" + url, source_digest))
            self.db.commit()
            findings, scripts = inspect_html(html, url, self.promotions)
            for finding in findings:
                if source_changed and finding["score"] >= 6:
                    key = hashlib.sha256((url + finding["source"] + finding["excerpt"]).encode()).hexdigest()
                    self.alert_once("evidence", key, "POSSIBLE CCL CLUE (" + finding["source"] + ", score " + str(finding["score"]) + ")\n" + finding["excerpt"] + "\n" + url)
            # Inspect a small, bounded number of same-site JavaScript assets.
            for script_url in scripts[:3]:
                if script_url in self.scanned_scripts or not self.robots.can_fetch(UA, script_url):
                    continue
                self.scanned_scripts.add(script_url)
                wait = self.last_request + max(2.0, float(os.getenv("REQUEST_DELAY_SECONDS", "3"))) - time.monotonic()
                if wait > 0: time.sleep(wait)
                self.last_request = time.monotonic()
                try:
                    response = self.session.get(script_url, timeout=15, allow_redirects=False)
                    if response.status_code in (403, 429, 503):
                        self.blocked = True
                        self.alert_once("blocked", script_url, "CCL script access restricted; check manually: " + script_url)
                        break
                    if response.status_code == 200 and len(response.content) <= 1_000_000:
                        js_digest = hashlib.sha256(response.content).hexdigest()
                        prior_js = self.db.execute("select digest from snapshots where url=?", ("js:" + script_url,)).fetchone()
                        js_changed = prior_js is None or prior_js[0] != js_digest
                        self.db.execute("insert or replace into snapshots values (?,?)", ("js:" + script_url, js_digest))
                        self.db.commit()
                        for finding in inspect_javascript(response.text, script_url, self.promotions):
                            if js_changed and finding["score"] >= 6:
                                key = hashlib.sha256((script_url + finding["excerpt"]).encode()).hexdigest()
                                self.alert_once("script_clue", key, "POSSIBLE CCL SCRIPT CLUE (score " + str(finding["score"]) + ")\n" + finding["excerpt"] + "\n" + script_url)
                except requests.RequestException:
                    log.warning("Script scan failed: %s", script_url)
            if self.blocked: break
            soup = BeautifulSoup(html, "html.parser")
            for name, product_url in products(soup, url):
                self.alert_once("product", product_url, "CCL £1 PC PRODUCT DETECTED\\n" + name + "\\n" + product_url)
            if relevant_visual := (priority >= 65 or CLUE.search(soup.get_text(" ", strip=True)[:6000])):
                visual_scan(soup, url, self)
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            body = soup.get_text(" ", strip=True)[:12000]
            relevant = bool(CLUE.search(title + " " + body))
            digest = hashlib.sha256((title + body).encode()).hexdigest()
            previous = self.db.execute("select digest from snapshots where url=?", (url,)).fetchone()
            self.db.execute("insert or replace into snapshots values (?,?)", (url, digest))
            self.db.commit()
            if relevant and (previous is None or previous[0] != digest):
                self.alert_once("clue", url + digest, "CCL CLUE / PAGE CHANGE\\n" + title[:180] + "\\n" + url)
            links = []
            for a in soup.select("a[href]"):
                dest = safe_url(a.get("href"), url)
                if not dest: continue
                label = a.get_text(" ", strip=True)[:200]
                alt = " ".join(img.get("alt", "") for img in a.select("img"))
                score = 100 if CLUE.search(label + " " + alt + " " + dest) else (65 if relevant else 10)
                if score >= 65: links.append((dest, score))
            self.queue.extend(links[:75])
            self.queue.append((url, priority))
        self.queue.extend(todo[limit:])
        self.queue.extend([(BASE, 100), (urljoin(BASE, "all-deals/"), 80)])
        self.queue = sorted(set(self.queue), key=lambda x: -x[1])[:250]
        log.info("Cycle complete; queued %d pages", len(self.queue))


# Visual scanning is optional at runtime but installed in the workflow.
def visual_scan(soup, url, finder):
    from vision import image_candidates, logo_match, browser_scan
    matches = []
    for image_url, target, context in image_candidates(soup, url)[:12]:
        if not finder.robots.can_fetch(UA, image_url):
            continue
        wait = finder.last_request + max(2.0, float(os.getenv("REQUEST_DELAY_SECONDS", "3"))) - time.monotonic()
        if wait > 0: time.sleep(wait)
        finder.last_request = time.monotonic()
        try:
            response = finder.session.get(image_url, timeout=12, allow_redirects=False)
            if response.status_code in (403, 429):
                log.warning("Image access restricted; stopping image checks on %s", url)
                break
            if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/") or len(response.content) > 2_000_000:
                continue
            score = logo_match(response.content)
            if score >= 12:
                matches.append((image_url, target, context, score))
        except requests.RequestException:
            continue
    if os.getenv("ENABLE_BROWSER", "1") == "1":
        try:
            rendered, rendered_matches = browser_scan(url)
            if rendered:
                for name, product_url in products(BeautifulSoup(rendered, "html.parser"), url):
                    finder.alert_once("product", product_url, "CCL £1 PC PRODUCT DETECTED (rendered page)\\n" + name + "\\n" + product_url)
            matches.extend(rendered_matches)
        except Exception as exc:
            log.warning("Optional browser scan unavailable: %s", exc)
    for image_url, target, context, score in matches:
        key = hashlib.sha256((url + image_url + target).encode()).hexdigest()
        finder.alert_once("logo", key, "POSSIBLE CCL MOHAWK LOGO - CHECK LINK\\nPage: " + url + "\\nImage: " + image_url + "\\nCandidate link: " + target + "\\nContext: " + context[:250] + "\\nVisual match score: " + str(score) + "\\nVisual matches can be false positives.")
        if safe_url(target, url):
            finder.queue.append((safe_url(target, url), 120))

def active():
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.year == 2026 and now.month == 11 and 23 <= now.day <= 30 and 13 <= now.hour < 15

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.getenv("TEST_TELEGRAM") == "1":
        if not notify("CCL Mohawk Finder: Telegram test successful."): raise SystemExit(1)
        return
    if not os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("DRY_RUN") != "1":
        raise SystemExit("Configure TELEGRAM_BOT_TOKEN in GitHub Actions secrets")
    finder = Finder()
    while True:
        if active():
            finder.cycle()
            time.sleep(max(30, int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))))
        else:
            now = datetime.now(ZoneInfo("Europe/London"))
            if now.year != 2026 or now.month != 11 or now.day > 30 or (now.day >= 23 and now.hour >= 15):
                log.info("Hunt window ended")
                break
            time.sleep(15)

if __name__ == "__main__":
    main()

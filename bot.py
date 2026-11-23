"""CCL Mohawk public-page clue monitor. No checkout or access-control bypass."""
import os, re, time, json, hashlib, logging, sqlite3
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from intelligence import inspect_html, inspect_javascript, load_promotions
from gemini_analysis import Gemini
from catalog import sitemap_urls, sitemap_seeds, priority as catalog_priority, catalog_links, price_candidates
import catalogue_index as index

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
        index.init(self.db)
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
        self.gemini = Gemini()
        self.scanned_scripts = set()
        self.last_scanned = {}
        self.clue_sources = set()
        self.stats = {"pages":0,"errors":0,"candidates":0,"sitemaps":0}
        self.last_heartbeat = time.monotonic()
        self.discover_sitemaps(response.text if self.allowed else "")
        self.queue.extend(index.pending(self.db,1000))

    def discover_sitemaps(self, robots_text):
        """Bounded sitemap traversal, robots compliant and fail-closed."""
        if not self.allowed: return
        pending = sitemap_seeds(robots_text)
        visited = set()
        found = []
        while pending and len(visited) < 15 and not self.blocked:
            url = pending.pop(0)
            if url in visited or not self.robots.can_fetch(UA,url): continue
            visited.add(url)
            self.throttle()
            try:
                r = self.session.get(url, timeout=18, allow_redirects=False)
                if r.status_code in (403,429,503):
                    self.blocked = True
                    self.alert_once("blocked",url,"CCL sitemap access restricted: "+url)
                    break
                if r.status_code != 200 or len(r.content)>2_000_000: continue
                children, pages = sitemap_urls(r.text)
                pending.extend(children[:20])
                found.extend(pages)
                self.stats["sitemaps"] += 1
            except requests.RequestException:
                self.stats["errors"] += 1
        prioritized = sorted({u for u in found if safe_url(u)},key=lambda u:-catalog_priority(u))
        high = [u for u in prioritized if catalog_priority(u) >= 65]
        broad = [u for u in prioritized if catalog_priority(u) < 65]
        index.discover(self.db,found)
        self.queue.extend((u,catalog_priority(u)) for u in high[:850])
        self.queue.extend((u,45) for u in broad[:650])
        self.queue = sorted(set(self.queue),key=lambda x:-x[1])[:1500]
        log.info("Catalogue discovery: %d sitemap files, %d URLs, %d queued",
                 self.stats["sitemaps"],len(found),len(self.queue))

    def throttle(self):
        wait = self.last_request + max(2.0,float(os.getenv("REQUEST_DELAY_SECONDS","3"))) - time.monotonic()
        if wait > 0: time.sleep(wait)
        self.last_request = time.monotonic()

    def heartbeat(self, force=False):
        if not force and time.monotonic()-self.last_heartbeat < 900: return
        self.last_heartbeat = time.monotonic()
        total,checked,edges=index.counts(self.db)
        message = ("CCL Mohawk Finder STATUS\n"
                   + "Catalogue URLs: " + str(total) + "\n"
                   + "Unique pages checked: " + str(checked) + "\n"
                   + "Clue links: " + str(edges) + "\n"
                   + "Pages fetched: " + str(self.stats["pages"]) + "\n"
                   + "Sitemaps: " + str(self.stats["sitemaps"]) + "\n"
                   + "Candidates: " + str(self.stats["candidates"]) + "\n"
                   + "Errors: " + str(self.stats["errors"]) + "\n"
                   + "Gemini calls: " + str(self.gemini.calls) + "\n"
                   + "Queued pages: " + str(len(self.queue)) + "\n"
                   + "Clue pages: " + str(len(self.clue_sources)) + "\n"
                   + "Access blocked: " + str(self.blocked))
        notify(message)

    def alert_once(self, kind, key, message):
        if self.db.execute("select 1 from seen where kind=? and value=?", (kind, key)).fetchone():
            return
        if notify(message):
            self.db.execute("insert or ignore into seen values (?,?)", (kind, key))
            self.db.commit()

    def fetch(self, url):
        if self.blocked or not self.allowed or not self.robots.can_fetch(UA, url):
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
            self.stats["pages"] += 1
            return page
        except requests.RequestException:
            self.stats["errors"] += 1
            log.exception("Fetch failed: %s", url)
            return None

    def ai_review(self, url, excerpt, source, image=None, mime="image/jpeg"):
        key = hashlib.sha256((url + source + excerpt).encode()).hexdigest()
        if self.db.execute("select 1 from seen where kind=? and value=?", ("ai_reviewed", key)).fetchone():
            return
        result = self.gemini.analyze(url=url, excerpt=excerpt, image=image, mime=mime)
        if result is None:
            return
        self.db.execute("insert or ignore into seen values (?,?)", ("ai_reviewed", key))
        self.db.commit()
        if result["relevant"] and result["confidence"] >= .7:
            message = ("GEMINI POSSIBLE CCL CLUE (NOT CONFIRMED)\n"
                + "Confidence: " + str(round(result["confidence"]*100)) + "%\n"
                + "Reason: " + result["reason"] + "\n"
                + "Clue: " + result["clue"] + "\n" + url)
            self.alert_once("ai_candidate", key, message)
            for term in result["suggested_keywords"]:
                if term.lower() not in [p.lower() for p in self.promotions] and len(self.promotions) < 30:
                    self.promotions.append(term)

    def cycle(self):
        now = time.monotonic()
        pending = sorted(set(self.queue), key=lambda x: -x[1])
        # Rotate across the catalogue rather than rescanning the same high-priority pages.
        todo = [item for item in pending if now-self.last_scanned.get(item[0],-1e9)>=600]
        deferred = [item for item in pending if item not in todo]
        self.queue = deferred
        limit = max(1, min(50, int(os.getenv("MAX_PAGES_PER_CYCLE", "25"))))
        # Reserve at least one third of requests for lower-ranked catalogue pages.
        high_todo = [x for x in todo if x[1] >= 65]
        low_todo = [x for x in todo if x[1] < 65]
        low_count = min(len(low_todo), max(1, limit // 3))
        selected = high_todo[:limit-low_count] + low_todo[:low_count]
        for url, priority in selected:
            if self.blocked: break
            self.last_scanned[url] = time.monotonic()
            html = self.fetch(url)
            if html is None: continue
            source_digest = hashlib.sha256(html.encode()).hexdigest()
            prior_source = self.db.execute("select digest from snapshots where url=?", ("source:" + url,)).fetchone()
            source_changed = prior_source is None or prior_source[0] != source_digest
            self.db.execute("insert or replace into snapshots values (?,?)", ("source:" + url, source_digest))
            self.db.commit()
            findings, scripts = inspect_html(html, url, self.promotions)
            if source_changed and findings:
                strongest = findings[0]
                if strongest["score"] >= 6:
                    self.ai_review(url, strongest["excerpt"], strongest["source"])
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
            title = soup.title.get_text(" ",strip=True) if soup.title else ""
            page_event = index.record(self.db,url,title,digest=source_digest,
                clue=bool(CLUE.search(title+" "+soup.get_text(" ",strip=True)[:12000])))
            if page_event in ("new","changed"):
                log.info("Catalogue %s: %s",page_event,url)
            for card_url,card_title,card_price in index.extract_cards(soup,url,safe_url):
                event = index.record(self.db,card_url,card_title,price=card_price or None)
                self.queue.append((card_url,110 if event=="price_changed" else 85 if event=="new" else 45))
                if event=="price_changed":
                    self.stats["candidates"]+=1
                    self.alert_once("price_change",card_url+card_price,
                        "CCL PRODUCT PRICE CHANGE (verify)\n"+card_title+"\nNew displayed price: "+card_price+"\n"+card_url)
            for name, product_url in price_candidates(soup, url, safe_url):
                self.stats["candidates"] += 1
                self.alert_once("visible_price", product_url, "POSSIBLE £1 PC LISTING - VERIFY PRICE\n" + name + "\n" + product_url)
                self.ai_review(product_url, name, "visible_price")
            for name, product_url in products(soup, url):
                self.stats["candidates"] += 1
                self.alert_once("product", product_url, "CCL £1 PC PRODUCT DETECTED (structured data, verify live)\n" + name + "\n" + product_url)
            if (source_changed and (CLUE.search(soup.get_text(" ", strip=True)[:6000])
                    or catalog_priority(url) >= 95 or (priority == 45 and self.stats["pages"] % 8 == 0))):
                visual_scan(soup, url, self)
            body = soup.get_text(" ", strip=True)[:12000]
            relevant = bool(CLUE.search(title + " " + body))
            digest = hashlib.sha256((title + body).encode()).hexdigest()
            previous = self.db.execute("select digest from snapshots where url=?", (url,)).fetchone()
            self.db.execute("insert or replace into snapshots values (?,?)", (url, digest))
            self.db.commit()
            if relevant and (previous is None or previous[0] != digest):
                if not findings:
                    self.ai_review(url, body[:1500], "page_text")
                self.alert_once("clue", url + digest, "CCL CLUE / PAGE CHANGE\\n" + title[:180] + "\\n" + url)
            links = []
            for a in soup.select("a[href]"):
                dest = safe_url(a.get("href"), url)
                if not dest: continue
                label = a.get_text(" ", strip=True)[:200]
                alt = " ".join(img.get("alt", "") for img in a.select("img"))
                score = 100 if CLUE.search(label + " " + alt + " " + dest) else (65 if relevant else 10)
                if score >= 65: links.append((dest, score))
            if relevant:
                for a in soup.select("a[href]")[:300]:
                    target=safe_url(a.get("href"),url)
                    if target and (CLUE.search(a.get_text(" ",strip=True)) or CLUE.search(target)):
                        index.connect(self.db,url,target,a.get_text(" ",strip=True))
                        self.queue.append((target,120))
                self.clue_sources.add(url)
                log.info("Clue trail: %s (%d distinct pages)", url, len(self.clue_sources))
            self.queue.extend(links[:75])
            self.queue.extend(catalog_links(soup,url,safe_url)[:100])
            # Product links in category grids can have opaque paths and names.
            for card in soup.select('[data-product-id] a[href], [itemtype*="Product"] a[href], .product-card a[href], .product-item a[href]')[:120]:
                dest = safe_url(card.get("href"),url)
                if dest: self.queue.append((dest,45))
            self.queue.append((url, priority))
        selected_set = set(selected)
        self.queue.extend(item for item in todo if item not in selected_set)
        self.queue.extend([(BASE, 100), (urljoin(BASE, "all-deals/"), 80)])
        self.queue.extend(index.pending(self.db,300))
        high_queue = sorted({x for x in self.queue if x[1] >= 65}, key=lambda x:-x[1])
        low_queue = sorted({x for x in self.queue if x[1] < 65}, key=lambda x:x[0])
        self.queue = high_queue[:900] + low_queue[:600]
        self.heartbeat()
        log.info("Cycle complete; pages=%d candidates=%d clue_pages=%d gemini=%d errors=%d queued=%d", self.stats["pages"],self.stats["candidates"],len(self.clue_sources),self.gemini.calls,self.stats["errors"],len(self.queue))


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
                finder.ai_review(target, context or "Possible Mohawk logo image", "logo_image", response.content, response.headers.get("content-type", "image/jpeg").split(";")[0])
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
    for match in matches:
        image_url, target, context, score = match[:4]
        if len(match) > 4:
            finder.ai_review(target, context or "Mohawk logo candidate", "rendered_image", match[4], "image/png")
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
            if finder.blocked:
                finder.heartbeat(force=True)
                log.error("Access restriction detected; scan stopped")
                break
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

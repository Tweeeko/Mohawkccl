"""Best-effort visual clue detection for public CCL pages."""
import io, os, re
from urllib.parse import urljoin
from urllib.parse import urlsplit, urlunsplit

def safe_url(href, parent):
    try:
        p = urlsplit(urljoin(parent, href))
        if p.scheme != 'https' or p.hostname not in ('www.cclonline.com', 'cclonline.com'): return None
        return urlunsplit((p.scheme,p.netloc,p.path or '/',p.query,''))
    except ValueError: return None
REF = os.path.join(os.path.dirname(__file__), "assets", "mohawk-reference.jpg")

def image_candidates(soup, url):
    result = []
    for img in soup.select("img[src],img[data-src]"):
        src = img.get("src") or img.get("data-src") or ""
        image = safe_url(src, url)
        if not image: continue
        anchor = img.find_parent("a", href=True)
        target = safe_url(anchor["href"], url) if anchor else url
        context = " ".join(filter(None, [img.get("alt", ""), img.get("title", ""), anchor.get_text(" ", strip=True) if anchor else ""]))[:400]
        result.append((image, target or url, context))
    return result

def logo_match(raw):
    """ORB feature match with RANSAC homography; score is number of inliers."""
    try:
        import cv2, numpy as np
        reference = cv2.imread(REF, cv2.IMREAD_GRAYSCALE) if os.path.exists(REF) else None
        target = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if target is None: return 0
        # Text-and-colour recognition works even without a stored reference image.
        import pytesseract
        from PIL import Image
        rgb = cv2.cvtColor(target, cv2.COLOR_GRAY2RGB)
        original = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        green = 0
        if original is not None:
            b,g,r = cv2.split(original)
            green = float(((g.astype("int16") > r.astype("int16") + 35) & (g.astype("int16") > b.astype("int16") + 25) & (g > 80)).mean())
        words = re.sub(r"[^A-Z ]", " ", pytesseract.image_to_string(Image.open(io.BytesIO(raw))).upper())
        if ("MOHAWK" in words and ("FOLLOW" in words or green > .04)) or ("FOLLOW THE MOHAWK" in words):
            return 20
        if reference is None: return 0
        detector = cv2.ORB_create(nfeatures=1800, fastThreshold=8)
        points1, desc1 = detector.detectAndCompute(reference, None)
        points2, desc2 = detector.detectAndCompute(target, None)
        if desc1 is None or desc2 is None: return 0
        matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc1, desc2, k=2)
        good = [a for pair in matches if len(pair) == 2 for a,b in [pair] if a.distance < .72*b.distance]
        if len(good) < 12: return len(good)
        source = np.float32([points1[m.queryIdx].pt for m in good]).reshape(-1,1,2)
        destination = np.float32([points2[m.trainIdx].pt for m in good]).reshape(-1,1,2)
        matrix, mask = cv2.findHomography(source, destination, cv2.RANSAC, 5)
        return int(mask.sum()) if matrix is not None and mask is not None else 0
    except Exception:
        return 0

def browser_scan(url):
    """Inspect rendered public DOM. No login, clicking or challenge solving."""
    from playwright.sync_api import sync_playwright
    matches = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width":1365,"height":900})
            page.goto(url, wait_until="domcontentloaded", timeout=18000)
            page.wait_for_timeout(900)
            html = page.content()
            for img in page.locator("img").all()[:30]:
                try:
                    if not img.is_visible(): continue
                    src = img.get_attribute("src") or ""
                    if not src: continue
                    score = logo_match(img.screenshot(timeout=2500))
                    if score < 12: continue
                    parent = img.locator("xpath=ancestor::a[1]")
                    href = parent.get_attribute("href") if parent.count() else url
                    matches.append((urljoin(url,src),safe_url(href,url) or url,img.get_attribute("alt") or "",score))
                except Exception:
                    continue
            return html,matches
        finally:
            browser.close()

"""Public-page evidence extraction, decoding and conservative clue scoring."""
import base64
import html
import json
import re
from urllib.parse import unquote, urljoin, urlsplit
from bs4 import BeautifulSoup, Comment

KEYWORDS = re.compile(r"mohawk|follow[\s_-]*the[\s_-]*mohawk|treasure[\s_-]*hunt|£\s*1\b|one[\s_-]*pound|hidden[\s_-]*pc|gaming[\s_-]*pc", re.I)
PROMO = re.compile(r"mohawk|follow[\s_-]*the[\s_-]*mohawk|treasure[\s_-]*hunt", re.I)
GENERIC = re.compile(r"webpack|react|hydration|chunk|sourceMappingURL|node_modules|favicon|analytics|tracking|google-tag-manager", re.I)
BASE64 = re.compile(r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{4}){4,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?(?![A-Za-z0-9+/])")
ESCAPES = re.compile(r"\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2})")
HEX = re.compile(r"(?<![0-9a-fA-F])(?:0x[0-9a-fA-F]{2}[\s,;]*){5,}")
URL = re.compile(r"https?://[^\s\"'<>\\]+")
MAX_TEXT = 1_000_000

def decode_candidates(text):
    """Bounded one-pass decoding; no code execution, no arbitrary decompression."""
    text = text[:MAX_TEXT]
    yield text
    unescaped = html.unescape(unquote(text))
    if unescaped != text:
        yield unescaped
    if ESCAPES.search(text):
        decoded = re.sub(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})",
            lambda m: chr(int(m.group(1) or m.group(2), 16)), text)
        yield decoded
    for match in list(BASE64.finditer(text))[:35]:
        raw = match.group()
        if len(raw) > 12000: continue
        try:
            decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True)
            if len(decoded) > 8192: continue
            result = decoded.decode("utf-8")
            if sum(c.isprintable() for c in result) / max(1, len(result)) > .85:
                yield result
        except (ValueError, UnicodeDecodeError):
            continue
    for match in list(HEX.finditer(text))[:20]:
        try:
            yield bytes.fromhex(re.sub(r"0x|[\s,;]", "", match.group())).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass

def evidence(text, source, context="", promo_terms=()):
    results = []
    for decoded in decode_candidates(text):
        for m in list(KEYWORDS.finditer(decoded))[:30]:
            excerpt = decoded[max(0,m.start()-110):min(len(decoded),m.end()+150)].replace("\n", " ")
            score = 1
            if PROMO.search(excerpt): score += 4
            if re.search(r"£\s*1|one[\s_-]*pound", excerpt, re.I): score += 3
            if re.search(r"pc|desktop|product|prize", excerpt, re.I): score += 2
            if source in ("html_comment","hidden_dom","data_attribute","image_metadata"): score += 1
            if GENERIC.search(excerpt) and not PROMO.search(excerpt): score -= 3
            if promo_terms and any(term.lower() in excerpt.lower() for term in promo_terms): score += 2
            if score < 4: continue
            results.append({"source":source,"score":score,"excerpt":excerpt[:260],"context":context[:140]})
    return sorted(results, key=lambda x:-x["score"])[:8]

def inspect_html(raw, url, promo_terms=()):
    soup = BeautifulSoup(raw[:2_000_000], "html.parser")
    found = []
    for c in soup.find_all(string=lambda s:isinstance(s,Comment))[:200]:
        found += evidence(str(c),"html_comment",url,promo_terms)
    for node in soup.find_all(True)[:6000]:
        attrs = node.attrs
        for k,v in attrs.items():
            if k.startswith("data-"):
                found += evidence(str(v),"data_attribute",k,promo_terms)
        style = str(attrs.get("style",""))
        hidden = node.has_attr("hidden") or attrs.get("aria-hidden") == "true" or "display:none" in style.replace(" ","").lower()
        if hidden:
            found += evidence(node.get_text(" ",strip=True)[:3000],"hidden_dom",node.name,promo_terms)
    for script in soup.select('script[type="application/json"],script[type="application/ld+json"],script#__NEXT_DATA__')[:80]:
        found += evidence(script.get_text()[:150000],"embedded_json",url,promo_terms)
    scripts = []
    for tag in soup.select("script[src]")[:100]:
        target = urljoin(url,tag.get("src",""))
        if urlsplit(target).hostname in ("www.cclonline.com","cclonline.com") and target.startswith("https://"):
            scripts.append(target)
    for tag in soup.select("img")[:100]:
        found += evidence(" ".join(str(tag.get(x,"")) for x in ("src","alt","title","data-src")),"image_metadata",url,promo_terms)
    return dedupe(found),list(dict.fromkeys(scripts))

def inspect_javascript(js, url, promo_terms=()):
    # Static analysis only. Never execute fetched JavaScript.
    return dedupe(evidence(js[:MAX_TEXT],"javascript_bundle",url,promo_terms))

def dedupe(items):
    unique={}
    for item in items:
        key=(item["source"],item["excerpt"])
        if key not in unique or item["score"]>unique[key]["score"]: unique[key]=item
    return sorted(unique.values(),key=lambda x:-x["score"])[:20]

def load_promotions(path="promotions.json"):
    try:
        with open(path,encoding="utf-8") as handle: data=json.load(handle)
        return [str(x).strip() for x in data.get("phrases",[]) if 3<=len(str(x))<=100][:30]
    except (OSError,ValueError,TypeError):
        return []

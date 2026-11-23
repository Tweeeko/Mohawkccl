"""Bounded public catalogue discovery and price candidate extraction."""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup

PRICE = re.compile(r"(?<![\d])£\s*1(?:[.,]00)?(?![\d.,])")
PC = re.compile(r"gaming[\s-]*(?:pc|desktop|computer)|(?:desktop|prebuilt|custom)[\s-]*(?:pc|computer)|gaming[\s-]*system|pc[\s-]*bundle",re.I)
HUNT = re.compile(r"mohawk|follow[\s-]*the[\s-]*mohawk|treasure[\s-]*hunt",re.I)
CAT = re.compile(r"gaming|desktop|computer|prebuilt|pc|system|deal|sale|bundle|offer",re.I)
MAX_SITEMAP_BYTES = 2_000_000

def sitemap_urls(xml_text, base="https://www.cclonline.com/"):
    """Return (child sitemap URLs, page URLs). No XML entity expansion."""
    if "<!DOCTYPE" in xml_text[:500].upper() or "<!ENTITY" in xml_text[:500].upper():
        return [],[]
    try: root=ET.fromstring(xml_text[:MAX_SITEMAP_BYTES])
    except ET.ParseError: return [],[]
    children=[]; pages=[]
    is_index=root.tag.rsplit("}",1)[-1]=="sitemapindex"
    for loc in root.iter():
        if loc.tag.rsplit("}",1)[-1]!="loc" or not loc.text: continue
        url=loc.text.strip()
        p=urlsplit(url)
        if p.scheme!="https" or p.hostname not in ("cclonline.com","www.cclonline.com"): continue
        (children if is_index else pages).append(url)
    return children[:150],pages[:25000]

def sitemap_seeds(robots_text):
    seeds=[]
    for line in robots_text.splitlines():
        if line.lower().startswith("sitemap:"):
            u=line.split(":",1)[1].strip()
            if urlsplit(u).hostname in ("www.cclonline.com","cclonline.com") and u.startswith("https://"):
                seeds.append(u)
    if not seeds: seeds=["https://www.cclonline.com/sitemap.xml"]
    return list(dict.fromkeys(seeds))

def priority(url, title=""):
    text=url+" "+title
    if HUNT.search(text): return 120
    if PC.search(text): return 95
    if CAT.search(text): return 65
    return 12

def price_candidates(soup, page_url, safe_url):
    """Conservative visible card/detail evidence, not generic sitewide £1 text."""
    results=[]; seen=set()
    selectors='[itemtype*="Product"],[data-product-id],article,li[class*="product"],div[class*="product-card"],div[class*="product-item"],main'
    for node in soup.select(selectors)[:1200]:
        text=node.get_text(" ",strip=True)[:1500]
        if not PRICE.search(text) or not (PC.search(text) or HUNT.search(text)): continue
        if node.name=="main" and len(text)>800: continue
        # Require a price-bearing element or price attribute near the title.
        priced=node.select_one('[itemprop="price"],[class*="price"],[data-price],meta[property="product:price:amount"]')
        if not priced and node.name=="main": continue
        value=(priced.get("content") or priced.get("data-price") or priced.get_text(" ",strip=True)) if priced else text
        if not PRICE.search(str(value)) and not re.fullmatch(r"1(?:[.,]00)?",str(value).strip()): continue
        anchor=node.select_one("a[href]")
        target=safe_url(anchor.get("href"),page_url) if anchor else page_url
        target=target or page_url
        key=(target,text[:120])
        if key in seen: continue
        seen.add(key)
        results.append((text[:180],target,"visible price"))
    return results[:30]

def catalog_links(soup, page_url, safe_url):
    links=[]
    for a in soup.select("a[href]")[:4000]:
        url=safe_url(a.get("href"),page_url)
        if not url: continue
        label=a.get_text(" ",strip=True)[:120]
        p=priority(url,label)
        if p>=65 or re.search(r"(?:[?&]page=|/page/\d+)",url,re.I):
            links.append((url,p))
    return sorted(set(links),key=lambda x:-x[1])[:300]


def generic_one_pound_cards(soup,page_url,safe_url):
    """Flag £1 products even when the item title does not mention a PC."""
    found=[]
    for node in soup.select('[data-product-id],[itemtype*="Product"],.product-card,.product-item,li[class*="product"]')[:500]:
        price=node.select_one('[itemprop="price"],[class*="price"],[data-price]')
        if price is None: continue
        value=str(price.get("content") or price.get("data-price") or price.get_text(" ",strip=True))
        if not (PRICE.search(value) or re.fullmatch(r"1(?:[.,]00)?",value.strip())): continue
        a=node.select_one("a[href]")
        if a is None: continue
        url=safe_url(a.get("href"),page_url)
        if url: found.append((a.get_text(" ",strip=True)[:180],url))
    return list(dict.fromkeys(found))[:30]

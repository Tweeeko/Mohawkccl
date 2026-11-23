"""Persistent catalogue intelligence: snapshots, price changes and clue links."""
import hashlib
import re
import time
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from catalog import price_candidates, priority

def init(db):
    db.execute("""create table if not exists catalogue (
        url text primary key, title text not null default '',
        price text not null default '', digest text not null default '',
        first_seen integer not null, last_seen integer not null,
        last_checked integer not null default 0, category text not null default '',
        clue integer not null default 0)""")
    db.execute("""create table if not exists clue_edges (
        source text not null, target text not null,
        context text not null default '',
        primary key(source,target))""")
    db.commit()

def record(db,url,title="",price=None,digest=None,clue=False,category=""):
    now=int(time.time())
    old=db.execute("select title,price,digest,clue from catalogue where url=?",(url,)).fetchone()
    title=(title or "")[:250]
    if old:
        new_price=old[1] if price is None else str(price)[:60]
        new_digest=old[2] if digest is None else digest
        db.execute("""update catalogue set title=?,price=?,digest=?,last_seen=?,
            last_checked=?,category=?,clue=? where url=?""",
            (title or old[0],new_price,new_digest,now,now,category,bool(clue or old[3]),url))
        event="price_changed" if price is not None and old[1] and new_price!=old[1] else ("changed" if digest and old[2] and digest!=old[2] else "unchanged")
    else:
        db.execute("""insert into catalogue(url,title,price,digest,first_seen,last_seen,last_checked,category,clue)
            values(?,?,?,?,?,?,?,?,?)""",(url,title,str(price or "")[:60],digest or "",now,now,now,category,int(clue)))
        event="new"
    db.commit()
    return event

def discover(db,urls):
    now=int(time.time())
    rows=[(u,"","","",now,now,0,urlsplit(u).path.split("/")[1][:60],0) for u in dict.fromkeys(urls)]
    db.executemany("""insert or ignore into catalogue
        (url,title,price,digest,first_seen,last_seen,last_checked,category,clue)
        values(?,?,?,?,?,?,?,?,?)""",rows)
    db.commit()
    return len(rows)

def pending(db,limit=1000):
    rows=db.execute("""select url,clue,last_checked,first_seen from catalogue
        order by clue desc,last_checked asc,first_seen desc limit ?""",(limit,)).fetchall()
    return [(u,120 if clue else max(45,priority(u))) for u,clue,_,_ in rows]

def connect(db,source,target,context):
    if source==target:return
    db.execute("insert or replace into clue_edges values(?,?,?)",(source,target,context[:200]))
    db.commit()

def extract_cards(soup,page_url,safe_url):
    """Extract products and prices from category cards in one request."""
    result=[]
    for node in soup.select('[data-product-id],[itemtype*="Product"],.product-card,.product-item,li[class*="product"]')[:500]:
        a=node.select_one("a[href]")
        if not a:continue
        url=safe_url(a.get("href"),page_url)
        if not url:continue
        title=a.get_text(" ",strip=True)[:180] or node.get("data-product-name","")[:180]
        price_node=node.select_one('[itemprop="price"],[class*="price"],[data-price]')
        price=""
        if price_node:
            price=(price_node.get("content") or price_node.get("data-price") or price_node.get_text(" ",strip=True))[:60]
        result.append((url,title,price))
    return list(dict.fromkeys(result))

def counts(db):
    total=db.execute("select count(*) from catalogue").fetchone()[0]
    checked=db.execute("select count(*) from catalogue where last_checked>0").fetchone()[0]
    edges=db.execute("select count(*) from clue_edges").fetchone()[0]
    return total,checked,edges

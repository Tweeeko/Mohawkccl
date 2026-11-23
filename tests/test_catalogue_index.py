import sqlite3
from bs4 import BeautifulSoup
import catalogue_index as index

def db():
    d=sqlite3.connect(":memory:")
    index.init(d)
    return d

def test_persistent_index_and_price_change():
    d=db()
    assert index.record(d,"https://www.cclonline.com/item/","Keyboard",price="£100")=="new"
    assert index.record(d,"https://www.cclonline.com/item/","Keyboard",price="£100")=="unchanged"
    assert index.record(d,"https://www.cclonline.com/item/","Keyboard",price="£1")=="price_changed"
    assert index.counts(d)[:2]==(1,1)

def test_bulk_discovery_and_graph():
    d=db()
    urls=["https://www.cclonline.com/keyboard/","https://www.cclonline.com/mouse/"]
    index.discover(d,urls)
    assert index.counts(d)[:2]==(2,0)
    assert len(index.pending(d))==2
    index.connect(d,urls[0],urls[1],"next clue")
    assert index.counts(d)[2]==1

def test_batch_product_cards():
    from bot import safe_url
    soup=BeautifulSoup('<div class="product-card"><a href="/item/">Gaming PC</a><span class="price">£1.00</span></div>',"html.parser")
    cards=index.extract_cards(soup,"https://www.cclonline.com/",safe_url)
    assert cards==[("https://www.cclonline.com/item/","Gaming PC","£1.00")]

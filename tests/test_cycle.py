"""End-to-end cycle smoke tests without network access or secrets."""
import sqlite3
import time
from unittest.mock import Mock
from bot import Finder
import catalogue_index as index

def make_finder(html):
    f=Finder.__new__(Finder)
    f.db=sqlite3.connect(":memory:")
    f.db.execute("create table seen(kind text,value text,primary key(kind,value))")
    f.db.execute("create table snapshots(url text primary key,digest text)")
    index.init(f.db)
    f.queue=[("https://www.cclonline.com/",100),("https://www.cclonline.com/other-item/",45)]
    f.last_scanned={}
    f.clue_sources=set()
    f.browser_checks=0
    f.blocked=False
    f.allowed=True
    f.promotions=["Follow the Mohawk"]
    f.scanned_scripts=set()
    f.stats={"pages":0,"errors":0,"candidates":0,"sitemaps":0}
    f.last_heartbeat=time.monotonic()
    f.gemini=Mock(calls=0,analyze=Mock(return_value=None))
    f.fetch=Mock(side_effect=lambda url: html.get(url))
    f.alert_once=Mock()
    f.heartbeat=Mock()
    return f

def test_complete_cycle_and_clue_trail(monkeypatch):
    monkeypatch.setenv("ENABLE_BROWSER","0")
    monkeypatch.setenv("MAX_PAGES_PER_CYCLE","3")
    f=make_finder({
      "https://www.cclonline.com/":'<html><title>Shop</title><a href="/other-item/">Other item</a></html>',
      "https://www.cclonline.com/other-item/":'<html><title>Follow the Mohawk</title><p>Next clue here</p></html>',
    })
    f.cycle()
    assert "https://www.cclonline.com/other-item/" in f.clue_sources
    assert f.fetch.call_count==2
    assert f.last_scanned
    f.cycle()
    assert f.fetch.call_count==2

def test_visible_pound_pc_alert(monkeypatch):
    monkeypatch.setenv("ENABLE_BROWSER","0")
    monkeypatch.setenv("MAX_PAGES_PER_CYCLE","2")
    f=make_finder({"https://www.cclonline.com/":'<div class="product-card"><a href="/offer/">Gaming PC</a><span class="price">£1.00</span></div>',"https://www.cclonline.com/other-item/":"<html></html>"})
    f.cycle()
    assert any(call.args[0]=="visible_price" for call in f.alert_once.call_args_list)

import json
from bs4 import BeautifulSoup
from bot import safe_url, products, CLUE, PC
from vision import image_candidates

def test_same_domain_only():
    assert safe_url("/gaming-pcs/") == "https://www.cclonline.com/gaming-pcs/"
    assert safe_url("https://evil.example/p") is None
    assert safe_url("http://www.cclonline.com/") is None
    assert safe_url("/checkout/") is None

def test_structured_product_one_pound():
    data = {"@context":"https://schema.org","@type":"Product","name":"Gaming PC","url":"/gaming-pcs/offer/","offers":{"@type":"Offer","price":"1.00","priceCurrency":"GBP"}}
    soup = BeautifulSoup('<script type="application/ld+json">'+json.dumps(data)+'</script>',"html.parser")
    assert list(products(soup,"https://www.cclonline.com/")) == [("Gaming PC","https://www.cclonline.com/gaming-pcs/offer/")]

def test_ignore_non_pc_product():
    data = {"@type":"Product","name":"Gaming Mouse","offers":{"price":"1","priceCurrency":"GBP"}}
    soup = BeautifulSoup('<script type="application/ld+json">'+json.dumps(data)+'</script>',"html.parser")
    assert list(products(soup,"https://www.cclonline.com/")) == []

def test_clue_keywords():
    assert CLUE.search("Follow the Mohawk")
    assert CLUE.search("Next clue")
    assert PC.search("Gaming PC")

def test_image_link_context():
    soup=BeautifulSoup('<a href="/pc/"><img src="/images/mohawk.png" alt="Follow the Mohawk"></a>',"html.parser")
    matches=image_candidates(soup,"https://www.cclonline.com/")
    assert matches[0][0]=="https://www.cclonline.com/images/mohawk.png"
    assert matches[0][1]=="https://www.cclonline.com/pc/"

from bs4 import BeautifulSoup
from catalog import sitemap_urls, sitemap_seeds, priority, price_candidates, catalog_links
from bot import safe_url

def test_sitemap_index():
    xml='<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://www.cclonline.com/products.xml</loc></sitemap></sitemapindex>'
    children,pages=sitemap_urls(xml)
    assert children==["https://www.cclonline.com/products.xml"] and not pages

def test_sitemap_urlset():
    xml='<urlset><url><loc>https://www.cclonline.com/gaming-pcs/test/</loc></url></urlset>'
    children,pages=sitemap_urls(xml)
    assert pages==["https://www.cclonline.com/gaming-pcs/test/"] and not children

def test_sitemap_external_blocked():
    assert sitemap_urls('<urlset><url><loc>https://example.com/a</loc></url></urlset>')==([],[])

def test_visible_price_product_card():
    soup=BeautifulSoup('<div class="product-card"><a href="/gaming-pcs/test/">Gaming PC</a><span class="price">£1.00</span></div>',"html.parser")
    result=price_candidates(soup,"https://www.cclonline.com/",safe_url)
    assert result and result[0][1]=="https://www.cclonline.com/gaming-pcs/test/"

def test_ignore_ordinary_price():
    soup=BeautifulSoup('<div class="product-card"><a href="/mice/">Gaming Mouse</a><span class="price">£1.00</span></div>',"html.parser")
    assert not price_candidates(soup,"https://www.cclonline.com/",safe_url)

def test_catalogue_links():
    soup=BeautifulSoup('<a href="/gaming-pcs/">Gaming PCs</a><a href="/account/">Account</a>',"html.parser")
    links=catalog_links(soup,"https://www.cclonline.com/",safe_url)
    assert links==[("https://www.cclonline.com/gaming-pcs/",95)]

def test_not_1_99():
    soup=BeautifulSoup('<div class="product-card"><a href="/gaming-pcs/test/">Gaming PC</a><span class="price">£1.99</span></div>',"html.parser")
    assert not price_candidates(soup,"https://www.cclonline.com/",safe_url)

def test_disguised_one_pound_product():
    from catalog import generic_one_pound_cards
    soup=BeautifulSoup('<div class="product-card"><a href="/mystery-item/">Mystery Item</a><span class="price">£1.00</span></div>',"html.parser")
    assert generic_one_pound_cards(soup,"https://www.cclonline.com/",safe_url)==[("Mystery Item","https://www.cclonline.com/mystery-item/")]

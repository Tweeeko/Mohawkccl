import base64
from intelligence import inspect_html, inspect_javascript, decode_candidates

def test_html_comments_and_hidden():
    html = '<!-- Follow the Mohawk £1 gaming PC -->' + '<div hidden>Mohawk prize PC</div><div data-clue="Follow the Mohawk">x</div>'
    found,_=inspect_html(html,"https://www.cclonline.com/")
    assert {x["source"] for x in found} >= {"html_comment","hidden_dom","data_attribute"}

def test_json_and_script_discovery():
    html = '<script type="application/json">{"clue":"Follow the Mohawk"}</script><script src="/assets/main.js"></script>'
    found,scripts=inspect_html(html,"https://www.cclonline.com/")
    assert any(x["source"]=="embedded_json" for x in found)
    assert scripts==["https://www.cclonline.com/assets/main.js"]

def test_js_decoding():
    clue="Follow the Mohawk £1 gaming PC"
    encoded=base64.b64encode(clue.encode()).decode()
    found=inspect_javascript('const s="'+encoded+'";',"https://www.cclonline.com/main.js")
    assert any("Mohawk" in x["excerpt"] for x in found)
    found=inspect_javascript(r'const s="\u004dohawk £1 gaming PC";',"https://www.cclonline.com/main.js")
    assert any("Mohawk" in x["excerpt"] for x in found)

def test_generic_code_filtered():
    assert not inspect_javascript("webpack sourceMappingURL react hydration","https://www.cclonline.com/main.js")

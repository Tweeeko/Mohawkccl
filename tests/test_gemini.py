from unittest.mock import patch, Mock
from gemini_analysis import Gemini

def test_gemini_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    ai=Gemini()
    assert ai.analyze(url="https://www.cclonline.com/",excerpt="Mohawk") is None

def test_gemini_rejects_external_url(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY","fake-test-key")
    ai=Gemini()
    assert ai.analyze(url="https://example.com/",excerpt="Mohawk") is None
    assert ai.calls == 0

def test_gemini_parses_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY","fake-test-key")
    ai=Gemini()
    mock=Mock()
    mock.status_code=200
    mock.json.return_value={"candidates":[{"content":{"parts":[{"text":'{"relevant":true,"confidence":0.85,"reason":"Promo logo","clue":"Follow the Mohawk","suggested_keywords":["Mohawk"]}'}]}}]}
    with patch("gemini_analysis.requests.post",return_value=mock) as post:
        result=ai.analyze(url="https://www.cclonline.com/",excerpt="Follow the Mohawk")
    assert result["relevant"] and result["confidence"]==0.85
    assert post.call_count==1

def test_gemini_quota_fail_closed(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY","fake-test-key")
    ai=Gemini()
    mock=Mock(status_code=429)
    with patch("gemini_analysis.requests.post",return_value=mock):
        assert ai.analyze(url="https://www.cclonline.com/",excerpt="Mohawk") is None
    assert ai.disabled

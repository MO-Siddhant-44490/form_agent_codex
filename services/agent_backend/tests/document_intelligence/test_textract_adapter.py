"""Textract adapter maps AnalyzeDocument FORMS/TABLES output to ParsedDocument
with real bounding boxes. Uses a canned Textract response (no live call);
live coverage is the gated integration test."""

from agent_backend.document_intelligence.parser import RegionKind
from agent_backend.document_intelligence.textract_adapter import TextractParserAdapter


def _word(id_, text, conf=95.0):
    return {
        "Id": id_,
        "BlockType": "WORD",
        "Text": text,
        "Confidence": conf,
        "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.2, "Height": 0.02}},
    }


def _kv(id_, entity, child_ids, value_id=None):
    block = {
        "Id": id_,
        "BlockType": "KEY_VALUE_SET",
        "EntityTypes": [entity],
        "Confidence": 95.0,
        "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.03}},
        "Relationships": [{"Type": "CHILD", "Ids": child_ids}],
    }
    if value_id:
        block["Relationships"].append({"Type": "VALUE", "Ids": [value_id]})
    return block


class FakeTextractClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def analyze_document(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def test_maps_form_key_values_with_bboxes():
    response = {
        "Blocks": [
            {
                "Id": "p1",
                "BlockType": "PAGE",
                "Page": 1,
                "Geometry": {"BoundingBox": {"Left": 0, "Top": 0, "Width": 1.0, "Height": 1.0}},
            },
            _word("wk1", "Full", 96.0),
            _word("wk2", "Name:", 96.0),
            _word("wv1", "Ada", 94.0),
            _word("wv2", "Lovelace", 94.0),
            _kv("k1", "KEY", ["wk1", "wk2"], value_id="v1"),
            {
                "Id": "v1",
                "BlockType": "KEY_VALUE_SET",
                "EntityTypes": ["VALUE"],
                "Geometry": {
                    "BoundingBox": {"Left": 0.4, "Top": 0.2, "Width": 0.3, "Height": 0.03}
                },
                "Relationships": [{"Type": "CHILD", "Ids": ["wv1", "wv2"]}],
            },
        ]
    }
    client = FakeTextractClient(response)
    adapter = TextractParserAdapter(client=client)
    parsed = adapter.parse("doc-1", b"fake-image-bytes", "image/png")

    assert client.calls[0]["FeatureTypes"] == ["FORMS"]
    assert parsed.parser == "textract"
    regions = parsed.pages[0].regions
    assert len(regions) == 1
    r = regions[0]
    assert r.kind is RegionKind.TEXT_LINE
    assert r.text == "Full Name: Ada Lovelace"
    assert r.confidence > 0.9
    x0, y0, x1, y1 = r.bounding_box
    assert x1 > x0 and y1 > y0  # real geometry, not a placeholder


def test_unsupported_mime_rejected():
    import pytest
    from agent_backend.document_intelligence.parser import ParserFailure

    with pytest.raises(ParserFailure, match="unsupported_type"):
        TextractParserAdapter(client=FakeTextractClient({})).parse("d", b"x", "text/plain")


def test_access_denied_classified_as_unavailable():
    import pytest
    from agent_backend.document_intelligence.parser import ParserFailure

    class DenyingClient:
        def analyze_document(self, **kwargs):
            raise type("AccessDeniedException", (Exception,), {})("no perms")

    with pytest.raises(ParserFailure) as exc:
        TextractParserAdapter(client=DenyingClient()).parse("d", b"x", "image/png")
    assert exc.value.reason == "parser_unavailable"

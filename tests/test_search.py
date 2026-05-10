"""Unit tests for the query parser + FTS match builder."""

from app.search import parse_query, to_fts_match


def test_parse_query_no_pack():
    assert parse_query("hello world") == (None, "hello world")


def test_parse_query_pack_only():
    assert parse_query("pack:airhorn") == ("airhorn", "")


def test_parse_query_pack_with_text():
    assert parse_query("pack:airhorn boom") == ("airhorn", "boom")


def test_parse_query_pack_anywhere():
    assert parse_query("loud pack:airhorn boom") == ("airhorn", "loud boom")


def test_parse_query_case_insensitive_pack():
    assert parse_query("PACK:Airhorn boom") == ("airhorn", "boom")


def test_parse_query_empty():
    assert parse_query("") == (None, "")
    assert parse_query("   ") == (None, "")


def test_parse_query_ignores_pack_with_no_slug():
    # bare "pack:" is just a stray colon, leave it as text
    assert parse_query("pack: hello") == (None, "pack: hello")


def test_to_fts_match_prefix_per_word():
    assert to_fts_match("alpha beta") == "alpha* beta*"


def test_to_fts_match_lowercases():
    assert to_fts_match("BRUH") == "bruh*"


def test_to_fts_match_strips_punctuation():
    assert to_fts_match("hello, world!") == "hello* world*"


def test_to_fts_match_empty_when_no_tokens():
    assert to_fts_match("") == ""
    assert to_fts_match("!!!") == ""

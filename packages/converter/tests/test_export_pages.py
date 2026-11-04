"""Page rules for `htrflow-campaigns export`: sets, file numbers, quality mean."""

import pytest

from htrflow_converter.export_pages import (
    PageRuleError,
    info_url,
    page_number,
    parse_page_set,
    quality_mean,
)

RA = "https://lbiiif.riksarkivet.se/v3/arkis!R0001203_00001/full/2500,/0/default.jpg"


def test_page_sets():
    assert parse_page_set("1") == {1}
    assert parse_page_set("1,3-4") == {1, 3, 4}
    assert parse_page_set("none") == frozenset()
    for bad in ("0", "a", "4-2", "", "1,,2"):
        with pytest.raises(PageRuleError):
            parse_page_set(bad)


def test_number_comes_from_the_source_image_id():
    assert page_number(RA, "R0001203") == 1
    assert page_number(RA.replace("_00001", "_00417"), "R0001203") == 417


def test_a_source_that_is_not_this_volume_is_refused():
    for url in (
        RA.replace("R0001203_", "R0009999_"),  # another volume
        RA.replace("_00001", "_0001"),  # four digits
        "https://example.org/scan.jpg",  # no image id
    ):
        with pytest.raises(PageRuleError):
            page_number(url, "R0001203")


def test_info_url_is_the_image_service_base():
    assert (
        info_url(RA)
        == "https://lbiiif.riksarkivet.se/v3/arkis!R0001203_00001/info.json"
    )
    with pytest.raises(PageRuleError):
        info_url("https://example.org/scan.jpg")


def test_quality_mean_leaves_out_zero_unscored_and_emptied_pages():
    results = {
        "0001": {"status": "ok", "quality": 0.2},  # emptied below
        "0002": {"status": "ok", "quality": 0.9},
        "0003": {"status": "ok", "quality": 0.0},  # QP 0
        "0004": {"status": "failed"},  # unscored
        "0005": {"status": "skipped", "quality": 0.7},
    }
    assert quality_mean(results, {"0001"}) == (pytest.approx(0.8), 2)
    assert quality_mean({"0001": {"status": "failed"}}, set()) == (None, 0)

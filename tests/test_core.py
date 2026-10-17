"""Pure helpers of the video pipeline: URL safety (SSRF), brand tags, output formats, text-quality checks."""
import pytest

from core import brand, fetch, formats, smart


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/x", "http://localhost/x", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/video.mp4", "http://192.168.1.1/", "file:///etc/passwd", "ftp://example.com/a.mp4",
])
def test_internal_and_non_http_urls_are_rejected(url):
    assert fetch.check_url(url) != ""


def test_hashtags_are_normalised_and_deduplicated():
    assert brand.clean_tags("деньги, финансы #инвестиции") == "#деньги #финансы #инвестиции"
    assert brand.clean_tags("ab AB #ab") == "#ab"


def test_tags_are_appended_to_description():
    assert brand.with_tags("Text", {"tags": "#a #b"}) == "Text\n\n#a #b"
    assert brand.with_tags("Text", None) == "Text"


def test_all_output_formats_have_even_dimensions():
    for fmt in formats.FORMATS:
        w, h = formats.size(fmt)
        assert w % 2 == 0 and h % 2 == 0, fmt


def test_fit_filter_uses_blur_for_widescreen_even_if_crop_requested():
    assert "boxblur" in formats.fit_filter("16:9", "crop")
    assert "crop=" in formats.fit_filter("4:5", "crop") and "boxblur" not in formats.fit_filter("4:5", "crop")


def test_cliche_detector_flags_template_phrases_and_passes_plain_text():
    bad = [{"text": "В современном мире всё меняется, и это не просто новость, а настоящая революция."}]
    good = [{"text": "Дорогу в Риме мостили камнем, а между плитами лили свинец."}]
    assert smart.cliche_hits(bad)
    assert smart.cliche_hits(good) == []

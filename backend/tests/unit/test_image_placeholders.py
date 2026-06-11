from __future__ import annotations

from pathlib import Path

from prototype.v2.image_placeholders import extract_screen_image_slots, has_screen_placeholder


def test_extract_slots_from_fss_raw() -> None:
    raw = Path(__file__).resolve().parents[2] / (
        "data/artifacts_final/004-금감원/work/금감원제안요청서_24년.raw.html"
    )
    if not raw.is_file():
        return
    slots = extract_screen_image_slots(raw)
    assert len(slots) >= 5
    assert any(len(s[0]) >= 1 for s in slots)
    assert all(
        p.suffix.lower() in (".png", ".jpg", ".jpeg") for s in slots for p in s[0]
    )
    # FUN-001: 본문 스크린샷만(별표 img 제외)
    first, _fn = slots[0]
    assert len(first) == 1
    assert "98" in first[0].name


def test_tiny_image_filtered() -> None:
    from prototype.v2.image_placeholders import _is_content_image

    repo = Path(__file__).resolve().parents[2]
    tiny = repo / "data/artifacts_final/004-금감원/work/금감원제안요청서_24년_images/imageFile99.png"
    big = repo / "data/artifacts_final/004-금감원/work/금감원제안요청서_24년_images/imageFile98.png"
    if tiny.is_file():
        assert not _is_content_image(tiny)
    if big.is_file():
        assert _is_content_image(big)


def test_breadcrumb_footnote_structural() -> None:
    from prototype.v2.image_placeholders import _is_breadcrumb_footnote

    assert _is_breadcrumb_footnote("* 시장감시 – 조사착수 전 – 매매자료 요청")
    assert _is_breadcrumb_footnote("* ①화면A, ②화면B")
    assert not _is_breadcrumb_footnote("* 동 사업에서 구축 예정인 모니터링시스템 활용")
    assert not _is_breadcrumb_footnote("* 원화마켓 업체 38개")


def test_dash_placeholder_detect() -> None:
    from prototype.v2.image_placeholders import (
        has_dash_placeholder,
        strip_dash_placeholder,
    )

    assert has_dash_placeholder("◦ 본문 – –")
    assert not has_dash_placeholder("◦ 본문 ▪ 산출물")
    assert strip_dash_placeholder("◦ 본문 – –") == "◦ 본문"


def test_extract_inline_figure_slots_fss() -> None:
    from prototype.v2.image_placeholders import extract_inline_figure_slots

    raw = Path(__file__).resolve().parents[2] / (
        "data/artifacts_final/004-금감원/work/금감원제안요청서_24년.raw.html"
    )
    if not raw.is_file():
        return
    slots = extract_inline_figure_slots(raw)
    assert len(slots) >= 5
    assert any("105" in str(p) for imgs, _ in slots for p in imgs)


def test_has_placeholder_escaped() -> None:
    assert not has_screen_placeholder("plain text")
    assert not has_screen_placeholder("< 구축 예정 국내·외 가상자산시장 모니터링 기능 및 화면(안) >")
    assert has_screen_placeholder("◦ 내용 &lt; 관련 화면(안) &gt;")
    assert has_screen_placeholder("< 관련 화면(안) >")
    assert has_screen_placeholder("< 자료 입력 관련 화면(안) >")

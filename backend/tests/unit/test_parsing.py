from __future__ import annotations

from app.phase1.extraction.parsing import normalize_line_breaks, split_by_markers


def test_circled_markers_on_separate_lines() -> None:
    atoms = split_by_markers("①첫번째 요건\n②두번째 요건\n③세번째")
    assert [(a.marker, a.text) for a in atoms] == [
        ("①", "첫번째 요건"),
        ("②", "두번째 요건"),
        ("③", "세번째"),
    ]


def test_continuation_lines_are_attached_to_previous_atom() -> None:
    atoms = split_by_markers("① 첫번째\n  부가 설명\n② 두번째")
    assert len(atoms) == 2
    assert atoms[0].marker == "①"
    assert "부가 설명" in atoms[0].text
    assert atoms[1].marker == "②"


def test_paren_digit_markers() -> None:
    atoms = split_by_markers("(1) A\n(2) B")
    assert [a.marker for a in atoms] == ["(1)", "(2)"]


def test_korean_letter_with_sub_bullets_stays_one_atom() -> None:
    """가. 아래 •·- 는 상위 항목 본문으로 묶는다."""
    atoms = split_by_markers("가. 첫\n• 둘째\n- 셋째")
    assert len(atoms) == 1
    assert atoms[0].marker == "가."
    assert "첫" in atoms[0].text
    assert "• 둘째" in atoms[0].text
    assert "- 셋째" in atoms[0].text


def test_circled_section_keeps_sub_bullets_together() -> None:
    """① 제목 아래 • 불릿은 조견표 한 줄로 유지."""
    cell = (
        "①원천 시스템 연계\n"
        "• 연계 방식은 각 시스템의 요구사항을 반영하되, 향후 수집 시스템 확대를 감안하여 "
        "다양한 인터페이스를 지원해야 합니다.\n"
        "• 다양한 원천 데이터 소스에 대한 연결 및 수집 관리 기능을 제공해야 합니다.\n"
        "②지원 파일 형식"
    )
    atoms = split_by_markers(cell)
    assert len(atoms) == 2
    assert atoms[0].marker == "①"
    assert atoms[0].text.startswith("원천 시스템 연계")
    assert "• 연계 방식은" in atoms[0].text
    assert "• 다양한 원천" in atoms[0].text
    assert atoms[1].marker == "②"
    assert atoms[1].text == "지원 파일 형식"


def test_bullet_only_list_splits_on_bullets() -> None:
    """상위 마커 없이 • 만 있으면 항목별로 분해."""
    atoms = split_by_markers("• 첫\n• 둘\n• 셋")
    assert len(atoms) == 3
    assert [a.marker for a in atoms] == ["•", "•", "•"]


def test_no_markers_yields_single_atom() -> None:
    atoms = split_by_markers("단일 라인입니다")
    assert len(atoms) == 1
    assert atoms[0].marker is None
    assert atoms[0].text == "단일 라인입니다"


def test_normalize_pdf_midword_line_breaks() -> None:
    raw = (
        "원천 시스템 연계\n"
        "• 연계 방식은 각 시스템의 요구사항을 반영하되, 향후 수집 시스템 확\n"
        "대를 감안하여 다양한 인터페이스를 지원해야 합니다.\n"
        "• 다양한 원천 데이터 소스에 대한 연결 및 수집 관리 기능을 제공해야\n"
        "합니다."
    )
    out = normalize_line_breaks(raw)
    assert "확\n대를" not in out
    assert "확대를" in out
    assert "제공해야\n합니다" not in out
    assert "제공해야 합니다" in out
    assert out.startswith("원천 시스템 연계")


def test_empty_input() -> None:
    assert split_by_markers("") == []
    assert split_by_markers("   \n\n   ") == []

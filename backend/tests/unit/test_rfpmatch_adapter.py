from __future__ import annotations

from prototype.rfpmatch import adapter


def test_section_tables_to_v2_reqs_maps_fields() -> None:
    section_tables = {
        "1장": [
            {
                "요구사항 ID": "보안_001",
                "항목명": "보안",
                "요구사항": "망분리",
                "상세요건": "물리적 망분리 적용",
                "추가정보": "",
                "페이지": 3,
                "Part": "Part1",
                "Section": "1장",
                "생성 출처": "표 / 룰 기반",
            }
        ]
    }
    reqs = adapter._section_tables_to_v2_reqs(section_tables, "문서명")
    assert len(reqs) == 1
    req = reqs[0]
    assert req.doc == "문서명"
    assert req.rid == "보안_001"
    assert req.top == "보안"
    assert req.mid == "망분리"
    assert req.detail == "물리적 망분리 적용"
    assert req.page == 3
    assert req.tab == "1장"
    assert req.section_path == "Part1 > 1장"
    assert req.source == "표 / 룰 기반"
    assert req.levels == ["보안", "망분리"]


def test_section_tables_to_v2_reqs_handles_non_int_page() -> None:
    section_tables = {
        "섹션": [
            {"요구사항 ID": "x_001", "항목명": "a", "요구사항": "b", "상세요건": "c", "페이지": ""}
        ]
    }
    reqs = adapter._section_tables_to_v2_reqs(section_tables, "문서")
    assert reqs[0].page is None
    assert reqs[0].table_id == -1

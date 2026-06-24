from __future__ import annotations

from prototype.v2.extract import Req
from prototype.v2.row_filter import filter_noise_rows, is_noise_row


def test_hw_spec_row_dropped() -> None:
    r = Req(
        doc="t", table_id=1, page=5, tab="프로젝트 범위",
        top="", mid="", detail="□K8S Worker CPU 6530P 2socket MEM 512GB",
        section_path="1.4.2 현황",
    )
    assert is_noise_row(r)


def test_overview_section_dropped() -> None:
    r = Req(
        doc="t", table_id=1, page=1, tab="제안 요청 개요",
        detail="▪ 사업명: 생성형 AI 플랫폼",
        section_path="1. 제안 요청 개요",
    )
    assert is_noise_row(r)


def test_real_requirement_kept() -> None:
    r = Req(
        doc="t", table_id=1, page=5, tab="프로젝트 범위",
        top="인프라", mid="클러스터",
        detail="- 쿠버네티스 클러스터는 고가용성으로 구축해야 함",
    )
    assert not is_noise_row(r)


def test_filter_noise_rows() -> None:
    reqs = [
        Req(doc="t", table_id=1, page=1, tab="T", detail="□K8S Worker CPU 6530P"),
        Req(doc="t", table_id=1, page=1, tab="T", detail="- 시스템은 99.9% 가용성을 제공해야 함"),
    ]
    kept, n = filter_noise_rows(reqs)
    assert n == 1
    assert len(kept) == 1
    assert "가용성" in kept[0].detail

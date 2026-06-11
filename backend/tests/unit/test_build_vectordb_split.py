from __future__ import annotations

from app.preprocessing.build_vectordb import split_text_into_chunks


def test_recursive_split_prefers_paragraph_boundary() -> None:
    section_a = "A" * 400
    section_b = "B" * 400
    text = f"{section_a}\n\n{section_b}"

    chunks = split_text_into_chunks(text, "demo.txt", chunk_size=500, chunk_overlap=50)
    texts = [chunk.text for chunk in chunks]

    assert len(texts) >= 2
    assert all(len(chunk.text) <= 500 for chunk in chunks)
    assert any(chunk.text.startswith(section_a[:40]) for chunk in chunks)
    assert any("BBBB" in chunk.text for chunk in chunks)


def test_recursive_split_keeps_overlap() -> None:
    text = "가" * 1200
    chunks = split_text_into_chunks(text, "demo.txt", chunk_size=500, chunk_overlap=100)

    assert len(chunks) >= 2
    for left, right in zip(chunks, chunks[1:], strict=False):
        max_overlap = min(100, len(left.text), len(right.text))
        assert any(left.text[-i:] == right.text[:i] for i in range(1, max_overlap + 1))


def test_recursive_split_does_not_cut_mid_word_when_space_exists() -> None:
    words = ["기술"] * 120
    text = " ".join(words)
    chunks = split_text_into_chunks(text, "demo.txt", chunk_size=300, chunk_overlap=40)

    assert chunks
    for chunk in chunks:
        assert "기술기술" not in chunk.text

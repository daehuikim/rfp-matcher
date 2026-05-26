from __future__ import annotations

from app.phase1.converters.base import count_html_features


def test_count_html_features_counts_td_and_p() -> None:
    html = """
    <html><body>
      <p>한 단락</p>
      <p>두번째</p>
      <table>
        <tr><td>a</td><td>b</td></tr>
        <tr><th>h</th><td>c</td></tr>
      </table>
    </body></html>
    """
    cells, paragraphs = count_html_features(html)
    assert cells == 4
    assert paragraphs == 2


def test_count_html_features_handles_empty() -> None:
    cells, paragraphs = count_html_features("<html></html>")
    assert cells == 0
    assert paragraphs == 0

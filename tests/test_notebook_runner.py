import pytest

from rag_eval.notebook_runner import load_case_ids_file


def test_load_case_ids_file_preserves_order(tmp_path):
    path = tmp_path / 'case-ids.txt'
    path.write_text('multihop_rag:9\nmultihop_rag:2\n', encoding='utf-8')

    assert load_case_ids_file(path) == ['multihop_rag:9', 'multihop_rag:2']


@pytest.mark.parametrize('contents', [
    '',
    ' multihop_rag:1\n',
    'multihop_rag:1\nmultihop_rag:1\n',
    'multihop_rag:1\n\nmultihop_rag:2\n',
    'multihop_rag:1\nmultihop_rag: 2\n',
])
def test_load_case_ids_file_rejects_duplicate_blank_or_whitespace_ids(tmp_path, contents):
    path = tmp_path / 'case-ids.txt'
    path.write_text(contents, encoding='utf-8')

    with pytest.raises(ValueError, match='case ID'):
        load_case_ids_file(path)

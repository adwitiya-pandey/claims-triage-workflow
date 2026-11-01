from pathlib import Path


def test_readme_tree_documents_retrieval_files():
    readme = Path("README.md").read_text(encoding="utf-8")
    tree = readme.split("## 2. Repository Structure\n", 1)[1].split("\n## 3.", 1)[0]

    for path in (
        "evals/retrieval_eval.py",
        "evals/retrieval_gold.jsonl",
        "scripts/build_index.py",
        "src/triage/embeddings.py",
        "src/triage/vectorstore.py",
        "tests/test_dense_retrieval.py",
        "tests/test_embeddings.py",
        "tests/test_readme_tree.py",
        "tests/test_retrieval_eval.py",
        "tests/test_retrieval_node.py",
        "tests/test_vectorstore.py",
    ):
        assert Path(path).name in tree

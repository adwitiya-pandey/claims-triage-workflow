"""Verify that every path documented in the README tree exists."""

import re
from pathlib import Path


def test_readme_repository_tree_paths_exist():
    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    section = readme.split("## 2. Repository Structure", maxsplit=1)[1]
    tree_block = re.search(r"```text\n(.*?)\n```", section, flags=re.DOTALL)
    assert tree_block is not None, "Repository structure tree is missing"

    entries = []
    parents = []
    for line in tree_block.group(1).splitlines():
        match = re.match(r"^((?:│   |    )*)(?:├──|└──) (.+)$", line)
        if match:
            depth = len(match.group(1)) // 4
            name = match.group(2).split("#", maxsplit=1)[0].strip()
            parents = parents[:depth]
            path = "/".join([*parents, name.rstrip("/")])
            entries.append(path)
            if name.endswith("/"):
                parents.append(name.rstrip("/"))

    assert entries, "Repository structure tree contains no paths"
    missing = [path for path in entries if not (root / path).exists()]
    assert not missing, f"README tree paths do not exist: {missing}"


def test_readme_tree_includes_retrieval_files():
    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    tree = readme.split("## 2. Repository Structure", maxsplit=1)[1]
    for filename in (
        "retrieval_eval.py",
        "retrieval_gold.jsonl",
        "build_index.py",
        "embeddings.py",
        "vectorstore.py",
        "test_dense_retrieval.py",
        "test_embeddings.py",
        "test_retrieval_eval.py",
        "test_retrieval_node.py",
        "test_vectorstore.py",
    ):
        assert filename in tree

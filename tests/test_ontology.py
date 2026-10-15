import subprocess
import sys
from pathlib import Path

import pytest

from app.graph.ontology import canonical_key, get_ontology

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text, key", [
    ("PyTorch", "pytorch"),
    ("  pytorch ", "pytorch"),
    ("Py-Torch", "py torch"),
    ("C++", "c++"),
    ("C#", "c#"),
    ("Node.JS", "node.js"),
    ("Kubernetes (k8s)", "kubernetes k8s"),
    ("scikit_learn", "scikit learn"),
    ("Ｐｙｔｈｏｎ", "python"),            # full-width characters (NFKC)
])
def test_canonical_key(text, key):
    assert canonical_key(text) == key


@pytest.mark.parametrize("text, canonical", [
    ("PyTorch", "PyTorch"),
    ("torch", "PyTorch"),
    ("Py-Torch", "PyTorch"),
    ("k8s", "Kubernetes"),
    ("Kubernetes (k8s)", "Kubernetes"),
    ("sklearn", "scikit-learn"),
    ("Pinecone", "Vector Databases"),
    ("RAG", "Retrieval-Augmented Generation"),
    ("Java", "Java"),
    ("JavaScript", "JavaScript"),
])
def test_resolve_exact_and_alias(text, canonical):
    assert get_ontology().resolve(text).name == canonical


@pytest.mark.parametrize("text", ["Rust", "Jav", "Pytorchh", "", None, "Basket weaving"])
def test_unknown_terms_do_not_resolve(text):
    # no fuzzy matching: near-misses stay unresolved
    assert get_ontology().resolve(text) is None


def test_domains():
    o = get_ontology()
    assert o.resolve_domain("NLP") == "nlp"
    assert o.resolve_domain("ML Infrastructure") == "ml_infrastructure"
    assert o.resolve_domain("null") is None
    assert o.resolve_domain(None) is None


def test_ontology_integrity():
    # load_ontology validates: unique keys, alias collisions, known prereqs/domains, no cycles
    o = get_ontology()
    assert len(o.skills) >= 50
    for p in o.prerequisites:
        assert o.resolve(p.source) and o.resolve(p.target)


def test_seed_file_is_generated_from_the_ontology():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_seed.py"), "--check"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

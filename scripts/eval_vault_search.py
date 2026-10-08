"""Compare vault search modes (words / meaning / hybrid) on real questions.

Usage (inside the container):
    docker compose exec -T copilot /opt/venv/bin/python scripts/eval_vault_search.py \
        scripts/vault_eval.local.yaml

The questions file lists each question and the note paths that answer it.
Reports Hit@3, Hit@5, MRR and the average size of what an assistant would
receive per search (a proxy for tokens).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from executive_copilot.config import load_config
from executive_copilot.logging import configure_stdio_safe_logging
from executive_copilot.services.vault_index import build_embedder, build_index

LIMIT = 8


def score(results: list[dict[str, Any]], expected: list[str]) -> tuple[int | None, int]:
    """(1-based rank of the first expected note or None, payload chars)."""
    notes: list[str] = []
    for r in results:
        if r["note"] not in notes:
            notes.append(r["note"])
    rank = next((i + 1 for i, n in enumerate(notes) if n in expected), None)
    return rank, len(json.dumps(results, ensure_ascii=False))


async def main(questions: list[dict[str, Any]]) -> None:
    configure_stdio_safe_logging("WARNING")
    config = load_config()
    index = build_index(config)
    index.refresh(force=True)
    embedder = build_embedder(config)
    if embedder is None:
        sys.exit("No OpenAI key stored: semantic search cannot be evaluated")
    t = time.time()
    embedded = await index.embed_missing(embedder)
    print(f"Embedded {embedded} sections in {time.time() - t:.1f}s ({embedder.tokens_used} tokens)\n")

    modes = {
        "palabras": lambda q: asyncio.sleep(0, index.search(q, limit=LIMIT)),
        "significado": lambda q: index.semantic_search(q, embedder, limit=LIMIT),
        "híbrida": lambda q: index.hybrid_search(q, embedder, limit=LIMIT),
    }
    table: dict[str, list[tuple[int | None, int, float]]] = {m: [] for m in modes}
    for item in questions:
        row = []
        for mode, run in modes.items():
            start = time.time()
            results = await run(item["q"])
            rank, size = score(results, item["expect"])
            table[mode].append((rank, size, time.time() - start))
            row.append(f"{mode}={'·' if rank is None else rank}")
        print(f"- {item['q'][:70]:<72} {'  '.join(row)}")

    print("\nModo         Hit@3   Hit@5   MRR    chars/búsqueda  ms/búsqueda")
    n = len(questions)
    for mode, rows in table.items():
        hit3 = sum(1 for r, _, _ in rows if r and r <= 3) / n
        hit5 = sum(1 for r, _, _ in rows if r and r <= 5) / n
        mrr = sum(1 / r for r, _, _ in rows if r) / n
        chars = sum(s for _, s, _ in rows) / n
        ms = sum(t for _, _, t in rows) / n * 1000
        print(f"{mode:<12} {hit3:>5.0%}  {hit5:>5.0%}  {mrr:>5.2f}  {chars:>10.0f}      {ms:>7.0f}")
    print(f"\nTokens de embeddings usados en total: {embedder.tokens_used}")


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "scripts/vault_eval.local.yaml"
    asyncio.run(main(yaml.safe_load(Path(source).read_text())["questions"]))

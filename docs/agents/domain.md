# Domain Docs

## Before exploring, read these

- `CONTEXT.md` at the repository root.
- If `CONTEXT-MAP.md` exists, read the relevant context documents it references.
- Read applicable decisions under `docs/adr/`.

If these files do not exist, proceed silently. Domain-modeling workflows create them lazily when terminology or architectural decisions are resolved.

## Layout

This repository uses a single-context layout:

/
├── CONTEXT.md
├── docs/adr/
└── src/

## Vocabulary

Use domain terms as defined in `CONTEXT.md`. Avoid synonyms that the glossary explicitly rejects. If a required concept is missing, record it as a possible domain-modeling gap.

## ADR conflicts

If proposed work contradicts an existing ADR, identify the conflict explicitly instead of silently overriding the decision.

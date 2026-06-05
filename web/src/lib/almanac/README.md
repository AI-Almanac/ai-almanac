# Almanac Content

Edit `content.ts` to add or revise the static Almanac of Weather Models entries.

Each item needs a stable `slug` because route URLs are built from it:

- `/almanac/models/{slug}`
- `/almanac/datasets/{slug}`
- `/almanac/architectures/{slug}`
- `/almanac/glossary`

Keep user-facing names domain-oriented. Avoid exposing implementation names unless the entry is about a true integration boundary.

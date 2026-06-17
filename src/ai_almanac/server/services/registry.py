"""Request-scoped async reads of the catalog: regions and registered models.

Replaces the synchronous registry shims that previously lived in
`ai_almanac.settings`, which opened a fresh blocking DB connection per query
from inside async handlers. Handlers load a `CatalogSnapshot` once per
request and pass it to pure helpers.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_almanac.server.services import data_sources, region_catalog


def _registry_entry(source: dict) -> dict:
    # Spread metadata first: the seeder copies the whole YAML entry into
    # metadata (including its slug `id`), so the canonical data_source id and
    # the other fields below must win. Otherwise model_name reaching job
    # submission is the slug, not the row id, and get_source() can't find it.
    return {
        **source["metadata"],
        "id": source["id"],
        "display_name": source["name"],
        "region": source.get("region") or "",
        "model_dir": source["path"],
    }


async def load_model_registry(region: str | None = None) -> list[dict]:
    """Ready model sources in the shape ROMP job assembly expects."""
    sources = await data_sources.get_model_sources(region)
    entries = [_registry_entry(s) for s in sources if s.get("status") == "ready"]
    entries.sort(key=lambda entry: (entry["region"], entry["display_name"]))
    return entries


@dataclass(frozen=True)
class CatalogSnapshot:
    regions: tuple[dict, ...]
    models: tuple[dict, ...]

    def region(self, region_id: str | None) -> dict | None:
        if not region_id:
            return None
        wanted = region_id.strip().lower()
        for region in self.regions:
            if region["id"].lower() == wanted:
                return region
        return None

    def region_by_romp_name(self, romp_name: str | None) -> dict | None:
        if not romp_name:
            return None
        wanted = romp_name.lower()
        if wanted == "custom":
            return None
        for region in self.regions:
            if (region.get("romp_name") or "custom").lower() == wanted:
                return region
        return None

    def models_for_region(self, region_id: str | None) -> list[dict]:
        if not region_id:
            return list(self.models)
        wanted = region_id.lower()
        return [
            model
            for model in self.models
            if (model.get("region") or "").lower() == wanted
        ]


async def load_catalog() -> CatalogSnapshot:
    return CatalogSnapshot(
        regions=tuple(await region_catalog.list_regions()),
        models=tuple(await load_model_registry()),
    )

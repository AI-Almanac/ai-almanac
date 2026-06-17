from ai_almanac.server.services.registry import _registry_entry


def test_registry_entry_canonical_id_wins_over_metadata_slug() -> None:
    # The seeder copies the whole YAML entry into metadata, including its slug
    # `id`. The registry must expose the data_source row id so model_name
    # reaching job submission resolves via get_source(); the slug stays as
    # yaml_id for UI links.
    source = {
        "id": "db956a33-e511-4ac7-8484-ac6b7fc3e877",
        "name": "AIFS",
        "path": "/data/aifs",
        "region": "ethiopia",
        "metadata": {"id": "aifs", "yaml_id": "aifs", "model_var": "tp"},
    }

    entry = _registry_entry(source)

    assert entry["id"] == "db956a33-e511-4ac7-8484-ac6b7fc3e877"
    assert entry["display_name"] == "AIFS"
    assert entry["model_dir"] == "/data/aifs"
    assert entry["yaml_id"] == "aifs"
    assert entry["model_var"] == "tp"

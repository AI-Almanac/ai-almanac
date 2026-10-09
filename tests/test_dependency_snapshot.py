from scripts.dependency_snapshot import resolved


def test_snapshot_covers_python_packages_from_pypi_and_conda_only() -> None:
    packages = [
        {"name": "pyjwt", "version": "2.15.1", "kind": "pypi", "requested_spec": '">=2.14.0"'},
        {"name": "anyio", "version": "4.15.1", "kind": "pypi", "requested_spec": None},
        {"name": "rasterio", "version": "1.5.0", "kind": "conda", "depends": ["python >=3.14"]},
        {"name": "openssl", "version": "3.5.0", "kind": "conda", "depends": ["libgcc >=14"]},
    ]

    assert resolved(packages) == {
        "pyjwt": {"package_url": "pkg:pypi/pyjwt@2.15.1", "relationship": "direct"},
        "anyio": {"package_url": "pkg:pypi/anyio@4.15.1", "relationship": "indirect"},
        "rasterio": {"package_url": "pkg:pypi/rasterio@1.5.0", "relationship": "indirect"},
    }

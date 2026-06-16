"""GCSStorage — key/path construction and bucket routing against a fake client.

The fake mimics the slice of the google-cloud-storage API that GCSStorage uses,
so these run without the real dependency or network. The gcsfs/xarray methods
(`open_nc_dataset`, `*_nc_output_files`) are thin library delegations and are
left to integration testing.
"""

from __future__ import annotations

import pytest

from ai_almanac.server.services.storage import GCSStorage

_PNG = b"\x89PNG\r\n\x1a\n" + b"rest-of-the-png"


class _FakeBlob:
    def __init__(self, bucket: _FakeBucket, name: str) -> None:
        self._bucket = bucket
        self.name = name

    @property
    def content_type(self):
        entry = self._bucket.store.get(self.name)
        return entry[1] if entry else None

    def exists(self) -> bool:
        return self.name in self._bucket.store

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._bucket.store[self.name] = (data, content_type)

    def download_as_bytes(self) -> bytes:
        return self._bucket.store[self.name][0]

    def download_as_text(self) -> str:
        return self._bucket.store[self.name][0].decode()

    def delete(self) -> None:
        if self.name not in self._bucket.store:
            raise FileNotFoundError(self.name)
        del self._bucket.store[self.name]

    def generate_signed_url(self, **kwargs) -> str:
        return f"https://signed/{self._bucket.name}/{self.name}?method={kwargs['method']}"


class _FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.store: dict[str, tuple[bytes, str | None]] = {}

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)


class _FakeClient:
    def __init__(self) -> None:
        self.buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self.buckets.setdefault(name, _FakeBucket(name))

    def list_blobs(self, bucket_name: str, prefix: str = ""):
        bucket = self.bucket(bucket_name)
        return [_FakeBlob(bucket, n) for n in bucket.store if n.startswith(prefix)]


@pytest.fixture
def store() -> GCSStorage:
    return GCSStorage(
        uploads_bucket="up",
        outputs_bucket="out",
        data_bucket="data",
        client=_FakeClient(),
    )


def test_is_not_local(store: GCSStorage) -> None:
    assert store.is_local is False


def test_job_output_uri_points_at_outputs_bucket(store: GCSStorage) -> None:
    assert store.job_output_uri("job1") == (
        "gs://out/job1/output",
        "gs://out/job1/figure",
    )


def test_resolve_obs_path(store: GCSStorage) -> None:
    # gs:// and absolute paths pass through unchanged
    assert store.resolve_obs_path("gs://b/x/y.nc") == "gs://b/x/y.nc"
    assert store.resolve_obs_path("/mnt/data/x.nc") == "/mnt/data/x.nc"
    # relative upload key resolves to the parent prefix in the uploads bucket
    assert store.resolve_obs_path("user/ds/file.nc") == "gs://up/user/ds"


def test_signed_urls_route_to_the_right_bucket(store: GCSStorage) -> None:
    upload = store.generate_upload_url("user/ds/f.nc", "https://api")
    assert upload == "https://signed/up/user/ds/f.nc?method=PUT"
    result = store.generate_result_url("job1", "figure", "p.png")
    assert result == "https://signed/out/job1/figure/p.png?method=GET"


def test_result_file_path_is_none_so_routers_redirect(store: GCSStorage) -> None:
    assert store.result_file_path("job1", "output", "m.nc") is None


def test_confirm_upload(store: GCSStorage) -> None:
    assert store.confirm_upload("user/ds/f.nc") is False
    store._bucket("up").blob("user/ds/f.nc").upload_from_string(b"x")
    assert store.confirm_upload("user/ds/f.nc") is True


def test_list_result_files_parses_kind_and_filename(store: GCSStorage) -> None:
    out = store._bucket("out")
    out.blob("job1/output/spatial_metrics_a.nc").upload_from_string(b"1")
    out.blob("job1/figure/plot.png").upload_from_string(b"2")
    out.blob("other/output/ignored.nc").upload_from_string(b"3")
    assert store.list_result_files("job1") == [
        ("output", "spatial_metrics_a.nc"),
        ("figure", "plot.png"),
    ]


def test_chat_figure_roundtrip_and_delete(store: GCSStorage) -> None:
    store.save_chat_figure("fig1", _PNG)
    # stored under the detected extension in the outputs bucket
    assert "chat-figures/fig1.png" in store._bucket("out").store
    assert store.chat_figure_local_path("fig1") is None  # forces the read path

    figure = store.read_chat_figure("fig1")
    assert figure is not None
    data, media_type = figure
    assert data == _PNG and media_type == "image/png"

    redirect = store.chat_figure_redirect_url("fig1")
    assert redirect == "https://signed/out/chat-figures/fig1.png?method=GET"

    store.delete_chat_figure("fig1")
    assert store.read_chat_figure("fig1") is None


def test_read_log(store: GCSStorage) -> None:
    assert store.read_log("job1") == ""
    store._bucket("out").blob("job1/run.log").upload_from_string(b"line one\n")
    assert store.read_log("job1") == "line one\n"


def test_local_workspace_methods_are_unavailable(store: GCSStorage) -> None:
    with pytest.raises(NotImplementedError):
        store.job_dir("job1")
    with pytest.raises(NotImplementedError):
        store.log_path("job1")

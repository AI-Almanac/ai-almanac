"""GCSStorage — key/path construction and bucket routing against a fake client.

The fake mimics the slice of the google-cloud-storage API that GCSStorage uses,
so these run without the real dependency or network. The gcsfs/xarray methods
(`open_nc_dataset`, `*_nc_output_files`) are thin library delegations and are
left to integration testing.
"""

from __future__ import annotations

import pytest
from google.auth import credentials as ga_credentials

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

    @property
    def size(self) -> int | None:
        entry = self._bucket.store.get(self.name)
        return len(entry[0]) if entry else None

    @property
    def md5_hash(self) -> str | None:
        import base64
        import hashlib

        entry = self._bucket.store.get(self.name)
        return base64.b64encode(hashlib.md5(entry[0]).digest()).decode() if entry else None

    def reload(self) -> None:
        pass

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._bucket.store[self.name] = (data, content_type)

    def download_as_bytes(self) -> bytes:
        return self._bucket.store[self.name][0]

    def open(self, mode: str = "rb"):
        import io

        return io.BytesIO(self._bucket.store[self.name][0])

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


class _FakeSigningCredentials(ga_credentials.Credentials, ga_credentials.Signing):
    """A credential that can sign locally, so `_signing_kwargs` stays empty and
    URL signing doesn't reach for the IAM signBlob API."""

    def refresh(self, request) -> None: ...
    def sign_bytes(self, message): return b""
    @property
    def signer_email(self): return "fake@local"
    @property
    def signer(self): return None


class _FakeClient:
    def __init__(self) -> None:
        self.buckets: dict[str, _FakeBucket] = {}
        self._credentials = _FakeSigningCredentials()

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


def test_result_url_points_at_the_backend_proxy(store: GCSStorage) -> None:
    # Result files are streamed through the backend, not the bucket directly, so
    # the browser fetches them same-origin (no signed URL, no bucket CORS).
    assert store.generate_result_url("job1", "figure", "p.png") == (
        "/jobs/job1/results/figure/p.png"
    )


def test_result_file_path_is_none_so_routers_stream(store: GCSStorage) -> None:
    assert store.result_file_path("job1", "output", "m.nc") is None


def test_open_result_stream_yields_bytes(store: GCSStorage) -> None:
    store._bucket("out").blob("job1/output/m.nc").upload_from_string(b"hello")
    stream = store.open_result_stream("job1", "output", "m.nc")
    assert stream is not None
    body, _media_type, size = stream
    assert b"".join(body) == b"hello"
    assert size == 5
    assert store.open_result_stream("job1", "output", "missing.nc") is None


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


# --- GcsArtifactStore --------------------------------------------------------


def test_gcs_artifact_store_publishes_size_and_checksum(store: GCSStorage) -> None:
    from ai_almanac.server.services.artifact_store import GcsArtifactStore

    out = store._bucket("out")
    out.blob("job1/output/metrics.nc").upload_from_string(b"netcdf-bytes")
    out.blob("job1/figure/plot.png").upload_from_string(_PNG)

    artifacts = {a.filename: a for a in GcsArtifactStore(store).publish("job1")}

    assert set(artifacts) == {"metrics.nc", "plot.png"}
    nc = artifacts["metrics.nc"]
    assert nc.size_bytes == len(b"netcdf-bytes")
    assert nc.checksum  # GCS md5 populated, no download
    assert nc.storage_key == "job1/output/metrics.nc"
    assert nc.media_type == "application/x-netcdf"
    assert artifacts["plot.png"].kind == "figure"


def test_gcs_artifact_store_delete_removes_only_that_job(store: GCSStorage) -> None:
    from ai_almanac.server.services.artifact_store import GcsArtifactStore

    out = store._bucket("out")
    out.blob("job1/output/a.nc").upload_from_string(b"1")
    out.blob("job1/run.log").upload_from_string(b"log")
    out.blob("job2/output/b.nc").upload_from_string(b"2")

    GcsArtifactStore(store).delete_job("job1")

    assert "job1/output/a.nc" not in out.store
    assert "job1/run.log" not in out.store
    assert "job2/output/b.nc" in out.store


def test_gcs_artifact_store_has_no_local_open_or_workspace(store: GCSStorage) -> None:
    from ai_almanac.server.services.artifact_store import GcsArtifactStore

    artifact_store = GcsArtifactStore(store)
    with pytest.raises(NotImplementedError):
        artifact_store.create_workspace("job1")

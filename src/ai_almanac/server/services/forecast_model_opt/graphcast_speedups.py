"""Speed-ups for GraphCast inference (earth2studio's GraphCastOperational and
GraphCastSmall, on weathernext's GraphCast), applied at load time.

On a DGX Spark they take a GraphCast Operational step from 1.59 s to 1.03 s
and a GraphCast Small step from 0.35 s to 0.21 s. Over ten days the forecast
drifts from the stock one 1.1-1.7 times as fast as two stock runs drift apart.

GraphCast runs as one jitted JAX program per step: a grid-to-mesh graph
network, 16 rounds of message passing on the multi-mesh, and a mesh-to-grid
network, every MLP in bfloat16. On a DGX Spark most of its step is
memory-bound work XLA cannot see past -- the graphs are fixed, but the
program treats them as data -- and the rest is earth2studio's wrapper moving
outputs through the CPU. ``envs/speedups_check.py`` checks each
speed-up on this environment:

* Fixed-order graph sums (inexact). The networks sum each node's incoming
  messages with ``jraph.segment_sum``, which XLA runs as an atomic scatter-add
  in bfloat16, in whatever order the atomics land, so repeat runs differ. Each
  graph is static, so each node's in-edges are laid out once in a row (nodes
  grouped by in-degree, which runs from 3 to thousands, so padding at most
  doubles the rows) and summed in a fixed order, accumulating in float32.
  The model becomes reproducible, which the exact ones are checked against.
* Outputs handed to torch on the GPU (exact). The wrapper copies every output
  field to the CPU, reshuffles it there and copies it back; it is assembled
  on the GPU instead and shared through DLPack, which also leaves JAX free to
  run ahead of the host.
* Static edge embeddings computed once (inexact). Every step embeds the
  fixed geometric features of all three graphs' edges (millions at 0.25
  degrees) through an MLP and layer norm; the first step's embeddings are
  kept and passed to the steps after it. Not bit for bit only because XLA
  lets a fused value stay float32 where the kept copy is stored as bfloat16,
  so each embedding rounds once more.
* Edge MLPs' first layer split by input (inexact). An edge MLP's input is the
  edge's latent and its two nodes' latents, concatenated per edge; its first
  layer is the sum of the three parts' projections, so the nodes are projected
  once per node and looked up per edge, never building the concatenation. The
  parts are summed in float32, so the layer rounds once where it rounded twice.

``ALMANAC_GRAPHCAST_EXACT=1`` applies only the exact ones.

Self-contained like aifs_speedups (loaded from forecast_model_opt/ beside
forecast_pipeline): JAX, haiku and weathernext are imported only when a model
is patched. The weathernext code is patched in place, but each patch defers
to the stock code except while a forward built here traces, so other users of
it (GenCast) are unaffected. A forward that fails to trace or run falls back
to earth2studio's own. None is applied to a model, or on a weathernext
version, not in VERIFIED.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import logging
import os
import sys
import types
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import NamedTuple

LOG = logging.getLogger(__name__)

# The package whose version VERIFIED holds; earth2studio model class -> the
# versions its speed-ups were checked on (envs/speedups_check.py).
PACKAGE = "weathernext"
VERIFIED: dict[str, set[str]] = {
    "GraphCastOperational": {"0.3.1.dev0"},
    "GraphCastSmall": {"0.3.1.dev0"},
}

# Set to 1 to apply only the speed-ups that leave the forecast bit for bit.
EXACT_ONLY_ENV = "ALMANAC_GRAPHCAST_EXACT"


class Speedup(NamedTuple):
    name: str
    patch: Callable[[object], bool]
    # Bit for bit; otherwise measured against the forecast's chance spread.
    exact: bool


def apply(model, *, exact_only: bool | None = None) -> list[str]:
    """Apply every speed-up that holds for this earth2studio model; returns
    their names (none for other models or unverified weathernext versions).
    ``exact_only`` defaults to off unless ALMANAC_GRAPHCAST_EXACT=1."""
    model_class = type(model).__name__
    verified = VERIFIED.get(model_class)
    if not verified:
        return []
    try:
        weathernext_version = version(PACKAGE)
    except PackageNotFoundError:
        return []
    if weathernext_version not in verified:
        LOG.warning(
            "GraphCast speed-ups not applied to %s: weathernext %s is not verified for it "
            "(verified: %s); run envs/speedups_check.py",
            model_class,
            weathernext_version,
            ", ".join(sorted(verified)),
        )
        return []
    if exact_only is None:
        exact_only = os.environ.get(EXACT_ONLY_ENV) == "1"
    applied = []
    for speedup in SPEEDUPS.get(model_class, ()):
        if exact_only and not speedup.exact:
            continue
        try:
            if speedup.patch(model):
                applied.append(speedup.name)
        except Exception:  # noqa: BLE001 - a speed-up must never stop a forecast
            LOG.exception("GraphCast speed-up %r not applied", speedup.name)
    LOG.info("GraphCast speed-ups applied: %s", ", ".join(applied) or "none")
    return applied


# --- the forward ---------------------------------------------------------------------

# What the patched weathernext code reads while a _Forward traces; empty
# otherwise, and then every patch defers to the stock code.
_TRACE: dict = {}
_STOCK: dict = {}


class _Forward:
    """GraphCast's jitted forward as earth2studio builds it (its haiku
    transform, the checkpoint's params, an empty state), with flags the
    patched weathernext code reads while it traces. Setting a flag drops the
    compiled programs, so the next call traces again. Called as earth2studio
    calls its own; falls back to that one if a program fails."""

    def __init__(self, model):
        import haiku as hk

        transforms = []
        stock_transform = hk.transform_with_state

        def recording(fn):
            transformed = stock_transform(fn)
            transforms.append(transformed)
            return transformed

        hk.transform_with_state = recording
        try:
            model._load_run_forward_from_checkpoint()
        finally:
            hk.transform_with_state = stock_transform
        [self._transformed] = transforms
        self._stock = model.run_forward
        self._params = model.ckpt.params
        self._configs = {
            "model_config": model.ckpt.model_config,
            "task_config": model.ckpt.task_config,
        }
        self.flags: dict[str, bool] = {}
        self._programs: dict = {}
        self._edges = None
        self._failed = False

    def set(self, **flags: bool) -> None:
        self.flags.update(flags)
        self._programs.clear()
        self._edges = None

    def _program(self, mode: str):
        import jax

        program = self._programs.get(mode)
        if program is None:
            program = self._programs[mode] = jax.jit(functools.partial(self._traced, mode))
        return program

    def _traced(self, mode, params, rng, inputs, targets_template, forcings, edges=None):
        import haiku as hk

        _TRACE.clear()
        _TRACE.update(self.flags, mode=mode, edges=edges)
        try:
            split = self.flags.get("split_edges")
            with hk.intercept_methods(_split_first_linear) if split else contextlib.nullcontext():
                out, _ = self._transformed.apply(
                    params,
                    {},
                    rng,
                    inputs=inputs,
                    targets_template=targets_template,
                    forcings=forcings,
                    **self._configs,
                )
        finally:
            _TRACE.clear()
        return out

    def __call__(self, *, rng, inputs, targets_template, forcings):
        if self._failed:
            return self._stock(
                rng=rng, inputs=inputs, targets_template=targets_template, forcings=forcings
            )
        args = (self._params, rng, inputs, targets_template, forcings)
        try:
            if not self.flags.get("cache_edges"):
                return self._program("plain")(*args)
            key = tuple(sorted(inputs.sizes.items()))
            if self._edges is not None and self._edges[0] == key:
                return self._program("cached")(*args, self._edges[1])
            out = self._capture(args, key)
            return out
        except Exception:  # noqa: BLE001 - a speed-up must never stop a forecast
            LOG.exception("GraphCast speed-ups failed; running the stock forward")
            self._failed = True
            self._edges = None
            return self._stock(
                rng=rng, inputs=inputs, targets_template=targets_template, forcings=forcings
            )

    def _capture(self, args, key):
        """The first step, its edge embeddings kept on the GPU for the next."""
        import jax

        _CAPTURED.clear()
        out = self._program("capture")(*args)
        jax.block_until_ready(out)
        jax.effects_barrier()
        if _CAPTURED:
            self._edges = (key, {name: jax.device_put(value) for name, value in _CAPTURED.items()})
        else:
            LOG.warning("GraphCast speed-ups: no edge embeddings captured; embedding every step")
            self.flags["cache_edges"] = False
        _CAPTURED.clear()
        return out


# Edge embeddings a capture trace hands to the host as the program runs.
_CAPTURED: dict = {}


def _keep(name, value) -> None:
    import numpy as np

    _CAPTURED[name] = np.asarray(value)


def _forward(model) -> _Forward:
    """The model's forward, rebuilt as a _Forward the first time, with the
    weathernext patches it relies on in place."""
    _install()
    if not isinstance(model.run_forward, _Forward):
        model.run_forward = _Forward(model)
    return model.run_forward


def _install() -> None:
    """Patch the weathernext code the flags act on (once; inert outside a
    _Forward's trace)."""
    if _STOCK:
        return
    import jraph
    from weathernext.utils import typed_graph_net
    from weathernext.utils.legacy import deep_typed_graph_net

    _STOCK.update(
        embed=deep_typed_graph_net.DeepTypedGraphNet._embed,
        aggregate=deep_typed_graph_net._get_aggregate_edges_for_nodes_fn,
        edge_update=typed_graph_net._edge_update,
        jax_gather=typed_graph_net.jax_gather,
        concatenated_args=jraph.concatenated_args,
    )
    deep_typed_graph_net.DeepTypedGraphNet._embed = _embed
    deep_typed_graph_net._get_aggregate_edges_for_nodes_fn = _aggregate_edges_for_nodes_fn
    typed_graph_net._edge_update = _edge_update
    jraph.concatenated_args = _concatenated_args


# --- fixed-order graph sums -------------------------------------------------------


def patch_fixed_order_sums(model) -> bool:
    _forward(model).set(fixed_sums=True)
    return True


def _aggregate_edges_for_nodes_fn(name):
    if name == "segment_sum":
        return _fixed_order_segment_sum
    return _STOCK["aggregate"](name)


def _fixed_order_segment_sum(
    data, segment_ids, num_segments=None, indices_are_sorted=False, unique_indices=False
):
    """jraph.segment_sum over a static graph, each segment's in-edges summed
    in edge order in float32 (see _in_edges)."""
    import jax.numpy as jnp
    import jraph
    import numpy as np

    layout = None
    if _TRACE.get("fixed_sums") and isinstance(segment_ids, np.ndarray) and num_segments:
        layout = _in_edges(segment_ids, int(num_segments))
    if layout is None:
        return jraph.segment_sum(
            data, segment_ids, num_segments, indices_are_sorted, unique_indices
        )
    groups, where = layout
    zero = jnp.zeros((1, *data.shape[1:]), data.dtype)
    # One zero row past the last edge, for the padding slots.
    rows = jnp.concatenate([data, zero])
    sums = [rows[slots].astype(jnp.float32).sum(axis=1).astype(data.dtype) for slots in groups]
    if where is None:
        return sums[0]
    # Segments without edges take the zero row past the groups' sums.
    return jnp.concatenate([*sums, zero])[where]


_IN_EDGES: dict = {}


def _in_edges(segment_ids, num_segments: int):
    """Each segment's in-edges in edge order, laid out in rows to sum: the
    segments grouped by in-degree within a factor of two, each group a
    [segments, its largest in-degree] array of edge indices padded with
    len(segment_ids), so at most twice the edges are summed however uneven
    the graph; and where each segment's sum lands in the groups' sums laid
    end to end (None when that is every segment in order). None where ids
    fall outside the segments."""
    import numpy as np

    key = (num_segments, hashlib.blake2b(segment_ids.tobytes(), digest_size=16).digest())
    if key in _IN_EDGES:
        return _IN_EDGES[key]
    layout = None
    num_edges = segment_ids.shape[0]
    if (
        segment_ids.ndim == 1
        and num_edges
        and 0 <= segment_ids.min() <= segment_ids.max() < num_segments
    ):
        counts = np.bincount(segment_ids, minlength=num_segments)
        order = np.argsort(segment_ids, kind="stable")
        starts = np.cumsum(counts) - counts
        band = np.ceil(np.log2(np.maximum(counts, 1))).astype(np.int64)
        band[counts == 0] = -1
        groups, members = [], []
        for b in np.unique(band[band >= 0]):
            segments = np.flatnonzero(band == b)
            width = int(counts[segments].max())
            column = np.arange(width)
            slot = starts[segments, None] + column
            inside = column < counts[segments, None]
            groups.append(
                np.where(inside, order[np.minimum(slot, num_edges - 1)], num_edges).astype(np.int32)
            )
            members.append(segments)
        landing = np.full(num_segments, sum(map(len, members)), dtype=np.int32)
        landing[np.concatenate(members)] = np.arange(sum(map(len, members)))
        in_order = len(groups) == 1 and len(members[0]) == num_segments
        layout = (groups, None if in_order else landing)
        LOG.info(
            "GraphCast speed-ups: %d edges into %d nodes (in-degree up to %d) summed in "
            "%d fixed-order group(s), %.2fx the edges",
            num_edges,
            num_segments,
            int(counts.max()),
            len(groups),
            sum(g.size for g in groups) / num_edges,
        )
    _IN_EDGES[key] = layout
    return layout


# --- outputs on the GPU -------------------------------------------------------------


def patch_outputs_on_gpu(model) -> bool:
    model.iterator_result_to_tensor = types.MethodType(_outputs_on_gpu, model)
    return True


class _Unsupported(Exception):
    pass


def _outputs_on_gpu(self, dataset):
    """iterator_result_to_tensor (a rollout step's outputs, as one tensor of
    the model's output variables) assembled on the GPU and shared with torch
    through DLPack; the stock conversion for anything else."""
    try:
        return _assemble_on_gpu(self, dataset)
    except _Unsupported as unsupported:
        if not getattr(self, "_stock_outputs_logged", False):
            self._stock_outputs_logged = True
            LOG.info("GraphCast speed-ups: outputs converted on the CPU (%s)", unsupported)
        return type(self).iterator_result_to_tensor(self, dataset)


def _assemble_on_gpu(model, dataset):
    import jax
    import jax.numpy as jnp
    import torch
    import xarray_jax

    module = sys.modules[type(model).__module__]
    if "ensemble" in dataset.dims or "batch" not in dataset.dims or dataset.sizes["time"] != 1:
        raise _Unsupported(f"dimensions {dict(dataset.sizes)}")
    levels = list(dataset.coords["level"].values) if "level" in dataset.coords else []
    fields = []
    for name in model._output_coords["variable"]:
        variable, _, level = module.WB2Lexicon.VOCAB[name].partition("::")
        array = dataset[variable]
        data = xarray_jax.unwrap_data(array)
        if not isinstance(data, jax.Array):
            raise _Unsupported(f"{variable} is a {type(data).__name__}")
        dims = list(array.dims)
        if level:
            data = jnp.take(data, levels.index(int(level)), axis=dims.index("level"))
            dims.remove("level")
        if sorted(dims) != ["batch", "lat", "lon", "time"]:
            raise _Unsupported(f"{variable} has dimensions {dims}")
        fields.append(jnp.transpose(data, [dims.index(d) for d in ("batch", "time", "lat", "lon")]))
    # batch, time, lead_time, variable, lat, lon; latitude from north to south.
    out = jnp.flip(jnp.stack(fields, axis=2)[:, :, None], axis=-2)
    return torch.from_dlpack(out)


# --- static edge embeddings -------------------------------------------------------


def patch_cached_edge_embeddings(model) -> bool:
    _forward(model).set(cache_edges=True)
    return True


def _embed(self, input_graph, embedder_network):
    """DeepTypedGraphNet._embed, handing its edge embeddings to the host on a
    capture trace (a callback: the forward runs inside a scan, which no value
    may leave otherwise) and using the kept ones on a cached trace (XLA then
    drops the edge embedders: nothing reads them)."""
    import jax

    latent = _STOCK["embed"](self, input_graph, embedder_network)
    mode = _TRACE.get("mode")
    if mode == "capture":
        for key, edge_set in latent.edges.items():
            jax.debug.callback(functools.partial(_keep, key.name), edge_set.features)
    elif mode == "cached":
        edges = _TRACE["edges"]
        latent = latent._replace(
            edges={
                key: edge_set._replace(features=edges[key.name])
                for key, edge_set in latent.edges.items()
            }
        )
    return latent


# --- split first edge layer -------------------------------------------------------


def patch_split_edge_layer(model) -> bool:
    _forward(model).set(split_edges=True)
    return True


class _Gathered:
    """Node features, looked up per edge by ``indices`` once projected."""

    __slots__ = ("indices", "nodes")

    def __init__(self, nodes, indices):
        self.nodes, self.indices = nodes, indices

    @property
    def shape(self):
        return (self.indices.shape[0], *self.nodes.shape[1:])

    @property
    def dtype(self):
        return self.nodes.dtype


class _Concat:
    """The inputs a concatenated_args network would concatenate, kept apart."""

    __slots__ = ("dtype", "parts", "shape")

    def __init__(self, parts):
        self.parts = parts
        self.dtype = parts[0].dtype
        self.shape = (*parts[0].shape[:-1], sum(part.shape[-1] for part in parts))


def _is_identity(fn) -> bool:
    sentinel = object()
    try:
        return fn(sentinel) is sentinel
    except Exception:  # noqa: BLE001 - anything else is not the identity
        return False


def _edge_update(
    graph,
    edge_fn,
    edge_set_key,
    edge_update_remat_block_size,
    gather_nodes_fn,
    pre_gather_senders_fn,
    pre_gather_receivers_fn,
    pre_gather_globals_fn,
    pre_gather_edges_fn,
):
    """typed_graph_net._edge_update passing the sender and receiver features
    ungathered, for the first layer to project before it gathers."""
    import jax

    args = (
        graph,
        edge_fn,
        edge_set_key,
        edge_update_remat_block_size,
        gather_nodes_fn,
        pre_gather_senders_fn,
        pre_gather_receivers_fn,
        pre_gather_globals_fn,
        pre_gather_edges_fn,
    )
    if not _TRACE.get("split_edges"):
        return _STOCK["edge_update"](*args)
    senders = graph.nodes[edge_set_key.node_sets[0]].features
    receivers = graph.nodes[edge_set_key.node_sets[1]].features
    edge_set = graph.edges[edge_set_key]
    if (
        edge_update_remat_block_size
        or gather_nodes_fn is not _STOCK["jax_gather"]
        or jax.tree_util.tree_leaves(graph.context.features)
        or not all(isinstance(x, jax.Array) for x in (senders, receivers, edge_set.features))
        or not all(
            _is_identity(fn)
            for fn in (pre_gather_senders_fn, pre_gather_receivers_fn, pre_gather_edges_fn)
        )
    ):
        return _STOCK["edge_update"](*args)
    features = edge_fn(
        edge_set.features,
        _Gathered(senders, edge_set.indices.senders),
        _Gathered(receivers, edge_set.indices.receivers),
        (),
    )
    return edge_set._replace(features=features)


def _concatenated_args(update=None, *, axis=-1):
    """jraph.concatenated_args, passing the parts on as one _Concat when one
    of them is a _Gathered (only on a split-edges trace). GraphCast nests
    these (one around each edge MLP, another around it and its layer norm),
    so a _Concat is passed on as it is."""
    import jax

    stock = _STOCK["concatenated_args"]

    def decorate(fn):
        concatenated = stock(fn, axis=axis)

        def wrapper(*args, **kwargs):
            leaves = jax.tree_util.tree_leaves(
                (args, kwargs), is_leaf=lambda x: isinstance(x, (_Gathered, _Concat))
            )
            if axis != -1 or not any(isinstance(x, (_Gathered, _Concat)) for x in leaves):
                return concatenated(*args, **kwargs)
            parts = [p for x in leaves for p in (x.parts if isinstance(x, _Concat) else [x])]
            return fn(_Concat(parts))

        return wrapper

    return decorate(update) if update else decorate


def _split_first_linear(next_f, args, kwargs, context):
    """hk.Linear on a _Concat: each part projected by its rows of the weight
    (a gathered part per node, then looked up per edge), summed in float32
    with the bias, rounded once."""
    import haiku as hk
    import jax.numpy as jnp

    if (
        context.method_name != "__call__"
        or not isinstance(context.module, hk.Linear)
        or not args
        or not isinstance(args[0], _Concat)
        or kwargs
    ):
        return next_f(*args, **kwargs)
    module, parts = context.module, args[0]
    size = parts.shape[-1]
    weight = hk.get_parameter("w", [size, module.output_size], parts.dtype, init=jnp.zeros)
    out, start = None, 0
    for part in parts.parts:
        rows = weight[start : start + part.shape[-1]]
        start += part.shape[-1]
        if isinstance(part, _Gathered):
            projected = jnp.dot(part.nodes, rows, preferred_element_type=jnp.float32)
            projected = projected[part.indices]
        else:
            projected = jnp.dot(part, rows, preferred_element_type=jnp.float32)
        out = projected if out is None else out + projected
    if module.with_bias:
        out = out + hk.get_parameter("b", [module.output_size], parts.dtype, init=jnp.zeros)
    return out.astype(parts.dtype)


FIXED_SUMS = Speedup("fixed-order graph sums", patch_fixed_order_sums, False)
OUTPUTS_ON_GPU = Speedup("outputs on the GPU", patch_outputs_on_gpu, True)
CACHED_EDGES = Speedup("static edge embeddings computed once", patch_cached_edge_embeddings, False)
SPLIT_EDGES = Speedup("edge MLPs' first layer split by input", patch_split_edge_layer, False)

# Per model, in order: the first makes repeat runs reproducible, which the
# exact ones are checked against.
SPEEDUPS: dict[str, tuple[Speedup, ...]] = {
    "GraphCastOperational": (FIXED_SUMS, OUTPUTS_ON_GPU, CACHED_EDGES, SPLIT_EDGES),
    "GraphCastSmall": (FIXED_SUMS, OUTPUTS_ON_GPU, CACHED_EDGES, SPLIT_EDGES),
}

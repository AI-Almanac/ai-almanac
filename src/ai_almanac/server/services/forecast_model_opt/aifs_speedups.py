"""Speed-ups for AIFS v2 inference, applied at load time.

On a DGX Spark they take an AIFS-ENS v2 member-step from 2.44 s to 1.68 s
exactly, and to about 1.22 s with the compiled blocks; an AIFS-single v2 step
from 1.74 s to AIFS2_TODO.

Both models are memory-bound here, and spend much of a step in anemoi-models
code paths that move more memory than they need to. The exact speed-ups are
inference-only rewrites of them; the same kernels run on the same values, so
the forecast is unchanged bit for bit (``envs/aifs_speedups_check.py`` checks
it, against a run made reproducible by the model's first speed-up):

* Deterministic noise projection (AIFS-ENS). The noise injector maps its
  coarse random field onto the hidden grid with ``torch.sparse.mm``, which
  accumulates with atomics, so repeat runs drift apart within a few steps. A
  fixed-order sum over the same non-zeros is one more summation order inside
  that spread, and makes a run reproducible.
* Cached static graph work in the encoder and decoder (AIFS-ENS). Every step
  the stock mappers re-extract their chunk subgraphs, re-sort the edges into
  CSC order and project the static edge attributes to 1024 channels: for the
  decoder's 13M edges that alone moves ~80 GB per step. The index work is
  done once, and the projected edges are kept resident when the GPU has the
  room.
* One autocast cast per input. Under autocast every linear casts its float32
  input itself; the mapper blocks' key/value and self/query pairs and the
  processor's q/k/v each share one input, cast once here instead.
* Processor attention in flash-attention's layout (both). The stock path
  permutes q/k/v to ``batch heads grid vars`` (copying them for the q/k norms)
  and the flash wrapper permutes them back; the norms act per head and flash
  attention reads by strides, so the round trip is skipped.
* The imputer's NaN lookup indexes with a tuple, not the list torch
  deprecates (both; same elements, and it no longer warns every step).

The inexact ones round differently, so the check measures how far they move
the forecast against how far it already moves by chance: to another member
for AIFS-ENS, between two runs of the stock model for AIFS-single (whose PyG
graph sums use atomics).

* Graph attention through Triton (AIFS-single). Its anemoi-models (0.9)
  computes the mappers' graph attention through PyG, materialising each
  edge's key, value and message; anemoi's later Triton kernel (vendored in
  anemoi_triton_gt.py) makes the same sums per destination node, in float32
  and a fixed order, so the model also becomes reproducible. The static graph
  work is then cached as for AIFS-ENS.
* Compiled processor and mapper blocks (both). ``torch.compile`` fuses their
  layer norms, casts and elementwise ops, which on these memory-bound models
  is most of what is left. A compiled AIFS-ENS forecast starts within a few
  percent of the spread between members and drifts, as any perturbation
  does, until it is in effect another member. It falls back to the
  uncompiled blocks if compilation fails.

``ALMANAC_AIFS_EXACT=1`` applies only the exact ones.

The cached mappers come from Adam Marchakitus's HPC inference work
(``run_model/fast_inference.py``), as do the noise projection and the
reasoning above.

Self-contained like forecast_pipeline (which loads this file from
forecast_model_opt/ beside it, in the package and in the Modal image alike):
torch and anemoi are imported only when a model is patched. Each speed-up
checks the structure it rewrites and steps aside, leaving the stock code,
wherever that differs. None is applied to a model, or on an anemoi-models
version, not in VERIFIED: the AIFS families run different mappers on
different anemoi versions, so what holds for one is not assumed for another.
Run the check after an upgrade, then add the version.
"""

from __future__ import annotations

import logging
import os
import types
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import NamedTuple

LOG = logging.getLogger(__name__)

# earth2studio model class -> the anemoi-models versions its speed-ups were
# checked on (envs/aifs_speedups_check.py).
VERIFIED: dict[str, set[str]] = {"AIFS2ENS": {"0.11.2"}}

# GPU memory the rollout's working set needs beside resident projected edges.
EDGE_CACHE_RESERVE_BYTES = 14 * 1024**3

# Set to 1 to apply only the speed-ups that leave the forecast bit for bit.
EXACT_ONLY_ENV = "ALMANAC_AIFS_EXACT"


class Speedup(NamedTuple):
    name: str
    patch: Callable[[object], bool]
    # Bit for bit; otherwise measured against the forecast's chance spread.
    exact: bool


def apply(model, *, exact_only: bool | None = None) -> list[str]:
    """Apply every speed-up that holds for this earth2studio model; returns
    their names (none for other models or unverified anemoi versions).
    ``exact_only`` defaults to off unless ALMANAC_AIFS_EXACT=1."""
    model_class = type(model).__name__
    verified = VERIFIED.get(model_class)
    if not verified:
        return []
    try:
        anemoi_version = version("anemoi-models")
    except PackageNotFoundError:
        return []
    if anemoi_version not in verified:
        LOG.warning(
            "AIFS speed-ups not applied to %s: anemoi-models %s is not verified for it "
            "(verified: %s); run envs/aifs_speedups_check.py",
            model_class,
            anemoi_version,
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
            LOG.exception("AIFS speed-up %r not applied", speedup.name)
    LOG.info("AIFS speed-ups applied: %s", ", ".join(applied) or "none")
    return applied


def _cast_once(x):
    """``x`` as autocast would cast it for a linear (bfloat16 for AIFS-ENS,
    float16 for AIFS-single)."""
    import torch

    if x.dtype == torch.float32 and torch.is_autocast_enabled("cuda"):
        return x.to(torch.get_autocast_dtype("cuda"))
    return x


# --- deterministic noise projection --------------------------------------------


def patch_noise_projector(model) -> bool:
    injector = getattr(model.model.model, "noise_injector", None)
    projector = getattr(injector, "noise_projector", None)
    if projector is None or not hasattr(projector, "projection_matrix"):
        return False
    projector._ell = None
    projector.forward = types.MethodType(_deterministic_projector_forward, projector)
    return True


def _deterministic_projector_forward(self, x, *args, **kwargs):
    """SparseProjector.forward with a fixed summation order."""
    import torch

    cache = self._ell
    if cache is None or cache[0].device != x.device:
        matrix = self.projection_matrix.coalesce()  # entries sorted by (row, col)
        rows, cols = matrix.indices()
        vals = matrix.values()
        n_rows = matrix.shape[0]
        counts = torch.bincount(rows, minlength=n_rows)
        slot = (
            torch.arange(rows.numel(), device=rows.device)
            - (torch.cumsum(counts, 0) - counts)[rows]
        )
        k_max = int(counts.max())
        ell_cols = torch.zeros((n_rows, k_max), dtype=torch.long, device=rows.device)
        ell_vals = torch.zeros((n_rows, k_max), dtype=vals.dtype, device=rows.device)
        ell_cols[rows, slot] = cols
        ell_vals[rows, slot] = vals
        cache = self._ell = (ell_cols.to(x.device), ell_vals.to(x.device))
    ell_cols, ell_vals = cache
    with torch.amp.autocast(device_type=x.device.type, enabled=self.autocast):
        out = [(ell_vals.unsqueeze(-1) * x[i][ell_cols]).sum(dim=1) for i in range(x.shape[0])]
    return torch.stack(out)


# --- cached static graph work in the encoder and decoder ------------------------


def patch_graph_mappers(model) -> bool:
    """AIFS-ENS v2: cache each mapper's static graph work for anemoi's own Triton
    graph attention."""
    from anemoi.models.triton.utils import edge_index_to_csc

    def csc_of(edge_index, num_nodes):
        return edge_index_to_csc(edge_index, num_nodes=num_nodes, reverse=True)

    def attend(block, query, key, value, edges, chunk):
        return block.conv(query, key, value, edges, chunk["csc"], chunk["reverse"])

    return _cache_mappers(
        model,
        "mapper_forward_with_edge_sharding",
        csc_of,
        attend,
        lambda block: getattr(block, "graph_attention_backend", None) == "triton",
    )


def patch_pyg_mappers_to_triton(model) -> bool:
    """AIFS-single v2: the mappers' PyG graph attention through the vendored
    Triton kernel, with their static graph work cached."""
    from torch_geometric.nn import MessagePassing

    gt = _vendored_triton_gt()

    def csc_of(edge_index, num_nodes):
        csc, perm = gt.edge_index_to_csc(edge_index, num_nodes)
        return csc, perm, None

    def attend(block, query, key, value, edges, chunk):
        return gt.gt_forward(query, key, value, edges, chunk["csc"])

    return _cache_mappers(
        model,
        "forward_with_edge_sharding",
        csc_of,
        attend,
        lambda block: isinstance(getattr(block, "conv", None), MessagePassing),
    )


def _vendored_triton_gt():
    """anemoi_triton_gt.py, loaded from beside this file as this file is loaded
    from beside forecast_pipeline."""
    import importlib.util
    import sys
    from pathlib import Path

    name = "anemoi_triton_gt"
    if (loaded := sys.modules.get(name)) is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _cache_mappers(model, method: str, csc_of, attend, supported) -> bool:
    """Route each mapper's edge-sharding forward (``method``) through the
    cached one, keeping its projected edges resident while the GPU has room
    for them beside the working set (decoder first: its edges are most of
    the gain). ``csc_of`` sorts a chunk's edges for ``attend``, the graph
    attention; mappers whose block ``supported`` rejects are left stock."""
    import torch
    from anemoi.models.layers.mapper import GraphTransformerBaseMapper

    inner = model.model.model
    free, _ = torch.cuda.mem_get_info()
    free += torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
    budget = free - EDGE_CACHE_RESERVE_BYTES
    patched = False
    for name in ("decoder", "encoder"):
        mapper = getattr(inner, name, None)
        if not isinstance(mapper, GraphTransformerBaseMapper):
            continue
        if (
            getattr(mapper, "shard_strategy", None) != "edges"
            or not hasattr(mapper, method)
            or not supported(getattr(mapper, "proc", None))
        ):
            LOG.info("AIFS speed-ups: %s mapper left stock (unsupported configuration)", name)
            continue
        need = mapper.edge_attr.shape[0] * mapper.proc.num_heads * mapper.proc.out_channels_conv * 2
        keep = need <= budget
        if keep:
            budget -= need
        mapper._fast_cache = {}
        mapper._fast_cache_edges = keep
        mapper._fast_csc_of = csc_of
        mapper._fast_attend = attend
        mapper._fast_block_forward = _block_forward_cached
        mapper._fast_stock_forward = getattr(mapper, method)
        setattr(mapper, method, types.MethodType(_mapper_forward_cached, mapper))
        LOG.info(
            "AIFS speed-ups: %s caches its static graph (projected edges resident: %s, %.1f GiB)",
            name,
            keep,
            need / 1024**3,
        )
        patched = True
    return patched


def _build_static_cache(mapper, size_full, batch_size):
    """The index bookkeeping of the stock edge-sharding path, done once."""
    import torch
    from anemoi.models.distributed.khop_edges import bipartite_subgraph
    from anemoi.models.layers import mapper as mapper_module

    edge_attr = mapper.trainable(mapper.edge_attr, batch_size)
    edge_index = mapper._expand_edges(mapper.edge_index_base, mapper.edge_inc, batch_size)
    nodes_src = torch.unique(edge_index[0])
    dst_nodes = torch.arange(size_full[1], device=edge_index.device)
    edge_index, _ = bipartite_subgraph(
        (nodes_src, dst_nodes), edge_index, size=size_full, relabel_nodes=True
    )
    size = (nodes_src.numel(), size_full[1])
    num_chunks = max(mapper.num_chunks, mapper_module.NUM_CHUNKS_INFERENCE_MAPPER)
    dst_chunks = torch.arange(size[1], device=edge_index.device).tensor_split(num_chunks)
    nodes_src_full = torch.arange(size[0], device=edge_index.device)
    chunks = []
    for dst_chunk in dst_chunks:
        ei_c, ea_c = bipartite_subgraph(
            (nodes_src_full, dst_chunk), edge_index, edge_attr, size=size, relabel_nodes=True
        )
        connected = torch.unique(ei_c[0])
        dst_c = torch.arange(size[1], device=edge_index.device)
        ei_c, _ = bipartite_subgraph((connected, dst_c), ei_c, size=size, relabel_nodes=True)
        chunk_size = (connected.numel(), dst_chunk.numel())
        csc, perm, reverse = mapper._fast_csc_of(ei_c, chunk_size)
        chunks.append(
            {
                "dst_chunk": dst_chunk,
                "src_index": nodes_src[connected],
                # Static edge attributes in CSC order: the edge MLP and projection
                # act row by row, so f(attr)[perm] == f(attr[perm]) bit for bit.
                "edge_attr_csc": ea_c.index_select(0, perm).contiguous(),
                "csc": tuple(t.contiguous() for t in csc),
                "reverse": None if reverse is None else tuple(t.contiguous() for t in reverse),
                "edges_csc": None,
            }
        )
    return {"size": size, "nodes_src": nodes_src, "chunks": chunks}


def _block_forward_cached(block, x, chunk, cache_edges, attend):
    """GraphTransformerMapperBlock.forward (edge sharding, no conditioning, one
    chunk) with the static edge projection from the cache, one cast per input,
    and ``attend`` for the graph attention."""
    import einops

    x_skip = x
    x = (block.layer_norm_attention_src(x[0]), block.layer_norm_attention_dest(x[1]))
    src, dst = _cast_once(x[0]), _cast_once(x[1])
    x_r = block.lin_self(dst)
    query = block.lin_query(dst)
    key = block.lin_key(src)
    value = block.lin_value(src)
    edges = chunk["edges_csc"]
    if edges is None:
        edges = chunk["edge_attr_csc"]
        # anemoi-models 0.11 runs the edge attributes through an MLP first; 0.9 does not.
        if (pre_mlp := getattr(block, "edge_pre_mlp", None)) is not None:
            edges = pre_mlp(edges)
        edges = block.lin_edge(edges)
        if cache_edges:
            chunk["edges_csc"] = edges
    query, key, value, edges = (
        einops.rearrange(
            t,
            "nodes (heads vars) -> nodes heads vars",
            heads=block.num_heads,
            vars=block.out_channels_conv,
        )
        for t in (query, key, value, edges)
    )
    if block.qk_norm:
        query = block.q_norm(query)
        key = block.k_norm(key)
    out = attend(block, query, key, value, edges, chunk)
    out = einops.rearrange(out, "nodes heads vars -> nodes (heads vars)")
    out = block.projection(out + x_r)
    out = out + x_skip[1]
    return block.run_node_dst_mlp(out) + out


def _mapper_forward_cached(
    self,
    x,
    batch_size,
    shard_shapes,
    model_comm_group=None,
    x_src_is_sharded=False,
    x_dst_is_sharded=False,
    keep_x_dst_sharded=False,
    cond=None,
    **kwargs,
):
    import torch

    if model_comm_group is not None or cond is not None or kwargs:
        return self._fast_stock_forward(
            x,
            batch_size,
            shard_shapes,
            model_comm_group,
            x_src_is_sharded,
            x_dst_is_sharded,
            keep_x_dst_sharded,
            cond=cond,
            **kwargs,
        )
    x_src, x_dst = x
    shapes_src, shapes_dst = shard_shapes
    size_full = (x_src.shape[0], x_dst.shape[0])
    key = (size_full, batch_size)
    state = self._fast_cache.get(key)
    if state is None:
        state = self._fast_cache[key] = _build_static_cache(self, size_full, batch_size)
    out_channels = self.out_channels_dst if self.out_channels_dst is not None else self.hidden_dim
    out_type = torch.get_autocast_gpu_dtype() if torch.is_autocast_enabled() else x_dst.dtype
    out_dst = torch.empty((*x_dst.shape[:-1], out_channels), device=x_dst.device, dtype=out_type)
    for chunk in state["chunks"]:
        x_src_chunk = x_src[chunk["src_index"]]
        x_dst_chunk = x_dst[chunk["dst_chunk"]]
        x_src_chunk, x_dst_chunk, _, _ = self.pre_process(
            (x_src_chunk, x_dst_chunk),
            (shapes_src, shapes_dst),
            None,
            x_src_is_sharded=True,
            x_dst_is_sharded=True,
        )
        x_dst_out = self._fast_block_forward(
            self.proc, (x_src_chunk, x_dst_chunk), chunk, self._fast_cache_edges, self._fast_attend
        )
        out_dst[chunk["dst_chunk"]] = self.post_process(
            x_dst_out, shapes_dst, None, keep_x_dst_sharded=True
        ).to(dtype=out_type)
    return out_dst


# --- processor attention ---------------------------------------------------------


def patch_processor_attention(model) -> bool:
    """One q/k/v cast, and flash attention's own layout, for each self-attention."""
    from anemoi.models.layers.attention import FlashAttentionWrapper, MultiHeadSelfAttention

    stock_forward = MultiHeadSelfAttention.forward
    stock_computation = MultiHeadSelfAttention.attention_computation

    def applies(self, model_comm_group):
        wrapper = self.attention
        return (
            not model_comm_group
            and not self.training
            and isinstance(wrapper, FlashAttentionWrapper)
            # Absent on anemoi-models versions that predate flash-attention 3.
            and not getattr(wrapper, "use_flash_attn_v3", False)
            and not getattr(wrapper, "use_rotary_embeddings", False)
            and getattr(self, "alibi_slopes", None) is None
        )

    def forward(self, x, shapes, batch_size, model_comm_group=None):
        if applies(self, model_comm_group):
            x = _cast_once(x)
        return stock_forward(self, x, shapes, batch_size, model_comm_group)

    def attention_computation(self, query, key, value, shapes, batch_size, model_comm_group=None):
        import einops

        if not applies(self, model_comm_group):
            return stock_computation(self, query, key, value, shapes, batch_size, model_comm_group)
        query, key, value = (
            einops.rearrange(
                t,
                "(batch grid) (heads vars) -> batch grid heads vars",
                batch=batch_size,
                heads=self.num_heads,
            )
            for t in (query, key, value)
        )
        if getattr(self, "qk_norm", False):
            query = self.q_norm(query)
            key = self.k_norm(key)
        window = self.window_size
        out = self.attention.attention(
            query,
            key,
            value,
            causal=False,
            window_size=(window, window) if window is not None else (-1, -1),
            dropout_p=0.0,
            softcap=self.softcap,
            alibi_slopes=None,
        )
        out = einops.rearrange(out, "batch grid heads vars -> (batch grid) (heads vars)")
        return self.projection(out)

    patched = 0
    for module in model.model.modules():
        if type(module) is MultiHeadSelfAttention:
            module.forward = types.MethodType(forward, module)
            module.attention_computation = types.MethodType(attention_computation, module)
            patched += 1
    return patched > 0


# --- imputer -----------------------------------------------------------------------


def patch_imputer_indexing(model) -> bool:
    """BaseImputer.get_nans with a tuple index: the elements its list selects."""
    import torch
    from anemoi.models.preprocessing.imputer import BaseImputer

    def get_nans(self, x):
        idx = (slice(None), slice(None), *([0] * (x.ndim - 4)), slice(None), slice(None))
        return torch.isnan(x[idx])

    if getattr(BaseImputer.get_nans, "_tuple_index", False):
        return True
    get_nans._tuple_index = True
    BaseImputer.get_nans = get_nans
    return True


# --- compiled blocks -----------------------------------------------------------------


def compile_blocks(model) -> bool:
    """Compile the processor blocks and the cached mapper blocks. Compilation
    happens at their first call; if it fails there they run uncompiled."""
    import torch
    from anemoi.models.layers.block import TransformerProcessorBlock

    # Each mapper chunk is its own shape, and dynamic=False compiles one graph
    # per shape: room for every chunk of both mappers.
    torch._dynamo.config.cache_size_limit = max(torch._dynamo.config.cache_size_limit, 64)
    torch._dynamo.config.suppress_errors = True
    compiled = 0
    for module in model.model.modules():
        if isinstance(module, TransformerProcessorBlock):
            module.compile(dynamic=False)
            compiled += 1
    for name in ("encoder", "decoder"):
        mapper = getattr(model.model.model, name, None)
        if getattr(mapper, "_fast_block_forward", None) is _block_forward_cached:
            mapper._fast_block_forward = torch.compile(_block_forward_cached, dynamic=False)
            compiled += 1
    return compiled > 0


PROCESSOR_ATTENTION = Speedup("processor attention layout", patch_processor_attention, True)
IMPUTER = Speedup("imputer tuple indexing", patch_imputer_indexing, True)
COMPILED = Speedup("compiled processor and mapper blocks", compile_blocks, False)

# Per model, in order. The first makes repeat runs reproducible, which the
# exact ones are checked against; the compiled blocks come last, compiling
# whatever the others left.
SPEEDUPS: dict[str, tuple[Speedup, ...]] = {
    "AIFS2ENS": (
        Speedup("deterministic noise projection", patch_noise_projector, True),
        Speedup("cached mapper graph work", patch_graph_mappers, True),
        PROCESSOR_ATTENTION,
        IMPUTER,
        COMPILED,
    ),
    "AIFS2": (
        Speedup("Triton graph attention", patch_pyg_mappers_to_triton, False),
        PROCESSOR_ATTENTION,
        IMPUTER,
        COMPILED,
    ),
}

"""Runs inside vLLM's worker processes, mixed in with `--worker-extension-cls`.

The LLM env does not install ai-almanac, so server/services/local_llm.py puts
only this directory on the server's PYTHONPATH: import nothing here but the
standard library and vLLM.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class DraftReload:
    """Refill a speculative-decoding draft model after a level-2 wake.

    Level-2 sleep discards every weight in vLLM's "weights" pool, the draft
    model's with the target's, but `reload_weights` only re-reads the target
    and wake only restores the draft's buffers. Left alone, the draft proposes
    from whatever the re-mapped memory holds: replies stay correct, since the
    target verifies every token, but almost none are accepted and the speedup
    is gone without an error anywhere.
    """

    def reload_draft_weights(self) -> bool:
        """Re-read the draft model's checkpoint in place; False without a draft.

        Call after `reload_weights`: weights the draft shares with the target
        (embeddings, LM head) are not in its checkpoint, and keep the values
        that reload just put back.

        The checkpoint is copied straight into the draft's existing tensors,
        as its first load did. vLLM's layerwise reload, which `reload_weights`
        uses for the target, is wrong here: it parks each layer on the meta
        device until finalized, and a DFlash draft's `load_weights` builds its
        fused KV buffers from the layers before then, leaving them on meta
        and the engine dead at the first draft.
        """
        from vllm.config import set_current_vllm_config
        from vllm.model_executor.model_loader import get_model_loader

        draft = self.get_draft_model()  # type: ignore[attr-defined]
        spec = self.vllm_config.speculative_config  # type: ignore[attr-defined]
        draft_config = getattr(spec, "draft_model_config", None)
        if draft is None or draft_config is None:
            return False
        if getattr(draft_config, "quantization", None):
            # Quantized weights are repacked after loading; copying the
            # checkpoint over the repacked tensors would corrupt them.
            raise NotImplementedError("reloading a quantized draft model in place")
        started = time.perf_counter()
        with set_current_vllm_config(self.vllm_config):  # type: ignore[attr-defined]
            loader = get_model_loader(self.vllm_config.load_config)  # type: ignore[attr-defined]
            draft.load_weights(loader.get_all_weights(draft_config, draft))
        logger.info("Reloaded draft model weights in %.2f s", time.perf_counter() - started)
        return True

"""Check server/services/forecast_model_opt/aifs_speedups.py on this environment.

Run after an earth2studio or anemoi-models upgrade (``ai-almanac env
check-speedups`` runs it in the model's forecast environment). The model's
speed-ups are added one at a time, each rollout's every step hashed:

* the first must make repeat runs reproducible, so the rest can be matched;
* an exact one must leave every step bit for bit as it was;
* an inexact one is measured: how far its first step moves 2 m temperature,
  against how far the forecast already moves by chance -- to another member
  for an ensemble model, between two runs of the stock model otherwise.

A pass prints the anemoi-models version to add to ``aifs_speedups.VERIFIED``
for the model; a failure names the speed-up. Needs a GPU, and initial
conditions for --date (cached in EARTH2STUDIO_DATA_CACHE, so a second check
downloads nothing).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import sys
import time
from collections import OrderedDict
from importlib.metadata import version
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "server" / "services" / "forecast_pipeline.py"

# How far an inexact speed-up may move the first step, as a share of the
# chance spread: rounding moves it a fraction of a member, or a few times the
# stock model's own run-to-run noise; a broken kernel moves it much further.
LIMITS = {"two members": 0.5, "two stock runs": 10.0}


def _load_pipeline():
    spec = importlib.util.spec_from_file_location("forecast_pipeline", PIPELINE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rollout(model, x0, c0, steps: int, *, first_t2m: list | None = None) -> tuple[list[str], float]:
    """Each step's output hashed, and the mean time of the steps after warm-up;
    ``first_t2m`` collects the first step's 2 m temperature."""
    import numpy as np
    import torch

    x = x0.unsqueeze(0)
    coords = OrderedDict([("ensemble", np.arange(1))] + list(c0.items()))
    hashes, times = [], []
    torch.cuda.synchronize()
    t = time.perf_counter()
    with torch.inference_mode():
        for step, (y, c) in enumerate(model.create_iterator(x, coords)):
            torch.cuda.synchronize()
            if step:
                times.append(time.perf_counter() - t)
            hashes.append(hashlib.sha256(y.contiguous().cpu().numpy().tobytes()).hexdigest())
            if step == 1 and first_t2m is not None:
                first_t2m.append(
                    y[0, ..., list(c["variable"]).index("t2m"), :, :].float().cpu().numpy()
                )
            if step >= steps:
                break
            # The hash is not the model's time: the clock restarts after it.
            t = time.perf_counter()
    return hashes, float(np.mean(times[2:] or times))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="AIFS2ENS", help="earth2studio model class")
    parser.add_argument("--date", default="2026-05-13T00:00", help="initial condition (UTC)")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--seed", type=int, default=1234, help="ensemble models only")
    parser.add_argument("--package", help="local checkpoint folder (default: the model's own)")
    args = parser.parse_args()

    import numpy as np
    import torch
    from earth2studio.data import fetch_data
    from earth2studio.models import px
    from earth2studio.models.auto import Package

    pipeline = _load_pipeline()
    speedups = pipeline._aifs_speedups()
    model_speedups = speedups.SPEEDUPS.get(args.model)
    if not model_speedups:
        print(f"FAIL: aifs_speedups has no speed-ups for {args.model}")
        return 1
    anemoi_version = version("anemoi-models")
    print(
        f"==> {args.model} on anemoi-models {anemoi_version}, {args.steps} steps from {args.date}"
    )

    torch.set_default_device("cuda")
    cls = getattr(px, args.model)
    pipeline._model_ifs_through_mirrors(cls)
    package = Package(args.package) if args.package else cls.load_default_package()
    members = "seed" in inspect.signature(cls.load_model).parameters
    model = cls.load_model(package, seed=args.seed) if members else cls.load_model(package)
    model = model.to("cuda")
    model.eval()
    ic = model.input_coords()
    x0, c0 = fetch_data(
        pipeline.resolve_data_source("ifs"),
        time=np.array([np.datetime64(args.date)]),
        variable=ic["variable"],
        lead_time=ic["lead_time"],
        device="cuda",
    )

    def mean_move(a, b) -> float:
        return float(np.abs(a - b).mean())

    stock = []
    _, stock_time = rollout(model, x0, c0, args.steps, first_t2m=stock)
    print(f"    stock: {stock_time:.3f}s per step")
    if not members:
        rollout(model, x0, c0, 1, first_t2m=stock)
        chance, spread = "two stock runs", mean_move(stock[0], stock[1])

    def within_chance(name: str, moved: float, step_time: float) -> bool:
        share = moved / spread if spread else float("inf")
        print(
            f"    + {name}: {step_time:.3f}s per step; after one step 2 m temperature moves "
            f"{moved:.4f} K, {share:.0%} of the {spread:.4f} K between {chance}"
        )
        if share > LIMITS[chance]:
            print(f"FAIL: {name} moves the forecast further than chance does")
            return False
        return True

    (first, *rest) = model_speedups
    if not first.patch(model):
        print(f"FAIL: {first.name} does not apply to this model")
        return 1
    first_t2m = []
    reference, step_time = rollout(model, x0, c0, args.steps, first_t2m=first_t2m)
    repeat, _ = rollout(model, x0, c0, args.steps)
    if repeat != reference:
        print(f"FAIL: two runs with {first.name} differ, so nothing can be checked bit for bit")
        return 1
    if members:
        member = []
        model.seed = args.seed + 1
        rollout(model, x0, c0, 1, first_t2m=member)
        model.seed = args.seed
        chance, spread = "two members", mean_move(member[0], first_t2m[0])
    if first.exact:
        print(f"    + {first.name}: {step_time:.3f}s per step, reproducible")
    elif within_chance(first.name, mean_move(first_t2m[0], stock[0]), step_time):
        print("      and reproducible")
    else:
        return 1

    for speedup in rest:
        if not speedup.patch(model):
            print(f"    {speedup.name}: does not apply to this model, skipped")
            continue
        t2m = []
        hashes, step_time = rollout(model, x0, c0, args.steps, first_t2m=t2m)
        if speedup.exact:
            if hashes != reference:
                step = next(
                    i for i, (a, b) in enumerate(zip(reference, hashes, strict=True)) if a != b
                )
                print(f"FAIL: {speedup.name} changes the forecast (first at step {step})")
                return 1
            print(f"    + {speedup.name}: {step_time:.3f}s per step, bit for bit identical")
            continue
        if not within_chance(speedup.name, mean_move(t2m[0], first_t2m[0]), step_time):
            return 1
        # What any later exact one is matched against.
        reference, first_t2m = hashes, t2m

    print(f"    {stock_time / step_time:.2f}x the stock model's speed")
    verified = speedups.VERIFIED.get(args.model, set())
    if anemoi_version in verified:
        print(f"PASS: already verified for {args.model} on anemoi-models {anemoi_version}")
    else:
        print(
            f"PASS: add {anemoi_version!r} to aifs_speedups.VERIFIED[{args.model!r}] "
            "to apply the speed-ups in forecasts"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

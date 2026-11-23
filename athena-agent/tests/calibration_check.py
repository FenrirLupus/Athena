"""Calibration: the estimate must converge on what the provider actually bills.

The mock is deliberately dishonest — it bills 1.7x the raw estimate — because that is what real tokenizers do
to `len/4`. If the factor converges to ~1.7 and the calibrated estimate lands within 10 % of the bill, the
budget is finally working in the units the window is measured in.
"""
import copy
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from context import calibration as cal_mod
from context import stack
from providers.mock import MockProvider
from server.channels import ChannelManager
from tools import registry as treg

TRUE_RATIO = 1.7
cfg = copy.deepcopy(loader.load())
cfg.setdefault("agents", {})["idle_sleep_minutes"] = 0
cfg["metrics"] = {"enabled": True, "usd_per_mtok": 1.0, "sample_resources": False}
checks = {}


class Dishonest(MockProvider):
    """Bills what a real tokenizer would bill, not what len/4 predicts.

    The raw count is computed with the *same* convention the estimator uses (chars/4 plus the per-message
    overhead), so the only difference being taught is the tokenizer ratio itself. Billing a different
    convention would make the factor converge on something that is partly a bug in this test.
    """

    def call(self, messages, **kwargs):
        result = super().call(messages, **kwargs)
        # the tokenizer ratio applies to CONTENT; the per-message framing cost is fixed, so it must not be
        # multiplied — scaling it too would make the factor absorb a constant and overshoot on long prompts
        prompt = max(1, sum(int(stack.est_tokens(str(m.get("content") or "")) * TRUE_RATIO) + 8
                            for m in messages))
        completion = int((result.get("usage") or {}).get("completion_tokens") or 1)
        result["usage"] = {"prompt_tokens": prompt, "completion_tokens": completion,
                           "total_tokens": prompt + completion}
        return result


with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles"
    root.mkdir()
    mgr = ChannelManager(cfg, Dishonest(), treg.default_registry(cfg), root)
    mgr.create("cal")

    checks["starts_neutral"] = cal_mod.factor_for(root / "cal") == 1.0

    for i in range(8):
        mgr.submit("cal", f"turn {i}", wait_s=30)

    stored = cal_mod.load(root / "cal")
    factor = float(stored.get("factor") or 1.0)
    print(f"factor after 8 turns: {factor} (true ratio {TRUE_RATIO}), samples={stored.get('samples')}")
    checks["converged"] = abs(factor - TRUE_RATIO) / TRUE_RATIO < 0.10
    checks["persisted"] = (root / "cal" / "metrics" / "calibration.json").exists()

    # the calibrated estimate should now match the bill within 10 %
    history = mgr.channel("cal", ensure=True).session.history()
    messages, report = stack.build("instructions here", root / "cal", history, "hello", cfg,
                                   factor=factor)
    est = report["prompt_tokens_est"]
    raw = report["est_raw"]
    # what the mock will bill for exactly these messages: content*ratio plus fixed framing per message
    billed = sum(int(stack.est_tokens(str(m.get("content") or "")) * TRUE_RATIO) + 8 for m in messages)
    error = abs(est - billed) / billed
    print(f"raw={raw} calibrated={est} billed~{billed} error={error:.1%}")
    checks["estimate_within_10pct"] = error < 0.10
    checks["signal_is_raw"] = raw < est          # the raw estimate stays uncalibrated for the next sample

    # a tiny prompt teaches nothing and must not move the factor
    before = float(cal_mod.load(root / "cal").get("factor"))
    cal_mod.update(root / "cal", 10, 400, "mock:mock-1")
    after = float(cal_mod.load(root / "cal").get("factor"))
    checks["ignores_tiny_prompts"] = before == after

    # a corrupt file falls back to neutral instead of breaking a turn
    (root / "cal" / "metrics" / "calibration.json").write_text("{not json", encoding="utf-8")
    checks["survives_corruption"] = cal_mod.factor_for(root / "cal") == 1.0

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

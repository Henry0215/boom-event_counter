#!/usr/bin/env python3
"""
Analyze the new SPEC2017 log set under:
  - baseline
  - fast_trans_normal
  - fast_trans_addi
  - fast_trans_sab
  - fast_trans_spec

This script follows the same baseline-vs-optimized metric mapping style as
spec07_logs/analyze_spec07.py and writes outputs to plots_spec2017_newset/.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

HAS_PLOTTING = True
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:
    HAS_PLOTTING = False

SCRIPT_DIR = Path(__file__).resolve().parent

LOG_DIRS = {
    "baseline": SCRIPT_DIR / "baseline",
    "normal": SCRIPT_DIR / "fast_trans_normal",
    "addi": SCRIPT_DIR / "fast_trans_addi",
    "sab": SCRIPT_DIR / "fast_trans_sab",
    "spec": SCRIPT_DIR / "fast_trans_spec",
}

VARIANT_LABELS = {
    "baseline": "Baseline",
    "normal": "CMAP",
    "addi": "CMAP+ADDI",
    "sab": "CMAP+ADDI+SAB",
    "spec": "CMAP+ADDI+SAB+Spec",
}

OPT_VARIANTS = ["normal", "addi", "sab", "spec"]

COLORS = {
    "normal": "#1f77b4",
    "addi": "#ff7f0e",
    "sab": "#2ca02c",
    "spec": "#9467bd",
}

# Baseline (standard BOOM counters)
BL_CYCLES = 0
BL_INSTS = 1
BL_EXE_LD = 31
BL_EXE_ST = 32
BL_DTLB_VALID = 33
BL_DTLB_MISS = 34
BL_DCACHE_VALID = 36
BL_DCACHE_NACK = 37
BL_DCACHE_REQ = 38
BL_L2_TLB_MISS = 57
BL_MINI_EXCEPTION = 61

# Optimized (custom event counters)
OPT_CYCLES = 0
OPT_INSTS = 1
OPT_EXE_LD = 2
OPT_EXE_ST = 3
OPT_DTLB_VALID = 4
OPT_DTLB_MISS = 5
OPT_DCACHE_VALID = 7
OPT_DCACHE_NACK = 8
OPT_DCACHE_REQ = 9
OPT_COMMIT_LD = 10
OPT_COMMIT_ST = 11
OPT_L2_TLB_MISS = 12
OPT_MINI_EXCEPTION = 15
OPT_CMAP_FAST_TRANS = 16
OPT_CMAP_LD_UPDATE = 17
OPT_CMAP_DEC_FAST_TRANS = 20
OPT_CMAP_VALID_NOT_SAME = 21
OPT_CMAP_ADDI_UPDATE = 22
OPT_SAB_CONFLICT = 24
OPT_SPEC_WAKEUP_TOTAL = 26
OPT_SPEC_WAKEUP_RETRY = 27
OPT_SPEC_WAKEUP_WRONG = 28
OPT_SPEC_WAKEUP_WRONG_RETRY = 29

# Benchmarks to analyze (user-editable, spec07-style list)
# Use normalized names without numeric prefix.
BENCHMARKS_TO_ANALYZE = [
    "perlbench_r",
    "mcf_r",
    "cactuBSSN_r",
    "parest_r",
    "x264_r",
    "blender_r",
    "cam4_r",
    "deepsjeng_r",
    "leela_r",
    # "nab_r",
    # "exchange2_r",
    "roms_r",
    "xz_r",
]


# Metric list (spec07-style): centralized definitions for change plots
# (file_name, title, y_label, extractor)
METRIC_CHANGE_PLOTS = [
    ("1_ipc_change.png", "SPEC2017 IPC Change vs Baseline (Higher = Better)", "IPC Change vs Baseline (%)", "get_ipc"),
    ("2_cycles_change.png", "SPEC2017 Cycles Change vs Baseline (Lower = Better)", "Cycles Change vs Baseline (%)", "get_cycles"),
    ("3_dtlb_miss_change.png", "SPEC2017 DTLB Miss Change vs Baseline (Lower = Better)", "DTLB Miss Change vs Baseline (%)", "get_dtlb_miss"),
    ("3a_dtlb_valid_change.png", "SPEC2017 DTLB Valid Access Change vs Baseline", "DTLB Access Change vs Baseline (%)", "get_dtlb_valid"),
    ("3b_l2_tlb_miss_change.png", "SPEC2017 L2 DTLB Miss Change vs Baseline (Lower = Better)", "L2 DTLB Miss Change vs Baseline (%)", "get_l2_tlb_miss"),
    ("4_dcache_nack_change.png", "SPEC2017 D-Cache Nack Change vs Baseline (Lower = Better)", "D-Cache Nack Change vs Baseline (%)", "get_dcache_nack"),
    ("4a_dcache_valid_change.png", "SPEC2017 D-Cache Valid Access Change vs Baseline", "D-Cache Access Change vs Baseline (%)", "get_dcache_valid"),
    ("4b_dcache_req_change.png", "SPEC2017 D-Cache L2 Request Change vs Baseline", "D-Cache L2 Request Change vs Baseline (%)", "get_dcache_req"),
    ("5_exe_ld_change.png", "SPEC2017 Execute Load Count Change vs Baseline", "Execute Load Change vs Baseline (%)", "get_exe_ld"),
    ("6_exe_st_change.png", "SPEC2017 Execute Store Count Change vs Baseline", "Execute Store Change vs Baseline (%)", "get_exe_st"),
    ("7_mini_exception_change.png", "SPEC2017 Mini Exception Change vs Baseline (Lower = Better)", "Mini Exception Change vs Baseline (%)", "get_mini_exception"),
]


def parse_log(path: Path) -> dict[int, float]:
    events: dict[int, float] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            typ = obj.get("type", "")
            m = re.match(r"event\s+(\d+)", typ)
            if m:
                val = obj.get("value")
                if isinstance(val, (int, float)):
                    events[int(m.group(1))] = float(val)
    return events


def normalize_bench_name(filename: str) -> str:
    m = re.match(r"^\d+\.(.+)$", filename)
    return m.group(1) if m else filename


def canonical_bench_name(name: str) -> str:
    n = normalize_bench_name(name)
    if n.endswith("_r"):
        n = n[:-2]
    return n.lower()


def load_spec07_benchmark_set(spec07_analyze_path: Path) -> set[str]:
    """Parse BENCHMARKS list from spec07 analyze script and return canonical names."""
    if not spec07_analyze_path.exists():
        print(f"[WARN] spec07 analyze script not found: {spec07_analyze_path}")
        return set()

    text = spec07_analyze_path.read_text(encoding="utf-8")
    m = re.search(r"BENCHMARKS\s*=\s*\[(.*?)\]", text, flags=re.S)
    if not m:
        print(f"[WARN] BENCHMARKS list not found in: {spec07_analyze_path}")
        return set()

    body = m.group(1)
    names = re.findall(r"['\"]([^'\"]+)['\"]", body)
    return {canonical_bench_name(n) for n in names}


def load_allow_benchmark_set(program_file: Path) -> set[str]:
    """Load canonical benchmark names from a program file like pro17.txt."""
    if not program_file.exists():
        print(f"[WARN] Program file not found, skip allow-list filter: {program_file}")
        return set()

    allow = set()
    with program_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            bench = line.split("/", 1)[0].strip()
            if bench:
                allow.add(canonical_bench_name(bench))
    return allow


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def geomean_ratios(values: list[float]) -> float | None:
    positive = [v for v in values if v > 0]
    if not positive:
        return None
    return math.exp(sum(math.log(v) for v in positive) / len(positive))


def list_benchmarks(
    reference_dir: Path,
    baseline_dir: Path,
    exclude_canonical: set[str],
    benchmark_list: list[str],
) -> list[str]:
    benches = []
    for bench in benchmark_list:
        # Support both "526.blender_r" and "blender_r" file naming.
        candidates = []
        p_direct = reference_dir / bench
        if p_direct.is_file():
            candidates.append(p_direct)
        candidates.extend(sorted(reference_dir.glob(f"*.{bench}")))
        candidates = [p for p in candidates if p.is_file() and not p.name.endswith("_origin")]

        if not candidates:
            print(f"[INFO] Skip benchmark (missing in spec): {bench}")
            continue

        p = candidates[0]
        if len(candidates) > 1:
            print(f"[WARN] Multiple files matched {bench}, use: {p.name}")

        c_name = canonical_bench_name(bench)
        if c_name in exclude_canonical:
            print(f"[INFO] Skip benchmark (overlap with spec07): {bench}")
            continue

        # Use fast_trans_spec as canonical sample set and require baseline counterpart.
        ev_spec = parse_log(p)
        spec_cycles = ev_spec.get(OPT_CYCLES)
        spec_insts = ev_spec.get(OPT_INSTS)
        if spec_cycles is None or spec_insts is None or spec_cycles <= 0:
            print(f"[INFO] Skip benchmark (invalid in spec): {bench}")
            continue

        bpath = baseline_dir / p.name
        if not bpath.exists():
            print(f"[INFO] Skip benchmark (missing in baseline): {bench}")
            continue

        ev_bl = parse_log(bpath)
        bl_cycles = ev_bl.get(BL_CYCLES)
        bl_insts = ev_bl.get(BL_INSTS)
        if bl_cycles is None or bl_insts is None or bl_cycles <= 0:
            print(f"[INFO] Skip benchmark (invalid in baseline): {bench}")
            continue

        benches.append(p.name)
    return benches


def load_all_data(benchmarks: list[str]) -> dict[str, dict[str, dict[int, float]]]:
    data: dict[str, dict[str, dict[int, float]]] = {}
    for variant, dir_path in LOG_DIRS.items():
        data[variant] = {}
        if not dir_path.is_dir():
            print(f"[WARN] Missing variant dir: {dir_path}")
            for bench in benchmarks:
                data[variant][bench] = {}
            continue

        for bench in benchmarks:
            p = dir_path / bench
            if not p.exists():
                print(f"[WARN] Missing file: {p}")
                data[variant][bench] = {}
                continue
            data[variant][bench] = parse_log(p)
    return data


def get_metric(data: dict, variant: str, bench: str, eid: int) -> float | None:
    return data.get(variant, {}).get(bench, {}).get(eid)


def get_ipc(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        cycles = get_metric(data, variant, bench, BL_CYCLES)
        insts = get_metric(data, variant, bench, BL_INSTS)
    else:
        cycles = get_metric(data, variant, bench, OPT_CYCLES)
        insts = get_metric(data, variant, bench, OPT_INSTS)
    if cycles is None or insts is None or cycles <= 0:
        return None
    return insts / cycles


def get_cycles(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_CYCLES if variant == "baseline" else OPT_CYCLES)


def get_dtlb_miss(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_DTLB_MISS if variant == "baseline" else OPT_DTLB_MISS)


def get_dtlb_valid(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_DTLB_VALID if variant == "baseline" else OPT_DTLB_VALID)


def get_dcache_nack(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_DCACHE_NACK if variant == "baseline" else OPT_DCACHE_NACK)


def get_dcache_valid(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_DCACHE_VALID if variant == "baseline" else OPT_DCACHE_VALID)


def get_dcache_req(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_DCACHE_REQ if variant == "baseline" else OPT_DCACHE_REQ)


def get_dcache_miss_rate(data: dict, variant: str, bench: str) -> float | None:
    """D-Cache miss-rate proxy: L2 request count / D-Cache valid access count."""
    dcache_req = get_dcache_req(data, variant, bench)
    dcache_valid = get_dcache_valid(data, variant, bench)
    if dcache_req is not None and dcache_valid is not None and dcache_valid > 0:
        return dcache_req / dcache_valid * 100
    return None


def get_l2_tlb_miss(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_L2_TLB_MISS if variant == "baseline" else OPT_L2_TLB_MISS)


def get_exe_ld(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        return get_metric(data, variant, bench, BL_EXE_LD)
    exe_ld = get_metric(data, variant, bench, OPT_EXE_LD)
    fast = get_metric(data, variant, bench, OPT_CMAP_FAST_TRANS)
    if exe_ld is None or fast is None:
        return exe_ld
    return exe_ld + fast


def get_exe_st(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_EXE_ST if variant == "baseline" else OPT_EXE_ST)


def get_mini_exception(data: dict, variant: str, bench: str) -> float | None:
    return get_metric(data, variant, bench, BL_MINI_EXCEPTION if variant == "baseline" else OPT_MINI_EXCEPTION)


def get_cmap_fast_trans(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        return None
    return get_metric(data, variant, bench, OPT_CMAP_FAST_TRANS)


def get_spec_wakeup_total(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        return None
    return get_metric(data, variant, bench, OPT_SPEC_WAKEUP_TOTAL)


def get_spec_wakeup_retry(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        return None
    return get_metric(data, variant, bench, OPT_SPEC_WAKEUP_RETRY)


def get_spec_wakeup_wrong(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        return None
    return get_metric(data, variant, bench, OPT_SPEC_WAKEUP_WRONG)


def get_spec_wakeup_wrong_retry(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        return None
    return get_metric(data, variant, bench, OPT_SPEC_WAKEUP_WRONG_RETRY)


def percent_change(bl: float | None, cur: float | None) -> float | None:
    if bl is None or cur is None or bl == 0:
        return None
    return (cur - bl) / bl * 100.0


def plot_change(data: dict, benches: list[str], output_path: Path, title: str, y_label: str, extractor) -> None:
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    width = 0.15
    offsets = [i - len(OPT_VARIANTS) / 2 + 0.5 for i in range(len(OPT_VARIANTS))]
    labels = [normalize_bench_name(b) for b in benches]

    for i, v in enumerate(OPT_VARIANTS):
        vals = []
        for b in benches:
            vals.append(percent_change(extractor(data, "baseline", b), extractor(data, v, b)) or 0.0)
        xpos = [xi + offsets[i] * width for xi in x]
        ax.bar(xpos, vals, width, label=VARIANT_LABELS[v], color=COLORS[v], edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    ax.set_title(title, fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.axhline(y=0, color="black", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_commit_ld_st_ratio(data: dict, benches: list[str], output_path: Path, variant: str = "spec") -> None:
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    ld_p, st_p = [], []
    for b in benches:
        if variant == "baseline":
            insts = get_metric(data, variant, b, BL_INSTS)
            ld = get_metric(data, variant, b, BL_EXE_LD)
            st = get_metric(data, variant, b, BL_EXE_ST)
        else:
            insts = get_metric(data, variant, b, OPT_INSTS)
            ld = get_metric(data, variant, b, OPT_COMMIT_LD)
            st = get_metric(data, variant, b, OPT_COMMIT_ST)
        if insts is None or insts <= 0 or ld is None or st is None:
            ld_p.append(0.0)
            st_p.append(0.0)
        else:
            ld_p.append(ld / insts * 100.0)
            st_p.append(st / insts * 100.0)

    order = sorted(range(len(benches)), key=lambda i: -(ld_p[i] + st_p[i]))
    b_sorted = [benches[i] for i in order]
    labels = [normalize_bench_name(b) for b in b_sorted]
    ld_sorted = [ld_p[i] for i in order]
    st_sorted = [st_p[i] for i in order]

    fig, ax = plt.subplots(figsize=(16, 6))
    x = list(range(len(b_sorted)))
    w = 0.6
    ax.bar(x, ld_sorted, w, label="Committed Load", color="#4C72B0", edgecolor="white", linewidth=0.5)
    ax.bar(x, st_sorted, w, bottom=ld_sorted, label="Committed Store", color="#DD8452", edgecolor="white", linewidth=0.5)

    avg_ld = mean(ld_p)
    avg_mem = mean([l + s for l, s in zip(ld_p, st_p)])
    ax.axhline(y=avg_ld, color="#4C72B0", linestyle="--", linewidth=1.2, alpha=0.7, label=f"Avg Load = {avg_ld:.1f}%")
    ax.axhline(y=avg_mem, color="#C44E52", linestyle="--", linewidth=1.2, alpha=0.7, label=f"Avg Mem = {avg_mem:.1f}%")

    for i, (ld, st) in enumerate(zip(ld_sorted, st_sorted)):
        ax.text(i, ld + st + 0.4, f"{ld + st:.1f}%", ha="center", va="bottom", fontsize=7.5, fontweight="bold")

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Committed Instruction Ratio (%)", fontsize=12)
    ax.set_title(f"SPEC2017 Committed Load/Store Ratio ({VARIANT_LABELS[variant]})", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_cmap_fast_trans(data: dict, benches: list[str], output_path: Path) -> None:
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    labels = [normalize_bench_name(b) for b in benches]
    width = 0.15
    offsets = [i - len(OPT_VARIANTS) / 2 + 0.5 for i in range(len(OPT_VARIANTS))]

    for i, v in enumerate(OPT_VARIANTS):
        vals = []
        for b in benches:
            val = get_cmap_fast_trans(data, v, b)
            vals.append((val / 1e6) if val is not None else 0.0)
        xpos = [xi + offsets[i] * width for xi in x]
        ax.bar(xpos, vals, width, label=VARIANT_LABELS[v], color=COLORS[v], edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("CMAP Fast Translate Count (Millions)", fontsize=12)
    ax.set_title("SPEC2017 CMAP Fast Translate Count at Dispatch", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_cmap_detail(data: dict, benches: list[str], output_path: Path, variant: str = "addi") -> None:
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    labels = [normalize_bench_name(b) for b in benches]
    w = 0.6

    fast = []
    ld_upd = []
    addi_upd = []
    dec_fast = []
    not_same = []
    for b in benches:
        ev = data.get(variant, {}).get(b, {})
        fast.append(ev.get(OPT_CMAP_FAST_TRANS, 0.0) / 1e6)
        ld_upd.append(ev.get(OPT_CMAP_LD_UPDATE, 0.0) / 1e6)
        addi_upd.append(ev.get(OPT_CMAP_ADDI_UPDATE, 0.0) / 1e6)
        dec_fast.append(ev.get(OPT_CMAP_DEC_FAST_TRANS, 0.0) / 1e6)
        not_same.append(ev.get(OPT_CMAP_VALID_NOT_SAME, 0.0) / 1e6)

    b1 = fast
    b2 = [a + b for a, b in zip(fast, ld_upd)]
    b3 = [a + b for a, b in zip(b2, addi_upd)]
    b4 = [a + b for a, b in zip(b3, dec_fast)]

    ax.bar(x, fast, w, label="CMAP Fast Translate", color="#1f77b4")
    ax.bar(x, ld_upd, w, bottom=b1, label="CMAP Load Update", color="#ff7f0e")
    ax.bar(x, addi_upd, w, bottom=b2, label="CMAP ADDI Update", color="#2ca02c")
    ax.bar(x, dec_fast, w, bottom=b3, label="CMAP Decode Fast Trans", color="#d62728")
    ax.bar(x, not_same, w, bottom=b4, label="CMAP Valid Not Same Page", color="#9467bd")

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Count (Millions)", fontsize=12)
    ax.set_title(f"CMAP Detail Breakdown ({VARIANT_LABELS[variant]})", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_cmap_accel_ratio(data: dict, benches: list[str], output_path: Path) -> None:
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    labels = [normalize_bench_name(b) for b in benches]
    width = 0.15
    offsets = [i - len(OPT_VARIANTS) / 2 + 0.5 for i in range(len(OPT_VARIANTS))]

    for i, v in enumerate(OPT_VARIANTS):
        vals = []
        for b in benches:
            fast = get_cmap_fast_trans(data, v, b)
            exe_ld = get_metric(data, v, b, OPT_EXE_LD)
            if fast is None or exe_ld is None:
                vals.append(0.0)
            else:
                total = fast + exe_ld
                vals.append(fast / total * 100.0 if total > 0 else 0.0)
        xpos = [xi + offsets[i] * width for xi in x]
        ax.bar(xpos, vals, width, label=VARIANT_LABELS[v], color=COLORS[v], edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("CMAP Accelerated Load Ratio (%)", fontsize=12)
    ax.set_title("SPEC2017 CMAP Accelerated Load Ratio (cmap_fast_translate / total_exe_ld)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_sab_conflict(data: dict, benches: list[str], output_path: Path) -> None:
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    sab_variants = ["sab", "spec"]
    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    labels = [normalize_bench_name(b) for b in benches]
    width = 0.25
    offsets = [i - len(sab_variants) / 2 + 0.5 for i in range(len(sab_variants))]

    for i, v in enumerate(sab_variants):
        vals = []
        for b in benches:
            c = get_metric(data, v, b, OPT_SAB_CONFLICT)
            vals.append((c / 1e3) if c is not None else 0.0)
        xpos = [xi + offsets[i] * width for xi in x]
        ax.bar(xpos, vals, width, label=VARIANT_LABELS[v], color=COLORS[v], edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("SAB Conflict Count (Thousands)", fontsize=12)
    ax.set_title("SPEC2017 SAB Store-Load Conflict Count", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_dcache_miss_rate(data: dict, benches: list[str], output_path: Path) -> None:
    """Bar chart: D-Cache miss-rate proxy difference vs baseline (percentage points)."""
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    labels = [normalize_bench_name(b) for b in benches]
    width = 0.15
    offsets = [i - len(OPT_VARIANTS) / 2 + 0.5 for i in range(len(OPT_VARIANTS))]

    for i, v in enumerate(OPT_VARIANTS):
        diffs = []
        for b in benches:
            bl_rate = get_dcache_miss_rate(data, "baseline", b)
            opt_rate = get_dcache_miss_rate(data, v, b)
            diffs.append((opt_rate - bl_rate) if (bl_rate is not None and opt_rate is not None) else 0.0)
        xpos = [xi + offsets[i] * width for xi in x]
        ax.bar(xpos, diffs, width, label=VARIANT_LABELS[v], color=COLORS[v], edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("D-Cache Miss Rate Difference vs Baseline (pp)", fontsize=12)
    ax.set_title("SPEC2017 D-Cache Miss Rate Proxy Difference vs Baseline", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.axhline(y=0, color="black", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_mini_excpt_absolute(data: dict, benches: list[str], output_path: Path) -> None:
    """Bar chart: mini_exception absolute count for baseline and optimized variants."""
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    all_variants = ["baseline"] + OPT_VARIANTS
    variant_colors = {"baseline": "#7f7f7f", **COLORS}

    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    labels = [normalize_bench_name(b) for b in benches]
    width = 0.13
    offsets = [i - len(all_variants) / 2 + 0.5 for i in range(len(all_variants))]

    for i, v in enumerate(all_variants):
        vals = []
        for b in benches:
            val = get_mini_exception(data, v, b)
            vals.append(val / 1e6 if val is not None else 0.0)
        xpos = [xi + offsets[i] * width for xi in x]
        ax.bar(xpos, vals, width, label=VARIANT_LABELS[v], color=variant_colors[v], edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Mini Exception Count (Millions)", fontsize=12)
    ax.set_title("SPEC2017 Mini Exception Absolute Count", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_sab_conflict_ratio(data: dict, benches: list[str], output_path: Path) -> None:
    """Bar chart: SAB conflict ratio = conflict / (conflict + mini_exception) for sab/spec variants."""
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    sab_variants = ["sab", "spec"]
    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    labels = [normalize_bench_name(b) for b in benches]
    width = 0.25
    offsets = [i - len(sab_variants) / 2 + 0.5 for i in range(len(sab_variants))]

    for i, v in enumerate(sab_variants):
        ratios = []
        for b in benches:
            conflict = get_metric(data, v, b, OPT_SAB_CONFLICT)
            excpt = get_mini_exception(data, v, b)
            if conflict is not None and excpt is not None and (conflict + excpt) > 0:
                ratios.append(conflict / (conflict + excpt) * 100)
            else:
                ratios.append(0.0)
        xpos = [xi + offsets[i] * width for xi in x]
        ax.bar(xpos, ratios, width, label=VARIANT_LABELS[v], color=COLORS[v], edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("SAB Conflict Ratio in (Conflict + Exceptions) (%)", fontsize=12)
    ax.set_title("SPEC2017 SAB Runtime Ratio: conflict / (conflict + mini_exception)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_spec_wakeup_ratios(data: dict, benches: list[str], output_path: Path) -> None:
    """Bar chart: spec wakeup retry/error ratios for spec variant."""
    if not HAS_PLOTTING:
        print(f"[WARN] matplotlib not available, skip {output_path.name}")
        return

    fig, ax = plt.subplots(figsize=(20, 7))
    x = list(range(len(benches)))
    labels = [normalize_bench_name(b) for b in benches]
    width = 0.2
    offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]

    retry_ratios = []
    wrong_in_wrong_total_ratios = []
    wrong_in_total_ratios = []
    wrong_retry_ratios = []

    for b in benches:
        total = get_spec_wakeup_total(data, "spec", b)
        retry = get_spec_wakeup_retry(data, "spec", b)
        wrong = get_spec_wakeup_wrong(data, "spec", b)
        wrong_retry = get_spec_wakeup_wrong_retry(data, "spec", b)

        retry_ratios.append((retry / total * 100) if (total and retry is not None and total > 0) else 0.0)
        wrong_in_wrong_total_ratios.append((wrong_retry / wrong * 100) if (wrong and wrong_retry is not None and wrong > 0) else 0.0)
        wrong_in_total_ratios.append((wrong / total * 100) if (total and wrong is not None and total > 0) else 0.0)
        wrong_retry_ratios.append((wrong_retry / retry * 100) if (retry and wrong_retry is not None and retry > 0) else 0.0)

    ax.bar([xi + offsets[0] for xi in x], retry_ratios, width, label="Retry / Total Wakeup (%)", color="#1f77b4", edgecolor="white", linewidth=0.5)
    ax.bar([xi + offsets[1] for xi in x], wrong_in_wrong_total_ratios, width, label="Wrong Retry / Total Wrong Wakeup (%)", color="#ff7f0e", edgecolor="white", linewidth=0.5)
    ax.bar([xi + offsets[2] for xi in x], wrong_in_total_ratios, width, label="Wrong / Total Wakeup (%)", color="#d62728", edgecolor="white", linewidth=0.5)
    ax.bar([xi + offsets[3] for xi in x], wrong_retry_ratios, width, label="Wrong Retry / Retry (%)", color="#9467bd", edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Ratio (%)", fontsize=12)
    ax.set_title("SPEC2017 Spec Wakeup Retry/Error Ratios (Spec Variant)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def print_summary(data: dict, benches: list[str]) -> None:
    print("\n" + "=" * 120)
    print("SPEC2017 New Logset: IPC Change vs Baseline (%)")
    print("=" * 120)

    header = f"{'Benchmark':<18}"
    for v in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[v]:>18}"
    print(header)

    gm: dict[str, list[float]] = {k: [] for k in OPT_VARIANTS}
    for b in benches:
        row = f"{normalize_bench_name(b):<18}"
        bl_ipc = get_ipc(data, "baseline", b)
        for v in OPT_VARIANTS:
            cur = get_ipc(data, v, b)
            chg = percent_change(bl_ipc, cur)
            if chg is None:
                row += f"  {'N/A':>18}"
            else:
                row += f"  {chg:+17.2f}%"
                if bl_ipc is not None and cur is not None and bl_ipc > 0:
                    gm[v].append(cur / bl_ipc)
        print(row)

    row = f"{'GEOMEAN':<18}"
    for v in OPT_VARIANTS:
        g = geomean_ratios(gm[v])
        row += f"  {((g - 1) * 100):+17.2f}%" if g is not None else f"  {'N/A':>18}"
    print(row)


def write_summary_csv(data: dict, benches: list[str], out_csv: Path) -> None:
    fields = [
        "benchmark", "norm_benchmark",
        "baseline_ipc",
        "normal_ipc_change_pct", "addi_ipc_change_pct", "sab_ipc_change_pct", "spec_ipc_change_pct",
        "baseline_cycles", "spec_cycles_change_pct",
        "baseline_dtlb_miss", "spec_dtlb_miss_change_pct",
        "baseline_dcache_nack", "spec_dcache_nack_change_pct",
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for b in benches:
            bl_ipc = get_ipc(data, "baseline", b)
            n_ipc = get_ipc(data, "normal", b)
            a_ipc = get_ipc(data, "addi", b)
            s_ipc = get_ipc(data, "sab", b)
            p_ipc = get_ipc(data, "spec", b)

            bl_c = get_cycles(data, "baseline", b)
            p_c = get_cycles(data, "spec", b)

            bl_dm = get_dtlb_miss(data, "baseline", b)
            p_dm = get_dtlb_miss(data, "spec", b)

            bl_dn = get_dcache_nack(data, "baseline", b)
            p_dn = get_dcache_nack(data, "spec", b)

            w.writerow({
                "benchmark": b,
                "norm_benchmark": normalize_bench_name(b),
                "baseline_ipc": f"{bl_ipc:.6f}" if bl_ipc is not None else "",
                "normal_ipc_change_pct": f"{percent_change(bl_ipc, n_ipc):.4f}" if percent_change(bl_ipc, n_ipc) is not None else "",
                "addi_ipc_change_pct": f"{percent_change(bl_ipc, a_ipc):.4f}" if percent_change(bl_ipc, a_ipc) is not None else "",
                "sab_ipc_change_pct": f"{percent_change(bl_ipc, s_ipc):.4f}" if percent_change(bl_ipc, s_ipc) is not None else "",
                "spec_ipc_change_pct": f"{percent_change(bl_ipc, p_ipc):.4f}" if percent_change(bl_ipc, p_ipc) is not None else "",
                "baseline_cycles": f"{bl_c:.0f}" if bl_c is not None else "",
                "spec_cycles_change_pct": f"{percent_change(bl_c, p_c):.4f}" if percent_change(bl_c, p_c) is not None else "",
                "baseline_dtlb_miss": f"{bl_dm:.0f}" if bl_dm is not None else "",
                "spec_dtlb_miss_change_pct": f"{percent_change(bl_dm, p_dm):.4f}" if percent_change(bl_dm, p_dm) is not None else "",
                "baseline_dcache_nack": f"{bl_dn:.0f}" if bl_dn is not None else "",
                "spec_dcache_nack_change_pct": f"{percent_change(bl_dn, p_dn):.4f}" if percent_change(bl_dn, p_dn) is not None else "",
            })


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze new SPEC2017 logs under baseline/fast_trans_* directories.")
    p.add_argument("--output-dir", default=str(SCRIPT_DIR / "plots_spec2017_newset"), help="Output directory.")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    ref = LOG_DIRS["spec"]
    if not ref.is_dir():
        raise SystemExit(f"Reference directory not found: {ref}")

    baseline_dir = LOG_DIRS["baseline"]
    if not baseline_dir.is_dir():
        raise SystemExit(f"Baseline directory not found: {baseline_dir}")

    spec07_benchmarks = load_spec07_benchmark_set(SCRIPT_DIR.parent / "spec07_logs" / "analyze_spec07.py")
    if spec07_benchmarks:
        print(f"Loaded {len(spec07_benchmarks)} benchmark names from spec07 for overlap filtering.")

    benches = list_benchmarks(ref, baseline_dir, spec07_benchmarks, BENCHMARKS_TO_ANALYZE)
    if not benches:
        raise SystemExit(f"No valid benchmark files found in {ref}")

    print("Detected benchmarks (by fast_trans_spec):")
    print("  " + ", ".join(normalize_bench_name(b) for b in benches))

    data = load_all_data(benches)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print_summary(data, benches)
    write_summary_csv(data, benches, out_dir / "spec2017_newset_summary.csv")

    plot_commit_ld_st_ratio(data, benches, out_dir / "0_commit_ld_st_ratio_spec.png", variant="spec")

    extractor_map = {
        "get_ipc": get_ipc,
        "get_cycles": get_cycles,
        "get_dtlb_miss": get_dtlb_miss,
        "get_dtlb_valid": get_dtlb_valid,
        "get_l2_tlb_miss": get_l2_tlb_miss,
        "get_dcache_nack": get_dcache_nack,
        "get_dcache_valid": get_dcache_valid,
        "get_dcache_req": get_dcache_req,
        "get_exe_ld": get_exe_ld,
        "get_exe_st": get_exe_st,
        "get_mini_exception": get_mini_exception,
    }

    for filename, title, y_label, extractor_name in METRIC_CHANGE_PLOTS:
        plot_change(
            data,
            benches,
            out_dir / filename,
            title,
            y_label,
            extractor_map[extractor_name],
        )

    plot_dcache_miss_rate(data, benches, out_dir / "4c_dcache_miss_rate.png")
    plot_mini_excpt_absolute(data, benches, out_dir / "5a_mini_excpt_absolute.png")
    plot_cmap_fast_trans(data, benches, out_dir / "8_cmap_fast_translate.png")
    plot_cmap_detail(data, benches, out_dir / "9_cmap_detail_addi.png", variant="addi")
    plot_cmap_accel_ratio(data, benches, out_dir / "10_cmap_accel_ratio.png")
    plot_sab_conflict(data, benches, out_dir / "11_sab_conflict.png")
    plot_sab_conflict_ratio(data, benches, out_dir / "11a_sab_conflict_ratio.png")
    plot_spec_wakeup_ratios(data, benches, out_dir / "12_spec_wakeup_ratios.png")

    print(f"\n[OK] CSV  : {out_dir / 'spec2017_newset_summary.csv'}")
    if HAS_PLOTTING:
        print(f"[OK] Plots: {out_dir}")
    else:
        print("[WARN] matplotlib 不可用，本次仅输出文本和 CSV。")


if __name__ == "__main__":
    main()

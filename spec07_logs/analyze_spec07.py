#!/usr/bin/env python3
"""
SPEC2006 Benchmark Performance Analysis Script
Compares Tracing_Map/SAB optimization variants against baseline BOOM v3.

Log directories:
  - baseline_logs      : Vanilla BOOM v3 (standard 64 HPM counters)
  - fast_trans_no_addi : Tracing_Map without ADDI optimization
  - fast_trans_addi    : Tracing_Map with ADDI optimization
  - fast_trans_sab     : Tracing_Map + ADDI + SAB
  - fast_disp_8way     : Tracing_Map + ADDI + SAB + dis_tracing_map_override (8-way)
    - fast_trans_spec    : Tracing_Map + ADDI + SAB + dis_tracing_map_override + spec_ld_wakeup
    - fast_trans_spec_new_counter : Spec variant with extra wakeup counters (events 26-29)

Event mapping:
  Baseline (standard BOOM HPM):
    event  0 = cycles
    event  1 = committed instructions
    event 34 = DTLB miss (total)
    event 37 = D-cache nack
    event 61 = mini_exception

  Optimized (custom event_counters):
    event  0 = cycles
    event  1 = committed instructions
    event  2 = execute ld count
    event  3 = execute st count
    event  4 = dtlb valid access
    event  5 = dtlb miss
    event  6 = dtlb miss (perf)
    event  7 = dcache valid access
    event  8 = dcache nack
    event  9 = dcache acquire
    event 10 = commit ld
    event 11 = commit st
    event 12 = L2 TLB miss
    event 13 = misalign excpt
    event 14 = page fault
    event 15 = mini_exception
    event 16 = tracing_map_fast_translate (dispatch)
    event 17 = tracing_map_load_update
    event 19 = tracing_map_valid_set
    event 20 = tracing_map_decode_fast_trans
    event 21 = tracing_map_valid_not_same_page
    event 22 = tracing_map_addi_update
    event 23 = tracing_map_same_cycle_overflow
    event 24 = sab_conflict
    event 25 = rollback_cycles
    event 26 = spec wakeup total count
    event 27 = spec wakeup from retry path count
    event 28 = wrong spec wakeup count
    event 29 = wrong spec wakeup from retry path count
"""

import json
import os
import sys
import re
from pathlib import Path
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

# ─── Configuration ───────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent

# Each variant maps to a list of directories.
# Multiple directories for the same variant will be averaged.
# Use _find_variant_dirs() helper to auto-detect numbered run directories,
# e.g. "baseline_logs", "baseline_logs_2", "baseline_logs_3" ...
def _find_variant_dirs(base_name: str) -> list[Path]:
    """Find all directories matching base_name, base_name_2, base_name_3, etc."""
    dirs = []
    base = SCRIPT_DIR / base_name
    if base.is_dir():
        dirs.append(base)
    # Look for _2, _3, ... suffixed directories
    for p in sorted(SCRIPT_DIR.iterdir()):
        if p.is_dir() and p != base:
            m = re.match(re.escape(base_name) + r'_(\d+)$', p.name)
            if m:
                dirs.append(p)
    return dirs if dirs else [base]  # fallback to base even if missing (will warn later)


def _find_variant_dirs_prefer(preferred_base: str, fallback_base: str) -> list[Path]:
    """Prefer preferred_base runs if present; otherwise use fallback_base runs."""
    preferred = [d for d in _find_variant_dirs(preferred_base) if d.is_dir()]
    if preferred:
        return preferred
    return _find_variant_dirs(fallback_base)

LOG_DIRS = {
    "baseline":      _find_variant_dirs("baseline_logs"),
    "no_addi":       _find_variant_dirs("fast_trans_no_addi"),
    "addi":          _find_variant_dirs("fast_trans_addi"),
    "sab":           _find_variant_dirs("fast_trans_sab"),
    # "disp_8way":     _find_variant_dirs("fast_disp_8way"),
    "spec":          _find_variant_dirs_prefer("fast_trans_spec_new_counter", "fast_trans_spec"),
}

# Friendly labels for plotting
VARIANT_LABELS = {
    "baseline":   "Baseline",
    "no_addi":    "Tracing_Map",
    "addi":       "Tracing_Map+ADDI",
    "sab":        "Tracing_Map+ADDI+SAB",
    # "disp_8way":  "SAB+Disp8",
    "spec":       "Tracing_Map+ADDI+SAB+Spec",
}

# Optimized variants (all except baseline)
# OPT_VARIANTS = ["no_addi", "addi", "sab", "disp_8way", "spec"]
OPT_VARIANTS = ["no_addi", "addi", "sab", "spec"]

# BENCHMARKS = [
#     "astar", "bwaves", "bzip2", "cactusADM", "calculix", "dealII",
#     "gcc", "gobmk", "h264ref", "hmmer", "lbm", "leslie3d",
#     "libquantum", "milc", "namd", "omnetpp", "povray", "sjeng",
#     "xalancbmk",
# ]

BENCHMARKS = [
    "astar", "bwaves", "bzip2", "calculix", "dealII",
    "gcc", "gobmk", "h264ref", "lbm", "leslie3d",
    "libquantum", "milc", "namd", "omnetpp", "povray", "sjeng",
    "xalancbmk",
]

# Event index mapping
# Baseline
BL_CYCLES      = 0
BL_INSTS       = 1
BL_EXE_LD      = 31
BL_EXE_ST      = 32
BL_DTLB_VALID  = 33
BL_DTLB_MISS   = 34
BL_DCACHE_VALID = 36
BL_DCACHE_NACK = 37
BL_DCACHE_REQ  = 38
BL_L2_TLB_MISS = 57
BL_MINI_EXCPT  = 61

# Optimized
OPT_CYCLES          = 0
OPT_INSTS           = 1
OPT_EXE_LD          = 2
OPT_EXE_ST          = 3
OPT_DTLB_VALID      = 4
OPT_DTLB_MISS       = 5
OPT_DCACHE_VALID    = 7
OPT_DCACHE_NACK     = 8
OPT_DCACHE_REQ      = 9
OPT_COMMIT_LD       = 10
OPT_COMMIT_ST       = 11
OPT_L2_TLB_MISS     = 12
OPT_MINI_EXCPT      = 15
OPT_Tracing_Map_FAST_TRANS = 16
OPT_Tracing_Map_LD_UPDATE  = 17
OPT_Tracing_Map_VALID_SET  = 19
OPT_Tracing_Map_DEC_TRANS  = 20
OPT_Tracing_Map_NOT_SAME   = 21
OPT_Tracing_Map_ADDI_UPD   = 22
OPT_Tracing_Map_OVERFLOW   = 23
OPT_SAB_CONFLICT    = 24
OPT_ROLLBACK        = 25
OPT_SPEC_WAKEUP_TOTAL       = 26
OPT_SPEC_WAKEUP_RETRY       = 27
OPT_SPEC_WAKEUP_WRONG       = 28
OPT_SPEC_WAKEUP_WRONG_RETRY = 29


# ─── Log Parsing ─────────────────────────────────────────────────────────────

def parse_log(filepath: Path) -> dict[int, int]:
    """Parse a benchmark log and return {event_id: value}."""
    events = {}
    with open(filepath, "r") as f:
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
                eid = int(m.group(1))
                events[eid] = obj["value"]
    return events


def load_all_data():
    """Load events from all variants and benchmarks, averaging across multiple runs.
    Returns nested dict: data[variant][benchmark] = {event_id: averaged_value}
    Also prints the number of runs detected per variant.
    """
    data = {}
    for variant, dirpaths in LOG_DIRS.items():
        data[variant] = {}
        valid_dirs = [d for d in dirpaths if d.is_dir()]
        num_runs = len(valid_dirs)
        if num_runs == 0:
            print(f"[WARN] No directories found for variant '{variant}': {dirpaths}")
            for bench in BENCHMARKS:
                data[variant][bench] = {}
            continue

        print(f"  {variant}: {num_runs} run(s) from {[d.name for d in valid_dirs]}")

        for bench in BENCHMARKS:
            # Collect events from each run
            all_run_events: list[dict[int, int]] = []
            for dirpath in valid_dirs:
                logfile = dirpath / f"{bench}_no_loop_predictor.log"
                if logfile.exists():
                    ev = parse_log(logfile)
                    if ev:  # only include non-empty results
                        all_run_events.append(ev)
                else:
                    logfile = dirpath / f"{bench}"
                    if logfile.exists():
                        ev = parse_log(logfile)
                        if ev:
                            all_run_events.append(ev)
                    else:
                        print(f"[WARN] Missing: {logfile}")

            if not all_run_events:
                data[variant][bench] = {}
                continue

            # Average across runs: collect all event_ids present in any run
            all_eids = set()
            for ev in all_run_events:
                all_eids.update(ev.keys())

            averaged = {}
            for eid in all_eids:
                values = [ev[eid] for ev in all_run_events if eid in ev]
                if values:
                    averaged[eid] = sum(values) / len(values)

            data[variant][bench] = averaged
    return data


# ─── Metric Extraction ──────────────────────────────────────────────────────

def get_metric(data: dict, variant: str, bench: str, event_id: int) -> float | None:
    """Get a metric value. Returns None if missing."""
    ev = data.get(variant, {}).get(bench, {})
    val = ev.get(event_id)
    return float(val) if val is not None else None


def get_ipc(data: dict, variant: str, bench: str) -> float | None:
    """Compute IPC for a variant/benchmark."""
    if variant == "baseline":
        cycles = get_metric(data, variant, bench, BL_CYCLES)
        insts = get_metric(data, variant, bench, BL_INSTS)
    else:
        cycles = get_metric(data, variant, bench, OPT_CYCLES)
        insts = get_metric(data, variant, bench, OPT_INSTS)
    if cycles and insts and cycles > 0:
        return insts / cycles
    return None


def get_cycles(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_CYCLES if variant == "baseline" else OPT_CYCLES
    return get_metric(data, variant, bench, eid)


def get_dtlb_miss(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_DTLB_MISS if variant == "baseline" else OPT_DTLB_MISS
    return get_metric(data, variant, bench, eid)


def get_dtlb_valid(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_DTLB_VALID if variant == "baseline" else OPT_DTLB_VALID
    return get_metric(data, variant, bench, eid)


def get_dcache_nack(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_DCACHE_NACK if variant == "baseline" else OPT_DCACHE_NACK
    return get_metric(data, variant, bench, eid)


def get_dcache_valid(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_DCACHE_VALID if variant == "baseline" else OPT_DCACHE_VALID
    return get_metric(data, variant, bench, eid)


def get_mini_excpt(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_MINI_EXCPT if variant == "baseline" else OPT_MINI_EXCPT
    return get_metric(data, variant, bench, eid)


def get_dcache_req(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_DCACHE_REQ if variant == "baseline" else OPT_DCACHE_REQ
    return get_metric(data, variant, bench, eid)


def get_dcache_miss_rate(data: dict, variant: str, bench: str) -> float | None:
    """D-Cache miss rate proxy: L2 request count / D-Cache valid access count."""
    dcache_req = get_dcache_req(data, variant, bench)
    dcache_valid = get_dcache_valid(data, variant, bench)
    if dcache_req is not None and dcache_valid is not None and dcache_valid > 0:
        return dcache_req / dcache_valid * 100
    return None


def get_l2_tlb_miss(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_L2_TLB_MISS if variant == "baseline" else OPT_L2_TLB_MISS
    return get_metric(data, variant, bench, eid)


def get_exe_ld(data: dict, variant: str, bench: str) -> float | None:
    """For optimized variants, total exe_ld = IQ→AGU loads + Tracing_Map bypass loads."""
    if variant == "baseline":
        return get_metric(data, variant, bench, BL_EXE_LD)
    exe_ld = get_metric(data, variant, bench, OPT_EXE_LD)
    tracing_map_fast = get_metric(data, variant, bench, OPT_Tracing_Map_FAST_TRANS)
    if exe_ld is not None and tracing_map_fast is not None:
        return exe_ld + tracing_map_fast
    return exe_ld


def get_exe_st(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_EXE_ST if variant == "baseline" else OPT_EXE_ST
    return get_metric(data, variant, bench, eid)


def get_tracing_map_fast_trans(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        return None
    return get_metric(data, variant, bench, OPT_Tracing_Map_FAST_TRANS)


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


# ─── Visualization ───────────────────────────────────────────────────────────

COLORS = {
    "no_addi":    "#1f77b4",
    "addi":       "#ff7f0e",
    "sab":        "#2ca02c",
    # "disp_8way":  "#d62728",
    "spec":       "#9467bd",
}

GROUPED_BAR_STYLE = dict(edgecolor='black', linewidth=0.5)


def _plot_grouped_bar(ax, x, offsets, width, index, values, label, color):
    if len(values) != len(x):
        return
    ax.bar(x + offsets[index] * width, values, width, label=label, color=color, **GROUPED_BAR_STYLE)


def plot_ipc_change(data: dict, output_dir: Path):
    """Bar chart: IPC percentage change vs. baseline for each benchmark."""
    fig, ax = plt.subplots(figsize=(10, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5
    ipc_geomean_changes = {}

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        speedup_ratios = []
        for bench in BENCHMARKS:
            bl_ipc = get_ipc(data, "baseline", bench)
            opt_ipc = get_ipc(data, var, bench)
            if bl_ipc and opt_ipc:
                changes.append((opt_ipc - bl_ipc) / bl_ipc * 100)
                speedup_ratios.append(opt_ipc / bl_ipc)
            else:
                changes.append(0)
            _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])
        if speedup_ratios:
            gm_ratio = np.prod(speedup_ratios) ** (1.0 / len(speedup_ratios))
            ipc_geomean_changes[var] = (gm_ratio - 1) * 100

    # Draw one dashed line per variant at its IPC geometric-mean change.
    if ipc_geomean_changes:
        for var, gm_change in sorted(ipc_geomean_changes.items(), key=lambda item: item[1], reverse=True):
            ax.axhline(y=gm_change, color=COLORS[var], linestyle='--', linewidth=1.1, alpha=0.9)

        # Show IPC geometric-mean improvements in the top-right corner.
        y_top = 0.97
        y_step = 0.055
        ordered_vars = [var for var in OPT_VARIANTS if var in ipc_geomean_changes]
        for idx, var in enumerate(ordered_vars):
            gm_change = ipc_geomean_changes[var]
            ax.text(
                0.985,
                y_top - idx * y_step,
                f"{VARIANT_LABELS[var]}: {gm_change:+.2f}%",
                color=COLORS[var],
                fontsize=9,
                ha='right',
                va='top',
                transform=ax.transAxes,
                bbox=dict(facecolor='white', edgecolor='none', alpha=0.7, pad=1.5),
            )

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("IPC Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 IPC Change vs Baseline (Higher = Better)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "1_ipc_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 1_ipc_change.png saved")


def plot_cycles_change(data: dict, output_dir: Path):
    """Bar chart: Cycles percentage change vs. baseline (negative = fewer cycles = better)."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_cycles(data, "baseline", bench)
            opt = get_cycles(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
            _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Cycles Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 Cycles Change vs Baseline (Lower = Better)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "2_cycles_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 2_cycles_change.png saved")


def plot_dtlb_miss_change(data: dict, output_dir: Path):
    """Bar chart: DTLB miss count percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_dtlb_miss(data, "baseline", bench)
            opt = get_dtlb_miss(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
            _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("DTLB Miss Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 DTLB Miss Change vs Baseline (Lower = Better)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "3_dtlb_miss_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 3_dtlb_miss_change.png saved")


def plot_dtlb_valid_change(data: dict, output_dir: Path):
    """Bar chart: DTLB valid access count percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_dtlb_valid(data, "baseline", bench)
            opt = get_dtlb_valid(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
        _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("DTLB Access Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 DTLB Valid Access Change vs Baseline", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "3a_dtlb_valid_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 3a_dtlb_valid_change.png saved")


def plot_dcache_nack_change(data: dict, output_dir: Path):
    """Bar chart: D-cache nack percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_dcache_nack(data, "baseline", bench)
            opt = get_dcache_nack(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
            _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("D-Cache Nack Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 D-Cache Nack Change vs Baseline (Lower = Better)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "4_dcache_nack_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 4_dcache_nack_change.png saved")


def plot_dcache_valid_change(data: dict, output_dir: Path):
    """Bar chart: D-cache valid access count percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_dcache_valid(data, "baseline", bench)
            opt = get_dcache_valid(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
            _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("D-Cache Access Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 D-Cache Valid Access Change vs Baseline", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "4a_dcache_valid_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 4a_dcache_valid_change.png saved")


def plot_dcache_req_change(data: dict, output_dir: Path):
    """Bar chart: D-cache L2 request count percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_dcache_req(data, "baseline", bench)
            opt = get_dcache_req(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
        _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("D-Cache L2 Request Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 D-Cache L2 Request Change vs Baseline", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "4b_dcache_req_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 4b_dcache_req_change.png saved")


def plot_dcache_miss_rate(data: dict, output_dir: Path):
    """Bar chart: D-Cache miss rate proxy difference vs baseline (percentage points)."""

    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        diffs = []
        for bench in BENCHMARKS:
            bl_rate = get_dcache_miss_rate(data, "baseline", bench)
            opt_rate = get_dcache_miss_rate(data, var, bench)
            if bl_rate is not None and opt_rate is not None:
                diffs.append(opt_rate - bl_rate)
            else:
                diffs.append(0)
        _plot_grouped_bar(ax, x, offsets, width, i, diffs, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("D-Cache Miss Rate Difference vs Baseline (pp)", fontsize=12)
    ax.set_title("SPEC2006 D-Cache Miss Rate Proxy Difference vs Baseline", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "4c_dcache_miss_rate.png", dpi=150)
    plt.close(fig)
    print("[OK] 4c_dcache_miss_rate.png saved")


def plot_l2_tlb_miss_change(data: dict, output_dir: Path):
    """Bar chart: L2 DTLB miss percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_l2_tlb_miss(data, "baseline", bench)
            opt = get_l2_tlb_miss(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
            _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("L2 DTLB Miss Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 L2 DTLB Miss Change vs Baseline (Lower = Better)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "3b_l2_tlb_miss_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 3b_l2_tlb_miss_change.png saved")


def plot_exe_ld_change(data: dict, output_dir: Path):
    """Bar chart: Execute load count percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_exe_ld(data, "baseline", bench)
            opt = get_exe_ld(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
        _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Execute Load Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 Execute Load Count Change vs Baseline", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "10_exe_ld_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 10_exe_ld_change.png saved")


def plot_exe_st_change(data: dict, output_dir: Path):
    """Bar chart: Execute store count percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_exe_st(data, "baseline", bench)
            opt = get_exe_st(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
        _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Execute Store Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 Execute Store Count Change vs Baseline", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "11_exe_st_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 11_exe_st_change.png saved")


def plot_mini_excpt_change(data: dict, output_dir: Path):
    """Bar chart: mini_exception percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl = get_mini_excpt(data, "baseline", bench)
            opt = get_mini_excpt(data, var, bench)
            if bl and opt and bl > 0:
                changes.append((opt - bl) / bl * 100)
            else:
                changes.append(0)
        _plot_grouped_bar(ax, x, offsets, width, i, changes, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Mini Exception Change vs Baseline (%)", fontsize=12)
    ax.set_title("SPEC2006 Mini Exception Change vs Baseline (Lower = Better)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "5_mini_excpt_change.png", dpi=150)
    plt.close(fig)
    print("[OK] 5_mini_excpt_change.png saved")


def plot_mini_excpt_absolute(data: dict, output_dir: Path):
    """Bar chart: mini_exception absolute count for baseline and optimized variants."""
    all_variants = ["baseline"] + OPT_VARIANTS
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.13
    offsets = np.arange(len(all_variants)) - len(all_variants) / 2 + 0.5

    variant_colors = {"baseline": "#7f7f7f", **COLORS}

    for i, var in enumerate(all_variants):
        counts_m = []
        for bench in BENCHMARKS:
            val = get_mini_excpt(data, var, bench)
            counts_m.append(val / 1e6 if val else 0)  # in millions
        _plot_grouped_bar(ax, x, offsets, width, i, counts_m, VARIANT_LABELS[var], variant_colors[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Mini Exception Count (Millions)", fontsize=12)
    ax.set_title("SPEC2006 Mini Exception Absolute Count", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "5a_mini_excpt_absolute.png", dpi=150)
    plt.close(fig)
    print("[OK] 5a_mini_excpt_absolute.png saved")


def plot_tracing_map_fast_trans(data: dict, output_dir: Path):
    """Bar chart: Tracing_Map fast translate count (absolute) for each optimized variant."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        counts = []
        for bench in BENCHMARKS:
            val = get_tracing_map_fast_trans(data, var, bench)
            counts.append(val / 1e6 if val else 0)  # in millions
        _plot_grouped_bar(ax, x, offsets, width, i, counts, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Tracing_Map Fast Translate Count (Millions)", fontsize=12)
    ax.set_title("SPEC2006 Tracing_Map Fast Translate Count at Dispatch", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "6_tracing_map_fast_translate.png", dpi=150)
    plt.close(fig)
    print("[OK] 6_tracing_map_fast_translate.png saved")


def plot_tracing_map_detail(data: dict, output_dir: Path):
    """Stacked bar: Tracing_Map detail breakdown for 'addi' variant
       (fast_trans, load_update, addi_update, decode_fast_trans, valid_not_same_page)."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.6

    # Pick the addi variant for detailed Tracing_Map breakdown
    var = "addi"

    fast_trans = []
    ld_update = []
    addi_upd = []
    dec_trans = []
    not_same = []

    for bench in BENCHMARKS:
        ev = data.get(var, {}).get(bench, {})
        fast_trans.append(ev.get(OPT_Tracing_Map_FAST_TRANS, 0) / 1e6)
        ld_update.append(ev.get(OPT_Tracing_Map_LD_UPDATE, 0) / 1e6)
        addi_upd.append(ev.get(OPT_Tracing_Map_ADDI_UPD, 0) / 1e6)
        dec_trans.append(ev.get(OPT_Tracing_Map_DEC_TRANS, 0) / 1e6)
        not_same.append(ev.get(OPT_Tracing_Map_NOT_SAME, 0) / 1e6)

    fast_trans = np.array(fast_trans)
    ld_update = np.array(ld_update)
    addi_upd = np.array(addi_upd)
    dec_trans = np.array(dec_trans)
    not_same = np.array(not_same)

    ax.bar(x, fast_trans, width, label='Tracing_Map Fast Translate', color='#1f77b4')
    ax.bar(x, ld_update, width, bottom=fast_trans, label='Tracing_Map Load Update', color='#ff7f0e')
    ax.bar(x, addi_upd, width, bottom=fast_trans + ld_update, label='Tracing_Map ADDI Update', color='#2ca02c')
    ax.bar(x, dec_trans, width, bottom=fast_trans + ld_update + addi_upd,
           label='Tracing_Map Decode Fast Trans', color='#d62728')
    ax.bar(x, not_same, width, bottom=fast_trans + ld_update + addi_upd + dec_trans,
           label='Tracing_Map Valid Not Same Page', color='#9467bd')

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Count (Millions)", fontsize=12)
    ax.set_title(f"Tracing_Map Detail Breakdown ({VARIANT_LABELS[var]})", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "7_tracing_map_detail_addi.png", dpi=150)
    plt.close(fig)
    print("[OK] 7_tracing_map_detail_addi.png saved")


def plot_tracing_map_accel_ratio(data: dict, output_dir: Path):
    """Bar chart: Tracing_Map accelerated load ratio (tracing_map_fast_translate / total_exe_ld) per benchmark.
    total_exe_ld = exe_is_ld + tracing_map_fast_translate, since Tracing_Map bypass loads skip IQ→AGU."""
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        ratios = []
        for bench in BENCHMARKS:
            fast = get_tracing_map_fast_trans(data, var, bench)
            exe_ld = get_metric(data, var, bench, OPT_EXE_LD)
            if fast is not None and exe_ld is not None:
                total_ld = exe_ld + fast
                ratios.append(fast / total_ld * 100 if total_ld > 0 else 0)
            else:
                ratios.append(0)
        _plot_grouped_bar(ax, x, offsets, width, i, ratios, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Tracing_Map Accelerated Load Ratio (%)", fontsize=12)
    ax.set_title("SPEC2006 Tracing_Map Accelerated Load Ratio (tracing_map_fast_translate / total_exe_ld)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "9_tracing_map_accel_ratio.png", dpi=150)
    plt.close(fig)
    print("[OK] 9_tracing_map_accel_ratio.png saved")


def plot_sab_conflict(data: dict, output_dir: Path):
    """Bar chart: SAB conflict count for variants that have SAB."""
    # sab_variants = ["sab", "disp_8way", "spec"]
    sab_variants = ["sab", "spec"]
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.25
    offsets = np.arange(len(sab_variants)) - len(sab_variants) / 2 + 0.5

    for i, var in enumerate(sab_variants):
        counts = []
        for bench in BENCHMARKS:
            val = get_metric(data, var, bench, OPT_SAB_CONFLICT)
            counts.append(val / 1e3 if val else 0)  # in thousands
        _plot_grouped_bar(ax, x, offsets, width, i, counts, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("SAB Conflict Count (Thousands)", fontsize=12)
    ax.set_title("SPEC2006 SAB Store-Load Conflict Count", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "8_sab_conflict.png", dpi=150)
    plt.close(fig)
    print("[OK] 8_sab_conflict.png saved")


def plot_sab_conflict_ratio(data: dict, output_dir: Path):
    """Bar chart: SAB conflict ratio = conflict / (conflict + mini_exception) for sab/spec variants."""
    sab_variants = ["sab", "spec"]
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.25
    offsets = np.arange(len(sab_variants)) - len(sab_variants) / 2 + 0.5

    for i, var in enumerate(sab_variants):
        ratios = []
        for bench in BENCHMARKS:
            conflict = get_metric(data, var, bench, OPT_SAB_CONFLICT)
            excpt = get_mini_excpt(data, var, bench)
            if conflict is not None and excpt is not None and (conflict + excpt) > 0:
                ratios.append(conflict / (conflict + excpt) * 100)
            else:
                ratios.append(0)
        _plot_grouped_bar(ax, x, offsets, width, i, ratios, VARIANT_LABELS[var], COLORS[var])

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("SAB Conflict Ratio in (Conflict + Exceptions) (%)", fontsize=12)
    ax.set_title("SPEC2006 SAB Runtime Ratio: conflict / (conflict + mini_exception)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "8a_sab_conflict_ratio.png", dpi=150)
    plt.close(fig)
    print("[OK] 8a_sab_conflict_ratio.png saved")


def plot_spec_wakeup_ratios(data: dict, output_dir: Path):
    """Bar chart: spec wakeup retry/error ratios for spec variant (grouped bars, not stacked)."""
    fig, ax = plt.subplots(figsize=(16, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.2
    offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]

    retry_ratios = []
    wrong_in_wrong_total_ratios = []
    wrong_in_total_ratios = []
    wrong_retry_ratios = []

    for bench in BENCHMARKS:
        total = get_spec_wakeup_total(data, "spec", bench)
        retry = get_spec_wakeup_retry(data, "spec", bench)
        wrong = get_spec_wakeup_wrong(data, "spec", bench)
        wrong_retry = get_spec_wakeup_wrong_retry(data, "spec", bench)

        retry_ratios.append((retry / total * 100) if (total is not None and retry is not None and total > 0) else 0)
        wrong_in_wrong_total_ratios.append((wrong_retry / wrong * 100) if (wrong is not None and wrong_retry is not None and wrong > 0) else 0)
        wrong_in_total_ratios.append((wrong / total * 100) if (total is not None and wrong is not None and total > 0) else 0)
        wrong_retry_ratios.append((wrong_retry / retry * 100) if (retry is not None and wrong_retry is not None and retry > 0) else 0)

    ax.bar(x + offsets[0], retry_ratios, width, label='Retry / Total Wakeup (%)', color='#1f77b4', edgecolor='black', linewidth=0.5)
    ax.bar(x + offsets[1], wrong_in_wrong_total_ratios, width, label='Wrong Retry / Total Wrong Wakeup (%)', color='#ff7f0e', edgecolor='black', linewidth=0.5)
    ax.bar(x + offsets[2], wrong_in_total_ratios, width, label='Wrong / Total Wakeup (%)', color='#d62728', edgecolor='black', linewidth=0.5)
    ax.bar(x + offsets[3], wrong_retry_ratios, width, label='Wrong Retry / Retry (%)', color='#9467bd', edgecolor='black', linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Ratio (%)", fontsize=12)
    ax.set_title("SPEC2006 Spec Wakeup Retry/Error Ratios (Spec Variant)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "12_spec_wakeup_ratios.png", dpi=150)
    plt.close(fig)
    print("[OK] 12_spec_wakeup_ratios.png saved")


# ─── Text Summary ────────────────────────────────────────────────────────────

def print_summary(data: dict):
    """Print a text summary table."""
    print("\n" + "=" * 120)
    print("SPEC2006 Performance Summary")
    print("=" * 120)

    # Header
    header = f"{'Benchmark':<14}"
    for var in ["baseline"] + OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(f"\n--- IPC ---")
    print(header)
    ipc_values = {var: [] for var in ["baseline"] + OPT_VARIANTS}
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        for var in ["baseline"] + OPT_VARIANTS:
            ipc = get_ipc(data, var, bench)
            if ipc:
                ipc_values[var].append(ipc)
            row += f"  {ipc:12.4f}" if ipc else f"  {'N/A':>12}"
        print(row)
    # IPC geometric mean (absolute)
    row = f"{'GEOMEAN':<14}"
    for var in ["baseline"] + OPT_VARIANTS:
        if ipc_values[var]:
            gm = np.prod(ipc_values[var]) ** (1.0 / len(ipc_values[var]))
            row += f"  {gm:12.4f}"
        else:
            row += f"  {'N/A':>12}"
    print(row)

    # IPC change
    print(f"\n--- IPC Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    geomeans = {var: [] for var in OPT_VARIANTS}
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl_ipc = get_ipc(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt_ipc = get_ipc(data, var, bench)
            if bl_ipc and opt_ipc:
                change = (opt_ipc - bl_ipc) / bl_ipc * 100
                row += f"  {change:+11.2f}%"
                geomeans[var].append(opt_ipc / bl_ipc)
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # Geometric mean
    row = f"{'GEOMEAN':<14}"
    for var in OPT_VARIANTS:
        if geomeans[var]:
            gm = np.prod(geomeans[var]) ** (1.0 / len(geomeans[var]))
            row += f"  {(gm - 1) * 100:+11.2f}%"
        else:
            row += f"  {'N/A':>12}"
    print(row)

    # DTLB miss change
    print(f"\n--- DTLB Miss Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_dtlb_miss(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_dtlb_miss(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # DTLB valid access change
    print(f"\n--- DTLB Valid Access Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_dtlb_valid(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_dtlb_valid(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # D-cache nack change
    print(f"\n--- D-Cache Nack Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_dcache_nack(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_dcache_nack(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # D-Cache valid access change
    print(f"\n--- D-Cache Valid Access Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_dcache_valid(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_dcache_valid(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # D-Cache L2 request change
    print(f"\n--- D-Cache L2 Request Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_dcache_req(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_dcache_req(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # D-Cache miss rate proxy difference vs baseline (percentage points)
    print(f"\n--- D-Cache Miss Rate Proxy Difference vs Baseline (pp) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    miss_rate_diff_values = {var: [] for var in OPT_VARIANTS}
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl_rate = get_dcache_miss_rate(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt_rate = get_dcache_miss_rate(data, var, bench)
            if bl_rate is not None and opt_rate is not None:
                diff = opt_rate - bl_rate
                row += f"  {diff:+11.2f}"
                miss_rate_diff_values[var].append(diff)
            else:
                row += f"  {'N/A':>12}"
        print(row)

    row = f"{'AVERAGE':<14}"
    for var in OPT_VARIANTS:
        if miss_rate_diff_values[var]:
            row += f"  {np.mean(miss_rate_diff_values[var]):+11.2f}"
        else:
            row += f"  {'N/A':>12}"
    print(row)

    # L2 DTLB miss change
    print(f"\n--- L2 DTLB Miss Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_l2_tlb_miss(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_l2_tlb_miss(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # Execute load change
    print(f"\n--- Execute Load Count Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_exe_ld(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_exe_ld(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # Execute store change
    print(f"\n--- Execute Store Count Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_exe_st(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_exe_st(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # Mini exception change
    print(f"\n--- Mini Exception Change vs Baseline (%) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        bl = get_mini_excpt(data, "baseline", bench)
        for var in OPT_VARIANTS:
            opt = get_mini_excpt(data, var, bench)
            if bl and opt and bl > 0:
                change = (opt - bl) / bl * 100
                row += f"  {change:+11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # Mini exception absolute count
    print(f"\n--- Mini Exception Absolute Count ---")
    header = f"{'Benchmark':<14}"
    for var in ["baseline"] + OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    abs_values = {var: [] for var in ["baseline"] + OPT_VARIANTS}
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        for var in ["baseline"] + OPT_VARIANTS:
            val = get_mini_excpt(data, var, bench)
            if val is not None:
                row += f"  {val:12,.0f}"
                abs_values[var].append(val)
            else:
                row += f"  {'N/A':>12}"
        print(row)

    row = f"{'AVERAGE':<14}"
    for var in ["baseline"] + OPT_VARIANTS:
        if abs_values[var]:
            row += f"  {np.mean(abs_values[var]):12,.0f}"
        else:
            row += f"  {'N/A':>12}"
    print(row)

    # Tracing_Map fast translate count (absolute)
    print(f"\n--- Tracing_Map Fast Translate Count (dispatch) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        for var in OPT_VARIANTS:
            val = get_tracing_map_fast_trans(data, var, bench)
            if val is not None:
                row += f"  {val:12,.0f}"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # Tracing_Map acceleration ratio: tracing_map_fast_translate / total_exe_ld
    # NOTE: exe_is_ld only counts loads through IQ→AGU path; Tracing_Map bypass loads
    #       go through retry path and are NOT counted in exe_is_ld.
    #       Total executed loads = exe_is_ld + tracing_map_fast_translate_cnt
    print(f"\n--- Tracing_Map Accelerated Load Ratio (tracing_map_fast_translate / total_exe_ld, %) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        for var in OPT_VARIANTS:
            fast = get_tracing_map_fast_trans(data, var, bench)
            exe_ld = get_metric(data, var, bench, OPT_EXE_LD)
            if fast is not None and exe_ld is not None:
                total_ld = exe_ld + fast  # total = IQ loads + Tracing_Map bypass loads
                if total_ld > 0:
                    ratio = fast / total_ld * 100
                    row += f"  {ratio:11.2f}%"
                else:
                    row += f"  {'N/A':>12}"
            else:
                row += f"  {'N/A':>12}"
        print(row)
    # Average
    row = f"{'AVERAGE':<14}"
    for var in OPT_VARIANTS:
        ratios = []
        for bench in BENCHMARKS:
            fast = get_tracing_map_fast_trans(data, var, bench)
            exe_ld = get_metric(data, var, bench, OPT_EXE_LD)
            if fast is not None and exe_ld is not None:
                total_ld = exe_ld + fast
                if total_ld > 0:
                    ratios.append(fast / total_ld * 100)
        if ratios:
            row += f"  {np.mean(ratios):11.2f}%"
        else:
            row += f"  {'N/A':>12}"
    print(row)

    # SAB conflict count (raw values)
    print(f"\n--- SAB Conflict Count ---")
    print(f"{'Benchmark':<14}  {'SAB方案':>12}  {'SPEC方案':>12}")

    sab_conflict_values = []
    spec_conflict_values = []
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        for var, values in [("sab", sab_conflict_values), ("spec", spec_conflict_values)]:
            conflict = get_metric(data, var, bench, OPT_SAB_CONFLICT)
            if conflict is not None:
                values.append(conflict)
                row += f"  {conflict:12.0f}"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    avg_row = f"{'AVERAGE':<14}"
    avg_row += f"  {np.mean(sab_conflict_values):12.0f}" if sab_conflict_values else f"  {'N/A':>12}"
    avg_row += f"  {np.mean(spec_conflict_values):12.0f}" if spec_conflict_values else f"  {'N/A':>12}"
    print(avg_row)

    # SAB runtime ratio: conflict / (conflict + mini_exception)
    print(f"\n--- SAB Runtime Ratio: conflict / (conflict + mini_exception) ---")
    print(f"{'Benchmark':<14}  {'SAB方案':>12}  {'SPEC方案':>12}")

    sab_ratio_values = []
    spec_ratio_values = []
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        for var, values in [("sab", sab_ratio_values), ("spec", spec_ratio_values)]:
            conflict = get_metric(data, var, bench, OPT_SAB_CONFLICT)
            excpt = get_mini_excpt(data, var, bench)
            if conflict is not None and excpt is not None and (conflict + excpt) > 0:
                ratio = conflict / (conflict + excpt) * 100
                values.append(ratio)
                row += f"  {ratio:11.2f}%"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    avg_row = f"{'AVERAGE':<14}"
    avg_row += f"  {np.mean(sab_ratio_values):11.2f}%" if sab_ratio_values else f"  {'N/A':>12}"
    avg_row += f"  {np.mean(spec_ratio_values):11.2f}%" if spec_ratio_values else f"  {'N/A':>12}"
    print(avg_row)

    # Spec wakeup retry/error ratios (spec variant, using new counters)
    print(f"\n--- Spec Wakeup Retry/Error Ratios (Spec Variant) ---")
    print(f"{'Benchmark':<14}  {'RetryPath占比':>12}  {'错误/总错误占比':>18}  {'错误Wakeup占总占比':>18}  {'Retry中错误占比':>14}")

    retry_total_values = []
    wrong_in_wrong_total_values = []
    wrong_in_total_values = []
    wrong_retry_values = []

    for bench in BENCHMARKS:
        total = get_spec_wakeup_total(data, "spec", bench)
        retry = get_spec_wakeup_retry(data, "spec", bench)
        wrong = get_spec_wakeup_wrong(data, "spec", bench)
        wrong_retry = get_spec_wakeup_wrong_retry(data, "spec", bench)

        retry_total = (retry / total * 100) if (total is not None and retry is not None and total > 0) else None
        wrong_in_wrong_total = (wrong_retry / wrong * 100) if (wrong is not None and wrong_retry is not None and wrong > 0) else None
        wrong_in_total = (wrong / total * 100) if (total is not None and wrong is not None and total > 0) else None
        wrong_retry_ratio = (wrong_retry / retry * 100) if (retry is not None and wrong_retry is not None and retry > 0) else None

        if retry_total is not None:
            retry_total_values.append(retry_total)
        if wrong_in_wrong_total is not None:
            wrong_in_wrong_total_values.append(wrong_in_wrong_total)
        if wrong_in_total is not None:
            wrong_in_total_values.append(wrong_in_total)
        if wrong_retry_ratio is not None:
            wrong_retry_values.append(wrong_retry_ratio)

        row = f"{bench:<14}"
        row += f"  {retry_total:11.2f}%" if retry_total is not None else f"  {'N/A':>12}"
        row += f"  {wrong_in_wrong_total:17.2f}%" if wrong_in_wrong_total is not None else f"  {'N/A':>18}"
        row += f"  {wrong_in_total:17.2f}%" if wrong_in_total is not None else f"  {'N/A':>18}"
        row += f"  {wrong_retry_ratio:13.2f}%" if wrong_retry_ratio is not None else f"  {'N/A':>14}"
        print(row)

    avg_row = f"{'AVERAGE':<14}"
    avg_row += f"  {np.mean(retry_total_values):11.2f}%" if retry_total_values else f"  {'N/A':>12}"
    avg_row += f"  {np.mean(wrong_in_wrong_total_values):17.2f}%" if wrong_in_wrong_total_values else f"  {'N/A':>18}"
    avg_row += f"  {np.mean(wrong_in_total_values):17.2f}%" if wrong_in_total_values else f"  {'N/A':>18}"
    avg_row += f"  {np.mean(wrong_retry_values):13.2f}%" if wrong_retry_values else f"  {'N/A':>14}"
    print(avg_row)

    print("\n" + "=" * 120)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("Loading SPEC2006 benchmark data...")
    data = load_all_data()

    output_dir = SCRIPT_DIR / "plots"
    output_dir.mkdir(exist_ok=True)

    print_summary(data)

    print("\nGenerating plots...")
    plot_ipc_change(data, output_dir)
    plot_cycles_change(data, output_dir)
    plot_dtlb_valid_change(data, output_dir)
    plot_dtlb_miss_change(data, output_dir)
    plot_dcache_valid_change(data, output_dir)
    plot_dcache_nack_change(data, output_dir)
    plot_dcache_req_change(data, output_dir)
    plot_dcache_miss_rate(data, output_dir)
    plot_l2_tlb_miss_change(data, output_dir)
    plot_exe_ld_change(data, output_dir)
    plot_exe_st_change(data, output_dir)
    plot_mini_excpt_change(data, output_dir)
    plot_mini_excpt_absolute(data, output_dir)
    plot_tracing_map_fast_trans(data, output_dir)
    plot_tracing_map_detail(data, output_dir)
    plot_tracing_map_accel_ratio(data, output_dir)
    plot_sab_conflict(data, output_dir)
    plot_sab_conflict_ratio(data, output_dir)
    plot_spec_wakeup_ratios(data, output_dir)

    print(f"\nAll plots saved to: {output_dir}")
    print("Done!")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
SPEC2006 Benchmark Performance Analysis Script
Compares CMAP/SAB optimization variants against baseline BOOM v3.

Log directories:
  - baseline_logs      : Vanilla BOOM v3 (standard 64 HPM counters)
  - fast_trans_no_addi : CMAP without ADDI optimization
  - fast_trans_addi    : CMAP with ADDI optimization
  - fast_trans_sab     : CMAP + ADDI + SAB
  - fast_disp_8way     : CMAP + ADDI + SAB + dis_cmap_override (8-way)
  - fast_trans_spec    : CMAP + ADDI + SAB + dis_cmap_override + spec_ld_wakeup

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
    event 16 = cmap_fast_translate (dispatch)
    event 17 = cmap_load_update
    event 19 = cmap_valid_set
    event 20 = cmap_decode_fast_trans
    event 21 = cmap_valid_not_same_page
    event 22 = cmap_addi_update
    event 23 = cmap_same_cycle_overflow
    event 24 = sab_conflict
    event 25 = rollback_cycles
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

LOG_DIRS = {
    "baseline":      _find_variant_dirs("baseline_logs"),
    "no_addi":       _find_variant_dirs("fast_trans_no_addi"),
    "addi":          _find_variant_dirs("fast_trans_addi"),
    "sab":           _find_variant_dirs("fast_trans_sab"),
    # "disp_8way":     _find_variant_dirs("fast_disp_8way"),
    "spec":          _find_variant_dirs("fast_trans_spec"),
}

# Friendly labels for plotting
VARIANT_LABELS = {
    "baseline":   "Baseline",
    "no_addi":    "CMAP",
    "addi":       "CMAP+ADDI",
    "sab":        "CMAP+ADDI+SAB",
    # "disp_8way":  "SAB+Disp8",
    "spec":       "CMAP+ADDI+SAB+Spec",
}

# Optimized variants (all except baseline)
# OPT_VARIANTS = ["no_addi", "addi", "sab", "disp_8way", "spec"]
OPT_VARIANTS = ["no_addi", "addi", "sab", "spec"]

BENCHMARKS = [
    "astar", "bwaves", "bzip2", "cactusADM", "calculix", "dealII",
    "gcc", "gobmk", "h264ref", "hmmer", "lbm", "leslie3d",
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
OPT_CMAP_FAST_TRANS = 16
OPT_CMAP_LD_UPDATE  = 17
OPT_CMAP_VALID_SET  = 19
OPT_CMAP_DEC_TRANS  = 20
OPT_CMAP_NOT_SAME   = 21
OPT_CMAP_ADDI_UPD   = 22
OPT_CMAP_OVERFLOW   = 23
OPT_SAB_CONFLICT    = 24
OPT_ROLLBACK        = 25


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


def get_l2_tlb_miss(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_L2_TLB_MISS if variant == "baseline" else OPT_L2_TLB_MISS
    return get_metric(data, variant, bench, eid)


def get_exe_ld(data: dict, variant: str, bench: str) -> float | None:
    """For optimized variants, total exe_ld = IQ→AGU loads + CMAP bypass loads."""
    if variant == "baseline":
        return get_metric(data, variant, bench, BL_EXE_LD)
    exe_ld = get_metric(data, variant, bench, OPT_EXE_LD)
    cmap_fast = get_metric(data, variant, bench, OPT_CMAP_FAST_TRANS)
    if exe_ld is not None and cmap_fast is not None:
        return exe_ld + cmap_fast
    return exe_ld


def get_exe_st(data: dict, variant: str, bench: str) -> float | None:
    eid = BL_EXE_ST if variant == "baseline" else OPT_EXE_ST
    return get_metric(data, variant, bench, eid)


def get_cmap_fast_trans(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        return None
    return get_metric(data, variant, bench, OPT_CMAP_FAST_TRANS)


# ─── Visualization ───────────────────────────────────────────────────────────

COLORS = {
    "no_addi":    "#1f77b4",
    "addi":       "#ff7f0e",
    "sab":        "#2ca02c",
    # "disp_8way":  "#d62728",
    "spec":       "#9467bd",
}


def plot_ipc_change(data: dict, output_dir: Path):
    """Bar chart: IPC percentage change vs. baseline for each benchmark."""
    fig, ax = plt.subplots(figsize=(20, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        changes = []
        for bench in BENCHMARKS:
            bl_ipc = get_ipc(data, "baseline", bench)
            opt_ipc = get_ipc(data, var, bench)
            if bl_ipc and opt_ipc:
                changes.append((opt_ipc - bl_ipc) / bl_ipc * 100)
            else:
                changes.append(0)
        bars = ax.bar(x + offsets[i] * width, changes, width,
                      label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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


def plot_l2_tlb_miss_change(data: dict, output_dir: Path):
    """Bar chart: L2 DTLB miss percentage change vs. baseline."""
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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
    fig, ax = plt.subplots(figsize=(20, 7))
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
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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


def plot_cmap_fast_trans(data: dict, output_dir: Path):
    """Bar chart: CMAP fast translate count (absolute) for each optimized variant."""
    fig, ax = plt.subplots(figsize=(20, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        counts = []
        for bench in BENCHMARKS:
            val = get_cmap_fast_trans(data, var, bench)
            counts.append(val / 1e6 if val else 0)  # in millions
        ax.bar(x + offsets[i] * width, counts, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("CMAP Fast Translate Count (Millions)", fontsize=12)
    ax.set_title("SPEC2006 CMAP Fast Translate Count at Dispatch", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "6_cmap_fast_translate.png", dpi=150)
    plt.close(fig)
    print("[OK] 6_cmap_fast_translate.png saved")


def plot_cmap_detail(data: dict, output_dir: Path):
    """Stacked bar: CMAP detail breakdown for 'addi' variant
       (fast_trans, load_update, addi_update, decode_fast_trans, valid_not_same_page)."""
    fig, ax = plt.subplots(figsize=(20, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.6

    # Pick the addi variant for detailed CMAP breakdown
    var = "addi"

    fast_trans = []
    ld_update = []
    addi_upd = []
    dec_trans = []
    not_same = []

    for bench in BENCHMARKS:
        ev = data.get(var, {}).get(bench, {})
        fast_trans.append(ev.get(OPT_CMAP_FAST_TRANS, 0) / 1e6)
        ld_update.append(ev.get(OPT_CMAP_LD_UPDATE, 0) / 1e6)
        addi_upd.append(ev.get(OPT_CMAP_ADDI_UPD, 0) / 1e6)
        dec_trans.append(ev.get(OPT_CMAP_DEC_TRANS, 0) / 1e6)
        not_same.append(ev.get(OPT_CMAP_NOT_SAME, 0) / 1e6)

    fast_trans = np.array(fast_trans)
    ld_update = np.array(ld_update)
    addi_upd = np.array(addi_upd)
    dec_trans = np.array(dec_trans)
    not_same = np.array(not_same)

    ax.bar(x, fast_trans, width, label='CMAP Fast Translate', color='#1f77b4')
    ax.bar(x, ld_update, width, bottom=fast_trans, label='CMAP Load Update', color='#ff7f0e')
    ax.bar(x, addi_upd, width, bottom=fast_trans + ld_update, label='CMAP ADDI Update', color='#2ca02c')
    ax.bar(x, dec_trans, width, bottom=fast_trans + ld_update + addi_upd,
           label='CMAP Decode Fast Trans', color='#d62728')
    ax.bar(x, not_same, width, bottom=fast_trans + ld_update + addi_upd + dec_trans,
           label='CMAP Valid Not Same Page', color='#9467bd')

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Count (Millions)", fontsize=12)
    ax.set_title(f"CMAP Detail Breakdown ({VARIANT_LABELS[var]})", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "7_cmap_detail_addi.png", dpi=150)
    plt.close(fig)
    print("[OK] 7_cmap_detail_addi.png saved")


def plot_cmap_accel_ratio(data: dict, output_dir: Path):
    """Bar chart: CMAP accelerated load ratio (cmap_fast_translate / total_exe_ld) per benchmark.
    total_exe_ld = exe_is_ld + cmap_fast_translate, since CMAP bypass loads skip IQ→AGU."""
    fig, ax = plt.subplots(figsize=(20, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.15
    offsets = np.arange(len(OPT_VARIANTS)) - len(OPT_VARIANTS) / 2 + 0.5

    for i, var in enumerate(OPT_VARIANTS):
        ratios = []
        for bench in BENCHMARKS:
            fast = get_cmap_fast_trans(data, var, bench)
            exe_ld = get_metric(data, var, bench, OPT_EXE_LD)
            if fast is not None and exe_ld is not None:
                total_ld = exe_ld + fast
                ratios.append(fast / total_ld * 100 if total_ld > 0 else 0)
            else:
                ratios.append(0)
        ax.bar(x + offsets[i] * width, ratios, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("CMAP Accelerated Load Ratio (%)", fontsize=12)
    ax.set_title("SPEC2006 CMAP Accelerated Load Ratio (cmap_fast_translate / total_exe_ld)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(BENCHMARKS, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "9_cmap_accel_ratio.png", dpi=150)
    plt.close(fig)
    print("[OK] 9_cmap_accel_ratio.png saved")


def plot_sab_conflict(data: dict, output_dir: Path):
    """Bar chart: SAB conflict count for variants that have SAB."""
    # sab_variants = ["sab", "disp_8way", "spec"]
    sab_variants = ["sab", "spec"]
    fig, ax = plt.subplots(figsize=(20, 7))
    x = np.arange(len(BENCHMARKS))
    width = 0.25
    offsets = np.arange(len(sab_variants)) - len(sab_variants) / 2 + 0.5

    for i, var in enumerate(sab_variants):
        counts = []
        for bench in BENCHMARKS:
            val = get_metric(data, var, bench, OPT_SAB_CONFLICT)
            counts.append(val / 1e3 if val else 0)  # in thousands
        ax.bar(x + offsets[i] * width, counts, width,
               label=VARIANT_LABELS[var], color=COLORS[var], edgecolor='white', linewidth=0.5)

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

    # CMAP fast translate count (absolute)
    print(f"\n--- CMAP Fast Translate Count (dispatch) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        for var in OPT_VARIANTS:
            val = get_cmap_fast_trans(data, var, bench)
            if val is not None:
                row += f"  {val:12,.0f}"
            else:
                row += f"  {'N/A':>12}"
        print(row)

    # CMAP acceleration ratio: cmap_fast_translate / total_exe_ld
    # NOTE: exe_is_ld only counts loads through IQ→AGU path; CMAP bypass loads
    #       go through retry path and are NOT counted in exe_is_ld.
    #       Total executed loads = exe_is_ld + cmap_fast_translate_cnt
    print(f"\n--- CMAP Accelerated Load Ratio (cmap_fast_translate / total_exe_ld, %) ---")
    header = f"{'Benchmark':<14}"
    for var in OPT_VARIANTS:
        header += f"  {VARIANT_LABELS[var]:>12}"
    print(header)
    for bench in BENCHMARKS:
        row = f"{bench:<14}"
        for var in OPT_VARIANTS:
            fast = get_cmap_fast_trans(data, var, bench)
            exe_ld = get_metric(data, var, bench, OPT_EXE_LD)
            if fast is not None and exe_ld is not None:
                total_ld = exe_ld + fast  # total = IQ loads + CMAP bypass loads
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
            fast = get_cmap_fast_trans(data, var, bench)
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
    plot_l2_tlb_miss_change(data, output_dir)
    plot_exe_ld_change(data, output_dir)
    plot_exe_st_change(data, output_dir)
    plot_mini_excpt_change(data, output_dir)
    plot_cmap_fast_trans(data, output_dir)
    plot_cmap_detail(data, output_dir)
    plot_cmap_accel_ratio(data, output_dir)
    plot_sab_conflict(data, output_dir)

    print(f"\nAll plots saved to: {output_dir}")
    print("Done!")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Combined analysis for 20 thesis benchmarks:
  - SPEC CPU 2006 (10): astar, bzip2, dealII, gobmk, h264ref, leslie3d, milc, namd, povray, xalancbmk
  - SPEC CPU 2017 (10): perlbench_r, blender_r, deepsjeng_r, mcf_r, cam4_r, leela_r, cactuBSSN_r,
                         gcc (from SPEC06 logs), lbm (from SPEC06 logs), omnetpp (from SPEC06 logs)

Loads data from spec07_logs/ and spec17_logs/ respectively, then produces unified tables and plots.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "combined_plots"

# ─── Benchmark Definitions ──────────────────────────────────────────────────

SPEC06_BENCHMARKS = [
    "astar", "bzip2", "dealII", "gobmk", "h264ref",
    "leslie3d", "milc", "namd", "povray", "xalancbmk",
]

SPEC17_FROM_SPEC06 = ["gcc", "lbm", "omnetpp"]

SPEC17_PURE = [
    "perlbench_r", "blender_r", "deepsjeng_r", "mcf_r",
    "cam4_r", "leela_r", "cactuBSSN_r",
]

SPEC17_BENCHMARKS = SPEC17_PURE + SPEC17_FROM_SPEC06
ALL_BENCHMARKS = SPEC06_BENCHMARKS + SPEC17_BENCHMARKS

BENCH_SUITE = {}
for b in SPEC06_BENCHMARKS:
    BENCH_SUITE[b] = "SPEC06"
for b in SPEC17_BENCHMARKS:
    BENCH_SUITE[b] = "SPEC17"


def bench_display_name(bench: str) -> str:
    if bench.endswith("_r"):
        return bench[:-2]
    return bench


def bench_display_names(benches: list[str]) -> list[str]:
    return [bench_display_name(bench) for bench in benches]

VARIANT_ORDER = ["no_addi", "addi", "sab", "spec"]
VARIANT_LABELS = {
    "baseline": "Baseline",
    "no_addi":  "Tracing Map",
    "addi":     "Tracing Map+ADDI",
    "sab":      "Tracing Map+ADDI+SAB",
    "spec":     "Tracing Map+ADDI+SAB+Spec",
}

COLORS = {
    "no_addi": "#1f77b4",
    "addi":    "#ff7f0e",
    "sab":     "#2ca02c",
    "spec":    "#9467bd",
}

# ─── Event Indices ───────────────────────────────────────────────────────────
# Baseline (standard BOOM HPM)
BL_CYCLES      = 0;  BL_INSTS       = 1
BL_EXE_LD      = 31; BL_EXE_ST      = 32
BL_DTLB_VALID  = 33; BL_DTLB_MISS   = 34
BL_DCACHE_VALID= 36; BL_DCACHE_NACK = 37; BL_DCACHE_REQ = 38
BL_L2_TLB_MISS = 57; BL_MINI_EXCPT  = 61

# Optimized (custom event counters)
OPT_CYCLES     = 0;  OPT_INSTS      = 1
OPT_EXE_LD     = 2;  OPT_EXE_ST     = 3
OPT_DTLB_VALID = 4;  OPT_DTLB_MISS  = 5
OPT_DCACHE_VALID=7;  OPT_DCACHE_NACK= 8; OPT_DCACHE_REQ = 9
OPT_COMMIT_LD  = 10; OPT_COMMIT_ST  = 11
OPT_L2_TLB_MISS= 12; OPT_MINI_EXCPT = 15
OPT_Tracing_Map_FAST_TRANS = 16; OPT_Tracing_Map_LD_UPDATE = 17
OPT_Tracing_Map_DEC_TRANS  = 20; OPT_Tracing_Map_NOT_SAME  = 21
OPT_Tracing_Map_ADDI_UPD   = 22; OPT_SAB_CONFLICT   = 24
OPT_ROLLBACK   = 25
OPT_SPEC_WAKEUP_TOTAL       = 26; OPT_SPEC_WAKEUP_RETRY       = 27
OPT_SPEC_WAKEUP_WRONG       = 28; OPT_SPEC_WAKEUP_WRONG_RETRY = 29


# ─── Log Parsing ────────────────────────────────────────────────────────────

def parse_log(filepath: Path) -> dict[int, float]:
    events: dict[int, float] = {}
    with open(filepath, "r", encoding="utf-8") as f:
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


def _find_variant_dirs(base_dir: Path, base_name: str) -> list[Path]:
    dirs = []
    base = base_dir / base_name
    if base.is_dir():
        dirs.append(base)
    for p in sorted(base_dir.iterdir()):
        if p.is_dir() and p != base:
            m = re.match(re.escape(base_name) + r"_(\d+)$", p.name)
            if m:
                dirs.append(p)
    return dirs if dirs else [base]


def _find_variant_dirs_prefer(base_dir: Path, preferred: str, fallback: str) -> list[Path]:
    pref = [d for d in _find_variant_dirs(base_dir, preferred) if d.is_dir()]
    if pref:
        return pref
    return _find_variant_dirs(base_dir, fallback)


def load_spec06_data(benchmarks: list[str]) -> dict[str, dict[str, dict[int, float]]]:
    spec06_dir = SCRIPT_DIR / "spec07_logs"
    log_dirs = {
        "baseline": _find_variant_dirs(spec06_dir, "baseline_logs"),
        "no_addi":  _find_variant_dirs(spec06_dir, "fast_trans_no_addi"),
        "addi":     _find_variant_dirs(spec06_dir, "fast_trans_addi"),
        "sab":      _find_variant_dirs(spec06_dir, "fast_trans_sab"),
        "spec":     _find_variant_dirs_prefer(spec06_dir, "fast_trans_spec_new_counter", "fast_trans_spec"),
    }

    data: dict[str, dict[str, dict[int, float]]] = {}
    for variant, dirpaths in log_dirs.items():
        data[variant] = {}
        valid_dirs = [d for d in dirpaths if d.is_dir()]
        if not valid_dirs:
            for bench in benchmarks:
                data[variant][bench] = {}
            continue

        for bench in benchmarks:
            all_run_events: list[dict[int, float]] = []
            for dirpath in valid_dirs:
                for suffix in [f"{bench}_no_loop_predictor.log", bench]:
                    logfile = dirpath / suffix
                    if logfile.exists():
                        ev = parse_log(logfile)
                        if ev:
                            all_run_events.append(ev)
                        break

            if not all_run_events:
                data[variant][bench] = {}
                continue

            all_eids = set()
            for ev in all_run_events:
                all_eids.update(ev.keys())
            averaged: dict[int, float] = {}
            for eid in all_eids:
                values = [ev[eid] for ev in all_run_events if eid in ev]
                if values:
                    averaged[eid] = sum(values) / len(values)
            data[variant][bench] = averaged
    return data


def load_spec17_data(benchmarks: list[str]) -> dict[str, dict[str, dict[int, float]]]:
    spec17_dir = SCRIPT_DIR / "spec17_logs"
    log_dirs = {
        "baseline": spec17_dir / "baseline",
        "no_addi":  spec17_dir / "fast_trans_normal",
        "addi":     spec17_dir / "fast_trans_addi",
        "sab":      spec17_dir / "fast_trans_sab",
        "spec":     spec17_dir / "fast_trans_spec",
    }

    data: dict[str, dict[str, dict[int, float]]] = {}
    for variant, dir_path in log_dirs.items():
        data[variant] = {}
        if not dir_path.is_dir():
            print(f"[WARN] Missing SPEC17 variant dir: {dir_path}")
            for bench in benchmarks:
                data[variant][bench] = {}
            continue

        for bench in benchmarks:
            candidates = [dir_path / bench]
            candidates.extend(sorted(dir_path.glob(f"*.{bench}")))
            candidates = [p for p in candidates if p.is_file()]

            if not candidates:
                data[variant][bench] = {}
                continue
            data[variant][bench] = parse_log(candidates[0])
    return data


def load_combined_data() -> dict[str, dict[str, dict[int, float]]]:
    all_spec06_benches = SPEC06_BENCHMARKS + SPEC17_FROM_SPEC06
    print(f"Loading SPEC06 data for: {all_spec06_benches}")
    spec06_data = load_spec06_data(all_spec06_benches)

    print(f"Loading SPEC17 data for: {SPEC17_PURE}")
    spec17_data = load_spec17_data(SPEC17_PURE)

    combined: dict[str, dict[str, dict[int, float]]] = {}
    for variant in ["baseline"] + VARIANT_ORDER:
        combined[variant] = {}
        for bench in SPEC06_BENCHMARKS:
            combined[variant][bench] = spec06_data.get(variant, {}).get(bench, {})
        for bench in SPEC17_FROM_SPEC06:
            combined[variant][bench] = spec06_data.get(variant, {}).get(bench, {})
        for bench in SPEC17_PURE:
            combined[variant][bench] = spec17_data.get(variant, {}).get(bench, {})

    return combined


# ─── Metric Extraction ──────────────────────────────────────────────────────

def _is_spec06_source(bench: str) -> bool:
    return bench in SPEC06_BENCHMARKS or bench in SPEC17_FROM_SPEC06

def get_metric(data: dict, variant: str, bench: str, eid: int) -> float | None:
    val = data.get(variant, {}).get(bench, {}).get(eid)
    return float(val) if val is not None else None

def get_ipc(data: dict, variant: str, bench: str) -> float | None:
    if variant == "baseline":
        c, i = get_metric(data, variant, bench, BL_CYCLES), get_metric(data, variant, bench, BL_INSTS)
    else:
        c, i = get_metric(data, variant, bench, OPT_CYCLES), get_metric(data, variant, bench, OPT_INSTS)
    return i / c if c and i and c > 0 else None

def get_cycles(data, var, bench):
    return get_metric(data, var, bench, BL_CYCLES if var == "baseline" else OPT_CYCLES)

def get_dtlb_miss(data, var, bench):
    return get_metric(data, var, bench, BL_DTLB_MISS if var == "baseline" else OPT_DTLB_MISS)

def get_dtlb_valid(data, var, bench):
    return get_metric(data, var, bench, BL_DTLB_VALID if var == "baseline" else OPT_DTLB_VALID)

def get_dcache_nack(data, var, bench):
    return get_metric(data, var, bench, BL_DCACHE_NACK if var == "baseline" else OPT_DCACHE_NACK)

def get_dcache_valid(data, var, bench):
    return get_metric(data, var, bench, BL_DCACHE_VALID if var == "baseline" else OPT_DCACHE_VALID)

def get_dcache_req(data, var, bench):
    return get_metric(data, var, bench, BL_DCACHE_REQ if var == "baseline" else OPT_DCACHE_REQ)

def get_mini_excpt(data, var, bench):
    return get_metric(data, var, bench, BL_MINI_EXCPT if var == "baseline" else OPT_MINI_EXCPT)

def get_l2_tlb_miss(data, var, bench):
    return get_metric(data, var, bench, BL_L2_TLB_MISS if var == "baseline" else OPT_L2_TLB_MISS)

def get_exe_ld(data, var, bench):
    if var == "baseline":
        return get_metric(data, var, bench, BL_EXE_LD)
    exe_ld = get_metric(data, var, bench, OPT_EXE_LD)
    fast = get_metric(data, var, bench, OPT_Tracing_Map_FAST_TRANS)
    if exe_ld is not None and fast is not None:
        return exe_ld + fast
    return exe_ld

def get_cmap_fast_trans(data, var, bench):
    if var == "baseline":
        return None
    return get_metric(data, var, bench, OPT_Tracing_Map_FAST_TRANS)

def get_dcache_miss_rate(data, var, bench):
    req = get_dcache_req(data, var, bench)
    valid = get_dcache_valid(data, var, bench)
    if req is not None and valid is not None and valid > 0:
        return req / valid * 100
    return None

def get_exe_st(data, var, bench):
    if var == "baseline":
        return get_metric(data, var, bench, BL_EXE_ST)
    return get_metric(data, var, bench, OPT_EXE_ST)

def get_commit_ld(data, var, bench):
    if var == "baseline":
        return get_metric(data, var, bench, BL_EXE_LD)
    return get_metric(data, var, bench, OPT_COMMIT_LD)

def get_commit_st(data, var, bench):
    if var == "baseline":
        return get_metric(data, var, bench, BL_EXE_ST)
    return get_metric(data, var, bench, OPT_COMMIT_ST)

def get_spec_wakeup_total(data, var, bench):
    return None if var == "baseline" else get_metric(data, var, bench, OPT_SPEC_WAKEUP_TOTAL)

def get_spec_wakeup_retry(data, var, bench):
    return None if var == "baseline" else get_metric(data, var, bench, OPT_SPEC_WAKEUP_RETRY)

def get_spec_wakeup_wrong(data, var, bench):
    return None if var == "baseline" else get_metric(data, var, bench, OPT_SPEC_WAKEUP_WRONG)

def get_spec_wakeup_wrong_retry(data, var, bench):
    return None if var == "baseline" else get_metric(data, var, bench, OPT_SPEC_WAKEUP_WRONG_RETRY)


def geomean(values: list[float]) -> float | None:
    positive = [v for v in values if v > 0]
    if not positive:
        return None
    return math.exp(sum(math.log(v) for v in positive) / len(positive))


def pct_change(bl, cur):
    if bl is None or cur is None or bl == 0:
        return None
    return (cur - bl) / bl * 100.0


# ─── Text Summary ───────────────────────────────────────────────────────────

def print_separator(title: str):
    print(f"\n{'─' * 130}")
    print(f"  {title}")
    print(f"{'─' * 130}")


def print_ipc_table(data: dict, benchmarks: list[str], suite_name: str):
    print_separator(f"{suite_name} — IPC 及变化率")

    header = f"{'Benchmark':<16} {'Suite':<7}"
    for var in ["baseline"] + VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)

    ipc_values = {var: [] for var in ["baseline"] + VARIANT_ORDER}
    speedup_ratios = {var: [] for var in VARIANT_ORDER}

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        for var in ["baseline"] + VARIANT_ORDER:
            ipc = get_ipc(data, var, bench)
            if ipc is not None:
                ipc_values[var].append(ipc)
                row += f" {ipc:14.4f}"
            else:
                row += f" {'N/A':>14}"
        print(row)

        bl_ipc = get_ipc(data, "baseline", bench)
        for var in VARIANT_ORDER:
            opt_ipc = get_ipc(data, var, bench)
            if bl_ipc and opt_ipc and bl_ipc > 0:
                speedup_ratios[var].append(opt_ipc / bl_ipc)

    row_gm = f"{'GEOMEAN':<16} {'':7}"
    for var in ["baseline"] + VARIANT_ORDER:
        gm = geomean(ipc_values[var])
        row_gm += f" {gm:14.4f}" if gm else f" {'N/A':>14}"
    print(row_gm)

    print(f"\n  IPC Change (%):")
    header2 = f"{'Benchmark':<16} {'Suite':<7}"
    for var in VARIANT_ORDER:
        header2 += f" {VARIANT_LABELS[var]:>14}"
    print(header2)

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl_ipc = get_ipc(data, "baseline", bench)
        for var in VARIANT_ORDER:
            opt_ipc = get_ipc(data, var, bench)
            chg = pct_change(bl_ipc, opt_ipc)
            row += f" {chg:+13.2f}%" if chg is not None else f" {'N/A':>14}"
        print(row)

    row_gm2 = f"{'GEOMEAN':<16} {'':7}"
    for var in VARIANT_ORDER:
        gm = geomean(speedup_ratios[var])
        row_gm2 += f" {(gm - 1) * 100:+13.2f}%" if gm else f" {'N/A':>14}"
    print(row_gm2)


def print_coverage_table(data: dict, benchmarks: list[str]):
    print_separator("Tracing Map 覆盖率 (accelerated load / total exe_ld, %)")

    header = f"{'Benchmark':<16} {'Suite':<7}"
    for var in VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)

    avg_vals = {var: [] for var in VARIANT_ORDER}
    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        for var in VARIANT_ORDER:
            fast = get_cmap_fast_trans(data, var, bench)
            exe_ld = get_metric(data, var, bench, OPT_EXE_LD)
            if fast is not None and exe_ld is not None:
                total_ld = exe_ld + fast
                ratio = fast / total_ld * 100 if total_ld > 0 else 0
                avg_vals[var].append(ratio)
                row += f" {ratio:13.2f}%"
            else:
                row += f" {'N/A':>14}"
        print(row)

    row_avg = f"{'AVERAGE':<16} {'':7}"
    for var in VARIANT_ORDER:
        if avg_vals[var]:
            row_avg += f" {np.mean(avg_vals[var]):13.2f}%"
        else:
            row_avg += f" {'N/A':>14}"
    print(row_avg)


def print_mini_excpt_table(data: dict, benchmarks: list[str]):
    print_separator("微异常 (Mini Exception) 绝对值 及 变化率 (%)")

    header = f"{'Benchmark':<16} {'Suite':<7} {'Baseline':>14}"
    for var in VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl = get_mini_excpt(data, "baseline", bench)
        row += f" {bl:14,.0f}" if bl is not None else f" {'N/A':>14}"
        for var in VARIANT_ORDER:
            val = get_mini_excpt(data, var, bench)
            row += f" {val:14,.0f}" if val is not None else f" {'N/A':>14}"
        print(row)

    print(f"\n  变化率 (%):")
    header2 = f"{'Benchmark':<16} {'Suite':<7}"
    for var in VARIANT_ORDER:
        header2 += f" {VARIANT_LABELS[var]:>14}"
    print(header2)

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl = get_mini_excpt(data, "baseline", bench)
        for var in VARIANT_ORDER:
            opt = get_mini_excpt(data, var, bench)
            chg = pct_change(bl, opt)
            row += f" {chg:+13.2f}%" if chg is not None else f" {'N/A':>14}"
        print(row)


def print_sab_table(data: dict, benchmarks: list[str]):
    print_separator("SAB 冲突检测次数")

    print(f"{'Benchmark':<16} {'Suite':<7} {'SAB方案':>14} {'完整方案':>14}")
    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        for var in ["sab", "spec"]:
            val = get_metric(data, var, bench, OPT_SAB_CONFLICT)
            row += f" {val:14,.0f}" if val is not None else f" {'N/A':>14}"
        print(row)


def print_dtlb_table(data: dict, benchmarks: list[str]):
    print_separator("DTLB Miss 变化率 (%)")

    header = f"{'Benchmark':<16} {'Suite':<7}"
    for var in VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl = get_dtlb_miss(data, "baseline", bench)
        for var in VARIANT_ORDER:
            opt = get_dtlb_miss(data, var, bench)
            chg = pct_change(bl, opt)
            row += f" {chg:+13.2f}%" if chg is not None else f" {'N/A':>14}"
        print(row)


def print_dtlb_miss_rate_table(data: dict, benchmarks: list[str]):
    print_separator("DTLB Miss Rate (dtlb_miss / dtlb_valid_access, %)")

    header = f"{'Benchmark':<16} {'Suite':<7} {'Baseline':>14}"
    for var in VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl_miss = get_dtlb_miss(data, "baseline", bench)
        bl_valid = get_dtlb_valid(data, "baseline", bench)
        if bl_miss is not None and bl_valid is not None and bl_valid > 0:
            row += f" {bl_miss / bl_valid * 100:13.2f}%"
        else:
            row += f" {'N/A':>14}"
        for var in VARIANT_ORDER:
            opt_miss = get_dtlb_miss(data, var, bench)
            opt_valid = get_dtlb_valid(data, var, bench)
            if opt_miss is not None and opt_valid is not None and opt_valid > 0:
                row += f" {opt_miss / opt_valid * 100:13.2f}%"
            else:
                row += f" {'N/A':>14}"
        print(row)


def print_dcache_tables(data: dict, benchmarks: list[str]):
    print_separator("D-Cache NACK 变化率 (%)")
    header = f"{'Benchmark':<16} {'Suite':<7}"
    for var in VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl = get_dcache_nack(data, "baseline", bench)
        for var in VARIANT_ORDER:
            opt = get_dcache_nack(data, var, bench)
            chg = pct_change(bl, opt)
            row += f" {chg:+13.2f}%" if chg is not None else f" {'N/A':>14}"
        print(row)

    print_separator("D-Cache Miss Rate 差值 (pp)")
    header = f"{'Benchmark':<16} {'Suite':<7} {'Baseline Rate':>14}"
    for var in VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl_rate = get_dcache_miss_rate(data, "baseline", bench)
        row += f" {bl_rate:13.2f}%" if bl_rate is not None else f" {'N/A':>14}"
        for var in VARIANT_ORDER:
            opt_rate = get_dcache_miss_rate(data, var, bench)
            if bl_rate is not None and opt_rate is not None:
                row += f" {opt_rate - bl_rate:+13.4f}"
            else:
                row += f" {'N/A':>14}"
        print(row)

    print_separator("D-Cache L2 Request 变化率 (%)")
    header = f"{'Benchmark':<16} {'Suite':<7}"
    for var in VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)
    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl = get_dcache_req(data, "baseline", bench)
        for var in VARIANT_ORDER:
            opt = get_dcache_req(data, var, bench)
            chg = pct_change(bl, opt)
            row += f" {chg:+13.2f}%" if chg is not None else f" {'N/A':>14}"
        print(row)


def print_spec_wakeup_table(data: dict, benchmarks: list[str]):
    print_separator("推测式唤醒统计 (完整方案)")

    print(f"{'Benchmark':<16} {'Suite':<7} {'Retry占比':>10} {'整体失败率':>12} {'Retry失败率':>12} {'正常路径失败':>12}")

    for bench in benchmarks:
        total = get_spec_wakeup_total(data, "spec", bench)
        retry = get_spec_wakeup_retry(data, "spec", bench)
        wrong = get_spec_wakeup_wrong(data, "spec", bench)
        wrong_retry = get_spec_wakeup_wrong_retry(data, "spec", bench)

        if total is None or total == 0 or retry is None or wrong is None or wrong_retry is None:
            print(f"{bench:<16} {BENCH_SUITE[bench]:<7} {'(no data)':>10}")
            continue

        retry_pct = retry / total * 100
        overall_fail = wrong / total * 100
        retry_fail = wrong_retry / retry * 100 if retry > 0 else 0
        normal_total = total - retry
        normal_wrong = wrong - wrong_retry
        normal_fail = normal_wrong / normal_total * 100 if normal_total > 0 else 0

        print(f"{bench:<16} {BENCH_SUITE[bench]:<7} {retry_pct:9.2f}% {overall_fail:11.2f}% {retry_fail:11.2f}% {normal_fail:11.2f}%")


def print_cmap_detail_table(data: dict, benchmarks: list[str]):
    print_separator("CMAP Update Count Comparison (Normal vs ADDI)")

    print(
        f"{'Benchmark':<16} {'Suite':<7} {'Normal Update':>16} {'ADDI Load Upd':>16} "
        f"{'ADDI ADDI Upd':>16} {'ADDI Total':>16} {'Delta':>14} {'Change(%)':>12}"
    )

    normal_values = []
    addi_ld_values = []
    addi_addi_values = []
    addi_total_values = []
    delta_values = []
    change_values = []

    for bench in benchmarks:
        ev_normal = data.get("no_addi", {}).get(bench, {})
        ev_addi = data.get("addi", {}).get(bench, {})

        normal_upd = ev_normal.get(OPT_Tracing_Map_LD_UPDATE, 0)
        addi_ld_upd = ev_addi.get(OPT_Tracing_Map_LD_UPDATE, 0)
        addi_addi_upd = ev_addi.get(OPT_Tracing_Map_ADDI_UPD, 0)
        addi_total = addi_ld_upd + addi_addi_upd

        normal_values.append(normal_upd)
        addi_ld_values.append(addi_ld_upd)
        addi_addi_values.append(addi_addi_upd)
        addi_total_values.append(addi_total)
        delta = addi_total - normal_upd
        delta_values.append(delta)
        change = (delta / normal_upd * 100) if normal_upd > 0 else None
        if change is not None:
            change_values.append(change)

        row = (
            f"{bench:<16} {BENCH_SUITE[bench]:<7} "
            f"{normal_upd:16,.0f} {addi_ld_upd:16,.0f} {addi_addi_upd:16,.0f} {addi_total:16,.0f} "
            f"{delta:+14,.0f}"
        )
        row += f" {change:11.2f}%" if change is not None else f" {'N/A':>12}"
        print(row)

    avg_line = (
        f"{'AVERAGE':<16} {'':7} "
        f"{np.mean(normal_values):16,.0f} {np.mean(addi_ld_values):16,.0f} "
        f"{np.mean(addi_addi_values):16,.0f} {np.mean(addi_total_values):16,.0f} "
        f"{np.mean(delta_values):+14,.0f}"
    )
    avg_line += f" {np.mean(change_values):11.2f}%" if change_values else f" {'N/A':>12}"
    print(avg_line)


def print_l2_tlb_table(data: dict, benchmarks: list[str]):
    print_separator("L2 TLB Miss 变化率 (%)")
    header = f"{'Benchmark':<16} {'Suite':<7}"
    for var in VARIANT_ORDER:
        header += f" {VARIANT_LABELS[var]:>14}"
    print(header)

    for bench in benchmarks:
        row = f"{bench:<16} {BENCH_SUITE[bench]:<7}"
        bl = get_l2_tlb_miss(data, "baseline", bench)
        for var in VARIANT_ORDER:
            opt = get_l2_tlb_miss(data, var, bench)
            chg = pct_change(bl, opt)
            row += f" {chg:+13.2f}%" if chg is not None else f" {'N/A':>14}"
        print(row)


# ─── Plotting ───────────────────────────────────────────────────────────────

def _plot_change_generic(data: dict, output_dir: Path, filename: str,
                         title: str, ylabel: str, extractor):
    """Generic grouped-bar chart showing percentage change vs baseline."""
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.18
    offsets = np.arange(len(VARIANT_ORDER)) - len(VARIANT_ORDER) / 2 + 0.5

    for i, var in enumerate(VARIANT_ORDER):
        changes = []
        for bench in ALL_BENCHMARKS:
            bl = extractor(data, "baseline", bench)
            opt = extractor(data, var, bench)
            chg = pct_change(bl, opt)
            changes.append(chg if chg is not None else 0)
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_title(title, fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.axhline(y=0, color="black", linestyle="-", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / filename, dpi=150)
    plt.close(fig)


def plot_ipc_change(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.18
    offsets = np.arange(len(VARIANT_ORDER)) - len(VARIANT_ORDER) / 2 + 0.5

    gm_data = {
        "spec06": {v: [] for v in VARIANT_ORDER},
        "spec17": {v: [] for v in VARIANT_ORDER},
        "all":    {v: [] for v in VARIANT_ORDER},
    }

    for i, var in enumerate(VARIANT_ORDER):
        changes = []
        for bench in ALL_BENCHMARKS:
            bl_ipc = get_ipc(data, "baseline", bench)
            opt_ipc = get_ipc(data, var, bench)
            if bl_ipc and opt_ipc:
                chg = (opt_ipc - bl_ipc) / bl_ipc * 100
                changes.append(chg)
                ratio = opt_ipc / bl_ipc
                gm_data["all"][var].append(ratio)
                if BENCH_SUITE[bench] == "SPEC06":
                    gm_data["spec06"][var].append(ratio)
                else:
                    gm_data["spec17"][var].append(ratio)
            else:
                changes.append(0)

        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.text(len(SPEC06_BENCHMARKS) / 2, ax.get_ylim()[1] * 0.95, "SPEC CPU 2006",
            ha="center", fontsize=10, color="gray", fontstyle="italic")
    ax.text(len(SPEC06_BENCHMARKS) + len(SPEC17_BENCHMARKS) / 2, ax.get_ylim()[1] * 0.95, "SPEC CPU 2017",
            ha="center", fontsize=10, color="gray", fontstyle="italic")

    y_top = 0.97
    y_step = 0.045
    for idx, var in enumerate(VARIANT_ORDER):
        gm06 = geomean(gm_data["spec06"][var])
        gm17 = geomean(gm_data["spec17"][var])
        gm_all = geomean(gm_data["all"][var])
        label_parts = [f"{VARIANT_LABELS[var]}:"]
        if gm06:
            label_parts.append(f"S06={((gm06 - 1) * 100):+.2f}%")
        if gm17:
            label_parts.append(f"S17={((gm17 - 1) * 100):+.2f}%")
        if gm_all:
            label_parts.append(f"All={((gm_all - 1) * 100):+.2f}%")
        ax.text(0.99, y_top - idx * y_step, " ".join(label_parts),
                color=COLORS[var], fontsize=8, ha="right", va="top",
                transform=ax.transAxes,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.7, pad=1.5))

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("IPC Change vs Baseline (%)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: IPC Change vs Baseline", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.axhline(y=0, color="black", linestyle="-", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "1_ipc_change.png", dpi=150)
    plt.close(fig)


def plot_coverage(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.18
    offsets = np.arange(len(VARIANT_ORDER)) - len(VARIANT_ORDER) / 2 + 0.5

    for i, var in enumerate(VARIANT_ORDER):
        ratios = []
        for bench in ALL_BENCHMARKS:
            fast = get_cmap_fast_trans(data, var, bench)
            exe_ld = get_metric(data, var, bench, OPT_EXE_LD)
            if fast is not None and exe_ld is not None:
                total_ld = exe_ld + fast
                ratios.append(fast / total_ld * 100 if total_ld > 0 else 0)
            else:
                ratios.append(0)
        ax.bar(x + offsets[i] * width, ratios, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Tracing Map Accelerated Load Ratio (%)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: Tracing Map Coverage", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "2_coverage.png", dpi=150)
    plt.close(fig)


def plot_mini_excpt_change(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.18
    offsets = np.arange(len(VARIANT_ORDER)) - len(VARIANT_ORDER) / 2 + 0.5

    for i, var in enumerate(VARIANT_ORDER):
        changes = []
        for bench in ALL_BENCHMARKS:
            bl = get_mini_excpt(data, "baseline", bench)
            opt = get_mini_excpt(data, var, bench)
            chg = pct_change(bl, opt)
            changes.append(chg if chg is not None else 0)
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Mini Exception Change vs Baseline (%)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: Mini Exception Change", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.axhline(y=0, color="black", linestyle="-", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "3_mini_excpt_change.png", dpi=150)
    plt.close(fig)


def plot_dtlb_miss_change(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.18
    offsets = np.arange(len(VARIANT_ORDER)) - len(VARIANT_ORDER) / 2 + 0.5

    for i, var in enumerate(VARIANT_ORDER):
        changes = []
        for bench in ALL_BENCHMARKS:
            bl = get_dtlb_miss(data, "baseline", bench)
            opt = get_dtlb_miss(data, var, bench)
            chg = pct_change(bl, opt)
            changes.append(chg if chg is not None else 0)
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("DTLB Miss Change vs Baseline (%)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: DTLB Miss Change", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.axhline(y=0, color="black", linestyle="-", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "4_dtlb_miss_change.png", dpi=150)
    plt.close(fig)


def plot_dcache_nack_change(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.18
    offsets = np.arange(len(VARIANT_ORDER)) - len(VARIANT_ORDER) / 2 + 0.5

    for i, var in enumerate(VARIANT_ORDER):
        changes = []
        for bench in ALL_BENCHMARKS:
            bl = get_dcache_nack(data, "baseline", bench)
            opt = get_dcache_nack(data, var, bench)
            chg = pct_change(bl, opt)
            changes.append(chg if chg is not None else 0)
        ax.bar(x + offsets[i] * width, changes, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("D-Cache NACK Change vs Baseline (%)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: D-Cache NACK Change", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.axhline(y=0, color="black", linestyle="-", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "5_dcache_nack_change.png", dpi=150)
    plt.close(fig)


def plot_sab_conflict(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    sab_variants = ["sab", "spec"]
    width = 0.3
    offsets = np.arange(len(sab_variants)) - len(sab_variants) / 2 + 0.5

    for i, var in enumerate(sab_variants):
        counts = []
        for bench in ALL_BENCHMARKS:
            val = get_metric(data, var, bench, OPT_SAB_CONFLICT)
            counts.append(val / 1e3 if val else 0)
        ax.bar(x + offsets[i] * width, counts, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("SAB Conflict Count (Thousands)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: SAB Store-Load Conflict Count", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "6_sab_conflict.png", dpi=150)
    plt.close(fig)


def plot_cycles_change(data: dict, output_dir: Path):
    _plot_change_generic(data, output_dir, "2_cycles_change.png",
                         "Combined 20 Benchmarks: Cycles Change vs Baseline",
                         "Cycles Change vs Baseline (%)", get_cycles)


def plot_dtlb_valid_change(data: dict, output_dir: Path):
    _plot_change_generic(data, output_dir, "3a_dtlb_valid_change.png",
                         "Combined 20 Benchmarks: DTLB Valid Access Change",
                         "DTLB Access Change vs Baseline (%)", get_dtlb_valid)


def plot_dcache_valid_change(data: dict, output_dir: Path):
    _plot_change_generic(data, output_dir, "4a_dcache_valid_change.png",
                         "Combined 20 Benchmarks: D-Cache Valid Access Change",
                         "D-Cache Access Change vs Baseline (%)", get_dcache_valid)


def plot_dcache_req_change(data: dict, output_dir: Path):
    _plot_change_generic(data, output_dir, "4b_dcache_req_change.png",
                         "Combined 20 Benchmarks: D-Cache L2 Request Change",
                         "D-Cache L2 Request Change vs Baseline (%)", get_dcache_req)


def plot_dcache_miss_rate(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.18
    offsets = np.arange(len(VARIANT_ORDER)) - len(VARIANT_ORDER) / 2 + 0.5

    for i, var in enumerate(VARIANT_ORDER):
        diffs = []
        for bench in ALL_BENCHMARKS:
            bl_rate = get_dcache_miss_rate(data, "baseline", bench)
            opt_rate = get_dcache_miss_rate(data, var, bench)
            diffs.append((opt_rate - bl_rate) if (bl_rate is not None and opt_rate is not None) else 0)
        ax.bar(x + offsets[i] * width, diffs, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("D-Cache Miss Rate Difference vs Baseline (pp)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: D-Cache Miss Rate Proxy Difference", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.axhline(y=0, color="black", linestyle="-", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "4c_dcache_miss_rate.png", dpi=150)
    plt.close(fig)


def plot_l2_tlb_miss_change(data: dict, output_dir: Path):
    _plot_change_generic(data, output_dir, "3b_l2_tlb_miss_change.png",
                         "Combined 20 Benchmarks: L2 TLB Miss Change",
                         "L2 TLB Miss Change vs Baseline (%)", get_l2_tlb_miss)


def plot_exe_ld_change(data: dict, output_dir: Path):
    _plot_change_generic(data, output_dir, "10_exe_ld_change.png",
                         "Combined 20 Benchmarks: Execute Load Count Change",
                         "Execute Load Change vs Baseline (%)", get_exe_ld)


def plot_exe_st_change(data: dict, output_dir: Path):
    _plot_change_generic(data, output_dir, "11_exe_st_change.png",
                         "Combined 20 Benchmarks: Execute Store Count Change",
                         "Execute Store Change vs Baseline (%)", get_exe_st)


def plot_mini_excpt_absolute(data: dict, output_dir: Path):
    all_variants = ["baseline"] + VARIANT_ORDER
    variant_colors = {"baseline": "#7f7f7f", **COLORS}

    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.13
    offsets = np.arange(len(all_variants)) - len(all_variants) / 2 + 0.5

    for i, var in enumerate(all_variants):
        counts_m = []
        for bench in ALL_BENCHMARKS:
            val = get_mini_excpt(data, var, bench)
            counts_m.append(val / 1e6 if val else 0)
        ax.bar(x + offsets[i] * width, counts_m, width,
               label=VARIANT_LABELS[var], color=variant_colors[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Mini Exception Count (Millions)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: Mini Exception Absolute Count", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "5a_mini_excpt_absolute.png", dpi=150)
    plt.close(fig)


def plot_cmap_fast_trans(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.18
    offsets = np.arange(len(VARIANT_ORDER)) - len(VARIANT_ORDER) / 2 + 0.5

    for i, var in enumerate(VARIANT_ORDER):
        counts = []
        for bench in ALL_BENCHMARKS:
            val = get_cmap_fast_trans(data, var, bench)
            counts.append(val / 1e6 if val else 0)
        ax.bar(x + offsets[i] * width, counts, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Tracing_Map Fast Translate Count (Millions)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: Tracing_Map Fast Translate Count at Dispatch", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "8_cmap_fast_translate.png", dpi=150)
    plt.close(fig)


def plot_cmap_detail(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    w = 0.36

    normal_upd_counts = []
    ld_upd_counts = []
    addi_upd_counts = []

    for bench in ALL_BENCHMARKS:
        ev_normal = data.get("no_addi", {}).get(bench, {})
        ev_addi = data.get("addi", {}).get(bench, {})
        normal_upd_counts.append(ev_normal.get(OPT_Tracing_Map_LD_UPDATE, 0) / 1e6)
        ld_upd_counts.append(ev_addi.get(OPT_Tracing_Map_LD_UPDATE, 0) / 1e6)
        addi_upd_counts.append(ev_addi.get(OPT_Tracing_Map_ADDI_UPD, 0) / 1e6)

    normal_upd_counts = np.array(normal_upd_counts)
    ld_upd_counts = np.array(ld_upd_counts)
    addi_upd_counts = np.array(addi_upd_counts)

    ax.bar(x - w / 2, normal_upd_counts, w, label="Normal CMAP Update (Tracing Map)", color="#1f77b4", edgecolor="black", linewidth=0.4)
    ax.bar(x + w / 2, ld_upd_counts, w, label="Tracing_Map Load Update (ADDI)", color="#ff7f0e", edgecolor="black", linewidth=0.4)
    ax.bar(x + w / 2, addi_upd_counts, w, bottom=ld_upd_counts, label="Tracing_Map ADDI Update", color="#2ca02c", edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Update Count (Millions)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: CMAP Update Count Comparison (Normal vs ADDI)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "9_cmap_detail_addi.png", dpi=150)
    plt.close(fig)


def plot_sab_conflict_ratio(data: dict, output_dir: Path):
    sab_variants = ["sab", "spec"]
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.3
    offsets = np.arange(len(sab_variants)) - len(sab_variants) / 2 + 0.5

    for i, var in enumerate(sab_variants):
        ratios = []
        for bench in ALL_BENCHMARKS:
            conflict = get_metric(data, var, bench, OPT_SAB_CONFLICT)
            excpt = get_mini_excpt(data, var, bench)
            if conflict is not None and excpt is not None and (conflict + excpt) > 0:
                ratios.append(conflict / (conflict + excpt) * 100)
            else:
                ratios.append(0)
        ax.bar(x + offsets[i] * width, ratios, width,
               label=VARIANT_LABELS[var], color=COLORS[var],
               edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("SAB Conflict Ratio in (Conflict + Exceptions) (%)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: SAB Conflict / (Conflict + Mini Exception)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "6a_sab_conflict_ratio.png", dpi=150)
    plt.close(fig)


def plot_spec_wakeup_ratios(data: dict, output_dir: Path):
    fig, ax = plt.subplots(figsize=(18, 9))
    x = np.arange(len(ALL_BENCHMARKS))
    width = 0.2
    offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]

    retry_ratios = []
    wrong_in_wrong_total = []
    wrong_in_total = []
    wrong_retry_ratios = []

    for bench in ALL_BENCHMARKS:
        total = get_spec_wakeup_total(data, "spec", bench)
        retry = get_spec_wakeup_retry(data, "spec", bench)
        wrong = get_spec_wakeup_wrong(data, "spec", bench)
        wrong_retry = get_spec_wakeup_wrong_retry(data, "spec", bench)

        retry_ratios.append((retry / total * 100) if (total and retry is not None and total > 0) else 0)
        wrong_in_wrong_total.append((wrong_retry / wrong * 100) if (wrong and wrong_retry is not None and wrong > 0) else 0)
        wrong_in_total.append((wrong / total * 100) if (total and wrong is not None and total > 0) else 0)
        wrong_retry_ratios.append((wrong_retry / retry * 100) if (retry and wrong_retry is not None and retry > 0) else 0)

    ax.bar(x + offsets[0], retry_ratios, width, label="Retry / Total Wakeup (%)", color="#1f77b4", edgecolor="black", linewidth=0.4)
    ax.bar(x + offsets[1], wrong_in_wrong_total, width, label="Wrong Retry / Total Wrong (%)", color="#ff7f0e", edgecolor="black", linewidth=0.4)
    ax.bar(x + offsets[2], wrong_in_total, width, label="Wrong / Total Wakeup (%)", color="#d62728", edgecolor="black", linewidth=0.4)
    ax.bar(x + offsets[3], wrong_retry_ratios, width, label="Wrong Retry / Retry (%)", color="#9467bd", edgecolor="black", linewidth=0.4)

    ax.axvline(x=len(SPEC06_BENCHMARKS) - 0.5, color="gray", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Ratio (%)", fontsize=12)
    ax.set_title("Combined 20 Benchmarks: Spec Wakeup Retry/Error Ratios", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(bench_display_names(ALL_BENCHMARKS), rotation=50, ha="right", fontsize=8)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "12_spec_wakeup_ratios.png", dpi=150)
    plt.close(fig)


def plot_commit_ld_st_ratio(data: dict, output_dir: Path):
    var = "spec"
    ld_p, st_p = [], []
    for bench in ALL_BENCHMARKS:
        insts = get_metric(data, var, bench, OPT_INSTS)
        ld = get_commit_ld(data, var, bench)
        st = get_commit_st(data, var, bench)
        if insts is None or insts <= 0 or ld is None or st is None:
            ld_p.append(0.0)
            st_p.append(0.0)
        else:
            ld_p.append(ld / insts * 100.0)
            st_p.append(st / insts * 100.0)

    order = sorted(range(len(ALL_BENCHMARKS)), key=lambda i: -(ld_p[i] + st_p[i]))
    b_sorted = [ALL_BENCHMARKS[i] for i in order]
    ld_sorted = [ld_p[i] for i in order]
    st_sorted = [st_p[i] for i in order]

    fig, ax = plt.subplots(figsize=(18, 9))
    x_pos = list(range(len(b_sorted)))
    w = 0.6
    ax.bar(x_pos, ld_sorted, w, label="Committed Load", color="#4C72B0", edgecolor="white", linewidth=0.5)
    ax.bar(x_pos, st_sorted, w, bottom=ld_sorted, label="Committed Store", color="#DD8452", edgecolor="white", linewidth=0.5)

    avg_ld = sum(ld_p) / len(ld_p) if ld_p else 0
    avg_mem = sum(l + s for l, s in zip(ld_p, st_p)) / len(ld_p) if ld_p else 0
    ax.axhline(y=avg_ld, color="#4C72B0", linestyle="--", linewidth=1.2, alpha=0.7, label=f"Avg Load = {avg_ld:.1f}%")
    ax.axhline(y=avg_mem, color="#C44E52", linestyle="--", linewidth=1.2, alpha=0.7, label=f"Avg Mem = {avg_mem:.1f}%")

    for i, (ld, st) in enumerate(zip(ld_sorted, st_sorted)):
        ax.text(i, ld + st + 0.4, f"{ld + st:.1f}%", ha="center", va="bottom", fontsize=7, fontweight="bold")

    ax.set_xlabel("Benchmark", fontsize=12)
    ax.set_ylabel("Committed Instruction Ratio (%)", fontsize=12)
    ax.set_title(f"Combined 20 Benchmarks: Committed Load/Store Ratio ({VARIANT_LABELS[var]})", fontsize=13)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(bench_display_names(b_sorted), rotation=50, ha="right", fontsize=8)
    ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig.savefig(output_dir / "0_commit_ld_st_ratio.png", dpi=150)
    plt.close(fig)


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 130)
    print("  Combined 20 Benchmark Analysis (SPEC CPU 2006 × 10 + SPEC CPU 2017 × 10)")
    print("=" * 130)

    data = load_combined_data()

    missing = []
    for bench in ALL_BENCHMARKS:
        bl_ipc = get_ipc(data, "baseline", bench)
        sp_ipc = get_ipc(data, "spec", bench)
        if bl_ipc is None or sp_ipc is None:
            missing.append(bench)
    if missing:
        print(f"\n[WARN] Missing data for: {missing}")

    print_ipc_table(data, SPEC06_BENCHMARKS, "SPEC CPU 2006 (10 benchmarks)")
    print_ipc_table(data, SPEC17_BENCHMARKS, "SPEC CPU 2017 (10 benchmarks)")
    print_ipc_table(data, ALL_BENCHMARKS, "Combined (20 benchmarks)")

    print_coverage_table(data, ALL_BENCHMARKS)
    print_mini_excpt_table(data, ALL_BENCHMARKS)
    print_sab_table(data, ALL_BENCHMARKS)
    print_dtlb_table(data, ALL_BENCHMARKS)
    print_dtlb_miss_rate_table(data, ALL_BENCHMARKS)
    print_dcache_tables(data, ALL_BENCHMARKS)
    print_l2_tlb_table(data, ALL_BENCHMARKS)
    print_spec_wakeup_table(data, ALL_BENCHMARKS)
    print_cmap_detail_table(data, ALL_BENCHMARKS)

    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"\nGenerating plots to {OUTPUT_DIR} ...")
    plot_commit_ld_st_ratio(data, OUTPUT_DIR)
    plot_ipc_change(data, OUTPUT_DIR)
    plot_cycles_change(data, OUTPUT_DIR)
    plot_coverage(data, OUTPUT_DIR)
    plot_dtlb_miss_change(data, OUTPUT_DIR)
    plot_dtlb_valid_change(data, OUTPUT_DIR)
    plot_l2_tlb_miss_change(data, OUTPUT_DIR)
    plot_dcache_nack_change(data, OUTPUT_DIR)
    plot_dcache_valid_change(data, OUTPUT_DIR)
    plot_dcache_req_change(data, OUTPUT_DIR)
    plot_dcache_miss_rate(data, OUTPUT_DIR)
    plot_mini_excpt_change(data, OUTPUT_DIR)
    plot_mini_excpt_absolute(data, OUTPUT_DIR)
    plot_sab_conflict(data, OUTPUT_DIR)
    plot_sab_conflict_ratio(data, OUTPUT_DIR)
    plot_cmap_fast_trans(data, OUTPUT_DIR)
    plot_cmap_detail(data, OUTPUT_DIR)
    plot_exe_ld_change(data, OUTPUT_DIR)
    plot_exe_st_change(data, OUTPUT_DIR)
    plot_spec_wakeup_ratios(data, OUTPUT_DIR)

    print("\nDone!")


if __name__ == "__main__":
    main()

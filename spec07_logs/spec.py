import json, re, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def parse_log(filepath):
    events = {}
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith('{'):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = obj.get('type','')
            m = re.match(r'event\s+(\d+)', typ)
            if m:
                events[int(m.group(1))] = obj['value']
    return events

log_dir = 'fast_trans_spec'
benchmarks = [
    'astar','bwaves','bzip2','cactusADM','calculix','dealII','gcc','gobmk',
    'h264ref','hmmer','lbm','leslie3d','libquantum','milc','namd',
    'omnetpp','povray','sjeng','xalancbmk'
]

ld_pcts = []
st_pcts = []
for bench in benchmarks:
    ev = parse_log(os.path.join(log_dir, bench + '_no_loop_predictor.log'))
    insts = ev[1]
    ld_pcts.append(ev[10] / insts * 100)
    st_pcts.append(ev[11] / insts * 100)

# Sort by total memory ratio (descending) for better visualization
indices = np.argsort([-(l+s) for l, s in zip(ld_pcts, st_pcts)])
benchmarks_sorted = [benchmarks[i] for i in indices]
ld_sorted = [ld_pcts[i] for i in indices]
st_sorted = [st_pcts[i] for i in indices]

fig, ax = plt.subplots(figsize=(16, 6))
x = np.arange(len(benchmarks_sorted))
bar_width = 0.6

bars_ld = ax.bar(x, ld_sorted, bar_width, label='Committed Load', color='#4C72B0', edgecolor='white', linewidth=0.5)
bars_st = ax.bar(x, st_sorted, bar_width, bottom=ld_sorted, label='Committed Store', color='#DD8452', edgecolor='white', linewidth=0.5)

# Add average lines
avg_ld = np.mean(ld_pcts)
avg_mem = np.mean([l+s for l, s in zip(ld_pcts, st_pcts)])
ax.axhline(y=avg_ld, color='#4C72B0', linestyle='--', linewidth=1.2, alpha=0.7, label=f'Avg Load = {avg_ld:.1f}%')
ax.axhline(y=avg_mem, color='#C44E52', linestyle='--', linewidth=1.2, alpha=0.7, label=f'Avg Mem = {avg_mem:.1f}%')

# Add percentage labels on bars
for i, (ld, st) in enumerate(zip(ld_sorted, st_sorted)):
    total = ld + st
    ax.text(i, total + 0.5, f'{total:.1f}%', ha='center', va='bottom', fontsize=7.5, fontweight='bold')

ax.set_xlabel('Benchmark', fontsize=12)
ax.set_ylabel('Committed Instruction Ratio (%)', fontsize=12)
ax.set_title('SPEC CPU 2006 Committed Load/Store Instruction Ratio (BOOM v3, 200M Instructions)', fontsize=13)
ax.set_xticks(x)
ax.set_xticklabels(benchmarks_sorted, rotation=45, ha='right', fontsize=9)
ax.set_ylim(0, 55)
ax.legend(loc='upper right', fontsize=9, framealpha=0.9)
ax.grid(axis='y', alpha=0.3, linestyle='-')

plt.tight_layout()
fig.savefig('plots/0_commit_ld_st_ratio.png', dpi=150)
plt.close(fig)
print('Saved to plots/0_commit_ld_st_ratio.png')

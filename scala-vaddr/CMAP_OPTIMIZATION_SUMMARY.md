# BOOM v3 CMAP 地址预测优化技术总结

> 目标平台：BOOM v3 MediumBoomV3Config (Chipyard 1.13.0)
> 核心配置：2-wide decode/dispatch, memWidth=1, 16-entry LDQ/STQ, 64-entry ROB, RV64GC

---

## 目录

1. [功能一：CMAP（核心功能）](#1-功能一cmap核心功能)
2. [功能二：ADDI 累积偏移优化](#2-功能二addi-累积偏移优化)
3. [功能三：SAB + Speculative Load Wakeup](#3-功能三sab--speculative-load-wakeup)
4. [总体性能收益](#4-总体性能收益)
5. [修改文件清单](#5-修改文件清单)

---

## 1. 功能一：CMAP（核心功能）

### 1.1 设计思想

CMAP（Cache-line Address Map）是一个按逻辑寄存器索引的地址预测表。核心思想：

- 程序中大量 Load 指令的地址形式为 `base_reg + imm`，而同一个 base_reg 在短时间内往往指向相近的地址
- CMAP 直接缓存每个逻辑寄存器的**基址值（base = vaddr - imm）**
- 下一次相同 base_reg 的 Load 可在 Decode 阶段直接计算预测地址：`predicted_vaddr = last_base + curr_imm`
- 命中时 Load 绕过 Issue Queue，直接写入 LDQ 并通过 retry 路径执行，节省 Issue Queue 排队延迟

> **优化要点**：直接存储 base 值而非 vaddr+imm，省去了 Decode 关键路径上的 12-bit 减法器和每条目 12-bit 的 imm 存储开销。减法 `base = vaddr - imm` 转移到非关键的 LSU 回写路径。

### 1.2 具体修改与信号

#### 1.2.1 CMAP 表结构 (`core.scala`)

| 信号名 | 类型 | 宽度（每条目） | 条目数 | 说明 |
|--------|------|-------------|--------|------|
| `cmap_valid` | `RegInit(Vec)` | 1 bit | 32 | 表项有效位 |
| `cmap_processing` | `RegInit(Vec)` | 1 bit | 32 | 正在等待 AGU 回写（Miss 后设置） |
| `cmap_processing_seq` | `RegInit(Vec)` | 8 bit | 32 | 序列号（防 ABA 问题） |
| `cmap_last_base` | `Reg(Vec)` | 40 bit | 32 | 基址寄存器值（vaddr - imm） |

> 索引方式：Direct-mapped，以 Load/STA 的 `lrs1`（基址逻辑寄存器号）为索引。

#### 1.2.2 MicroOp 新增字段 (`micro-op.scala`)

| 信号名 | 宽度 | 说明 |
|--------|------|------|
| `cmap_addr_ready` | 1 bit | Decode 阶段 CMAP 命中，地址已就绪（Load 可绕过 IQ） |
| `cmap_vaddr` | 40 bit | CMAP 预测的虚拟地址 |
| `cmap_will_update` | 1 bit | 该指令 Miss 后将回写 CMAP（LSU 写回） |
| `cmap_seq` | 8 bit | 序列号，与 `cmap_processing_seq` 匹配以防止过时回写 |

#### 1.2.3 BrUpdateMasks 新增字段 (`functional-unit.scala`)

| 信号名 | 宽度 | 说明 |
|--------|------|------|
| `cmap_flush` | 1 bit | 全局 CMAP 刷新信号（mispredict/rollback/flush 时置位） |

#### 1.2.4 Decode 阶段 CMAP 查找 (`core.scala`)

**CMAP Hit 路径：**
1. 检查 `cmap_valid(lrs1)` 且无阻塞条件（无全局刷新、无前序寄存器冲突）
2. 计算 `predicted_vaddr = cmap_last_base(lrs1) + curr_imm + pending_offset`
3. Load：设置 `cmap_addr_ready = true`，在 Dispatch 时绕过 Issue Queue
4. STA：设置 `sab_vaddr_valid = true`，不绕过 IQ 但提供地址给 SAB 使用

**CMAP Miss 路径：**
1. 如果 `!cmap_valid(lrs1)` 且 `!cmap_processing(lrs1)`
2. 设置 `cmap_will_update = true`，递增 `cmap_processing_seq`
3. 设置 `cmap_processing(lrs1) = true`
4. 指令正常进入 Issue Queue → AGU → LSU

**阻塞条件检查：**
- `rs1 === 0.U`（x0寄存器）
- Load Reserved（LR 指令）
- Self-load（`ldst === lrs1`）
- 前序指令重命名同一寄存器（非 ADDI 自增）
- 全局刷新（mispredict/rollback/flush）
- 同周期前序 Load 已 set_processing 同一寄存器

#### 1.2.5 CMAP 失效机制 (`core.scala`)

| 失效场景 | 范围 | 触发条件 |
|---------|------|---------|
| 寄存器重命名失效 | 单条目 | 非 ADDI 自增指令修改 `ldst`（含 seq 递增） |
| 分支误预测 | 全部 32 条目 | `b1.mispredict_mask =/= 0` |
| Rollback | 全部 32 条目 | `rob.io.commit.rollback` |
| Exception/Flush | 全部 32 条目 | `rob.io.flush.valid` |

> 关键设计：失效时 `cmap_processing_seq` 递增，拒绝所有 in-flight 的过时回写。

#### 1.2.6 Dispatch 阶段绕过 Issue Queue (`dispatch.scala`)

```
addr_ready_bypass = cmap_addr_ready && uses_ldq
dis(w).valid := ren_uops(w).valid && uses_this_iq && !addr_ready_bypass(w)
```

- `BasicDispatcher` 和 `CompactingDispatcher` 均实现了此绕过逻辑
- 仅 Load 绕过（STA 仍需进 IQ 等待 rs2/STD）

#### 1.2.7 LDQ Dispatch 写入 (`lsu.scala`)

CMAP 命中的 Load 在 Dispatch 时直接写入 LDQ：
```scala
ldq(ld_enq_idx).bits.addr.valid      := true.B
ldq(ld_enq_idx).bits.addr.bits       := cmap_vaddr
ldq(ld_enq_idx).bits.addr_is_virtual := true.B  // vaddr，仍需 TLB
```

#### 1.2.8 LSU CMAP 回写 (`lsu.scala`)

Load 和 STA 在 AGU 完成后回写 CMAP（共享同一端口，memWidth=1 互斥）：

| 信号 | 方向 | 说明 |
|------|------|------|
| `cmap_update_valid` | LSU→Core | 回写有效 |
| `cmap_update_lreg` | LSU→Core | 逻辑寄存器号 |
| `cmap_update_base` | LSU→Core | 基址值 `vaddr - sign_extend(imm)`（在 LSU 侧计算） |
| `cmap_update_seq` | LSU→Core | 序列号（与 processing_seq 匹配才接受） |
| `cmap_clear_processing_valid` | LSU→Core | 不可缓存地址仅清除 processing 状态 |
| `cmap_clear_processing_lreg` | LSU→Core | 清除目标寄存器 |
| `cmap_clear_processing_seq` | LSU→Core | 清除时的序列号 |

#### 1.2.9 dis_cmap_override（同周期快速路径）(`lsu.scala`)

```scala
val dis_cmap_override = Wire(Vec(numLdqEntries, Bool()))
// 同周期 dispatch 的 CMAP Load 注入 AgePriorityEncoder
existing_ready || dis_cmap_override(i)
```

节省 1 个周期：LDQ 是 Reg，AgePriorityEncoder 读旧值；override 使本周期写入的条目立即参与 retry 选择。

### 1.3 资源开销分析

| 资源类别 | 数量 | 位宽计算 |
|---------|------|---------|
| **CMAP 表 Reg** | 32 条目 | `valid(1) + processing(1) + seq(6) + last_base(40) = 48 bit/entry` |
| **CMAP 表总容量** | | 32 × 48 = **1,536 bit = 192 Byte** |
| **MicroOp 增量** | 每条 uop | `cmap_addr_ready(1) + cmap_vaddr(40) + cmap_will_update(1) + cmap_seq(6) = 48 bit` |
| **BrUpdateMasks 增量** | 全局 1 bit | `cmap_flush(1)` |
| **Decode 组合逻辑** | coreWidth=2 | 每 slot: 40-bit 加法器（vaddr 计算）+ 寄存器冲突检测（无需减法器） |
| **LSU 回写端口** | memWidth=1 | 复用已有端口，LSU 侧增加 40-bit 减法器（`base = vaddr - imm`） |

**总计新增寄存器**：~1,536 bit = 192 Byte

### 1.4 性能收益

单独启用 CMAP（无 ADDI 优化）相对 Baseline 的 SPEC2007 结果：

| 指标 | 数值 |
|------|------|
| **IPC GEOMEAN 提升** | **+0.99%** |
| IPC GEOMEAN | 0.5751 → 0.5808 |
| 最佳 benchmark | dealII +8.12%, bzip2 +3.41%, astar +3.25% |
| 回归 benchmark | h264ref -3.87%, cactusADM -1.70% |
| DTLB Miss 平均降低 | 约 -5% ~ -24%（减少重复 TLB 访问） |

---

## 2. 功能二：ADDI 累积偏移优化

### 2.1 设计思想

程序中常见模式：`addi x5, x5, 8; ld x10, 0(x5)` — 基址寄存器通过 ADDI 自增后被 Load 使用。纯 CMAP 无法处理这种情况（ADDI 会导致寄存器值变化，CMAP 条目需失效）。

解决方案：**不失效**，而是将 ADDI 的偏移量累积到 `cmap_pending_offset` 中，Load 在计算预测地址时加上此累积偏移。

### 2.2 具体修改与信号

#### 2.2.1 Pending Offset Buffer (`core.scala`)

| 信号名 | 类型 | 宽度 | 条目数 | 说明 |
|--------|------|------|--------|------|
| `cmap_pending_offset` | `RegInit(Vec)` | 14 bit (signed) | 32 | 累积 ADDI 偏移 (±8192 范围) |

#### 2.2.2 同周期 ADDI 转发逻辑 (`core.scala`)

处理同一 decode 周期内多条 ADDI 和 Load 的依赖关系：

| 信号名 | 类型 | 长度 | 说明 |
|--------|------|------|------|
| `addi_same_cycle_offset` | `Wire(Vec)` | (coreWidth+1) × 32 × 15 bit | 前向传播累积偏移 |
| `addi_same_cycle_overflow` | `Wire(Vec)` | (coreWidth+1) × 32 × 1 bit | 溢出检测标记 |
| `same_cycle_pending_offsets` | `Wire(Vec)` | (slot+1) × 15 bit | 每 slot 的前序 ADDI 累积偏移 |

#### 2.2.3 ADDI 处理规则

| 场景 | 处理 |
|------|------|
| `addi x5, x5, 8`（自增） | 累积到 `cmap_pending_offset(x5)`，**不失效** CMAP |
| `addi x5, x6, 8`（非自增） | 视为普通寄存器重命名，**失效** `cmap_valid(x5)` |
| `addiw x5, x5, 8` | **忽略**，不参与优化（32-bit 运算 + 符号扩展，偏移不可预测） |
| 累积溢出（超过 ±8192） | **失效** CMAP 条目 |
| processing 状态下的 ADDI | 仍然累积到 `pending_offset`（AGU 回写后使用） |

#### 2.2.4 地址计算公式

```
predicted_vaddr = cmap_last_base(lrs1)              // 基址寄存器值
                + curr_imm                            // 当前立即数偏移
                + cmap_pending_offset(lrs1)            // 已累积的 ADDI 偏移
                + same_cycle_pending_offset             // 同周期前序 ADDI 偏移
```

#### 2.2.5 同周期多指令排序

关键 Chisel Last-Writer-Wins 语义约束（代码排布顺序）：

1. **LSU 回写**（最先）— 最低优先级
2. **Load/STA 处理**（CMAP 查找 + SET_PROCESSING）
3. **ADDI 偏移累积**
4. **寄存器失效**（非自增指令修改 ldst）
5. **全局失效**（mispredict/rollback/flush）— 最高优先级

### 2.3 资源开销分析

| 资源类别 | 数量 | 说明 |
|---------|------|------|
| **pending_offset Reg** | 32 × 14 bit = **448 bit** | 每个逻辑寄存器一个 14-bit 有符号累积偏移 |
| **同周期转发逻辑** | coreWidth × 32 × 15 bit Wire | 前向传播链（组合逻辑） |
| **加法器** | 每 slot 1 个 15-bit 加法器 | ADDI 偏移累积 |
| **溢出检测** | 每 slot 1-bit 比较 | 14-bit 符号位检查 |

**总计新增寄存器**：448 bit = 56 Byte（主要是 Decode 阶段的组合逻辑链前向传播 ADDI 偏移到后续 slot）。

### 2.4 性能收益

CMAP + ADDI 相对 Baseline 的 SPEC2007 结果：

| 指标 | 数值 |
|------|------|
| **IPC GEOMEAN 提升** | **+1.26%**（相对 CMAP 多 +0.27%） |
| IPC GEOMEAN | 0.5751 → 0.5824 |
| 最佳 benchmark | astar +8.55%（从 +3.25% 提升至 +8.55%，ADDI 效果显著） |
| CMAP 预测次数提升 | libquantum: 273 → 40,195,273（几乎完全由 ADDI 解锁） |
| DTLB Miss 进一步降低 | libquantum: -62.82%（大量循环访问通过 ADDI+CMAP 预测） |

> ADDI 优化对循环步进模式（stride access）效果极为显著，使 CMAP 的覆盖面大幅提升。

---

## 3. 功能三：SAB + Speculative Load Wakeup

### 3.1 设计思想

CMAP 绕过 Issue Queue 的 Load 执行更早，但这增加了 Store-Load 内存序冲突（mini_exception）的风险。SAB（Store Address Buffer）在 Dispatch 阶段预检测冲突，延迟有冲突的 Load 直到 Store 地址解析完成。

同时，将 `spec_ld_wakeup` 覆盖 `fired_load_retry` 路径，使 CMAP bypass Load 也能投机唤醒依赖指令。

### 3.2 具体修改与信号

#### 3.2.1 SAB 流水线结构 (`core.scala`)

| 参数 | 值 | 说明 |
|------|------|------|
| `numSabPipeStages` | 5（可配置） | SAB 流水线总级数 |
| `numSabRegStages` | 4 | 跨周期寄存器级数 (= numSabPipeStages - 1) |
| `sabVaddrWidth` | 37 bit | Dword 对齐的 vaddr 宽度 (coreMaxAddrBits - 3) |

**流水线示意图：**

```
pipe1 (Wire)  →  pipe2 (Reg)  →  pipe3 (Reg)  →  ...  →  pipe4 (Reg)
 ↑ 本周期STA       1周期前          2周期前              4周期前
```

每级流水线存储 `coreWidth` 条 STA 条目：

| 信号名 | 每条目宽度 | 说明 |
|--------|-----------|------|
| `sabRegValid(s)(w)` | 1 bit | 条目有效 |
| `sabRegVaddr(s)(w)` | 37 bit | Dword 对齐虚拟地址 |
| `sabRegStqIdx(s)(w)` | 4 bit | STQ 索引 |

#### 3.2.2 MicroOp 新增字段 (`micro-op.scala`)

| 信号名 | 宽度 | 说明 |
|--------|------|------|
| `sab_vaddr_valid` | 1 bit | STA 的 CMAP 预测地址有效（供 SAB 写入） |
| `sab_conflict` | 1 bit | Dispatch 阶段检测到 Store-Load 冲突 |
| `sab_conflict_stq_idx` | 4 bit | 冲突 Store 的 STQ 索引 |

#### 3.2.3 SAB 冲突检测逻辑 (`core.scala`)

**STA 写入（pipe1）：**
- 条件：`dis_fire(w) && is_sta && sab_vaddr_valid`
- 写入 Dword 对齐地址和 STQ 索引

**Load 冲突检测：**

| 冲突类型 | 比较范围 | 处理方式 |
|---------|---------|---------|
| 同周期冲突 | pipe1（前序 slot） | **降级**：清除 `cmap_addr_ready`，Load 回到正常 IQ→AGU 路径 |
| 跨周期冲突 | pipe2 ~ pipe8 (全部 Reg 级) | **标记**：设置 `sab_conflict` + `sab_conflict_stq_idx` |

跨周期冲突使用 CAM 比较，总比较数 = `numSabRegStages × coreWidth` = 4 × 2 = 8 条。

#### 3.2.4 SAB 冲突门控（LSU 侧）(`lsu.scala`)

1. **AgePriorityEncoder 门控**：有 `sab_conflict` 的 Load 需等待冲突 Store 地址解析后才允许 retry
```scala
val sab_ok = !e.uop.sab_conflict ||
  !stq(sab_stq_idx).valid ||
  (stq(sab_stq_idx).bits.addr.valid && !stq(sab_stq_idx).bits.addr_is_virtual)
```

2. **can_fire 级门控**：`ldq_retry_sab_store_ready` 作为 `can_fire_load_retry` 的附加条件

#### 3.2.5 SAB 刷新 (`core.scala`)

```scala
when (mispredict || rollback || flush) {
  for (s <- 0 until numSabRegStages)
    for (w <- 0 until coreWidth)
      sabRegValid(s)(w) := false.B
}
```

#### 3.2.6 Speculative Load Wakeup 扩展 (`lsu.scala`)

原始 BOOM 仅对 `fired_load_incoming`（从 Issue Queue 发出的 Load）做投机唤醒。修改后覆盖 `fired_load_retry`（CMAP bypass Load 通过 retry 路径执行）：

```scala
io.core.spec_ld_wakeup(w).valid := enableFastLoadUse.B &&
  (fired_load_incoming(w) || fired_load_retry(w)) &&  // 新增 retry 路径
  !mem_spec_wakeup_uop(w).fp_val &&
  mem_spec_wakeup_uop(w).pdst =/= 0.U
```

### 3.3 资源开销分析

| 资源类别 | 数量 | 位宽计算 |
|---------|------|---------|
| **SAB pipe1（Wire）** | coreWidth=2 条 | `valid(1) + vaddr(37) + stq_idx(4) = 42 bit/entry` × 2 = 84 bit |
| **SAB Reg 级** | 4 级 × 2 条 = 8 条 | 8 × 42 = **336 bit** |
| **SAB CAM 比较器** | coreWidth × 8 条 | 每条 37-bit 比较器 × 8 = 16 个比较器 |
| **MicroOp 增量** | 每条 uop | `sab_vaddr_valid(1) + sab_conflict(1) + sab_conflict_stq_idx(4) = 6 bit` |
| **spec_ld_wakeup 增量** | 仅增加 MUX | `fired_load_retry` 已有信号复用 |
| **BoomCoreParams** | 1 个参数 | `numSabPipeStages: Int = 5` |

**总计新增寄存器**：~336 bit = 42 Byte

### 3.4 性能收益

CMAP + ADDI + SAB 相对 Baseline 的 SPEC2007 结果：

| 指标 | 数值 |
|------|------|
| **IPC GEOMEAN 提升** | **+1.63%**（相对 CMAP+ADDI 多 +0.37%） |
| IPC GEOMEAN | 0.5751 → 0.5845 |
| h264ref 改善 | -3.08% → -0.18%（SAB 大幅减少了内存序冲突） |
| calculix 改善 | -0.13% → +0.43% |
| namd mini_exception | +77.12% → -3.23%（从严重回归变为改善） |
| bzip2 改善 | +3.33% → +4.57% |

CMAP + ADDI + SAB + Spec 相对 Baseline 的 SPEC2007 结果：

| 指标 | 数值 |
|------|------|
| **IPC GEOMEAN 提升** | **+2.07%**（相对 SAB 多 +0.44%） |
| IPC GEOMEAN | 0.5751 → 0.5870 |
| 最佳 benchmark | astar +9.55%, bzip2 +8.13%, dealII +6.93% |
| 唯一回归 | hmmer -1.67%, cactusADM -1.46% |
| h264ref 翻转 | 从 -3.87%(CMAP) → **+2.31%**(全部优化) |

---

## 4. 总体性能收益

### 4.1 IPC GEOMEAN 逐级提升

| 优化阶段 | IPC GEOMEAN | 相对 Baseline | 增量 |
|---------|-------------|-------------|------|
| Baseline | 0.5751 | — | — |
| +CMAP | 0.5808 | **+0.99%** | +0.99% |
| +ADDI | 0.5824 | **+1.26%** | +0.27% |
| +SAB | 0.5845 | **+1.63%** | +0.37% |
| +Spec | 0.5870 | **+2.07%** | +0.44% |

### 4.2 各 Benchmark 详细 IPC 变化 (%)

| Benchmark | CMAP | +ADDI | +SAB | +Spec |
|-----------|------|-------|------|-------|
| astar | +3.25 | +8.55 | +8.60 | **+9.55** |
| bwaves | -0.02 | -0.02 | +0.01 | +0.03 |
| bzip2 | +3.41 | +3.33 | +4.57 | **+8.13** |
| cactusADM | -1.70 | -1.72 | -1.70 | -1.46 |
| calculix | -0.20 | -0.13 | +0.43 | +0.51 |
| dealII | **+8.12** | +6.75 | +7.09 | +6.93 |
| gcc | +0.45 | +0.68 | +0.86 | +1.49 |
| gobmk | +0.77 | +0.57 | +0.65 | +1.29 |
| h264ref | -3.87 | -3.08 | -0.18 | **+2.31** |
| hmmer | +0.57 | +0.02 | -0.71 | -1.67 |
| lbm | +2.75 | +2.64 | +2.62 | +2.70 |
| leslie3d | +1.27 | +1.13 | +1.19 | +1.13 |
| libquantum | -0.06 | -0.07 | +0.06 | -0.04 |
| milc | +2.35 | +2.89 | +2.91 | +2.82 |
| namd | +0.66 | +0.60 | +0.62 | +1.18 |
| omnetpp | -0.02 | +0.14 | +1.66 | +2.17 |
| povray | +0.67 | +0.91 | +0.94 | +0.46 |
| sjeng | +0.42 | +0.49 | +0.56 | +1.17 |
| xalancbmk | +0.52 | +0.96 | +1.31 | +1.38 |
| **GEOMEAN** | **+0.99** | **+1.26** | **+1.63** | **+2.07** |

### 4.3 总资源开销汇总

| 模块 | 新增寄存器 | 主要组合逻辑 |
|------|-----------|-------------|
| CMAP 表 | 32 × 48 bit = 1,536 bit | 40-bit 加法器 × coreWidth（Decode）+ 40-bit 减法器 × memWidth（LSU 回写） |
| ADDI Pending Offset | 32 × 14 bit = 448 bit | 15-bit 前向传播链 × coreWidth |
| SAB 流水线 | 8 × 42 bit = 336 bit | 37-bit CAM 比较器 × 16 |
| MicroOp 增量 | 54 bit / uop | — |
| **总计** | **~2,320 bit ≈ 290 Byte** | 加法器 + CAM |

---

## 5. 修改文件清单

| 文件路径 | 修改内容 |
|---------|---------|
| `common/parameters.scala` | 新增 `numSabPipeStages` 参数 |
| `common/micro-op.scala` | 新增 CMAP/SAB 相关 MicroOp 字段 |
| `exu/core.scala` | CMAP 表、Decode 查找、ADDI 累积、SAB 流水线、失效逻辑、性能计数器 |
| `exu/dispatch.scala` | CMAP Load 绕过 Issue Queue 逻辑（Basic + Compacting Dispatcher） |
| `exu/execution-units/functional-unit.scala` | BrUpdateMasks 新增 `cmap_flush` |
| `lsu/lsu.scala` | LDQ dispatch 写入、CMAP 回写、dis_cmap_override、SAB 门控、spec_ld_wakeup 扩展、LSUCoreIO 接口 |

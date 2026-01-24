# Load-CLAR和Fat-Load功能时序检查报告

## 1. 功能流程总览

### Fat-Load流程
```
Decode → Dispatch → Execute → DCache Access → Writeback → CLAR Write + CMAP Update
```

### Load-CLAR流程
```
Decode (CMAP Check) → Dispatch → CLAR Ready Check → CLAR Read → Writeback
```

---

## 2. 关键时序路径分析

### ⚠️ **问题1: Decode阶段的组合逻辑深度**
**位置**: `exu/core.scala` lines 824-863

**问题描述**:
```scala
for (i <- 0 until numCmapEntries) {  // 4个entry遍历
  when (cmap_valid(i) && cmap_base_reg(i) === rs1) {
    // 地址计算
    val target_offset = cmap_row_offset(i) + imm_offset  // 加法器
    val no_carry = !target_offset(log2Ceil(cacheBlockBytes))  // 进位检测
    // LRU更新 - 所有entry的比较和递增
    for (j <- 0 until numCmapEntries) {
      when (cmap_lru(j) < cmap_lru(cmap_hit_idx)) {
        cmap_lru(j) := cmap_lru(j) + 1.U
      }
    }
  }
}
```

**时序路径**:
1. CMAP valid/reg比较 (4路并行)
2. 加法器: row_offset + imm_offset
3. 进位检测
4. LRU比较链 (4x4 = 16次比较)
5. LRU递增 (4个加法器)
6. 设置is_load_clar标志

**严重性**: 🔴 **高** - 这条路径在decode阶段，直接影响处理器频率

**建议修复**:
- **方案A**: 将LRU更新延迟到下一周期（使用RegNext）
- **方案B**: 使用伪LRU算法减少比较次数
- **方案C**: 将CMAP检查移到更早的阶段（但需要valid instruction）

---

### ⚠️ **问题2: Load-CLAR的冲突检测组合路径**
**位置**: `lsu/lsu.scala` lines 1476-1497

**问题描述**:
```scala
// STQ遍历检查冲突
for (j <- 0 until numStqEntries) {  // 16个entry
  when (stq(j).valid && load_clar_candidate_e.bits.st_dep_mask(j) && stq(j).bits.addr.valid) {
    val addr_match = (st_addr(corePAddrBits-1,3) === ld_addr(corePAddrBits-1,3))
    val mask_conflict = (ld_mask & st_mask) =/= 0.U
    when (addr_match && mask_conflict) {
      load_clar_has_conflict := true.B
    }
  }
}
// 同一周期使用结果
when (load_clar_has_candidate && load_clar_has_conflict) {
  ldq(load_clar_idx_candidate).bits.is_load_clar := false.B  // 降级
}
```

**时序路径**:
1. 16个STQ entry的valid检查
2. 16个地址比较
3. 16个mask AND + 非零检测
4. OR树归约 → load_clar_has_conflict
5. 使用conflict信号控制can_fire和降级

**严重性**: 🟡 **中** - 在writeback阶段，但可能影响关键路径

**当前状态**: ✅ **已正确使用Wire** - 同周期反应避免使用过时数据

---

### ⚠️ **问题3: CLAR Bank的数据更新逻辑**
**位置**: `lsu/lsu.scala` lines 255-283

**问题描述**:
```scala
// 同周期处理fat-load write和store update
when (io.write_val && !io.flush) {
  val store_matches_new_row = io.store_update_val && (store_row_base === new_base_addr)
  when (store_matches_new_row) {
    // 组合路径：地址比较 → mask计算 → 数据merge
    regionData := (io.write_row & ~full_mask) | (update_data_shifted & full_mask)
  }
}
```

**时序路径**:
1. 地址对齐计算 (2次)
2. 地址比较
3. Byte offset计算
4. Mask左移 (可变移位)
5. FillInterleaved生成完整mask
6. 128位数据AND + OR合并

**严重性**: 🟡 **中** - 宽数据操作可能影响时序

**建议**: 考虑分两周期：第一周期写入，第二周期store update

---

### ⚠️ **问题4: Fat-Load优先级与Bank冲突检测**
**位置**: `lsu/lsu.scala` lines 1440-1461

**问题描述**:
```scala
val fat_load_active = Wire(Vec(4, Bool()))
for (w <- 0 until memWidth) {
  when (io.dmem.resp(w).valid && io.dmem.resp(w).bits.uop.uses_ldq) {
    val ldq_idx = io.dmem.resp(w).bits.uop.ldq_idx
    when (ldq(ldq_idx).bits.is_fat_load) {
      fat_load_active(fat_bank_id) := true.B
    }
  }
}
// 立即用于load-clar选择
val load_clar_candidates = (0 until numLdqEntries).map(i => {
  e.valid && !fat_load_active(e.bits.clar_bank_id) && ...
})
```

**时序路径**:
1. memWidth个响应valid检查
2. LDQ索引读取
3. is_fat_load检查
4. Bank ID提取
5. fat_load_active设置
6. numLdqEntries次检查使用fat_load_active

**严重性**: 🟢 **低** - Wire正确使用，同周期阻止冲突

**状态**: ✅ **正确** - 避免了fat-load和load-clar同时访问同一bank

---

### ⚠️ **问题5: CMAP更新的时序**
**位置**: `exu/core.scala` lines 972-1018

**问题描述**:
```scala
when (lsu.io.core.fat_load_cmap_update(w).valid) {
  val exe_uop = lsu.io.core.exe(w).iresp.bits.uop
  val rs1 = exe_uop.lrs1
  // 查找现有entry
  for (i <- 0 until numCmapEntries) {
    when (cmap_valid(i) && cmap_base_reg(i) === rs1) {
      found_entry := true.B
    }
  }
  // LRU更新
  for (j <- 0 until numCmapEntries) {
    when (cmap_lru(j) < cmap_lru(update_idx)) {
      cmap_lru(j) := cmap_lru(j) + 1.U
    }
  }
}
```

**时序问题**:
- CMAP更新发生在fat-load响应时
- 但decode阶段会在**同一周期**读取CMAP进行匹配
- 可能存在read-after-write hazard

**严重性**: 🔴 **高** - 可能导致功能错误

**建议修复**:
```scala
// 方案1: 使用bypass逻辑
val cmap_update_this_cycle = Wire(Vec(memWidth, Valid(new CmapUpdate)))
// 在decode检查时考虑bypass

// 方案2: 确保CMAP更新不与使用同周期发生（通过pipeline stages隔离）
```

---

## 3. 功能正确性检查

### ✅ **正确项**

1. **Wire用于同周期信号传播**
   - `fat_load_active`: 正确阻止同周期冲突
   - `load_clar_has_conflict`: 正确用于同周期降级判断

2. **Load-CLAR降级机制**
   - 检测到冲突立即降级为普通load
   - 使用`is_load_clar := false.B`而非额外标志位

3. **Fat-Load优先级**
   - 正确使用Wire阻止load-clar选择
   - 写入CLAR时失效冲突的load-clar

4. **Store Commit更新**
   - 正确检测地址匹配
   - 支持字节级部分更新
   - 正确处理与fat-load的并发

5. **Rename阶段的CMAP/CLAR失效**
   - 正确在目标寄存器重命名时失效
   - 通过IO接口flush CLAR banks

---

### ⚠️ **潜在功能问题**

#### **问题A: CMAP读写冲突**
**场景**: Fat-load在cycle N更新CMAP，decode在cycle N尝试读取同一entry

**后果**: Decode可能读到更新前的旧值，导致load-clar判断错误

**修复**: 需要添加bypass或stall逻辑

---

#### **问题B: Load-CLAR地址未正确设置**
**位置**: `lsu/lsu.scala` line 477

```scala
when(io.core.dis_uops(w).bits.is_load_clar) {
  ldq(ld_enq_idx).bits.addr := clars(io.core.dis_uops(w).bits.clar_bank_id).io.read_paddr
}
```

**问题**: ClarBank没有`read_paddr`输出！应该使用baseAddr

**严重性**: 🔴 **致命错误** - 编译会失败

**修复**: 
```scala
// 需要在ClarBank添加输出或重新设计
val read_base_addr = Output(UInt(coreMaxAddrBits.W))
io.read_base_addr := baseAddr
```

---

#### **问题C: CLAR offset计算不一致**
**Decode阶段**: 
```scala
clar_offset_wire := target_offset(log2Ceil(xLen/8) + dcache_row_bits - 1, log2Ceil(xLen/8))
```

**问题**: `dcache_row_bits`可能计算不正确
```scala
val dcache_row_bits = log2Ceil(cacheBlockBytes)  // 应该是 log2Ceil(cacheBlockBytes/(xLen/8))
```

**严重性**: 🟡 **中** - offset计算错误会导致读取错误的word

---

## 4. 优先级修复建议

### 🔴 **立即修复**

1. **修复load-clar地址设置问题**
   - 在ClarBank添加baseAddr输出
   - 或在dispatch时从CMAP重新计算地址

2. **修复dcache_row_bits计算**
   ```scala
   val dcache_row_bits = log2Ceil(encRowBits / xLen)  // Row内有多少个word
   ```

3. **处理CMAP读写冲突**
   - 添加bypass逻辑或
   - 确保更新和读取在不同周期

### 🟡 **优化建议**

1. **优化Decode阶段LRU更新**
   - 延迟到下一周期或
   - 使用伪LRU算法

2. **考虑CLAR Bank更新延迟**
   - 将store update延迟一周期
   - 简化同周期合并逻辑

3. **添加性能计数器**
   - load-clar命中率
   - 降级次数
   - CMAP命中率

---

## 5. 测试建议

### 功能测试
- [ ] Load-CLAR正确从CLAR读取数据
- [ ] Fat-Load正确写入CLAR
- [ ] Store commit正确更新CLAR
- [ ] 冲突检测正确降级load-clar
- [ ] CMAP正确追踪和匹配
- [ ] 寄存器重命名正确失效CLAR

### 时序测试
- [ ] 静态时序分析 (STA)
- [ ] 关键路径识别
- [ ] 频率目标验证

### 边界情况
- [ ] 所有CLAR banks同时busy
- [ ] CMAP满时的替换
- [ ] Fat-load和load-clar同时访问同一bank
- [ ] Store commit与fat-load同周期重叠

---

## 6. 总结

**当前状态**: 
- 基本功能框架完整 ✅
- Wire信号使用正确 ✅
- 存在关键bug需要修复 🔴

**关键问题**:
1. Load-CLAR地址设置错误 (编译失败)
2. CMAP读写冲突可能导致功能错误
3. Decode阶段时序可能过长

**建议行动**:
1. 先修复编译错误 (地址设置)
2. 修正offset计算
3. 添加CMAP bypass或隔离逻辑
4. 进行时序评估

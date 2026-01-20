# Row Data 传输和 Load-Clar 降级功能实现

## 概述
本文档描述了两个新增功能的实现：
1. 在 DCache 和 MSHRs 中支持传输完整的 row data
2. 在 writeback 阶段检测 store-load 冲突并将 load-clar 降级为普通 load

## 功能 1: Row Data 传输支持

### 背景
为了支持 fat-load 指令，需要在缓存系统中传输完整的缓存行数据（row data），而不仅仅是一个字（word）。

### 实现细节

#### DCache 响应
在 `dcache.scala` 中，cache response 已经包含了 `row_data` 字段：
```scala
cache_resp(w).bits.row_data := s2_data_muxed(w)
```
- `s2_data_muxed(w)` 包含完整的 `encRowBits` 位数据
- 对于 cache hit，直接传递行数据

#### MSHRs 响应
在 `mshrs.scala` 中，为两种 MSHR 类型添加了 `row_data` 支持：

**1. 普通 MSHR (BoomMSHR):**
```scala
io.resp.bits.row_data := data  // Pass full row data for fat-load
```
- `data` 变量包含从 line buffer 读取的完整行数据
- 通过 `io.lb_resp` 获取，类型为 `UInt(encRowBits.W)`

**2. MMIO MSHR (BoomMMIOUnit):**
```scala
io.resp.bits.row_data := 0.U  // MMIO doesn't support row data
```
- MMIO 访问不支持 row data，设为 0
- MMIO 请求通常是单字访问，不需要完整行

### 数据流

```
DCache Hit 路径:
Cache Array → s2_data_muxed → cache_resp.row_data → LSU

DCache Miss 路径:
TileLink → Line Buffer → MSHR → io.resp.row_data → LSU

MMIO 路径:
TileLink → MMIO Unit → io.resp.row_data (= 0) → LSU
```

### 使用场景
当执行 fat-load 指令时：
1. Load 请求发送到 DCache
2. DCache/MSHR 响应时返回 `row_data`
3. LSU 在 writeback 阶段检测到 `is_fat_load` 标志
4. 将 `row_data` 写入指定的 CLAR bank

---

## 功能 2: Load-Clar 降级机制

### 背景
Load-clar 指令从 CLAR banks 读取数据，但如果存在 store-load 冲突，数据可能不一致。需要检测这种情况并将 load-clar 降级为普通 load。

### 实现细节

#### 新增字段
在 `LDQEntry` 中添加：
```scala
val clar_downgraded = Bool()  // load-clar downgraded to normal load due to st-ld conflict
```

#### 降级检测逻辑
在 LSU 的 writeback 阶段（`lsu.scala` 第 1395 行附近）：

```scala
for (i <- 0 until numLdqEntries) {
  when (ldq(i).valid && ldq(i).bits.is_load_clar && 
        !ldq(i).bits.clar_downgraded && ldq(i).bits.addr.valid) {
    val ld_addr = ldq(i).bits.addr.bits
    val ld_mask = GenByteMask(ld_addr, ldq(i).bits.uop.mem_size)
    
    // Check all older stores in STQ
    for (j <- 0 until numStqEntries) {
      when (stq(j).valid && ldq(i).bits.st_dep_mask(j) && stq(j).bits.addr.valid) {
        val st_addr = stq(j).bits.addr.bits
        val st_mask = GenByteMask(st_addr, stq(j).bits.uop.mem_size)
        val addr_match = (st_addr(corePAddrBits-1,3) === ld_addr(corePAddrBits-1,3))
        val mask_conflict = (ld_mask & st_mask) =/= 0.U
        
        // If there's any conflict with older stores, downgrade to normal load
        when (addr_match && mask_conflict) {
          ldq(i).bits.clar_downgraded := true.B
        }
      }
    }
  }
}
```

#### 检测条件
降级发生在以下情况：
1. **地址匹配:** Store 和 load 访问相同的双字地址（8 字节对齐）
2. **字节掩码冲突:** Store 和 load 的字节掩码有重叠
3. **依赖关系:** Store 在 load 的 `st_dep_mask` 中，表示它比 load 更老

#### Load-Clar 执行控制
修改后的 `can_fire_load_clar` 逻辑：
```scala
val can_fire_load_clar = widthMap(w => {
  val has_ready_clar = (0 until numLdqEntries).map(i => {
    val e = ldq(i)
    e.valid && e.bits.is_load_clar && 
    !e.bits.clar_downgraded &&  // Don't fire if downgraded
    clars(e.bits.clar_bank_id).io.read_ready && 
    !e.bits.succeeded &&
    !IsKilledByBranch(io.core.brupdate, e.bits.uop)
  }).reduce(_||_)
  has_ready_clar && !dmem_resp_fired(w) && !wb_forward_valid(w)
})
```

### 降级后的行为
被降级的 load-clar 指令：
1. **不会从 CLAR 读取:** `!e.bits.clar_downgraded` 条件阻止从 CLAR 读取
2. **等待正常路径:** 
   - 如果可以 store-to-load forward，从 STQ 获取数据
   - 否则，等待从 DCache 的响应
3. **保持语义正确:** 确保读取到最新的数据

### 检测时机
- **位置:** Writeback 阶段，在尝试从 CLAR 读取之前
- **频率:** 每个周期检查所有有效的 load-clar 指令
- **持久性:** 一旦设置 `clar_downgraded`，该标志在指令生命周期内保持

---

## 集成和影响

### 对 Fat-Load 的影响
- Fat-load 现在可以正确地将完整行数据写入 CLAR
- 支持 cache hit 和 miss 两种情况
- MMIO 访问返回 0，不影响功能

### 对 Load-Clar 的影响
- 增强了内存一致性保证
- 避免了从 CLAR 读取过期数据
- 降级机制确保了正确性，可能略微影响性能

### 性能考虑

**Row Data 传输:**
- ✅ 无额外延迟（数据已在 cache 流水线中）
- ✅ 不增加带宽压力（只是传递额外的信号）

**降级检测:**
- ⚠️ 每周期检查 `numLdqEntries × numStqEntries` 对
- ✅ 逻辑简单，关键路径影响小
- ✅ 只在有 load-clar 指令时激活

### 正确性保证
1. **内存顺序:** 降级确保 load 看到最新的 store 数据
2. **缓存一致性:** Row data 从一致的缓存行读取
3. **异常处理:** 降级的 load 正常处理 TLB 和缓存 miss

---

## 使用示例

### Fat-Load 场景
```
// 加载完整缓存行到 CLAR bank 0
fat-load x0, 0(x1)
```
**执行流程:**
1. 发送 load 请求到 DCache
2. Cache hit → `s2_data_muxed` 包含行数据
3. `io.dmem.resp.row_data` 传输到 LSU
4. Writeback 阶段写入 `clars[0]`

### Load-Clar 正常场景
```
// 从 CLAR bank 0 加载
load-clar x2, bank=0, offset=1
```
**执行流程:**
1. 检查 STQ，无冲突
2. `clar_downgraded = false`
3. 从 CLAR bank 0 读取数据
4. 直接写回寄存器

### Load-Clar 降级场景
```
store x3, 0(x1)          // Store to address A
load-clar x2, bank=0     // Load-clar from address A
```
**执行流程:**
1. Load-clar 在 LDQ 中等待
2. 检测到与 older store 的地址/掩码冲突
3. 设置 `clar_downgraded = true`
4. **不从 CLAR 读取**
5. 等待 store-to-load forward 或 DCache 响应
6. 获取正确的数据并写回

---

## 调试和验证

### 关键信号监控
```scala
// 监控 row data 传输
cache_resp(w).bits.row_data
mshrs.io.resp.bits.row_data

// 监控降级状态
ldq(i).bits.clar_downgraded
ldq(i).bits.is_load_clar

// 监控冲突检测
addr_match && mask_conflict
```

### Assert 检查
建议添加的断言：
```scala
// 确保降级的 load-clar 不会从 CLAR 读取
assert(!(ldq(i).bits.is_load_clar && 
         ldq(i).bits.clar_downgraded && 
         clars(ldq(i).bits.clar_bank_id).io.read_en))

// 确保 MMIO 的 row_data 为 0
assert(!(mmio_resp.valid && mmio_resp.bits.row_data =/= 0.U))
```

---

## 未来改进

### Row Data 传输
1. **压缩传输:** 对于稀疏数据，可以压缩 row data
2. **选择性传输:** 只在 fat-load 时传输完整行
3. **多行支持:** 支持跨行的 fat-load

### 降级机制
1. **更精细的检测:** 只在真正冲突时降级（考虑 word offset）
2. **预测降级:** 根据历史预测是否会冲突
3. **快速路径:** 为非降级的 load-clar 优化延迟
4. **统计信息:** 跟踪降级率以指导优化

### 性能优化
1. **并行检测:** 将 STQ 检查并行化
2. **Early kill:** 在更早阶段检测冲突
3. **CLAR 预取:** 主动预取可能需要的行

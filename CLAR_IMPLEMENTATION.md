# CLAR (Cache Line Address Register) 实现文档

## 概述
本文档描述了在 BOOM (Berkeley Out-of-Order Machine) 处理器中实现的 load-clar 和 fat-load 功能。

## 功能说明

### 1. Fat-Load
Fat-load 是一种特殊的加载指令，它从缓存中读取一整行数据(row data)并存储到 CLAR banks 中。

**特性:**
- 一次性读取 `encRowBits` 位的数据
- 将数据存储到指定的 CLAR bank (0-3)
- 数据在 CLARS 中管理，供后续的 load-clar 指令使用

**流程:**
1. Fat-load 指令在 LSU 中执行普通的 load 操作
2. 当 DCache 响应返回时，除了返回正常的 `data` 字段，还返回 `row_data` 字段
3. 在 writeback 阶段，检测到 `is_fat_load` 标志后，将 `row_data` 写入指定的 CLAR bank
4. CLAR bank 标记为 valid 和 ready 状态

### 2. Load-Clar
Load-clar 是一种从 CLAR banks 中读取数据的加载指令，不需要访问缓存。

**特性:**
- 直接从 CLAR bank 读取数据，避免缓存访问
- 指定 bank ID (0-3) 和 offset (字偏移)
- 在 LSU 的 writeback 阶段抢占端口写入寄存器堆
- 优先级低于 DCache 响应和 store-to-load forwarding

**流程:**
1. Load-clar 指令在 LDQ 中等待
2. 在 writeback 阶段，检查对应的 CLAR bank 是否 ready
3. 如果 ready，从 CLAR bank 读取数据
4. 根据 offset 提取相应的字(word)
5. 通过 `io.core.exe.iresp` 或 `fresp` 端口写入寄存器堆

## 数据结构

### ClarBank
```scala
class ClarBank {
  val io = {
    val write_val: Bool      // 写使能信号
    val write_row: UInt      // 写入的行数据 (encRowBits 位)
    val write_prc: UInt(4.W) // PRC (可用于权限/状态管理)
    val read_en: Bool        // 读使能信号
    val read_data: UInt      // 读出的行数据
    val read_prc: UInt(4.W)  // 读出的 PRC
    val read_ready: Bool     // Bank 是否有有效数据
    val flush: Bool          // 清空信号
  }
  
  // 内部状态
  val valid: Bool          // Bank 是否有效
  val data_ready: Bool     // 数据是否就绪
  val prc: UInt(4.W)       // 权限/状态码
  val regionData: UInt     // 存储的行数据
}
```

### MicroOp 新增字段
```scala
val is_load_clar: Bool     // 是否为 load-clar 指令
val is_fat_load: Bool      // 是否为 fat-load 指令
val clar_bank_id: UInt(2.W) // 使用的 CLAR bank (0-3)
val clar_offset: UInt(2.W)  // bank 内的字偏移 (0-3)
```

### LDQEntry 新增字段
```scala
val is_load_clar: Bool
val clar_bank_id: UInt(2.W)
val clar_offset: UInt(2.W)
val is_fat_load: Bool
```

## LSU 修改

### CLARS 实例化
在 LSU 中创建了 4 个 ClarBank 实例：
```scala
val clars = Seq.fill(4) { Module(new ClarBank) }
```

### Writeback 阶段处理

#### Fat-Load 处理
```scala
when (ldq(ldq_idx).bits.is_fat_load) {
  val fat_bank_id = ldq(ldq_idx).bits.clar_bank_id
  clars(fat_bank_id).io.write_val := true.B
  clars(fat_bank_id).io.write_row := io.dmem.resp(w).bits.row_data
  clars(fat_bank_id).io.write_prc := 0.U
}
```

#### Load-Clar 处理
1. **检测就绪的 load-clar:**
   - 遍历 LDQ，找到有效的 load-clar 指令
   - 检查对应 CLAR bank 是否 ready
   - 使用年龄优先编码器选择最老的就绪指令

2. **数据读取:**
   - 使能对应 CLAR bank 的读端口
   - 从 bank 读取完整行数据
   - 根据 offset 提取目标字

3. **写回寄存器:**
   - 通过 `iresp` (整数) 或 `fresp` (浮点) 端口写回
   - 标记 LDQ entry 为 succeeded

## 优先级
在 writeback 阶段的优先级(从高到低):
1. DCache 响应 (`dmem_resp_fired`)
2. Store-to-Load Forwarding (`wb_forward_valid`)
3. Load-Clar (`can_fire_load_clar`)

## 使用示例

### Fat-Load 示例
```
// 假设要加载地址 0x1000 的整行到 CLAR bank 2
fat-load x0, 0(x1)     // x1 = 0x1000
// 微架构设置: is_fat_load=1, clar_bank_id=2
```

### Load-Clar 示例
```
// 从 CLAR bank 2 的 offset 1 加载数据到 x2
load-clar x2, bank=2, offset=1
// 微架构设置: is_load_clar=1, clar_bank_id=2, clar_offset=1
```

## 注意事项

1. **Bank 数量:** 当前实现支持 4 个 CLAR banks (bank ID 0-3)

2. **数据宽度:** 
   - Row data: `encRowBits` 位 (通常是缓存行宽度)
   - Word size: `xLen` 位 (32 或 64 位)

3. **并发控制:** 
   - Fat-load 和 load-clar 不会同时写同一个 bank
   - Load-clar 读取时不会与写入冲突(通过 valid 和 ready 标志控制)

4. **刷新机制:** 
   - 每个 bank 支持 flush 操作
   - Flush 会清除 valid 和 data_ready 标志

5. **异常处理:**
   - Load-clar 支持分支预测失败的撤销
   - 通过 branch mask 检查指令是否被杀死

## 性能优化

1. **减少缓存访问:** Load-clar 从 CLAR 读取，避免重复访问 DCache
2. **提高带宽利用:** Fat-load 一次性加载整行数据
3. **降低延迟:** Load-clar 不需要 TLB 转换和缓存查找

## 未来改进方向

1. **动态 Bank 分配:** 根据运行时需求动态分配 CLAR banks
2. **预取支持:** 结合预取器自动触发 fat-load
3. **多级 CLAR:** 支持不同大小的 CLAR 结构
4. **压缩存储:** 在 CLAR 中存储压缩数据以提高容量

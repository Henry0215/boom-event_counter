# CMAP Bypass Load Debugging Plan

## Problem Summary
- FPGA bypass load causes random kernel data corruption (different slab each run)
- Debug version (bypass disabled) works fine
- Previous fixes (!is_amo, store_blocked_counter) didn't help

## Static Analysis Results
After exhaustive code review, no single definitive bug was found.
All CMAP arithmetic, invalidation, ordering, and pipeline paths appear logically correct.

## Recommended Debugging Strategy

### Step 1: Add Runtime Verification WITHOUT Disabling Bypass (HIGHEST PRIORITY)

Add a **post-execute verification** in LSU. When a bypass load completes TLB translation 
via retry path, also send it through the issue queue for AGU verification. Compare the 
two vaddrs. If mismatch, set `order_fail` to re-execute the load with AGU result.

Key changes in `lsu.scala`:

```scala
// In the retry TLB completion section (around line 920):
when (will_fire_load_retry(w) && !exe_tlb_miss(w)) {
  // After TLB hit for a bypass load, force it to also go through AGU
  // by keeping it in the issue queue path for verification
  // If the load had cmap_addr_ready at dispatch, mark it for verification
  when (ldq(ldq_idx).bits.uop.cmap_addr_ready) {
    // Store the CMAP-predicted vaddr for later comparison
    ldq(ldq_idx).bits.cmap_verified_vaddr := exe_tlb_vaddr(w)
    ldq(ldq_idx).bits.needs_cmap_verify := true.B
  }
}
```

This approach keeps bypass enabled but adds safety checking.

### Step 2: Simpler Alternative - Disable Bypass, Add CMAP Counters

If Step 1 is too complex, temporarily disable bypass (`false.B &&` in dispatch.scala) 
but add CMAP VERIFY counters:
- Count total CMAP predictions
- Count CMAP mismatches (predicted vaddr != AGU vaddr)
- Run Linux kernel to see if any mismatches occur

### Step 3: Focus Areas for CMAP Prediction Errors

If Step 2 shows mismatches, the bug is in CMAP prediction logic. Focus on:

1. **pending_offset residue after invalidation** (Chisel last-writer-wins):
   When `add xR, ...` (slot 0) and `addi xR, xR, imm` (slot 1) are in the same
   decode cycle, ADDI Processing (code order: later) overwrites pending_offset
   set by Single-Register Invalidation (code order: earlier). The residue value
   persists until next Load miss resets it.
   
   **Fix**: Add `same_cycle_invalidates_lrs1` check for non-load register writes:
   ```scala
   // In ADDI Processing, also check if earlier slot INVALIDATED the register
   val earlier_invalidation = (0 until w).map { i =>
     dec_fire(i) && dec_uops(i).ldst_val && dec_uops(i).dst_rtype === RT_FIX &&
     dec_uops(i).ldst === lrs1 &&
     !(is_prev_addi_i && dec_uops(i).ldst === dec_uops(i).lrs1)
   }
   val same_cycle_invalidation = if (w == 0) false.B else earlier_invalidation.reduce(_ || _)
   ```

2. **ADDIW sign extension**: `uopADDIW` performs 32-bit addition with sign extension.
   The CMAP treats ADDIW the same as ADDI for pending_offset accumulation.
   But ADDIW truncates the result to 32 bits and sign-extends to 64 bits.
   This means the actual register value change from ADDIW may differ from
   the 12-bit immediate when there's 32-bit overflow.
   
   **Example**: If x5 = 0x000000007FFFFFF0 and `addiw x5, x5, 0x20`:
   - Actual result: (0x7FFFFFF0 + 0x20) & 0xFFFFFFFF = 0x80000010
   - Sign-extended to 64-bit: 0xFFFFFFFF80000010
   - Address change: 0xFFFFFFFF80000010 - 0x000000007FFFFFF0 = 0xFFFFFFFF00000020
   - But CMAP accumulates pending_offset += 0x20 (treating it like ADDI)
   - **MASSIVE ERROR**: CMAP thinks vaddr changed by +0x20, but actually changed by -0x100000000+0x20

   **THIS IS VERY LIKELY THE ROOT CAUSE!**

3. **8-bit sequence number wrap-around**: Unlikely in practice but theoretically possible.

## CRITICAL BUG: ADDIW Handling

The CMAP treats `ADDIW` identically to `ADDI` for pending_offset accumulation.
But `ADDIW` performs 32-bit addition with sign extension to 64 bits.

When the 32-bit result overflows (crosses the 0x7FFFFFFF / 0x80000000 boundary),
the actual 64-bit register value change is NOT equal to the immediate value.

**Example**:
- Base register x5 = 0x7FFFFFF0 (positive 32-bit value)
- `addiw x5, x5, 32` → 32-bit result: 0x80000010 → sign-ext: 0xFFFFFFFF80000010
- Actual change: 0xFFFFFFFF80000010 - 0x7FFFFFF0 = very large negative number
- CMAP pending_offset only adds 32, predicting wrong vaddr by ~4GB!

**This happens in Linux kernel frequently** because kernel addresses use
the upper half of the 64-bit address space.

### Fix for ADDIW:
Option A: Don't accumulate ADDIW in pending_offset. Treat ADDIW like a regular 
          instruction that invalidates the CMAP entry.
Option B: Only allow ADDI (not ADDIW) for pending_offset accumulation.

```scala
// In CMAP ADDI Processing:
val is_addi_only = uop.uopc === uopADDI  // Exclude uopADDIW!
when (is_addi_only && uop.ldst_val) {     // Changed from (is_addi && ...)
```

This single-line change could fix the kernel panic!

//******************************************************************************
// Copyright (c) 2012 - 2019, The Regents of the University of California (Regents).
// All Rights Reserved. See LICENSE and LICENSE.SiFive for license details.
//------------------------------------------------------------------------------

//------------------------------------------------------------------------------
//------------------------------------------------------------------------------
// BOOM Instruction Dispatcher
//------------------------------------------------------------------------------
//------------------------------------------------------------------------------


package boom.exu

import chisel3._
import chisel3.util._

import freechips.rocketchip.config.Parameters

import boom.common._
import boom.util._

class DispatchIO(implicit p: Parameters) extends BoomBundle
{
  // incoming microops from rename2
  val ren_uops = Vec(coreWidth, Flipped(DecoupledIO(new MicroOp)))

  // outgoing microops to issue queues
  // N issues each accept up to dispatchWidth uops
  // dispatchWidth may vary between issue queues
  val dis_uops = MixedVec(issueParams.map(ip=>Vec(ip.dispatchWidth, DecoupledIO(new MicroOp))))
}

abstract class Dispatcher(implicit p: Parameters) extends BoomModule
{
  val io = IO(new DispatchIO)
}

/**
 * This Dispatcher assumes worst case, all dispatched uops go to 1 issue queue
 * This is equivalent to BOOMv2 behavior
 */
class BasicDispatcher(implicit p: Parameters) extends Dispatcher
{
  issueParams.map(ip=>require(ip.dispatchWidth == coreWidth))

  // CMAP optimization: Load instructions with ready address bypass issue queue
  // STA does NOT bypass (it still needs issue queue for rs2/STD),
  // but its CMAP-predicted vaddr is used by SAB at dispatch for conflict detection.
  val addr_ready_bypass = VecInit((0 until coreWidth).map(w => 
    io.ren_uops(w).bits.cmap_addr_ready && 
    io.ren_uops(w).bits.uses_ldq))

  // // Debug: Print CMAP bypass at dispatch
  // for (w <- 0 until coreWidth) {
  //   when (io.ren_uops(w).valid && addr_ready_bypass(w)) {
  //     printf("[DISPATCH] CMAP BYPASS: slot=%d rob_idx=%d pc=0x%x paddr=0x%x uses_ldq=%d is_sta=%d iq_type=0x%x\n",
  //       w.U, io.ren_uops(w).bits.rob_idx, io.ren_uops(w).bits.debug_pc, io.ren_uops(w).bits.cmap_paddr,
  //       io.ren_uops(w).bits.uses_ldq, io.ren_uops(w).bits.ctrl.is_sta, io.ren_uops(w).bits.iq_type)
  //   }
  // }

  val ren_readys = io.dis_uops.map(d=>VecInit(d.map(_.ready)).asUInt).reduce(_&_)

  for (w <- 0 until coreWidth) {
    io.ren_uops(w).ready := ren_readys(w)
  }

  for {i <- 0 until issueParams.size
       w <- 0 until coreWidth} {
    val issueParam = issueParams(i)
    val dis        = io.dis_uops(i)

    // Only dispatch to issue queue if the uop uses this queue AND is not bypassing
    val uses_this_iq = (io.ren_uops(w).bits.iq_type & issueParam.iqType.U) =/= 0.U
    dis(w).valid := io.ren_uops(w).valid && uses_this_iq && !addr_ready_bypass(w)
    dis(w).bits  := io.ren_uops(w).bits
  }
}

/**
 *  Tries to dispatch as many uops as it can to issue queues,
 *  which may accept fewer than coreWidth per cycle.
 *  When dispatchWidth == coreWidth, its behavior differs
 *  from the BasicDispatcher in that it will only stall dispatch when
 *  an issue queue required by a uop is full.
 */
class CompactingDispatcher(implicit p: Parameters) extends Dispatcher
{
  issueParams.map(ip => require(ip.dispatchWidth >= ip.issueWidth))

  val ren_readys = Wire(Vec(issueParams.size, Vec(coreWidth, Bool())))

  for (((ip, dis), rdy) <- issueParams zip io.dis_uops zip ren_readys) {
    val ren = Wire(Vec(coreWidth, Decoupled(new MicroOp)))
    ren <> io.ren_uops

    val uses_iq = ren map (u => (u.bits.iq_type & ip.iqType.U).orR)
    
    // CMAP optimization: Load instructions with ready address bypass issue queue
    val addr_ready_bypass = ren map (u => 
      u.bits.cmap_addr_ready && u.bits.uses_ldq)

    // Only request an issue slot if the uop needs to enter that queue
    // AND it's not bypassing via CMAP address ready
    (ren zip io.ren_uops zip uses_iq zip addr_ready_bypass) foreach {case (((u,v),q),bypass) =>
      u.valid := v.valid && q && !bypass}

    val compactor = Module(new Compactor(coreWidth, ip.dispatchWidth, new MicroOp))
    compactor.io.in  <> ren
    dis <> compactor.io.out

    // The queue is considered ready if:
    // 1. The uop doesn't use this queue, OR
    // 2. The uop is bypassing via CMAP address ready
    rdy := (ren zip uses_iq zip addr_ready_bypass) map {case ((u,q),bypass) => 
      u.ready || !q || bypass}
  }

  (ren_readys.reduce((r,i) =>
      VecInit(r zip i map {case (r,i) =>
        r && i})) zip io.ren_uops) foreach {case (r,u) =>
          u.ready := r}
}

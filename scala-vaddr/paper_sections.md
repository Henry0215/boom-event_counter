# 基于地址预测的超标量处理器 Load 指令延迟优化

---

## 第一章 引论

### 1.1 选题背景

随着集成电路工艺的持续演进，现代高性能处理器在指令级并行（Instruction-Level Parallelism, ILP）的挖掘上取得了长足的进步。超标量（Superscalar）处理器通过乱序执行（Out-of-Order Execution）、寄存器重命名（Register Renaming）和推测执行（Speculative Execution）等技术，能够在每个时钟周期内发射并执行多条指令，显著提升了单线程性能[1]。然而，处理器核心计算速度的增长远超存储系统访问速度的提升，这一被称为"存储墙"（Memory Wall）的问题已成为制约处理器性能的首要瓶颈[2]。

在超标量乱序处理器的流水线中，Load 指令占据着极为关键的地位。根据经典教科书的统计，Load 指令约占全部动态指令的 25%–35%[3]。本文基于 BOOM v3 处理器在 SPEC CPU 2007 基准测试集上的实测数据验证了这一范围：在 19 个基准程序中，**已提交 Load 指令占已提交总指令数的 16.74%–33.90%，平均为 24.87%**；若将 Store 指令一并计入，访存指令合计平均占比达 34.06%。表 1-1 给出了各基准程序的详细统计。更重要的是，Load 指令位于数据依赖链的关键路径上：大量后续的算术、逻辑和分支指令都依赖于 Load 的返回数据。因此，Load 指令的执行延迟直接决定了这些依赖指令的发射时机，对处理器的整体吞吐率产生放大效应。

**表 1-1　SPEC CPU 2007 各基准程序提交的 Load/Store 指令占比（已提交指令数 ≈ 200M）**

| 基准程序 | 提交 Load 占比 | 提交 Store 占比 | 访存合计占比 |
|:---|:---:|:---:|:---:|
| astar | 28.24% | 13.46% | 41.70% |
| bwaves | 33.90% | 1.75% | 35.65% |
| bzip2 | 23.32% | 9.35% | 32.67% |
| cactusADM | 26.61% | 9.89% | 36.50% |
| calculix | 19.36% | 0.30% | 19.66% |
| dealII | 21.84% | 5.56% | 27.40% |
| gcc | 22.64% | 16.32% | 38.95% |
| gobmk | 21.28% | 11.41% | 32.70% |
| h264ref | 31.56% | 6.17% | 37.73% |
| hmmer | 23.45% | 11.15% | 34.60% |
| lbm | 16.74% | 7.87% | 24.61% |
| leslie3d | 25.06% | 8.61% | 33.67% |
| libquantum | 19.86% | 7.44% | 27.30% |
| milc | 28.34% | 14.81% | 43.15% |
| namd | 22.11% | 4.40% | 26.51% |
| omnetpp | 25.71% | 15.66% | 41.37% |
| povray | 31.64% | 15.31% | 46.96% |
| sjeng | 19.71% | 6.56% | 26.28% |
| xalancbmk | 31.12% | 8.65% | 39.77% |
| **平均** | **24.87%** | **9.19%** | **34.06%** |

在传统的乱序处理器微架构中，一条 Load 指令从译码（Decode）到最终获得数据，需要经历多个流水级：首先在发射队列（Issue Queue, IQ）中等待源操作数就绪，然后经过选择（Select）逻辑被发射至地址生成单元（Address Generation Unit, AGU），计算出虚拟地址后访问数据 TLB（Translation Lookaside Buffer）进行地址翻译，最后访问 L1 数据缓存（L1 Data Cache）。以 RISC-V 开源处理器 BOOM（Berkeley Out-of-Order Machine）v3 的 Medium 配置为例，从发射到数据返回至少需要 3–4 个时钟周期（假设 L1 缓存命中）。在此期间，所有依赖该 Load 的指令均被阻塞在发射队列中，造成流水线气泡，降低了有效 IPC（Instructions Per Cycle）。

尽管现代处理器已经采用了投机唤醒（Speculative Wakeup）机制——即在 Load 访问缓存前就乐观地唤醒依赖指令，以部分隐藏 Load 延迟——但这一机制仍受限于 Load 必须首先通过发射队列的选择仲裁。在发射队列资源紧张或存在长延迟指令占用的情况下，Load 指令本身的发射就可能被延迟数个周期，进而推迟整个依赖链的执行。此外，当投机唤醒失败（例如 L1 缓存未命中）时，处理器需要重放（Replay）被错误唤醒的指令，造成额外的性能损失和功耗开销。

### 1.2 研究意义

鉴于 Load 指令在乱序处理器性能中的关键地位，围绕降低 Load 执行延迟的研究持续受到学术界和工业界的关注。现有工作主要从以下几个方面展开：

**（1）Load 地址预测与提前执行。** 如果能够在流水线的早期阶段（如译码阶段）获取 Load 的目标地址，则 Load 可以绕过发射队列的等待，直接提前发起缓存访问，从而大幅缩短有效延迟。该方向的技术包括基于步进模式的统计预测（如 Austin 和 Sohi 在 1995 年提出的"零周期 Load"概念[4]）和基于寄存器值缓存的精确地址获取。近年来，Apple Silicon 处理器中被发现内置了 Load 地址预测器（Load Address Predictor, LAP）[5]，表明这一技术路线已在商用处理器中得到实际部署。

**（2）存储依赖预测（Memory Dependence Prediction, MDP）。** 乱序执行中 Load 与先序 Store 之间的地址依赖关系是制约 Load 提前执行的另一重要因素。MDP 技术通过预测 Load-Store 之间的依赖关系，允许预测无依赖的 Load 绕过未解析的 Store 提前执行，从而提高访存并行度[6][7]。

**（3）投机调度与依赖指令唤醒。** 即使 Load 能够提前发射，其依赖指令的唤醒时机仍然影响性能。投机调度（Speculative Scheduling）技术预测 Load 的缓存命中延迟，提前唤醒依赖指令，以在 Load 数据返回时立即使用[8][9]。

本文的工作从一个新颖的角度出发：在超标量处理器的译码阶段引入一个轻量级的寄存器基址缓存结构——**CMAP（Cache-line Address Map）**。CMAP 为每个逻辑寄存器维护一份基址值（base register value）的精确副本，该副本在 Load/Store 经 AGU 执行后由 LSU 回写更新。当后续 Load 指令译码时，CMAP 直接以缓存的基址加上当前立即数计算出目标地址。由于 CMAP 存储的是实际观测到的精确寄存器值而非统计推测，其投机性仅在于"缓存的基址值是否仍与寄存器当前值一致"——即时效性（Staleness）推测，而非值本身的猜测。命中的 Load 绕过发射队列，直接注入 Load Queue 通过重试（Retry）路径执行，从而节省了发射队列排队和仲裁的延迟。在此基础上，本文进一步提出了三项协同优化：

- **ADDI 累积偏移优化**：识别并追踪基址寄存器的 ADDI 自增模式，将步进偏移累积到预测地址中，大幅提升 CMAP 对循环顺序访问模式的覆盖率。
- **SAB（Store Address Buffer）冲突预检测**：在 Dispatch 阶段利用预测的 Store 地址进行轻量级 Store-Load 冲突检测，降低 CMAP 提前执行 Load 引入的内存序违例（Memory Order Violation）风险。
- **投机唤醒扩展**：将 BOOM 原有的投机唤醒机制扩展覆盖 CMAP 的 Retry 路径，使绕过发射队列的 Load 也能投机唤醒其依赖指令，进一步缩短从 Load 发射到依赖指令执行的延迟。

本文基于开源 RISC-V 超标量处理器 BOOM v3（Chipyard 1.13.0 框架，MediumBoomV3Config 配置）进行 RTL 级实现与验证，以 SPEC CPU 2007 基准测试集进行性能评估。实验结果表明，上述优化组合在硬件开销极低的条件下（新增约 2,320 bit ≈ 290 Byte 寄存器），实现了 IPC GEOMEAN **+2.07%** 的提升，其中多个基准测试获得了超过 5% 的性能改善。

### 1.3 引论参考文献

[1] J. E. Smith and G. S. Sohi, "The microarchitecture of superscalar processors," *Proceedings of the IEEE*, vol. 83, no. 12, pp. 1609–1624, Dec. 1995.

[2] W. A. Wulf and S. A. McKee, "Hitting the memory wall: Implications of the obvious," *ACM SIGARCH Computer Architecture News*, vol. 23, no. 1, pp. 20–24, Mar. 1995.

[3] J. L. Hennessy and D. A. Patterson, *Computer Architecture: A Quantitative Approach*, 6th ed. Morgan Kaufmann, 2017.

[4] T. M. Austin and G. S. Sohi, "Zero-cycle loads: Microarchitecture support for reducing load latency," in *Proc. 28th Annual IEEE/ACM International Symposium on Microarchitecture (MICRO-28)*, 1995, pp. 82–92.

[5] J. Kim, D. Genkin, and Y. Yarom, "SLAP: Data speculation attacks via load address prediction on Apple Silicon," in *Proc. IEEE Symposium on Security and Privacy (S&P)*, 2025.

[6] G. Z. Chrysos and J. S. Emer, "Memory dependence prediction using store sets," in *Proc. 25th Annual International Symposium on Computer Architecture (ISCA)*, 1998, pp. 142–153.

[7] A. Moshovos, S. E. Breach, T. N. Vijaykumar, and G. S. Sohi, "Dynamic speculation and synchronization of data dependences," in *Proc. 24th Annual International Symposium on Computer Architecture (ISCA)*, 1997, pp. 181–193.

[8] A. Perais, A. Seznec, P. Michaud, A. Sembrant, and E. Hagersten, "Cost-effective speculative scheduling in high performance processors," in *Proc. 42nd Annual International Symposium on Computer Architecture (ISCA)*, 2015, pp. 247–259.

[9] I. Kim and M. H. Lipasti, "Understanding scheduling replay schemes," in *Proc. 10th International Symposium on High Performance Computer Architecture (HPCA)*, 2004, pp. 198–209.

---

## 第二章 相关工作

本章围绕 Load 指令延迟优化的三个核心方面回顾已有工作：指令发射与地址预测优化（2.1 节）、存储依赖预测技术（2.2 节）以及 Load 投机调度与延迟隐藏机制（2.3 节）。

### 2.1 指令发射优化与 Load 地址预测

在乱序超标量处理器中，发射队列（Issue Queue）负责缓存已译码的指令，并通过唤醒-选择（Wakeup-Select）逻辑在操作数就绪后将指令发射至执行单元。发射队列的容量有限，而其仲裁延迟（通常是 CAM 匹配 + 优先级选择的关键路径）是处理器时钟频率的制约因素之一。对于 Load 指令而言，进入发射队列等待基址寄存器就绪、经选择后发射至 AGU 计算地址、再访问 TLB 和缓存，整个流程构成了一条长延迟链。

**零周期 Load（Zero-cycle Loads）。** Austin 和 Sohi [4] 在 1995 年提出了零周期 Load 的概念。其核心思想是利用 Load 地址的可预测性：程序中大量 Load 指令访问的地址遵循步进（Stride）模式或上次值（Last Value）模式。他们提出使用基于 PC 索引的增量表（Delta Table）预测 Load 地址，在地址计算之前就向缓存发起投机访问。当预测正确时，Load 的有效延迟可降至零周期。该工作的评估表明，步进预测器（Stride Predictor）能够以较高的准确率覆盖相当比例的 Load 指令。此工作开创了 Load 地址预测的研究方向，是本文 CMAP 设计的重要思想来源。与 Zero-cycle Loads 基于 PC 索引的步进预测器不同，本文提出的 CMAP 以逻辑寄存器号为索引，直接缓存经 LSU 回写的精确基址值（而非通过步进模式推测），天然地支持不同 PC 的 Load 共享同一基址寄存器的场景（例如结构体的不同字段访问），并且无需维护步进检测逻辑。

**寄存器追踪的地址早解析（Early Load Address Resolution via Register Tracking）。** Bekerman 等人 [10] 在 ISCA 2000 上提出了一种通过追踪寄存器值来提前解析 Load 地址的技术。该方案在处理器前端维护一个寄存器追踪表（Register Tracking Table），追踪形如 `reg ± immediate` 的简单运算链。当一条 Load 指令的基址寄存器的整条计算链可被完整追踪时（即链上所有指令均为 `reg ± imm` 形式的简单运算），该 Load 被标记为"可追踪"（Trackable），其地址可在前端直接计算，从而提前发射至存储子系统。该技术的关键特性在于其非投机性（Non-speculative）：追踪表精确维护寄存器的计算历史，地址的正确性由构造保证，无需后续验证。

本文的 CMAP 与 Register Tracking 在"于前端提前获取 Load 地址"这一目标上高度一致，但在机制设计上存在以下本质差异：

（1）**覆盖范围**。Register Tracking 要求基址寄存器的整条计算链必须全部由 `reg ± imm` 形式的简单运算构成；一旦链上出现 Load 指令、乘法、移位或其他复杂运算，该链即断裂，后续 Load 变为"不可追踪"（Untrackable）。而程序中大量的指针解引用（`ld x5, 0(x6)` 后 `ld x7, 8(x5)`）、链表遍历和间接访问模式都会导致这种情况。CMAP 没有此限制：无论基址寄存器的值是由 Load、ALU 还是任何其他指令产生的，只要该寄存器曾被用作 Load/STA 的基址，LSU 回写时就会将实际计算出的精确基址值（`vaddr - imm`）缓存到 CMAP 中。因此 CMAP 能够覆盖 Register Tracking 无法处理的 Load-after-Load 链式访问等场景。

（2）**值的来源与投机性质**。Register Tracking 通过追踪前端指令流的 `reg ± imm` 计算链来推导寄存器值，是非投机的，但仅限于可追踪的简单运算链。CMAP 存储的是 LSU 回写的**精确观测值**——即寄存器在上一次被用作基址时的实际值副本，并非统计预测或模式推测。CMAP 的投机性仅在于**时效性推测**：缓存的基址值是否仍与寄存器当前值一致。这种"精确值缓存 + 时效性推测"的设计使 CMAP 既保留了高精度（值本身是精确的），又突破了计算链追踪的覆盖率限制。

（3）**发射队列资源占用**。Register Tracking 方案中，提前解析地址的 Load 仍然需要占用发射队列（Issue Queue）表项，只是发射时机提前。CMAP 命中的 Load 则完全绕过发射队列，不占用 IQ 资源，等效于减小了 IQ 的有效负载，使其他指令能更快获得仲裁机会。

（4）**Store-Load 排序处理**。Register Tracking 对 Store-Load 排序采用保守策略：不允许 Trackable Load 绕过 Untrackable Store 执行，大幅限制了优化的有效应用范围。CMAP 同时为 Store 地址（STA）也提供缓存基址，结合 SAB 机制在 Dispatch 阶段进行精确的地址级冲突检测，以更细粒度的方式处理 Store-Load 排序，避免了过度保守的全局阻塞。

（5）**步进模式支持**。Register Tracking 虽然能追踪 `addi` 链，但其追踪的是精确的计算历史，对于较长的 ADDI 自增链，追踪表需要维护不断增长的累积偏移量。CMAP 的 ADDI 累积偏移机制（`cmap_pending_offset`）以独立的 14-bit 有符号寄存器专门追踪自增模式，设计更为简洁，并包含溢出保护和同周期多 ADDI 的前向转发链。

**Load Slice Core。** Carlson 等人 [11] 在 ISCA 2015 上提出了 Load Slice Core（LSC）微架构。LSC 的核心洞察是：提升处理器性能的关键在于加速 Load 指令的执行，因为 Load 位于大多数长延迟依赖链的根部。LSC 在传统乱序核心的基础上增加了一个专用的"Load Slice"前端，用于识别和优先执行与 Load 地址计算相关的指令切片（Slice）。通过将地址计算相关指令从完整的乱序引擎中分离出来优先处理，Load 可以更早地获取数据，整个执行窗口的有效利用率得到提升。LSC 以较低的面积开销实现了显著的性能提升。本文的 CMAP 方案与 LSC 共享"加速 Load 执行"的核心目标，但采用了不同的技术路径：CMAP 通过直接预测地址绕过发射队列，而 LSC 通过优先调度地址相关的指令切片来加速地址计算。

**SLAP: Apple Silicon 中的 Load 地址预测器。** 近期的安全研究 [5] 揭示了 Apple M 系列处理器（自 M2 起）中内置了 Load 地址预测器（LAP）。该 LAP 基于步进模式（Stride）预测 Load 地址，允许处理器在地址计算完成之前投机地发起数据缓存访问。这一发现从工业实践的角度验证了 Load 地址预测技术的有效性和实用性。Apple 的实现使用基于历史步进值的统计预测方案，而本文的 CMAP 采用精确寄存器基址值缓存方案——存储的是 LSU 回写的实际观测值而非统计推测值，两者在机制上具有本质差异，但都旨在将 Load 的缓存访问提前至地址计算之前。

**指令调度与发射队列优化。** Alipour 等人 [12] 在 HPCA 2020 上提出了 Delay-and-Bypass 调度策略。该工作识别出发射队列中的非关键指令（Non-critical Instructions），将它们延迟发射或绕过某些流水级，以减少关键路径上的资源竞争。这一"区分关键性进行差异化调度"的思想与本文的设计理念存在呼应：CMAP 本质上将高置信度的可预测 Load 识别出来，允许它们绕过发射队列的常规路径，从而为发射队列中的其他指令释放资源，同时加速了 Load 自身的执行。

### 2.2 存储依赖预测

在乱序执行的处理器中，Load 和 Store 的执行顺序可能与程序顺序不同。当一条 Load 指令在其前序的某条 Store 完成地址计算之前执行时，如果两者访问了相同的内存地址，就会产生内存序违例（Memory Order Violation）。处理器必须检测这种违例并重新执行受影响的 Load 及其后续依赖指令，代价高昂。为了在保证正确性的前提下最大化 Load-Store 并行度，存储依赖预测（Memory Dependence Prediction, MDP）技术应运而生。

**动态推测与数据依赖同步。** Moshovos 等人 [7] 在 ISCA 1997 上的开创性工作首次系统地研究了存储-加载（Store-Load）数据依赖的动态预测问题。他们指出，传统乱序处理器对所有 Load 采用"盲目推测"（Blind Speculation）策略——即总是允许 Load 绕过地址未解析的 Store 执行，违例时再进行补救。这种策略在冲突频繁时会导致大量的 Squash 和重执行。Moshovos 等人提出利用运行时历史信息预测 Store-Load 对之间的依赖关系：对于预测存在依赖的对，推迟 Load 的执行直到对应 Store 的地址解析完成；对于预测无依赖的对，允许 Load 提前执行。实验结果表明，该技术能够在几乎消除不必要等待的同时大幅减少内存序违例。

**Store Sets。** Chrysos 和 Emer [6] 在 ISCA 1998 上提出了 Store Sets 方案，它已成为最广泛采用的存储依赖预测算法之一。Store Sets 的核心思想是：当检测到某条 Load 因为某条 Store 发生内存序违例时，将这两条指令关联到同一个"Store Set"中。此后，属于同一 Store Set 的 Load 将等待其关联的 Store 完成后才执行。Store Sets 使用 SSIT（Store Set Identifier Table）和 LFST（Last Fetched Store Table）两个硬件表实现，以 PC 为索引进行查找。该方案的优势在于简洁高效，且能够自然地处理多对多的 Store-Load 依赖关系。Store Sets 在 Intel 和 AMD 等商用处理器中得到了广泛的工程应用。

本文的 SAB（Store Address Buffer）机制与传统 MDP 技术有着不同的设计思路。传统 MDP（如 Store Sets）基于 PC 历史来预测依赖关系，属于控制流（Control-flow）驱动的预测；而 SAB 利用 CMAP 在 Dispatch 阶段已经预测出的 Store 地址，直接进行地址比较来检测 Store-Load 冲突，属于数据流（Data-flow）驱动的检测。SAB 无需额外的预测器训练或历史表维护，其检测结果直接反映了地址层面的实际冲突情况，具有更高的精确度。SAB 与 CMAP 协同工作：CMAP 为 Load 和 STA 都提供预测地址，SAB 在 Dispatch 阶段利用这些预测地址进行冲突预筛查，对检测到冲突的 Load 推迟其通过 Retry 路径的执行，直到冲突 Store 的地址确认解析完成，从而在利用 CMAP 加速 Load 执行的同时控制了内存序违例的增长。

### 2.3 Load 投机调度与延迟隐藏

在超标量乱序处理器中，Load 指令的执行延迟是不确定的：L1 缓存命中时通常为 3–4 周期，但在 TLB 缺失、缓存缺失等情况下可能增加到数十甚至数百个周期。为了最大化流水线利用率，现代处理器普遍采用投机调度（Speculative Scheduling）策略：在 Load 发射后，调度器乐观地假设 Load 将按最短延迟（即 L1 命中延迟）完成，提前唤醒依赖于该 Load 的指令。如果 Load 实际的延迟更长（如缓存未命中），已被唤醒的依赖指令需要通过重放（Replay）机制回退并重新执行。

**投机调度与重放机制。** Kim 和 Lipasti [9] 在 HPCA 2004 上对处理器中的投机调度重放机制进行了系统性的分析。他们研究了不同的重放策略——包括选择性重放（Selective Replay）和全量重放（Full Replay）——对性能和资源利用率的影响。研究表明，投机调度对于 Load 密集的工作负载至关重要：禁用投机调度会导致显著的性能下降（通常超过 10%），因为依赖指令必须等待 Load 完成后才被唤醒。然而，不当的投机策略也会引发大量不必要的重放，浪费功耗和执行资源。该工作为后续的投机调度优化提供了全面的基准分析。

**低成本投机调度。** Perais 等人 [8] 在 ISCA 2015 上提出了一种低成本的投机调度方案。他们的核心观察是：并非所有 Load 的依赖指令都同样关键——只有位于关键路径上的依赖指令才真正影响性能。该方案使用一个基于 Bloom Filter 的关键性预测器（Criticality Predictor）来识别关键 Load，仅对关键 Load 的依赖指令执行投机唤醒，而非关键 Load 的依赖指令则延迟到 Load 实际完成后再唤醒。这种差异化策略在保持性能的同时大幅减少了不必要的投机和重放开销。该工作揭示了投机调度中"精准性"的重要性：不是越激进越好，而是应该在合适的位置以合适的时机进行投机。

**Load 延迟预测。** 除了二元的"命中/未命中"投机外，也有工作尝试更细粒度地预测 Load 延迟。Liu 等人 [13] 提出了基于 Load 延迟预测（Load Latency Prediction）的调度器能量优化方案，通过预测每条 Load 的实际延迟来更精确地安排依赖指令的唤醒时机。Gunadi 和 Lipasti [14] 在窄位宽调度器的工作中进一步分析了 Load 延迟误预测的各类原因，并提出了针对性的缓解措施。这些工作表明，准确预测 Load 延迟是投机调度有效性的关键。

本文的工作在投机唤醒方面做出了以下贡献：BOOM v3 原有的投机唤醒机制仅覆盖从发射队列正常发射的 Load（`fired_load_incoming` 路径），而 CMAP 绕过发射队列的 Load 通过 Retry 路径执行（`fired_load_retry`），不在原有投机唤醒的覆盖范围内。本文将投机唤醒扩展至 Retry 路径，使 CMAP 加速的 Load 也能提前唤醒依赖指令，实现了从"Load 提前发射"到"依赖链整体提前执行"的完整加速。此外，SAB 冲突检测从源头减少了因 CMAP 提前执行导致的内存序违例，降低了投机失败后重放的概率，与投机唤醒扩展形成了互补。

### 2.4 本章小结

表 2-1 对上述相关工作及其与本文方案的关系进行了总结。

| 研究方向 | 代表性工作 | 核心思想 | 与本文的关系 |
|---------|-----------|---------|-------------|
| Load 地址预测 | Zero-cycle Loads [4] | 基于步进模式预测 Load 地址 | CMAP 的思想来源；CMAP 以寄存器号索引，支持更广泛的基址复用场景 |
| 寄存器地址追踪 | Early Load Address Resolution [10] | 非投机追踪 `reg±imm` 计算链 | CMAP 改用精确值缓存 + 时效性推测，覆盖范围更广、可绕过 IQ |
| Load 优先调度 | Load Slice Core [11] | 分离并优先执行 Load 地址计算切片 | 共享加速 Load 目标；CMAP 通过预测绕过 IQ 而非优先调度 |
| 工业 LAP | Apple Silicon LAP [5] | 步进预测器 + 投机缓存访问 | 验证了地址预测的工业实用性；CMAP 采用基址缓存方案 |
| IQ 差异化调度 | Delay-and-Bypass [12] | 区分指令关键性进行差异化发射 | CMAP 将可预测 Load 从 IQ 中分流 |
| 存储依赖预测 | Moshovos et al. [7]; Store Sets [6] | 预测 Store-Load 依赖以优化执行顺序 | SAB 采用数据流驱动的地址比较替代控制流驱动的历史预测 |
| 投机调度 | Perais et al. [8]; Kim & Lipasti [9] | 预测 Load 命中延迟，提前唤醒依赖指令 | 本文扩展投机唤醒覆盖 CMAP Retry 路径 |

### 相关工作参考文献

[4] T. M. Austin and G. S. Sohi, "Zero-cycle loads: Microarchitecture support for reducing load latency," in *Proc. MICRO-28*, 1995.

[5] J. Kim, D. Genkin, and Y. Yarom, "SLAP: Data speculation attacks via load address prediction on Apple Silicon," in *Proc. IEEE S&P*, 2025.

[6] G. Z. Chrysos and J. S. Emer, "Memory dependence prediction using store sets," in *Proc. ISCA*, 1998.

[7] A. Moshovos, S. E. Breach, T. N. Vijaykumar, and G. S. Sohi, "Dynamic speculation and synchronization of data dependences," in *Proc. ISCA*, 1997.

[8] A. Perais, A. Seznec, P. Michaud, A. Sembrant, and E. Hagersten, "Cost-effective speculative scheduling in high performance processors," in *Proc. ISCA*, 2015.

[9] I. Kim and M. H. Lipasti, "Understanding scheduling replay schemes," in *Proc. HPCA*, 2004.

[10] M. Bekerman, A. Yoaz, F. Gabbay, S. Jourdan, M. Kalaev, and R. Ronen, "Early load address resolution via register tracking," in *Proc. ISCA*, 2000.

[11] T. E. Carlson, W. Heirman, O. Allam, S. Kaxiras, and L. Eeckhout, "The load slice core microarchitecture," in *Proc. ISCA*, 2015.

[12] M. Alipour, S. Kaxiras, D. Black-Schaffer, and E. Hagersten, "Delay and bypass: Ready and criticality aware instruction scheduling in out-of-order processors," in *Proc. HPCA*, 2020.

[13] Y. Liu, G. Memik, and G. Reinman, "Reducing the energy of speculative instruction schedulers," in *Proc. ICCD*, 2005.

[14] E. Gunadi and M. H. Lipasti, "Narrow width dynamic scheduling," *Journal of Instruction-Level Parallelism*, vol. 9, 2007.

# 基于地址预测的超标量处理器 Load 指令延迟优化

---

## 第一章 引论

### 1.1 选题背景

#### 1.1.1 处理器性能演进与指令级并行

自 20 世纪 70 年代微处理器问世以来，处理器性能的增长始终是计算机体系结构研究的核心驱动力。在长达三十余年的时间里，这种增长主要源于两个方面的贡献：一是晶体管特征尺寸的持续缩小带来的时钟频率提升，二是微架构层面对指令级并行（Instruction-Level Parallelism, ILP）的深入挖掘[1]。

在 ILP 挖掘的技术演进中，处理器微架构经历了从简单标量流水线到超标量乱序执行的跨越式发展。早期的标量流水线处理器（如 MIPS R2000/R3000）通过经典的五级流水线（取指-译码-执行-访存-写回）实现了每周期一条指令的理想吞吐率。然而，由于数据相关（Data Hazard）、控制相关（Control Hazard）和结构相关（Structural Hazard）等流水线冒险的存在，实际 IPC 往往远低于 1.0。为了突破单发射的吞吐率上限，超标量（Superscalar）处理器应运而生。超标量处理器在每个时钟周期内从指令流中取出、译码并发射多条指令至多个功能单元并行执行，理论峰值 IPC 可达发射宽度 $N$。以 Intel P6 微架构（1995 年）为代表的乱序超标量处理器将 Tomasulo 算法[15]中的动态调度思想与寄存器重命名、重排序缓冲区（Reorder Buffer, ROB）相结合，形成了现代乱序执行引擎的基本框架[1]。

乱序执行（Out-of-Order Execution）是现代高性能处理器挖掘 ILP 的核心技术。其基本思想是：允许指令不按照程序序（Program Order）执行，而是在操作数就绪且功能单元可用时立即发射执行，最终通过 ROB 保证指令按程序序提交（In-order Commit），维护精确异常（Precise Exception）语义。乱序执行引擎的关键组件包括：（1）寄存器重命名（Register Renaming），通过将逻辑寄存器映射到物理寄存器来消除写后写（WAW）和读后写（WAR）等假相关，仅保留真数据依赖（RAW）；（2）发射队列（Issue Queue, IQ）或称保留站（Reservation Station），缓存已译码但尚未执行的指令，通过唤醒-选择（Wakeup-Select）逻辑在操作数就绪时将指令发射至执行单元；（3）重排序缓冲区（ROB），维护指令的程序序信息，支持按序提交和错误推测的恢复。

在乱序执行的基础上，推测执行（Speculative Execution）技术进一步拓展了处理器可以"看到"的指令窗口。分支预测（Branch Prediction）允许处理器在分支结果确定之前沿预测路径继续取指和执行，从而避免了分支指令造成的流水线停顿。现代分支预测器（如 TAGE、Perceptron 等）的预测精度已超过 95%–99%[16]，使得推测执行成为乱序处理器不可或缺的性能基础。值得注意的是，推测执行的概念并不局限于分支预测——值预测（Value Prediction）[17]、地址预测（Address Prediction）[4]、存储依赖预测（Memory Dependence Prediction）[6]等技术都属于广义推测执行的范畴，本文所研究的 Load 地址预测正是这一技术谱系中的重要一环。

然而，约自 2005 年起，由于功耗密度逼近散热极限（"功耗墙"问题）以及 Dennard 缩放定律的失效，单纯依靠提升时钟频率来获得性能增长的路径已基本终结。处理器的发展转向多核并行（Multi-core）与异构计算（Heterogeneous Computing）方向，但单线程性能仍然是决定程序响应延迟（Latency）和许多难以并行化的工作负载性能的关键因素。Amdahl 定律指出，程序中串行部分的执行时间是多核加速比的硬性上限。因此，提升单线程 IPC 的微架构优化——特别是针对关键路径上的延迟优化——仍然具有不可替代的研究价值和工程意义。

#### 1.1.2 存储墙问题与访存延迟瓶颈

处理器核心计算速度的增长远超主存储器（DRAM）访问速度的提升，这一日益扩大的差距被 Wulf 和 McKee 在 1995 年的经典论文中定义为"存储墙"（Memory Wall）问题[2]。具体而言，从 1980 年至 2000 年代，处理器时钟频率以每年约 50%–60% 的速率增长，而 DRAM 的访问延迟改善速率仅为每年约 7%–9%。这意味着以处理器时钟周期计量，主存访问的相对延迟在二十年间增长了约两个数量级：从 1980 年代的数个周期增长至 2000 年代的数百个周期。尽管 DRAM 带宽通过 DDR 技术的迭代（DDR、DDR2、DDR3、DDR4、DDR5）获得了显著提升，但单次随机访问的延迟改善极为有限——现代 DDR5 DRAM 的 tCAS（Column Access Strobe）延迟仍约为 13–14 ns，与二十年前的 DRAM 相比改善幅度不大。

为了弥合处理器与主存之间的速度鸿沟，现代处理器普遍采用多级缓存层次结构（Cache Hierarchy）。典型的高性能处理器配置包括：（1）L1 指令缓存和 L1 数据缓存，容量通常为 32–64 KB，访问延迟约 3–5 个时钟周期，与处理器核心运行在相同频率上；（2）L2 缓存，容量通常为 256 KB–1 MB（每核），访问延迟约 10–15 个时钟周期；（3）L3 缓存（Last-Level Cache, LLC），容量通常为数 MB 至数十 MB（共享），访问延迟约 30–50 个时钟周期。缓存的有效性建立在程序局部性（Locality）原理之上——时间局部性（Temporal Locality）和空间局部性（Spatial Locality）使得大多数内存访问可以在缓存中命中，避免了高代价的主存访问。

尽管缓存层次结构显著降低了平均访存延迟，但即便是 L1 数据缓存的 3–5 周期访问延迟，对于现代超标量处理器流水线而言也并非可以忽略的代价。在一个 4 宽度发射的乱序处理器中，每周期可以完成最多 4 条指令。一条 L1 命中的 Load 需要 3–4 个周期，其依赖指令在此期间无法执行。如果这些依赖指令又被其他指令所依赖（形成长依赖链），那么单条 Load 的延迟可以通过依赖链传播，影响远超一条指令的执行窗口，产生"涟漪效应"。当 L1 缓存未命中时，这种影响更为剧烈：L2 访问需要额外 10 余个周期，L3 访问需要额外 30–50 个周期，而主存访问则可能需要 200 个周期以上。在此期间，依赖于该 Load 的大量指令被阻塞，ROB 和发射队列迅速填满，处理器最终可能因为窗口资源耗尽而停顿（Stall），有效 IPC 急剧下降。

#### 1.1.3 Load 指令在程序执行中的关键地位

在超标量乱序处理器的流水线中，Load 指令占据着极为关键的地位。这种关键性体现在两个层面：指令频率和数据依赖关键路径。

**指令频率方面。** 根据 Hennessy 和 Patterson 的经典教科书[3]，Load 指令约占全部动态指令的 25%–35%。这一统计在不同的指令集架构（ISA）和工作负载特征下可能有所变化：RISC 架构（如 RISC-V、ARM）由于采用 Load-Store 体系结构，所有数据操作必须先从内存加载到寄存器，因此 Load 指令占比往往偏高；而 CISC 架构（如 x86）虽然支持内存-寄存器操作数，但编译器优化和微操作（micro-op）分解后，实际的 Load 微操作数量同样可观。本文基于 BOOM v3 处理器在 SPEC CPU 2007 基准测试集上的实测数据验证了这一范围：在 19 个基准程序中，**已提交 Load 指令占已提交总指令数的 16.74%–33.90%，平均为 24.87%**；若将 Store 指令一并计入，访存指令合计平均占比达 34.06%。表 1-1 给出了各基准程序的详细统计。

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

从表 1-1 的数据中可以观察到几个值得注意的趋势：（1）整型基准程序（如 gcc、gobmk、sjeng）和浮点基准程序（如 bwaves、cactusADM、leslie3d）的 Load 占比没有显著系统性差异，说明 Load 延迟优化对两类工作负载都具有普遍意义；（2）部分程序的访存指令合计占比超过 40%（如 astar 41.70%、milc 43.15%、povray 46.96%），意味着几乎每两条指令中就有一条访存操作，存储子系统的效率对这些程序的性能影响尤为显著；（3）Store 指令的占比变化范围极大（0.30%–16.32%），反映了不同程序的读写特征差异。

**数据依赖关键路径方面。** Load 指令的关键性不仅体现在其高频率，更体现在它在程序数据流图（Data Flow Graph, DFG）中所处的位置。在典型的程序执行中，Load 指令是将数据从存储层次引入寄存器文件的唯一途径（在 Load-Store 架构中）。一条 Load 完成后，其返回的数据值通常会被多条后续指令所消费——这些消费者可以是算术逻辑运算指令（ALU）、比较与分支指令、甚至是其他 Load 指令（当返回值被用作下一条 Load 的基址时，形成指针追踪链）。在乱序处理器的执行窗口中，Load 指令往往位于数据依赖链的"根部"：一条 Load 的延迟会向下游传播至所有直接和间接依赖于它的指令，形成从 Load 出发的关键路径。

为了更直观地理解这种放大效应，考虑如下代码片段（以 RISC-V 汇编表示）：

```
ld   x5, 0(x10)      # Load: 从内存加载数据到 x5
add  x6, x5, x11     # ALU: 依赖 x5
sll  x7, x6, x12     # ALU: 依赖 x6
bne  x7, x0, label   # 分支: 依赖 x7
```

在这一依赖链中，`ld` 指令的延迟直接决定了 `add` 指令的最早发射时机；`add` 的结果传递给 `sll`；`sll` 的结果又影响 `bne` 分支的解析时机。如果 `ld` 指令的有效延迟为 4 个周期（L1 缓存命中），则 `bne` 分支最早在第 7 个周期才能解析（4 + 1 + 1 + 1，假设 ALU 和分支各 1 周期）。在此期间，如果 `bne` 之后有更多依赖于分支结果的指令（推测执行路径），整个推测窗口的正确性确认都被延迟。如果能将 `ld` 的有效延迟缩短 1–2 个周期，不仅 `add` 可以更早执行，整条依赖链上的所有指令都能提前 1–2 个周期完成，分支也能更早解析——这就是 Load 延迟优化的放大效应。

更为典型的情形出现在指针追踪（Pointer Chasing）访问模式中：

```
ld   x5, 0(x10)      # Load 1: 加载指针
ld   x6, 8(x5)       # Load 2: 解引用指针，依赖 Load 1
ld   x7, 16(x6)      # Load 3: 再次解引用，依赖 Load 2
```

这种 Load-after-Load 的链式依赖在链表遍历、树结构搜索、哈希表查找等数据结构操作中极为常见。由于每一级 Load 都必须等待前一级 Load 返回数据才能计算自己的地址，这些 Load 之间完全无法并行，形成了严格的串行依赖链。每条 Load 的延迟都会完整地累加在总延迟中，任何单条 Load 延迟的缩短都能直接减少整条链的执行时间。

#### 1.1.4 Load 指令在超标量流水线中的执行延迟分析

在传统的乱序处理器微架构中，一条 Load 指令从进入后端到最终获得数据，需要经历多个流水级，每个流水级都贡献了不可忽略的延迟：

**（1）发射队列等待（Issue Queue Wait）。** Load 指令经过译码、寄存器重命名和分派（Dispatch）后进入发射队列。在发射队列中，Load 必须等待其基址寄存器（Base Register）的值就绪。如果基址寄存器的值由一条尚未执行完成的指令产生（例如另一条 Load 或长延迟的 ALU 指令），则当前 Load 可能在发射队列中等待数个周期甚至更长。此外，即使操作数已就绪，Load 还必须竞争发射端口（Issue Port）的仲裁：当多条操作数就绪的指令同时竞争有限的发射端口时，部分指令会因仲裁失败而额外延迟。

**（2）地址生成（Address Generation, AGU）。** Load 指令被发射至地址生成单元后，AGU 将基址寄存器值与指令中编码的立即数偏移相加，计算出访存的虚拟地址。在 RISC-V 的 Load 指令格式（`ld rd, imm(rs1)`）中，这一计算为一次 64 位整数加法。AGU 通常与 ALU 共享执行端口或使用专用加法器，延迟为 1 个时钟周期。

**（3）地址翻译（TLB Lookup）。** 虚拟地址计算完成后，Load 需要访问数据 TLB（Data Translation Lookaside Buffer）将虚拟地址翻译为物理地址。TLB 是页表（Page Table）的硬件缓存，存储了近期使用的虚拟-物理地址映射。在 TLB 命中的情况下，地址翻译通常需要 1 个时钟周期。然而，TLB 未命中时，处理器需要进行页表遍历（Page Table Walk），这可能需要数十甚至数百个周期（取决于页表级数和页表是否在缓存中）。现代处理器通常采用多级 TLB（L1 DTLB + L2 TLB）和硬件页表遍历器（Hardware Page Table Walker）来降低 TLB 缺失的平均代价。

**（4）缓存访问（L1 Data Cache Access）。** 获得物理地址后，Load 指令访问 L1 数据缓存。L1 数据缓存通常为组相联（Set-Associative）结构，访问过程包括：使用地址的索引位（Index Bits）定位缓存组（Set），同时读取该组中所有路（Way）的标签（Tag）和数据，将标签与地址的标签位进行比较以确定命中路，最后根据地址的偏移位（Offset Bits）从命中路的数据中提取所需字节。在现代设计中，标签比较和数据读取通常并行进行，L1 缓存访问延迟约为 1–2 个时钟周期。某些高频设计中，L1 缓存被进一步流水线化以满足时序要求。

**（5）数据写回（Data Writeback）。** 缓存返回的数据经过可能的字节对齐（Byte Alignment）和符号扩展（Sign Extension）处理后，写入物理寄存器文件，同时唤醒发射队列中依赖于该 Load 目标寄存器的指令。

以 RISC-V 开源处理器 BOOM（Berkeley Out-of-Order Machine）v3 的 MediumBoomV3Config 配置为例，从 Load 被发射队列选中到数据返回至少需要 3–4 个时钟周期（假设 L1 缓存命中）。但这一延迟统计不包含 Load 在发射队列中的排队等待时间——在实际执行中，由于操作数未就绪、发射端口竞争等原因，Load 的总体有效延迟往往远超 3–4 个周期。在此期间，所有依赖该 Load 的指令均被阻塞在发射队列中，造成流水线气泡（Pipeline Bubble），降低了有效 IPC（Instructions Per Cycle）。

#### 1.1.5 现有延迟隐藏机制的局限性

为了缓解 Load 延迟对依赖指令的阻塞效应，现代处理器已经发展出多种延迟隐藏（Latency Hiding）技术。然而，这些技术各自存在不同程度的局限性，这也是本文工作的直接动机所在。

**投机唤醒（Speculative Wakeup）。** 投机唤醒是当前高性能处理器中最普遍采用的 Load 延迟隐藏技术。其核心思想是：在 Load 被发射至存储子系统后，调度器不等待 Load 实际完成，而是乐观地假设 Load 将按最短延迟（即 L1 缓存命中延迟）返回数据，提前唤醒依赖于该 Load 的指令。被唤醒的依赖指令随即参与仲裁，可以在 Load 数据到达的同一周期或下一周期被发射执行，从而将 Load 延迟从依赖指令的等待时间中"隐藏"掉。然而，投机唤醒存在两个根本性的局限：

- **仍受限于发射队列调度。** 投机唤醒的前提是 Load 指令已经从发射队列中被选中并发射。如果 Load 本身因为基址寄存器未就绪、发射端口被占用或发射队列资源紧张而延迟发射，那么投机唤醒也随之延迟，依赖链的执行起点被推后。换言之，投机唤醒只能隐藏从 Load 发射到数据返回的缓存访问延迟，无法隐藏 Load 在发射队列中的排队等待延迟。

- **推测失败的代价。** 当 Load 的实际延迟超过预期（例如 L1 缓存未命中导致需要访问 L2 或更高层次缓存），已被投机唤醒并可能已经执行的依赖指令获取到的数据是无效的，必须通过重放（Replay）机制回退和重新执行。重放不仅浪费了已消耗的执行资源和功耗，还可能引发级联重放（Cascade Replay）——被错误唤醒的指令的消费者也可能已被唤醒并执行，它们同样需要重放。在极端情况下，一次 Load 缓存未命中可能触发数十条指令的重放。

**预取（Prefetching）。** 硬件预取器（Hardware Prefetcher）通过监测访存地址的模式（如顺序流、步进流），提前将可能被访问的数据从低层次存储搬运到高层次缓存中，以降低 Load 缓存未命中时的延迟代价。预取技术在降低缓存缺失率方面效果显著，特别是对于具有规则访问模式的科学计算和流式处理工作负载。然而，预取并不能缩短 L1 缓存命中时的固有延迟（3–5 周期），也无法解决 Load 在发射队列中的排队等待问题。预取针对的是缓存缺失场景，而本文关注的是即使在缓存命中情况下的延迟优化——两者是互补而非替代的关系。

**乱序执行窗口。** 乱序执行引擎本身就是一种延迟容忍（Latency Tolerance）机制：当一条 Load 等待数据返回时，处理器可以继续执行窗口中其他不依赖于该 Load 的独立指令，从而"容忍" Load 的延迟。然而，乱序窗口的有效性受限于 ROB 和发射队列的容量——这些硬件资源的大小决定了处理器能够"看到"多远的指令流。增大这些结构的容量可以提升延迟容忍能力，但面临面积、功耗和时序的严峻约束。更重要的是，当程序的数据依赖链较长或 Load 延迟过高时，即使是大窗口也可能无法找到足够的独立指令来填充等待期。

综上所述，现有的延迟隐藏技术虽然各有成效，但在缩短 Load 从进入后端到数据就绪的总有效延迟方面仍存在显著的优化空间，特别是在发射队列排队等待这一常被忽视但影响重大的延迟来源上。本文的工作正是着眼于这一空白：通过在流水线前端（译码阶段）引入轻量级的地址预测机制，使高置信度的 Load 指令绕过发射队列直接发起缓存访问，从根本上消除其发射队列排队延迟，实现从"发射后加速"到"发射前预测"的范式转换。

#### 1.1.6 RISC-V 开源处理器生态与研究平台

本文的研究基于 RISC-V 开源指令集架构及 BOOM（Berkeley Out-of-Order Machine）处理器进行实现与验证，选择这一研究平台具有以下考量。

RISC-V 是一种开放、免版权费（Royalty-free）的指令集架构，由加州大学伯克利分校于 2010 年发起，现已成为学术研究和工业应用中增长最快的 ISA 之一。RISC-V 的开放性使研究者能够自由地获取、修改和扩展处理器设计，避免了商用 ISA（如 x86、ARM）所面临的知识产权壁垒。RISC-V 的模块化设计（基础整数指令集 + 标准扩展）和简洁的编码格式也便于微架构研究中的分析和实验。

BOOM 是基于 RISC-V 的高性能乱序超标量处理器，采用 Chisel 硬件描述语言编写，是 Chipyard SoC 开发框架的核心组件之一。BOOM v3 实现了完整的乱序执行引擎，包括可参数化的多宽度取指/译码/分派/发射/提交流水线、寄存器重命名、分支预测、非阻塞缓存、硬件预取等现代处理器特征。其 MediumBoomV3Config 配置（2 宽度取指、3 宽度译码/分派/提交、3 条流水线的整数/内存/浮点执行单元）代表了一个中等规模的乱序超标量设计，在复杂度和研究可行性之间取得了良好的平衡。

基于开源处理器进行微架构研究的优势在于：（1）**完全的设计可见性**——研究者可以查看和修改从前端到后端的所有 RTL 代码，精确理解每个微架构决策的影响；（2）**可复现性**——开源代码库保证了研究结果的可复现和可验证；（3）**端到端验证**——在 RTL 级实现的优化可以通过完整的仿真流程（包括 Linux 启动、标准基准测试运行）进行真实的性能评估，而非依赖于模拟器（Simulator）的近似估计。本文所有实验均在 BOOM v3 的 RTL 实现上进行，通过 Chipyard 1.13.0 框架生成 Verilog，使用 Verilator 进行周期精确仿真，确保了实验结果的准确性和可靠性。

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

[15] R. M. Tomasulo, "An efficient algorithm for exploiting multiple arithmetic units," *IBM Journal of Research and Development*, vol. 11, no. 1, pp. 25–33, Jan. 1967.

[16] A. Seznec, "A 64-Kbytes ITTAGE indirect branch predictor," in *Proc. 2nd JILP Workshop on Computer Architecture Competitions (JWAC-2): Championship Branch Prediction (CBP-2)*, 2011.

[17] M. H. Lipasti, C. B. Wilkerson, and J. P. Shen, "Value locality and load value prediction," in *Proc. 7th International Conference on Architectural Support for Programming Languages and Operating Systems (ASPLOS VII)*, 1996, pp. 138–147.

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

"""认知诊断预测试（M2 · CDT）。

职责：出一小卷探针题 → 批改 → 输出「每个知识点处在什么水平」和「这人
怎么学得进去」。

设计取舍（这三条决定了它能不能充当 CDT）：
1. **固定题库，不靠 LLM 现编题**。诊断题必须可复现：同一份作答两次批改
   结果要一样，别的同学横向比才有意义。让模型现场出题，题的难度和考察点
   每次都在漂，估出来的能力值就没有可比性——那是"聊天"不是"测量"。
   代价是知识点覆盖受题库限制，换来的是可标定（每题有 difficulty 参数）。
2. **IRT-lite（1PL 逻辑斯蒂）而不是简单加分**。
       P(答对) = sigmoid(a * (θ - d))
   用二次方程就知道为什么不能简单加分：一个 θ=0.8 的人答对 d=0.3 的题，
   几乎是废信息（P≈0.93），掌握度不该涨；反过来他答错 d=0.5 的题才是
   强信号，应该跌得多。更新量取残差 (y - P)，天然实现"意外才更新"。
   没上全量 MLE 是刻意的：样本量只有几道题，MLE 会过拟合到边界。
3. **认知风格只做两个维度，且必须带置信度**。数据只有几道题选项，
   强行输出"你属于 XX 型"是占卜。这里输出 value + confidence，
   低于 MIN_CONFIDENCE 的维度下游（personalize）一律不注入提示词。

纯函数、零 LLM、零 IO——批改逻辑能直接写单测。
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from agent.knowledge_graph import (
    DEFAULT_MASTERY,
    PREREQ_READY_THRESHOLD,
    canonical_topic,
    categories,
    mastery_of,
    normalize_topic,
    recommend_next,
)

# --- IRT 参数 ------------------------------------------------------------
# 区分度：5.0 表示能力比难度高 0.2 时 P(答对)≈0.73，过渡带约 0.4 宽。
# 之所以不用 1PL 常见的 1.7：我们的 θ 和 d 都压缩在 0~1，低区分度会让过渡带
# 宽到「θ=0.8 的人答对 d=0.3 的题仍有 18% 意外」，预期内的作答也在涨分，
# 等于给学霸刷分。抬高区分度才能让「意外」真正成为唯一驱动。
DISCRIMINATION = 5.0
# 学习率：残差收缩系数。取 0.4 是给单卷作答留余量——
# 一次预测试直接把掌握度推到 0/1 会让它盖过后续几十轮对话的证据。
LEARNING_RATE = 0.4
# 能力值夹取边界。取 0.05/0.95 而不是 0/1：留出"永远还会错/永远还会对"
# 的余地，避免画像出现"绝对掌握"这种无法被后续纠正的状态。
ABILITY_MIN = 0.05
ABILITY_MAX = 0.95

# --- 认知风格 ------------------------------------------------------------
ITEM_FACTUAL = "factual"      # 事实记忆题
ITEM_CONCEPTUAL = "conceptual"  # 概念辨析 / 推理题
# 单题作答超过这个毫秒数记为"沉吟"（MCQ 读题+判断通常 3~6s）
SLOW_ANSWER_MS = 8000
# 维度判定阈值（0~1 的 value 落在中间区间一律算 balanced，不许硬分类型）
POLE_HIGH = 0.62
POLE_LOW = 0.38
# 置信度：作答数少于 THIS 就别拿风格说事
MIN_STYLE_SAMPLES = 4
MIN_CONFIDENCE = 0.5

# 每题都必须带 explanation：学生答错时它本身就是学习资源，
# 而且这里是后台返回给前端的，不存在"占篇幅"的问题。
_ITEM_BANK: list[dict[str, Any]] = [
    # ---------- 操作系统 ----------
    {
        "id": "OS-PROC-1",
        "topic": "进程与线程",
        "difficulty": 0.25,
        "type": ITEM_FACTUAL,
        "stem": "关于同一进程内的多个线程，下列说法正确的是？",
        "options": [
            "它们共享进程的堆和全局变量，但各有自己独立的栈",
            "它们各自拥有完全独立的虚拟地址空间",
            "线程切换必须同时切换页表，开销远大于进程切换",
            "一个线程崩溃不会影响同一进程里的其他线程",
        ],
        "answer": 0,
        "explanation": (
            "线程共享所属进程的地址空间（堆、全局变量、打开的文件），"
            "私有的是栈和寄存器。既然共享页表，线程切换就不必换页表，"
            "开销小于进程切换；也正因为共享地址空间，一个线程踩坏内存"
            "会让整个进程一起崩。"
        ),
    },
    {
        "id": "OS-PROC-2",
        "topic": "进程与线程",
        "difficulty": 0.5,
        "type": ITEM_CONCEPTUAL,
        "stem": "服务器上要处理大量并发的短任务，选多线程而不是多进程，最主要的理由是？",
        "options": [
            "线程的创建、上下文切换和通信开销远小于进程，共享数据也更方便",
            "线程一定跑得比进程快",
            "线程不需要内核调度，完全由用户态程序自己管理",
            "线程之间天然隔离，一个出错不影响其它线程",
        ],
        "answer": 0,
        "explanation": (
            "共享地址空间意味着创建时不用复制页表、通信不用走 IPC。"
            "B 错在『一定』；C 只有用户级线程成立，内核级线程仍由内核调度；"
            "D 正好说反，隔离性是进程的优点。"
        ),
    },
    {
        "id": "OS-SCHED-1",
        "topic": "进程调度算法",
        "difficulty": 0.3,
        "type": ITEM_FACTUAL,
        "stem": "时间片轮转（RR）调度最突出的特点是？",
        "options": [
            "每个就绪任务轮流获得一个时间片，保证交互型任务的响应性",
            "总是让预计运行时间最短的任务先执行",
            "优先级一旦确定就再也不会改变",
            "整个过程中不会发生抢占和上下文切换",
        ],
        "answer": 0,
        "explanation": (
            "RR 是抢占式的分时策略，按时间片轮转以保证响应时间。"
            "B 是 SJF/SPF；C 是静态优先级，与之相对的是多级反馈队列的"
            "动态调整；D 显然错，RR 每个时间片结束都会抢占。"
        ),
    },
    {
        "id": "OS-SCHED-2",
        "topic": "进程调度算法",
        "difficulty": 0.65,
        "type": ITEM_CONCEPTUAL,
        "stem": "多级反馈队列（MLFQ）相比单纯的先来先服务（FCFS），主要改进是什么？",
        "options": [
            "不需要预先知道任务运行时长，也能兼顾短任务响应和长任务吞吐",
            "彻底消除了上下文切换带来的开销",
            "保证所有任务获得完全相等的 CPU 时间",
            "仅靠硬件 FIFO 队列即可实现，无需内核参与",
        ],
        "answer": 0,
        "explanation": (
            "MLFQ 用多级队列 + 动态升降级：新任务进高优先级队列，用完配额"
            "就降级、长时间等待可适当提升。这样不必预知运行时间，也能避免"
            "FCFS 的护航效应（一个长任务堵住一串短任务）。"
        ),
    },
    {
        "id": "OS-DEAD-1",
        "topic": "死锁",
        "difficulty": 0.3,
        "type": ITEM_FACTUAL,
        "stem": "下列哪一项不是死锁产生的必要条件？",
        "options": ["优先级反转", "互斥", "请求与保持", "循环等待"],
        "answer": 0,
        "explanation": (
            "死锁四必要条件是：互斥、请求与保持、不可剥夺、循环等待。"
            "优先级反转是实时调度里的经典问题（高优先级任务被低优先级"
            "任务持有的锁阻塞），不属于死锁四条件。"
        ),
    },
    {
        "id": "OS-DEAD-2",
        "topic": "死锁",
        "difficulty": 0.7,
        "type": ITEM_CONCEPTUAL,
        "stem": "银行家算法属于死锁的哪种处理策略？",
        "options": [
            "避免：每次分配前先试探，确认系统仍处于安全状态才真正分配",
            "预防：破坏四个必要条件中的某一个",
            "检测：定期检查资源分配图中是否存在环",
            "解除：剥夺资源或直接终止相关进程",
        ],
        "answer": 0,
        "explanation": (
            "银行家算法在分配前做安全性试探（能否找到一个安全序列），"
            "属于死锁避免。预防是事前破坏必要条件，检测和解除都是事后处理。"
        ),
    },
    {
        "id": "OS-MM-1",
        "topic": "虚拟内存与页面置换",
        "difficulty": 0.35,
        "type": ITEM_FACTUAL,
        "stem": "哪种页面置换算法可能出现 Belady 异常（分配更多物理块，缺页率反而上升）？",
        "options": ["FIFO", "LRU", "OPT（最佳置换）", "Clock"],
        "answer": 0,
        "explanation": (
            "FIFO 不具备栈特性：先入的页面可能恰恰是即将被访问的页面。"
            "LRU、OPT、Clock 都满足栈特性，增加物理块不会让缺页率升高。"
        ),
    },
    {
        "id": "OS-MM-2",
        "topic": "虚拟内存与页面置换",
        "difficulty": 0.7,
        "type": ITEM_CONCEPTUAL,
        "stem": "进程频繁缺页、系统大量时间花在换页上，CPU 利用率反而急剧下降，这一现象称为？",
        "options": ["抖动（thrashing）", "内存泄漏", "Belady 异常", "Cache 污染"],
        "answer": 0,
        "explanation": (
            "抖动（颠簸）的根因是分配给进程的物理块数少于它当前的工作集大小，"
            "导致刚换出的页立刻又要换入。内存泄漏是空间不断流失，"
            "Belady 异常是页框数与缺页率关系的性质，二者不同。"
        ),
    },
    # ---------- 计算机网络 ----------
    {
        "id": "NET-IP-1",
        "topic": "TCP/IP 分层模型与 ARP / DNS",
        "difficulty": 0.25,
        "type": ITEM_FACTUAL,
        "stem": "ARP 协议解决的问题是？",
        "options": [
            "由 IP 地址找出对应的 MAC 地址",
            "由域名找出对应的 IP 地址",
            "由 MAC 地址反查网卡厂商",
            "为主机自动分配 IP 地址",
        ],
        "answer": 0,
        "explanation": (
            "ARP 做同链路内 IP → MAC 的解析。域名→IP 是 DNS，"
            "自动分配 IP 是 DHCP。"
        ),
    },
    {
        "id": "NET-IP-2",
        "topic": "TCP/IP 分层模型与 ARP / DNS",
        "difficulty": 0.6,
        "type": ITEM_CONCEPTUAL,
        "stem": "关于 ARP 与 DNS 的区别，正确的是？",
        "options": [
            "ARP 解析同链路上的 IP 到 MAC，DNS 解析域名到 IP，两者协议层次不同",
            "两者都在应用层实现",
            "DNS 只在局域网内有效，ARP 可以跨网段",
            "ARP 基于 UDP，DNS 基于 TCP",
        ],
        "answer": 0,
        "explanation": (
            "ARP 属于链路层、作用域通常止于局域网；DNS 是应用层命名系统。"
            "C 完全说反了，D 也错——DNS 默认用 UDP 报文；ARP 甚至不是"
            "IP 层协议，直接封装在以太帧里。"
        ),
    },
    {
        "id": "NET-TCPUDP-1",
        "topic": "TCP 与 UDP",
        "difficulty": 0.3,
        "type": ITEM_FACTUAL,
        "stem": "关于 UDP，下列说法正确的是？",
        "options": [
            "首部开销小、无连接，不保证可靠送达和顺序",
            "通过超时重传保证数据一定可靠送达",
            "自带拥塞控制，不会把带宽打满",
            "发送数据前需要先三次握手建立连接",
        ],
        "answer": 0,
        "explanation": (
            "UDP 首部仅 8 字节，无连接、尽最大努力交付。"
            "可靠传输、拥塞控制、三次握手统统属于 TCP。"
        ),
    },
    {
        "id": "NET-TCPUDP-2",
        "topic": "TCP 与 UDP",
        "difficulty": 0.6,
        "type": ITEM_CONCEPTUAL,
        "stem": "相比 UDP，TCP 牺牲实时性换来了什么？",
        "options": [
            "可靠有序的字节流：连接管理、确认重传、流量与拥塞控制",
            "更大的单报文长度上限",
            "更低的协议首部开销",
            "支持一对多广播",
        ],
        "answer": 0,
        "explanation": (
            "TCP 用序列号、确认重传、滑动窗口和拥塞控制换来可靠性与有序性，"
            "代价是握手与重传带来的延迟抖动。首部更小和支持广播都是 UDP 的特点。"
        ),
    },
    {
        "id": "NET-HSK-1",
        "topic": "TCP 三次握手与四次挥手",
        "difficulty": 0.4,
        "type": ITEM_FACTUAL,
        "stem": "TCP 建立连接为什么需要三次握手，两次不够？",
        "options": [
            "两次无法让双方都确认对方的初始序列号已被接收到",
            "两次握手无法完成 TCP 选项协商",
            "三次是操作系统规定的硬性上限",
            "两次握手会导致端口号冲突",
        ],
        "answer": 0,
        "explanation": (
            "两次握手只能单向确认。经典的失效连接请求场景：一个早已过期"
            "的 SYN 迟到服务端，两次握手会让它误建连接并空等；"
            "第三次握手使双方的 ISN 都被对端确认。"
        ),
    },
    {
        "id": "NET-HSK-2",
        "topic": "TCP 三次握手与四次挥手",
        "difficulty": 0.7,
        "type": ITEM_CONCEPTUAL,
        "stem": "四次挥手中，主动关闭方发出最后一个 ACK 后要进入 TIME_WAIT 等待 2MSL，原因是？",
        "options": [
            "确保最后的 ACK 能到达对方使其正常关闭，同时让本次连接的残余报文在网络中消散",
            "等待对方重新发起连接以便复用端口",
            "等待内核回收 TCP 首部缓冲区",
            "防止对方在此期间发起握手攻击",
        ],
        "answer": 0,
        "explanation": (
            "若最后的 ACK 丢失，对方会重发 FIN，处于 TIME_WAIT 的本端还能补一次 ACK；"
            "同时避免旧连接的迟到报文被使用相同四元组的新连接误收。"
        ),
    },
    {
        "id": "NET-TLS-1",
        "topic": "HTTPS 与 TLS 握手",
        "difficulty": 0.35,
        "type": ITEM_FACTUAL,
        "stem": "关于 HTTPS，下列说法正确的是？",
        "options": [
            "HTTPS 是在 TCP 之上用 TLS 加密 HTTP 报文，默认端口是 443",
            "HTTPS 使用对称加密完成身份认证",
            "HTTPS 不需要建立 TCP 连接",
            "HTTPS 与 HTTP 使用同一个默认端口",
        ],
        "answer": 0,
        "explanation": (
            "HTTPS = HTTP over TLS：先三次握手建 TCP，再 TLS 握手。"
            "身份认证与密钥交换靠非对称加密（证书），应用数据用对称加密；"
            "HTTP 默认端口是 80，两者不同。"
        ),
    },
    {
        "id": "NET-TLS-2",
        "topic": "HTTPS 与 TLS 握手",
        "difficulty": 0.7,
        "type": ITEM_CONCEPTUAL,
        "stem": "TLS 握手用非对称加密交换对称密钥，之后的应用数据却改用对称加密，主要原因是？",
        "options": [
            "非对称运算开销大且对数据长度有限制，不适合加密大量数据",
            "对称加密更安全，所以要把最好的留在后面用",
            "非对称加密无法在互联网上传输",
            "浏览器不支持用非对称加密传输应用层数据",
        ],
        "answer": 0,
        "explanation": (
            "非对称运算慢得多，且单次能加密的数据长度受密钥长度限制。"
            "真正需要它的是『交换密钥 + 用证书证明对方是谁』，"
            "批量数据交给协商出的会话密钥做对称加密。"
        ),
    },
]


def get_item(item_id: str) -> dict[str, Any] | None:
    wanted = (item_id or "").strip().lower()
    for item in _ITEM_BANK:
        if item["id"].lower() == wanted:
            return item
    return None


def bank_size() -> int:
    return len(_ITEM_BANK)


def topics_in_bank() -> list[str]:
    seen: list[str] = []
    for item in _ITEM_BANK:
        if item["topic"] not in seen:
            seen.append(item["topic"])
    return seen


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    """发给学生看的题：绝对不能带 answer / explanation。

    少删一个字段就等于发答案，而且答案泄漏是那种"前端看着也正常、
    只有诊断数据是废的"的静默故障——安全起见这里显式白名单。
    """
    return {
        "id": item["id"],
        "topic": item["topic"],
        "stem": item["stem"],
        "options": list(item["options"]),
        "type": item["type"],
        "difficulty": item["difficulty"],
    }


def _clamp(value: float, low: float = ABILITY_MIN, high: float = ABILITY_MAX) -> float:
    return min(high, max(low, value))


def pick_topics(
    mastery: dict[str, float] | None = None,
    requested: Iterable[str] | None = None,
    count: int = 4,
) -> list[str]:
    """决定考哪几个知识点。

    显式指定 > 按掌握度推荐 > 默认广度优先。指定优先是因为
    预测试常常要"只补测某几个知识点"，此时替用户自作主张重排是好心办坏事。
    """
    if requested:
        topics = [canonical_topic(t) for t in requested]
        known = [t for t in topics if t in topics_in_bank()]
        # 指定了但题库里没有的知识点要原样保留在上游报错，
        # 这里静默丢弃会让调用方认为"考了但没题"。
        return known[: max(1, count)] or topics[: max(1, count)]

    if mastery:
        return [r["topic"] for r in recommend_next(mastery, limit=max(1, count))]

    # 冷启动：按分类轮转取样，保证两科都覆盖到，
    # 否则很容易出现"8 道题全在操作系统"。
    from agent.knowledge_graph import get_node

    buckets: dict[str, list[str]] = {c: [] for c in categories()}
    for topic in topics_in_bank():
        node = get_node(topic)
        key = node["category"] if node else "others"
        buckets.setdefault(key, []).append(topic)

    picked: list[str] = []
    round_index = 0
    while len(picked) < max(1, count) and any(buckets.values()):
        for bucket in list(buckets.values()):
            if round_index < len(bucket):
                picked.append(bucket[round_index])
                if len(picked) >= max(1, count):
                    break
        round_index += 1
    return picked


def build_pretest(
    mastery: dict[str, float] | None = None,
    topics: Iterable[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """组卷。返回的 items 不含答案（public_item 已过滤）。"""
    limit = max(1, min(limit, 16))
    from agent.knowledge_graph import get_node, learning_order, prerequisites

    selected_topics = pick_topics(mastery, topics, count=max(2, limit // 2))

    # 补足：推荐出来的知识点会被「先修未达标」挡掉余下选项，经常只剩两三个，
    # 于是要 8 题却只发得出 4 题。这里按图谱顺序继续补，
    # 但只补「先修要么已经在卷子里、要么已经达标」的知识点——
    # 不做这层检查就等于把先修约束白做了，会测到他根本啃不动的东西。
    if not topics and len(selected_topics) < limit:
        def _item_count(topic: str) -> int:
            return sum(1 for i in _ITEM_BANK if i["topic"] == topic)

        def _ready_or_in_paper(topic: str) -> bool:
            """能不能把这个知识点加进卷子。

            两条放行条件缺一不可：
            - 先修是「没测过」：允许加，前提是它会连着先修一起出现在卷子里
              （单独考上层知识点等于拿没学过的东西为难人）；
            - 先修是「测过且达标」：当然可以加。
            反过来，**先修测过但不达标的一律不放行**——这是与图谱
            recommend_next 保持一致的关键：既然已经知道他基础薄弱，
            就不该顺杆往上考——明知他啃不动还拿来考，诊断出来的低分
            反映的是基础缺失而不是这个知识点本身的掌握度。
            """
            for parent in prerequisites(topic):
                score = mastery_of(mastery or {}, parent)
                if score is None:
                    if parent not in selected_topics:
                        return False
                elif score < PREREQ_READY_THRESHOLD:
                    return False
            return True

        current_items = sum(_item_count(t) for t in selected_topics)
        if current_items < limit:
            for candidate in learning_order(topics_in_bank()):
                if candidate in selected_topics or not _ready_or_in_paper(candidate):
                    continue
                selected_topics.append(candidate)
                current_items += _item_count(candidate)
                if current_items >= limit:
                    break
    items: list[dict[str, Any]] = []
    for topic in selected_topics:
        for item in _ITEM_BANK:
            if item["topic"] != topic:
                continue
            if len(items) >= limit:
                break
            items.append(item)
        if len(items) >= limit:
            break

    # 抽到全是同一类题会让认知风格的两个维度失去对照，
    # 组卷末尾做一次"事实题是否缺席"的兜底补齐。
    types_present = {i["type"] for i in items}
    if ITEM_FACTUAL not in types_present or ITEM_CONCEPTUAL not in types_present:
        missing = (
            ITEM_FACTUAL if ITEM_FACTUAL not in types_present else ITEM_CONCEPTUAL
        )
        for item in _ITEM_BANK:
            if item["type"] != missing or item in items:
                continue
            if item["topic"] in selected_topics:
                continue
            # 换掉最后一个类型的题，保证两类都有代表
            if items:
                items[-1] = item
            types_present.add(missing)
            break

    return {
        "count": len(items),
        "topics": selected_topics,
        "items": [public_item(i) for i in items],
    }


def probability_correct(theta: float, difficulty: float) -> float:
    """IRT 1PL：能力 θ 的学生答对难度 d 的题的概率。"""
    return 1.0 / (1.0 + math.exp(-DISCRIMINATION * (theta - difficulty)))


def update_ability(theta: float, difficulty: float, correct: bool) -> tuple[float, float]:
    """一次作答后更新能力值，返回 (新能力值, 更新前的答对概率)。

    用残差 (实际作答 - 期望作答) 驱动：
      答对了本该答对的题 -> 残差接近 0 -> 几乎不动（信息量低）
      答错了本该答对的题 -> 残差接近 -1 -> 明显下调（强信号）
    直接加分的做法在这里就失效了：它会让学霸把所有题刷到 1.0。
    """
    probability = probability_correct(theta, difficulty)
    observed = 1.0 if correct else 0.0
    new_theta = _clamp(theta + LEARNING_RATE * (observed - probability))
    return round(new_theta, 3), round(probability, 3)


def infer_cognitive_style(responses: list[dict[str, Any]]) -> dict[str, Any]:
    """从作答行为推断认知风格（Felder-Silverman 简化版，两个维度）。

    只做两个维度的原因写在文件头：几道选择题支撑不起更多结论。
    - reflective ↔ active：由"要不要线索"和"想多久"合成
    - sensing  ↔ intuitive：由事实题与概念题的正确率之差决定
    """
    answered = [r for r in responses if r.get("item_type")]
    total = len(answered)
    if total < MIN_STYLE_SAMPLES:
        return {
            "ready": False,
            "confidence": round(min(1.0, total / MIN_STYLE_SAMPLES), 2),
            "dimensions": [],
            "note": f"样本不足（{total}/{MIN_STYLE_SAMPLES}），暂不做风格判定",
        }

    hints = sum(1 for r in answered if r.get("hint_requested"))
    timed = [r["elapsed_ms"] for r in answered if isinstance(r.get("elapsed_ms"), int)]
    slow = sum(1 for ms in timed if ms > SLOW_ANSWER_MS)
    # 时间信息只覆盖一部分作答时按比例折算，不能拿"总体客/总答"当沉默比，
    # 否则 16 题只有 1 题带了耗时，也会得出"他很不沉吟"的错误结论。
    slow_ratio = (slow / len(timed)) if timed else 0.0
    hint_ratio = hints / total
    reflective_value = round(_clamp(0.65 * hint_ratio + 0.35 * slow_ratio, 0.0, 1.0), 2)

    factual = [r for r in answered if r["item_type"] == ITEM_FACTUAL]
    conceptual = [r for r in answered if r["item_type"] == ITEM_CONCEPTUAL]
    acc = lambda rows: (
        sum(1 for r in rows if r["correct"]) / len(rows) if rows else None
    )
    acc_factual = acc(factual)
    acc_conceptual = acc(conceptual)

    if acc_factual is None or acc_conceptual is None:
        # 缺一类题就没法做"两类相对强弱"的判断
        intuitive_value = 0.5
        delta = None
    else:
        delta = round(acc_conceptual - acc_factual, 3)
        intuitive_value = round(_clamp(0.5 + 0.6 * delta, 0.0, 1.0), 2)

    def pole(value: float, high_label: str, low_label: str) -> str:
        if value >= POLE_HIGH:
            return high_label
        if value <= POLE_LOW:
            return low_label
        return "balanced"

    dimensions = [
        {
            "key": "reflective_active",
            "dimension": "沉思型 ↔ 主动型",
            "value": reflective_value,
            "pole": pole(reflective_value, "reflective", "active"),
            "evidence": {
                "answers": total,
                "hints_requested": hints,
                "slow_answers": slow,
                "timed_answers": len(timed),
            },
        },
        {
            "key": "sensing_intuitive",
            "dimension": "感悟型 ↔ 直觉型",
            "value": intuitive_value,
            "pole": pole(intuitive_value, "intuitive", "sensing"),
            "evidence": {
                "answers": total,
                "accuracy_factual": acc_factual,
                "accuracy_conceptual": acc_conceptual,
                "accuracy_gap": delta,
            },
        },
    ]

    return {
        "ready": True,
        "confidence": round(min(1.0, total / 8), 2),
        "dimensions": dimensions,
        "note": "",
    }


def merge_ability_evidence(
    existing: dict[str, Any] | None, details: list[dict[str, Any]]
) -> dict[str, Any]:
    """把本次作答累加进能力证据表（StudentProfile.ability_vector）。

    knowledge_mastery 里那个分数是从这里推导出来的结论，留着原始计数才能
    在换了估计模型（比如从 1PL 换成三级模型）之后用历史数据重算，
    而不是让所有老画像作废、没人愿意再测一次。
    """
    merged: dict[str, Any] = dict(existing or {})
    for detail in details:
        key = normalize_topic(detail["topic"])
        entry = dict(merged.get(key) or {})
        entry["theta"] = detail["ability_after"]
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["correct"] = int(entry.get("correct", 0)) + (1 if detail["correct"] else 0)
        entry["last_difficulty"] = detail["difficulty"]
        merged[key] = entry
    return merged


def weak_points_from(details: list[dict[str, Any]]) -> list[str]:
    """把错题转成薄弱点，喂给画像现有的 weak_points。

    只在「本该答对却答错」时记（p_correct_before >= 0.5）：
    一道难题答错是正常的，记进来只会把薄弱点列表淹没在噪声里，
    而 history 里本来就有完整错题，不必靠 weak_points 兜底。

    文案取题干而不是答案解释：下游 personalize 是把它当作「他历史上理解错
    的地方」念给模型的，顺手贴上正确答案等于在提示词里剧透。
    """
    STAGE_TRUNCATE = 30
    notes: list[str] = []
    for detail in details:
        if detail["correct"] or detail.get("p_correct_before", 0.0) < 0.5:
            continue
        stem = (detail.get("stem") or "").strip()
        note = f"{detail['topic']}：{stem[:STAGE_TRUNCATE]}"
        if note not in notes:
            notes.append(note)
    return notes


def score_attempt(
    answers: list[dict[str, Any]],
    prior: dict[str, float] | None = None,
) -> dict[str, Any]:
    """批改一份答卷。

    answers: [{item_id, choice, elapsed_ms?, hint_requested?}]
    choice 是选项下标（0 起）。同一知识点多题按顺序累积更新。

    返回的 mastery 用 **归一键**（小写无空格），这是刻意的：
    profiler / personalize 也用同一套键，用原文会查不到掌握度，
    症状是"刚测完的画像在对话里完全不起作用"。
    """
    prior = prior or {}
    per_topic_before: dict[str, float] = {}
    details: list[dict[str, Any]] = []
    responses_for_style: list[dict[str, Any]] = []
    correct_count = 0
    invalid: list[str] = []

    for raw in answers:
        item = get_item(raw.get("item_id", ""))
        if item is None:
            invalid.append(str(raw.get("item_id", "")))
            continue
        topic = canonical_topic(item["topic"])
        topic_key = normalize_topic(topic)

        prior_score = prior.get(topic_key, per_topic_before.get(topic_key))
        theta = DEFAULT_MASTERY if prior_score is None else float(prior_score)
        per_topic_before.setdefault(topic_key, theta)

        choice = raw.get("choice")
        correct = isinstance(choice, int) and choice == item["answer"]
        if correct:
            correct_count += 1

        new_theta, probability = update_ability(theta, item["difficulty"], correct)
        per_topic_before[topic_key] = new_theta

        details.append(
            {
                "item_id": item["id"],
                "topic": topic,
                "stem": item["stem"],
                "item_type": item["type"],
                "difficulty": item["difficulty"],
                "choice": choice,
                "correct": correct,
                "correct_answer": item["answer"],
                "p_correct_before": probability,
                "ability_before": round(theta, 3),
                "ability_after": new_theta,
                "delta": round(new_theta - theta, 3),
                "explanation": item["explanation"],
            }
        )
        responses_for_style.append(
            {
                "item_type": item["type"],
                "correct": correct,
                "elapsed_ms": raw.get("elapsed_ms"),
                "hint_requested": bool(raw.get("hint_requested", False)),
            }
        )

    graded = len(details)
    return {
        "graded": graded,
        "invalid_items": invalid,
        "correct": correct_count,
        "accuracy": round(correct_count / graded, 3) if graded else 0.0,
        "mastery": {k: round(v, 2) for k, v in per_topic_before.items()},
        "details": details,
        "cognitive_style": infer_cognitive_style(responses_for_style),
    }

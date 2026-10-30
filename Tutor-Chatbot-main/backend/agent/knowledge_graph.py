"""学科知识图谱（M2）。

为什么需要它：T5 的画像只有「知识点 → 分数」一张平表，缺失的两件事决定了
它做不了 CDT——一是**知识点之间的依赖关系**（没学过进程与线程，就不该去
测死锁），二是**冷启动时的施测顺序**（先测哪个知识点信息量最大）。图谱正是
补这两块。

设计取舍：
1. **不引 networkx**。图只有 8 个节点，邻接字典 + 一次 DFS 就够，多一个
   重依赖只会拖慢安装、增加队友复现成本。
2. **知识点名必须与现有体系同构**。节点就是 `knowledge/cs/**/*.md` 的一级
   标题（`_build...` 里的 8 篇文档），因为它们是 RAG 唯一的检索源、也是
   profiler 写进 knowledge_mastery 的键。另起一套命名会让画像和图谱对不上。
3. **掌握度不存图里**。图是静态结构，掌握度是按 (user, classroom) 变化的，
   混在一起等于把静态数据写成动态数据。由调用方传 mastery 进来叠加
   （`graph_payload`）。
4. **纯函数、零 IO、零 LLM**——图谱是资产不是运行时状态，能直接写单测。
"""

from __future__ import annotations

from typing import Any, Iterable

CATEGORY_OS = "操作系统"
CATEGORY_NETWORK = "计算机网络"

# 知识点 → 结构信息。
# prereq（先修）：学 A 之前必须先掌握 B，方向是 A -> B，别写反。
# related（相关）：同层或易混淆，用来出对比题，不参与路径计算。
_NODES: dict[str, dict[str, Any]] = {
    "进程与线程": {
        "category": CATEGORY_OS,
        "doc": "cs/os/process-thread.md",
        "prereq": [],
        "related": ["进程调度算法", "虚拟内存与页面置换"],
    },
    "进程调度算法": {
        "category": CATEGORY_OS,
        "doc": "cs/os/scheduling.md",
        "prereq": ["进程与线程"],
        "related": ["死锁"],
    },
    "死锁": {
        "category": CATEGORY_OS,
        "doc": "cs/os/deadlock.md",
        "prereq": ["进程与线程"],
        "related": ["进程调度算法"],
    },
    "虚拟内存与页面置换": {
        "category": CATEGORY_OS,
        "doc": "cs/os/memory-management.md",
        "prereq": ["进程与线程"],
        "related": ["进程调度算法"],
    },
    "TCP/IP 分层模型与 ARP / DNS": {
        "category": CATEGORY_NETWORK,
        "doc": "cs/network/tcp-ip-model.md",
        "prereq": [],
        "related": ["TCP 与 UDP"],
    },
    "TCP 与 UDP": {
        "category": CATEGORY_NETWORK,
        "doc": "cs/network/tcp-vs-udp.md",
        "prereq": ["TCP/IP 分层模型与 ARP / DNS"],
        "related": ["TCP 三次握手与四次挥手"],
    },
    "TCP 三次握手与四次挥手": {
        "category": CATEGORY_NETWORK,
        "doc": "cs/network/tcp-handshake.md",
        "prereq": ["TCP 与 UDP"],
        "related": ["HTTPS 与 TLS 握手", "TCP 与 UDP"],
    },
    "HTTPS 与 TLS 握手": {
        "category": CATEGORY_NETWORK,
        "doc": "cs/network/http-https.md",
        "prereq": ["TCP 三次握手与四次挥手"],
        "related": ["TCP 与 UDP"],
    },
}

DEFAULT_MASTERY = 0.5
# 先修知识点掌握度低于这个值，就认为「还没到学它的时候」
PREREQ_READY_THRESHOLD = 0.4


def normalize_topic(topic: str) -> str:
    """与 profiler / personalize 完全一致的归一规则。

    三处只要有一处不一致，图谱查到的掌握度就永远是 None——
    这是最容易复发也最难查的一类 bug（症状是「明明学过却说 unexplored」）。
    """
    return "".join((topic or "").split()).lower()


def topics() -> list[str]:
    """全部知识点（拓扑序：先修在前）。"""
    return learning_order(list(_NODES.keys()))


def categories() -> list[str]:
    seen: list[str] = []
    for meta in _NODES.values():
        if meta["category"] not in seen:
            seen.append(meta["category"])
    return seen


def has_topic(topic: str) -> bool:
    """图谱认不认这个知识点。

    profiler 里的 topic 是模型自由抽取的，可能抽到图谱外的名字
    （比如「零拷贝」）。遇到这种情况调用方要退化处理，而不是建个空节点。
    """
    key = normalize_topic(topic)
    return any(normalize_topic(t) == key for t in _NODES)


def canonical_topic(topic: str) -> str:
    """归一但不丢原文：把模型抽出来的写法映射回图谱里的标准名。

    找不到就原样返回——多一个未知键不会污染图谱，返回 None 才会在
    序列化时炸一串 KeyError。
    """
    key = normalize_topic(topic)
    for name in _NODES:
        if normalize_topic(name) == key:
            return name
    return (topic or "").strip()


def get_node(topic: str) -> dict[str, Any] | None:
    name = canonical_topic(topic)
    if name not in _NODES:
        return None
    meta = _NODES[name]
    return {"topic": name, **meta}


def prerequisites(topic: str, transitive: bool = True) -> list[str]:
    """某知识点的先修集合，不含自己，按学习顺序返回。

    transitive=False 只取直接先修（用来在前端画一级箭头）。
    图里有环就该循环下去；这里的 `visited` 是防环的最后一道保险，
    而不是省事的提前 return —— 提前 return 会静默吞掉一条边。
    """
    name = canonical_topic(topic)
    if name not in _NODES:
        return []

    result: list[str] = []
    visited: set[str] = set()

    def walk(current: str) -> None:
        for parent in _NODES[current]["prereq"]:
            if normalize_topic(parent) in visited:
                continue
            visited.add(normalize_topic(parent))
            walk(parent)
            result.append(parent)

    walk(name)

    # 后序遍历（先递归祖先、再把自己 append 进来）产出的已经是拓扑序，
    # 即「最浅的先修在最前」。这里千万不能再 reversed 一次——那会把
    # HTTPS 的先修链反着给前端，画出来是一堆指向天空的箭头。
    if not transitive:
        direct = {normalize_topic(p) for p in _NODES[name]["prereq"]}
        return [p for p in result if normalize_topic(p) in direct]
    return result


def related_topics(topic: str) -> list[str]:
    meta = get_node(topic)
    if not meta:
        return []
    return [t for t in meta["related"] if t in _NODES]


def learning_order(candidates: Iterable[str]) -> list[str]:
    """把任意知识点集合按「先修在前」排成一个可执行的顺序。

    同一个知识点先在一个坑位出现就被排到前面，遇到图谱外的名字直接原样
    排在末尾（不是丢掉——丢掉会让推荐列表莫名变短，还查不出原因）。
    """
    wanted = [canonical_topic(t) for t in candidates]
    ordered: list[str] = []
    seen: set[str] = set()

    for item in wanted:
        for pre in prerequisites(item):
            key = normalize_topic(pre)
            if key not in seen:
                seen.add(key)
                ordered.append(pre)
        key = normalize_topic(item)
        if key not in seen:
            seen.add(key)
            ordered.append(item)
    return ordered


def learning_path(topic: str) -> list[str]:
    """学到某知识点要走完的完整路径（含它自己）。"""
    name = canonical_topic(topic)
    return [*prerequisites(name), name] if name in _NODES else []


def mastery_of(mastery: dict[str, float] | None, topic: str) -> float | None:
    """按归一键取掌握度。没记录返回 None（未测），不是 0（测了不会）。"""
    if not mastery:
        return None
    key = normalize_topic(topic)
    for raw_topic, score in mastery.items():
        if normalize_topic(raw_topic) == key:
            try:
                return round(min(1.0, max(0.0, float(score))), 2)
            except (TypeError, ValueError):
                return None
    return None


def recommend_next(
    mastery: dict[str, float] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """下一步学什么。

    三条排序准则，优先级从高到低：
    1. **先修必须先达标**：某个知识点的先修没学到阈值（0.4）就直接跳过它，
       而不是推荐分数最低的那一个——推一个他根本啃不动的知识点，
       比不推荐更糟，会直接把人劝退。
    2. **自己还没掌握的优先**：掌握度升序。
    3. **同分时按图谱顺序**（基础在前），保证结果确定、可复现。

    返回值带 `score` 和 `blocked_by`，让前端能解释「为什么推这个」，
    也方便排查「为什么死锁总推不出来」。
    """
    mastery = mastery or {}
    order = {normalize_topic(t): i for i, t in enumerate(topics())}
    candidates: list[dict[str, Any]] = []

    for topic in _NODES:
        score = mastery_of(mastery, topic)
        blocking = [
            p
            for p in prerequisites(topic)
            if (mastery_of(mastery, p) or 0.0) < PREREQ_READY_THRESHOLD
        ]
        candidates.append(
            {
                "topic": topic,
                "category": _NODES[topic]["category"],
                "score": score,
                "mastery": score,
                "blocked_by": blocking,
                "order": order[normalize_topic(topic)],
            }
        )

    ready = [c for c in candidates if not c["blocked_by"]]
    key_func = lambda c: (
        c["score"] if c["score"] is not None else DEFAULT_MASTERY,
        c["order"],
    )
    ranked = sorted(ready, key=key_func)[: max(1, limit)]
    for item in ranked:
        item["measured"] = item["score"] is not None
        # None 是「没测过」，0.5 才是给排序用的占位；回给调用方时要还原语义
        item["score"] = None if item["score"] is None else item["score"]
    return ranked


def graph_payload(mastery: dict[str, float] | None = None) -> dict[str, Any]:
    """供 GET /students/{id}/knowledge-graph 返回的完整结构。

    掌握度叠加在节点上（2.5 取舍第 3 条），边单独给出一个列表便于前端画箭头。
    """
    mastery = mastery or {}
    nodes = [
        {
            "topic": topic,
            "category": meta["category"],
            "doc": meta["doc"],
            "mastery": mastery_of(mastery, topic),
            "measured": mastery_of(mastery, topic) is not None,
        }
        for topic, meta in _NODES.items()
    ]
    edges: list[dict[str, str]] = []
    for topic, meta in _NODES.items():
        for parent in meta["prereq"]:
            edges.append({"from": parent, "to": topic, "type": "prerequisite"})
        for peer in meta["related"]:
            edges.append({"from": topic, "to": peer, "type": "related"})

    seen_edges: set[tuple[str, str, str]] = set()
    unique_edges: list[dict[str, str]] = []
    for edge in edges:
        triple = (edge["from"], edge["to"], edge["type"])
        if triple in seen_edges:
            continue
        seen_edges.add(triple)
        unique_edges.append(edge)

    return {
        "categories": categories(),
        "node_count": len(nodes),
        "nodes": nodes,
        "edges": unique_edges,
        "learning_order": list(_NODES.keys()),
    }

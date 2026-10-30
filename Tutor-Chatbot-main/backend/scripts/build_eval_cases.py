"""评测集扩充工具：下载公开题库 → 抽问句 → 自动映射到本库 topic → 产出候选。

为什么需要这个脚本
------------------
检索评测集的成本几乎全在「每条都要有正确的 expected topic」上，不在问句本身。
所以这里把三件事自动化，只把「确认标签」这一件必须人脑判断的事留给人：

  core 层  从本库 8 篇文档的「## 面试常问」抽问句
           —— 归属文档即正确答案，**零标注成本、标签 100% 可信**，直接可用
  ext  层  从公开题库抽问句，用「本库自动抽取的 TF-IDF 特征词」猜 topic
           —— 只产出候选，label_source=auto，**必须人审后才算数**
  oos  层  公开题库里没有命中任何 topic 的问句
           —— 是库外负例的候选来源，用来观察「有没有把无关问题硬塞进本库」

为什么 ext 层的自动标签不能直接进门禁
------------------------------------
自动标签靠「问句里的词和知识文档里的词重合」判定归属。而检索本身也吃词重合
（BM25 一路尤其明显）。用这种题做门禁，等于用同一套信号既出题又判卷——
分数会虚高，且永远发现不了「语义对但用词不同」的失败。这正是要人工确认的原因。
判断标准很简单：这条题正常人会认为答案在那篇文档里吗？是才留。

用法（在 backend/ 目录下，用 venv 的 python）
    python build_eval_cases.py --download       # 下载题库到 eval_src/（约 250KB）
    python build_eval_cases.py --gen            # 生成候选 rag_eval_candidates.json
    python build_eval_cases.py --stats          # 看现有评测集的题量与覆盖
    python build_eval_cases.py --download --gen # 一步到位

审核流程
    1. 打开 rag_eval_candidates.json，翻 ext 层，把认同的 case 的
       "label_source" 从 "auto" 改成 "manual"（改错 topic 就直接改 expected）
    2. 重跑 --gen，脚本会把 label_source=manual 的挑出来，打印可合并的片段
    3. 人工把片段贴进 rag_eval_cases.json
    （不提供自动合并：评测集是金标准，写进去必须经过人手一次）

语料许可
    eval_src/ 下的内容是第三方仓库快照，仅作本地评测语料，不随本仓库分发
    （已加入 backend/.gitignore）。分发或商用前请自行核对上游 LICENSE。
"""

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/（脚本已移入 scripts/）

import jieba

BACKEND_DIR = Path(__file__).resolve().parents[1]  # backend/（脚本已移入 scripts/）
KNOWLEDGE_DIR = BACKEND_DIR.parents[1] / "knowledge"
SRC_DIR = BACKEND_DIR / "eval_src"
CASES_FILE = BACKEND_DIR / "rag_eval_cases.json"
CANDIDATES_FILE = BACKEND_DIR / "rag_eval_candidates.json"

# 全部实测可从 raw.githubusercontent.com 直连下载（2026-10-19，1~2s）；
# huggingface.co 与 hf-mirror.com 在本机均超时，所以不走 HF 数据集。
SOURCES = [
    {
        "id": "jg-os-01",
        "domain": "os",
        "url": "https://raw.githubusercontent.com/Snailclimb/JavaGuide/main/docs/cs-basics/operating-system/operating-system-basic-questions-01.md",
        "note": "JavaGuide 操作系统面试题①",
    },
    {
        "id": "jg-os-02",
        "domain": "os",
        "url": "https://raw.githubusercontent.com/Snailclimb/JavaGuide/main/docs/cs-basics/operating-system/operating-system-basic-questions-02.md",
        "note": "JavaGuide 操作系统面试题②",
    },
    {
        "id": "jg-linux",
        "domain": "os",
        "url": "https://raw.githubusercontent.com/Snailclimb/JavaGuide/main/docs/cs-basics/operating-system/linux-intro.md",
        "note": "JavaGuide Linux 入门（含命令与内核概念）",
    },
    {
        "id": "jg-net-q1",
        "domain": "network",
        "url": "https://raw.githubusercontent.com/Snailclimb/JavaGuide/main/docs/cs-basics/network/other-network-questions.md",
        "note": "JavaGuide 计算机网络面试题①",
    },
    {
        "id": "jg-net-q2",
        "domain": "network",
        "url": "https://raw.githubusercontent.com/Snailclimb/JavaGuide/main/docs/cs-basics/network/other-network-questions2.md",
        "note": "JavaGuide 计算机网络面试题②",
    },
    {
        "id": "jg-net-xie",
        "domain": "network",
        "url": "https://raw.githubusercontent.com/Snailclimb/JavaGuide/main/docs/cs-basics/network/computer-network-xiexiren-summary.md",
        "note": "谢希仁《计算机网络》笔记",
    },
    {
        "id": "jg-http",
        "domain": "network",
        "url": "https://raw.githubusercontent.com/Snailclimb/JavaGuide/main/docs/cs-basics/network/http-vs-https.md",
        "note": "HTTP 与 HTTPS 专题",
    },
    {
        "id": "csnotes-http",
        "domain": "network",
        "url": "https://raw.githubusercontent.com/CyC2018/CS-Notes/master/notes/HTTP.md",
        "note": "CS-Notes HTTP（陈述式，标题多为名词，用于术语类查询）",
    },
    {
        "id": "csnotes-deadlock",
        "domain": "os",
        "url": "https://raw.githubusercontent.com/CyC2018/CS-Notes/master/notes/%E8%AE%A1%E7%AE%97%E6%9C%BA%E6%93%8D%E4%BD%9C%E7%B3%BB%E7%BB%9F%20-%20%E6%AD%BB%E9%94%81.md",
        "note": "CS-Notes 死锁",
    },
    {
        "id": "csnotes-memory",
        "domain": "os",
        "url": "https://raw.githubusercontent.com/CyC2018/CS-Notes/master/notes/%E8%AE%A1%E7%AE%97%E6%9C%BA%E6%93%8D%E4%BD%9C%E7%B3%BB%E7%BB%9F%20-%20%E5%86%85%E5%AD%98%E7%AE%A1%E7%90%86.md",
        "note": "CS-Notes 内存管理",
    },
    {
        "id": "csnotes-proc",
        "domain": "os",
        "url": "https://raw.githubusercontent.com/CyC2018/CS-Notes/master/notes/%E8%AE%A1%E7%AE%97%E6%9C%BA%E6%93%8D%E4%BD%9C%E7%B3%BB%E7%BB%9F%20-%20%E8%BF%9B%E7%A8%8B%E7%AE%A1%E7%90%86.md",
        "note": "CS-Notes 进程管理",
    },
    {
        "id": "csnotes-transport",
        "domain": "network",
        "url": "https://raw.githubusercontent.com/CyC2018/CS-Notes/master/notes/%E8%AE%A1%E7%AE%97%E6%9C%BA%E7%BD%91%E7%BB%9C%20-%20%E4%BC%A0%E8%BE%93%E5%B1%82.md",
        "note": "CS-Notes 传输层",
    },
]

# 直连失败时的镜像前缀（实测 gh-proxy 可用但慢一倍，只作 fallback）
MIRRORS = ["", "https://gh-proxy.com/"]

# 停用词：只影响特征词质量，不影响判定逻辑
STOP = set(
    "的了和是在有与及或就是对不也都而等这那个一为以从到可能如果所以因为需要可以"
    "什么么怎样如何哪些为什时候我们他们你们它们以及一种一个进行通过使用实现"
    "下面上面之后之前同时另外此外例如比如注意这里那里".split()
)

# 抽问句：标题行 + 加粗问句（1. **xxx？** 答案）
HEADING_RE = re.compile(r"^#{2,4}\s+(.+?)\s*$")
BOLD_Q_RE = re.compile(r"^\s*(?:\d+\.|[-*+])\s+\*\*(.+?)\*\*")
# 疑问句式：含问号，或以疑问词收尾。
# 不含「区别 / 不同」——它们在对比表格的小标题里极常见（如「地址结构不同」），
# 收进来会混入大量不是问句的碎片。
Q_TAIL_RE = re.compile(r"(吗|呢|么|什么|怎么|怎样|为什么|为何|如何|哪些|哪个|多少)$")


def has_question_form(text: str) -> bool:
    if "？" in text or "?" in text:
        return True
    t = text.rstrip("。.！! ")
    return bool(Q_TAIL_RE.search(t))


def clean_title(text: str) -> str:
    """去掉导航装饰：⭐️、序号前缀、多余空白。"""
    t = re.sub(r"[⭐️\s]+", " ", text).strip()
    t = re.sub(r"^[\d.、]+\s*", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" :：。.")


def toks(text: str) -> list[str]:
    out = []
    for w in jieba.cut(text):
        w = w.strip()
        if len(w) < 2 or w in STOP:
            continue
        if re.fullmatch(r"[\W_]+", w):
            continue
        out.append(w)
    return out


# ---------------- 知识库 topic 与特征词 ----------------


def load_knowledge() -> dict[str, str]:
    """topic 名 → 文档全文。topic 取一级标题，与 chroma metadata 一致。"""
    docs = {}
    for p in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        text = p.read_text(encoding="utf-8")
        m = re.search(r"^# (.+)$", text, re.M)
        if m:
            docs[m.group(1).strip()] = text
    if not docs:
        raise SystemExit("知识库为空：%s" % KNOWLEDGE_DIR)
    return docs


class FeatureIndex:
    """从知识库自动抽取的 topic 特征词表，附带判定所需的统计量。

    不手写特征词表：知识库一扩，手写表就得跟着改，改漏了判定会静默变差。

    **权重必须用归一化词频（词数/本篇总词数），不能用原始计数**：
    tcp-vs-udp.md 里提到过几次 HTTP（讲 HTTP Keep-Alive），原始计数会让
    「HTTP」成为那篇的高分特征词，于是「HTTP 和 HTTPS 有什么区别？」被误判成
    TCP 与 UDP——实测踩过。归一化后 HTTP 在那篇的占比极低，权重自然掉下去。
    """

    def __init__(self, docs: dict[str, str], top_n: int = 40):
        self.tf = {t: Counter(toks(text)) for t, text in docs.items()}
        self.total = {t: max(sum(c.values()), 1) for t, c in self.tf.items()}
        self.df = Counter()
        for c in self.tf.values():
            for w in c:
                self.df[w] += 1
        self.n = len(self.tf)

        self.feat = {}
        for t, c in self.tf.items():
            scored = [
                # cnt >= 3：出现两次的往往只是顺带一提（http-https.md 提过
                # WebSocket，于是「什么是 WebSocket」被判成 HTTPS 主题）。
                # 抬到 3 次能把「提及」和「主题」分开。
                (self._weight(t, w, cnt), w) for w, cnt in c.items() if cnt >= 3
            ]
            scored.sort(reverse=True)
            self.feat[t] = [w for _, w in scored[:top_n]]

    def _weight(self, topic: str, word: str, cnt: int) -> float:
        return (cnt / self.total[topic]) * math.log(1.0 + self.n / self.df[word])

    def classify(self, query: str) -> tuple[str, list[str], float, bool]:
        """返回 (topic, 命中词, 置信分, 是否有专属证据)。

        `strong` 判据：命中的词里有没有「只在某一篇出现」的（df == 1）。
        比分数阈值可解释——「死锁」只在 deadlock.md 出现，命中它就是硬证据；
        「进程」横跨三四篇，命中它说明不了什么。低置信的题优先人工看，
        这样审的通常是真错判，而不是按分数瞎猜阈值。
        """
        qs = set(toks(query))
        best_topic, best_hit, best_score = "", [], 0.0
        for t, ws in self.feat.items():
            hit = [w for w in ws if w in qs]
            if not hit:
                continue
            score = sum(self._weight(t, w, self.tf[t][w]) for w in hit)
            if score > best_score:
                best_topic, best_hit, best_score = t, hit, score

        if best_topic:
            best_hit.sort(key=lambda w: -self._weight(best_topic, w, self.tf[best_topic][w]))
            strong = any(self.df[w] == 1 for w in best_hit)
        else:
            strong = False
        return best_topic, best_hit, best_score, strong


# ---------------- 抽取 ----------------


def extract_questions(md: str) -> list[tuple[str, str]]:
    """返回 [(问句, form)]，form 为 question / term。"""
    out = []
    for line in md.splitlines():
        m = HEADING_RE.match(line)
        if m:
            t = clean_title(m.group(1))
            if t:
                out.append((t, "question" if has_question_form(t) else "term"))
            continue
        m = BOLD_Q_RE.match(line)
        if m:
            t = clean_title(m.group(1))
            if t:
                out.append((t, "question" if has_question_form(t) else "term"))
    return out


def extract_core(docs: dict[str, str]) -> list[dict]:
    """本库「## 面试常问」小节 → 现成评测题。

    这些问句本来就在知识库里、且归属文档已知，expected 天然正确，
    是零标注成本的高质量题源。缺点也明显：问句与检索目标同源，
    措辞高度重合，比真实学生提问容易——所以只作为 core 层，
    要和 ext 层的「换一种说法」的题分开统计。
    """
    out = []
    for topic, text in docs.items():
        m = re.search(r"^## 面试常问\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
        if not m:
            continue
        for q, form in extract_questions(m.group(1)):
            if 6 <= len(q) <= 60 and form == "question":
                out.append({"query": q, "expected": [topic], "source": "kb-interview"})
    return out


# ---------------- 下载 ----------------


def download(force: bool = False) -> None:
    SRC_DIR.mkdir(exist_ok=True)
    ok = fail = 0
    for src in SOURCES:
        dest = SRC_DIR / ("%s.md" % src["id"])
        if dest.exists() and not force:
            print("  跳过（已存在）%s" % src["id"])
            ok += 1
            continue

        body = None
        for prefix in MIRRORS:
            url = prefix + src["url"]
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    if resp.status != 200:
                        continue
                    body = resp.read()
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print("  镜像失败 %-14s %s: %s" % (src["id"], prefix or "直连", exc))

        if not body:
            print("  ✗ 下载失败 %s" % src["id"])
            fail += 1
            continue

        dest.write_bytes(body)
        print("  ✓ %-14s %6d B  %s" % (src["id"], len(body), src["note"]))
        ok += 1

    print("\n下载完成：成功 %d / 失败 %d → %s" % (ok, fail, SRC_DIR))
    if fail:
        raise SystemExit(1)


# ---------------- 生成候选 ----------------


def norm(s: str) -> str:
    return s.replace(" ", "").lower()


def gen(index: "FeatureIndex", docs: dict[str, str], include_terms: bool = False) -> dict:
    existing = {norm(c["query"]) for c in json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]}
    docs = load_knowledge()

    core = [c for c in extract_core(docs) if norm(c["query"]) not in existing]

    files = sorted(SRC_DIR.glob("*.md"))
    if not files:
        raise SystemExit("eval_src 为空，先跑 --download")

    seen = set(existing)
    weak, auto, review, oos = [], [], [], []
    for f in files:
        for q, form in extract_questions(f.read_text(encoding="utf-8")):
            if not (6 <= len(q) <= 60):
                continue
            # 默认只收真正的问句。CS-Notes 的二级标题是陈述式章节名
            # （「二、HTTP 方法」「破坏互斥条件」），学生不会这么提问，
            # 收进来只会把候选池稀释成噪音。要收加 --include-terms。
            if form != "question" and not include_terms:
                continue
            key = norm(q)
            if key in seen:
                continue
            seen.add(key)

            topic, hit, score, strong = index.classify(q)
            item = {
                "query": q,
                "expected": [topic] if topic else [],
                "form": form,
                "label_source": "auto" if topic else "",
                "confidence": round(score, 4),
                "strong": strong,
                "evidence": hit[:5],
            }
            if not topic:
                item["suggested_kind"] = "oos"
                oos.append(item)
            elif not strong:
                # 只命中横跨多篇的通用词（如「进程」「内存」），证据不专一。
                # 这类最容易判错，单独列出来先看，别混进 auto 层冒充高置信。
                item["suggested_kind"] = "weak-evidence"
                weak.append(item)
            elif form == "question":
                auto.append(item)
            else:
                # 名词性标题（如「拥塞控制」）不是真实提问形态，
                # 单独放 review：人工判断是否改写成问句后收录
                item["suggested_kind"] = "term-to-question"
                review.append(item)

    # 证据强的排前面，人审从头翻最划算
    auto.sort(key=lambda c: -c["confidence"])
    weak.sort(key=lambda c: -c["confidence"])

    return {
        "note": (
            "评测集候选。auto 层由「问句词 ∩ 知识文档特征词」自动判定 topic，"
            "必须先人工把 label_source 改成 manual 才能并进 rag_eval_cases.json——"
            "自动标签与检索共用词重合信号，直接当门禁会虚高。"
            "evidence 是判定依据（命中的特征词，按权重降序），confidence 是加权分，"
            "strong=true 表示命中了本篇独有的词（硬证据）。"
            "weak 层只命中跨篇通用词，判错概率最高，优先审。"
        ),
        "core": core,
        "auto": auto,
        "weak": weak,
        "review": review,
        "oos": oos,
    }


def print_candidates(data: dict) -> None:
    print("\n候选统计")
    print("  core（本库面试常问，标签可信，可直接用）: %d" % len(data["core"]))
    print("  auto（外部问句 + 专属证据，需人审）     : %d" % len(data["auto"]))
    print("  weak（只有跨篇通用词，判错概率最高）    : %d" % len(data["weak"]))
    print("  review（名词性标题，需改写为问句）      : %d" % len(data["review"]))
    print("  oos（未命中任何 topic，库外负例候选）    : %d" % len(data["oos"]))

    confs = sorted(c["confidence"] for c in data["auto"] + data["weak"])
    if confs:
        def q(p):
            return confs[min(len(confs) - 1, int(len(confs) * p))]
        print(
            "\n置信分分布：最小 %.4f  P25 %.4f  中位 %.4f  P75 %.4f  最大 %.4f"
            % (confs[0], q(.25), q(.5), q(.75), confs[-1])
        )

    by_topic = Counter((c["expected"] or ["?"])[0] for c in data["auto"])
    if by_topic:
        print("\nauto 层按 topic 分布（题量过少的 topic 说明该知识点问句覆盖不足）：")
        for t, n in by_topic.most_common():
            print("  %-24s %d" % (t, n))


def dump_cases(payload: dict) -> str:
    """按 topic 分组写出评测集，保持人读友好的空行分组。

    不用 json.dumps 整体输出：那样 75 条会连成一整块，
    评审时找不到「这个 topic 有哪几题」，改题容易改错位置。
    """
    groups: list[tuple[str, list[dict]]] = []
    index = {}
    for c in payload["cases"]:
        key = (c.get("expected") or ["（库外）"])[0]
        if key not in index:
            index[key] = len(groups)
            groups.append((key, []))
        groups[index[key]][1].append(c)

    body = []
    for gname, items in groups:
        chunk = ",\n".join(
            "    " + json.dumps(c, ensure_ascii=False) for c in items
        )
        body.append(chunk)
    cases_text = ",\n\n".join(body)

    head = json.dumps(
        {k: v for k, v in payload.items() if k != "cases"}, ensure_ascii=False, indent=2
    )
    head = head[:-2]  # 去掉结尾的 \n}，后面手工接 cases
    return head + ',\n  "cases": [\n' + cases_text + "\n  ]\n}\n"


def merge_core(data: dict) -> int:
    """把 core 层（本库「面试常问」）合进正式评测集，返回新增条数。

    只有这一层可以自动合并：expected 就是该问句所在文档的一级标题，
    不存在判定误差。auto / weak 层必须人工确认，不提供自动合并入口。
    """
    payload = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    existing = {norm(c["query"]) for c in payload["cases"]}
    used_ids = {c["id"] for c in payload["cases"]}

    added = 0
    for item in data["core"]:
        if norm(item["query"]) in existing:
            continue
        added += 1
        new_id = "iv-%d" % added
        while new_id in used_ids:
            new_id = "iv-%d" % (added + 100)
        used_ids.add(new_id)
        existing.add(norm(item["query"]))
        payload["cases"].append(
            {
                "id": new_id,
                "query": item["query"],
                # 用完整 topic 名而不是简写子串：判定是子串包含，
                # 简写（如「死锁」）会把 topic 名里恰好含该词的题也判成命中。
                "expected": [item["expected"][0]],
                "kind": "kb-interview",
                "aspect": "面试常问",
            }
        )

    if not added:
        print("core 层没有新题（与现有评测集完全重复）")
        return 0

    CASES_FILE.write_text(dump_cases(payload), encoding="utf-8")
    print("已合并 %d 条 core 题 → %s（当前共 %d 条）" % (added, CASES_FILE.name, len(payload["cases"])))
    print("下一步：跑 python smoke_rag.py --save-base rag_baseline.json 更新基线")
    return added


def stats() -> None:
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    cases = data["cases"]
    print("现有评测集：%d 条" % len(cases))
    print("\n按 kind：")
    for k, n in Counter(c["kind"] for c in cases).most_common():
        print("  %-10s %d" % (k, n))
    print("\n按 topic（计分题）：")
    for t, n in Counter((c["expected"] or ["（库外）"])[0] for c in cases).most_common():
        print("  %-24s %d" % (t, n))
    print("\n按问法：")
    for a, n in Counter(c.get("aspect", "?") for c in cases).most_common():
        print("  %-14s %d" % (a, n))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="下载题库到 eval_src/")
    ap.add_argument("--gen", action="store_true", help="生成候选 JSON")
    ap.add_argument("--stats", action="store_true", help="看现有评测集覆盖")
    ap.add_argument("--force", action="store_true", help="已存在的文件也重新下载")
    ap.add_argument("--include-terms", action="store_true", help="连名词性标题一起收（噪音多）")
    ap.add_argument("--merge-core", action="store_true", help="把 core 层并入正式评测集（幂等）")
    args = ap.parse_args()

    if not (args.download or args.gen or args.stats or args.merge_core):
        ap.print_help()
        return 0

    if args.download:
        print("下载题库 → %s" % SRC_DIR)
        download(force=args.force)

    if args.stats:
        stats()

    if args.gen:
        docs = load_knowledge()
        index = FeatureIndex(docs)
        print("\n知识库 %d 个 topic，特征词各 %d 个" % (len(index.feat), len(next(iter(index.feat.values())))))
        data = gen(index, docs, include_terms=args.include_terms)
        CANDIDATES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print_candidates(data)
        print("\n候选已写入：%s" % CANDIDATES_FILE)
        print("下一步：人工把认同的 case 的 label_source 改成 manual，再决定是否并入正式集。")

    if args.merge_core:
        if not CANDIDATES_FILE.exists():
            raise SystemExit("没有候选文件，先跑 --gen")
        data = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
        merge_core(data)
        stats()

    return 0


if __name__ == "__main__":
    sys.exit(main())

/**
 * 假后端 —— 只用于前端自测，不入库、不调模型。
 *
 * 为什么需要它：本机跑不起真后端（没有 .env / venv，MySQL 没起），
 * 但「一轮多个 Agent 分气泡」这条链路必须能自己验掉，
 * 否则联调时才第一次看到效果，出问题分不清是前端还是后端。
 *
 * 所有返回结构与 `docs/specs/API_CONTRACT.md` 一致，场景表与
 * `backend/agent/scenarios.py` 逐字对齐 —— 换成真后端时前端一行不用改。
 *
 * 跑法：node mock-backend/server.js      （默认 8000 端口，与入口页默认值一致）
 */

const http = require("node:http");

const PORT = Number(process.env.MOCK_PORT || 8000);

// --- 场景表：与 backend/agent/scenarios.py 保持一致 -----------------------

const SCENARIOS = {
  v1: {
    key: "v1",
    label: "1v1 辅导",
    steps: [{ role: "tutor", label: "主讲助教", stance: "主讲", handoff: "" }],
  },
  group: {
    key: "group",
    label: "小组讨论",
    steps: [
      { role: "tutor", label: "主讲助教", stance: "主讲", handoff: "" },
      { role: "peer", label: "学伴", stance: "补充", handoff: "" },
    ],
  },
  debate: {
    key: "debate",
    label: "辩论对抗",
    steps: [
      { role: "tutor", label: "主讲助教", stance: "立论", handoff: "" },
      { role: "peer", label: "学伴", stance: "质疑", handoff: "" },
      { role: "examiner", label: "考官", stance: "追问", handoff: "" },
    ],
  },
};

/** 与后端 `normalize_scenario` 同策略：空 / 未知一律回落 v1，不报 422。 */
function normalizeScenario(scenario) {
  const key = String(scenario || "").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(SCENARIOS, key) ? key : "v1";
}

// --- 内存数据 -------------------------------------------------------------

const users = new Map(); // username -> User
const classrooms = new Map(); // id -> Classroom
const members = new Map(); // classroomId -> Member[]
const messages = new Map(); // classroomId -> ChatMessage[]

let seq = { user: 0, classroom: 0, message: 0 };

function now() {
  return new Date().toISOString().replace("T", " ").slice(0, 19);
}

// 预置一个课堂 1，入口页默认填的就是它
classrooms.set(1, {
  id: 1,
  name: "计算机八股冲刺班",
  subject: "cs",
  description: "预置课堂，用于本地自测",
  is_private: false,
  scenario: "group",
  created_at: now(),
});
seq.classroom = 1;
members.set(1, []);
messages.set(1, []);

function touchClassroom(cid) {
  if (!members.has(cid)) members.set(cid, []);
  if (!messages.has(cid)) messages.set(cid, []);
}

function appendMessage(cid, msg) {
  touchClassroom(cid);
  seq.message += 1;
  const row = {
    id: seq.message,
    user_id: msg.user_id ?? null,
    username: msg.username ?? null,
    role: msg.role,
    content: msg.content,
    topic: msg.topic ?? null,
    hint_level: msg.hint_level ?? 0,
    created_at: now(),
    agent_role: msg.agent_role ?? null,
    // 与后端 db/models.py:150 对齐：sources 随消息落库，刷新后前端据此恢复"依据"。
    // 只有 assistant 回复才有依据；人类消息留 null。
    sources:
      msg.role === "assistant" ? (msg.sources ?? null) : null,
  };
  messages.get(cid).push(row);
  return row;
}

// ---  mock 知识：命中关键词就给真实八股内容 --------------------------------

const KB = [
  {
    match: /死锁|deadlock/i,
    topic: "死锁",
    source: "cs/os/deadlock.md",
    tutor:
      "死锁的四个必要条件是：互斥、占有且等待、不可抢占、循环等待。\n\n四个条件必须同时成立才会产生死锁，所以破坏任意一个就能预防。\n\n面试常问「怎么解决」，标准答法是按四个方向分别对应：\n1. 破坏占有且等待 —— 一次性申请所有资源；\n2. 破坏不可抢占 —— 申请不到就释放已有资源；\n3. 破坏循环等待 —— 给资源编号，按序申请；\n4. 破坏互斥 —— 能共享的就不独占（适用面最窄）。",
    peer:
      "我记这四个条件用的是一句话：「互斥占有不可抢，最后绕成一个环」。\n\n补充一个容易漏的角度：上面说的是**预防**，代价是资源利用率低。真要实用，一般是允许死锁发生，再做**检测 + 恢复**——比如数据库里画等待图，检测到环就挑一个事务回滚。 InnoDB 就是这么干的，它有死锁检测，超时也会自动回滚代价小的事务。",
    examiner:
      "追问一道：\n\n系统里有 3 个进程、同类资源共 10 个。当前 P1 已占 4 个、P2 已占 2 个、P3 已占 2 个，都不打算释放，且都要再申请 1 个才结束。这个状态安全吗？如果此刻又来一个 P4 申请 2 个，银行家算法会不会批准？\n\n判断标准：能不能找出一个安全序列，让所有进程都跑完；找不出来就说明处于不安全状态。",
  },
  {
    match: /三次握手|握手|handshake/i,
    topic: "TCP 三次握手",
    source: "cs/network/tcp-handshake.md",
    tutor:
      "三次握手解决的是「双方都确认对方的发送和接收能力正常」。\n\n① 客户端发 SYN，seq=x；\n② 服务端回 SYN+ACK，seq=y，ack=x+1；\n③ 客户端回 ACK，ack=y+1。\n\n为什么两次不行：两次只能让客户端确认服务端正常，服务端无法确认客户端的接收能力正常。\n\n为什么不是四次：服务端的 SYN 和 ACK 可以合并在一个报文里发，没必要拆。",
    peer:
      "补一个面试高频追问：「三次握手的目的是什么？」光答「建立连接」是不够的，标准说法是**同步双方的初始序列号 ISN**，同时互换窗口大小、MSS 等参数。\n\n还有个实用角度：握手过程中如果第三次 ACK 丢了，服务端会重传 SYN+ACK，重传次数由 `tcp_synack_retries` 控制，默认 5 次，期间客户端以为连接已建立就发数据的话，实际上数据会丢。",
    examiner:
      "追问一道：\n\n SYN 报文不携带数据，但它消耗一个序列号，这是为什么？另外，如果客户端发完 SYN 就宕机了（SYN Flood 攻击场景），服务端会怎样？\n\n判断标准：能说清「三次握手里序列号的意义」，以及 SYN 队列 / syncookie 的作用，就不是背答案。",
  },
  {
    match: /进程.*线程|线程.*进程|协程/i,
    topic: "进程与线程",
    source: "cs/os/process-thread.md",
    tutor:
      "一句话区别：进程是**资源分配**的基本单位，线程是**CPU 调度**的基本单位。\n\n进程有独立的地址空间，线程共享所属进程的地址空间，但有自己的栈、寄存器和程序计数器。\n\n由此推出三点：\n1. 线程切换开销小（不用换页表）；\n2. 线程间通信快（共享内存，但要加锁）；\n3. 一个线程崩了可能拖垮整个进程。",
    peer:
      "补充个好记的角度：**进程像房子，线程像房子里的人**。房子之间互相隔离，人之间共享客厅厨房，但每人有自己的房间（栈）。\n\n再加一层：协程是**用户态线程**，切换不用陷入内核，开销更小。Python 的 asyncio、Go 的 goroutine 都是这个思路，适合 IO 密集型，但吃不到多核——因为内核根本看不见它。",
    examiner:
      "追问一道：\n\n多线程一定比单线程快吗？在什么情况下多线程反而更慢？\n\n判断标准：能提到「上下文切换开销」「锁竞争」「GIL」，并区分 CPU 密集型与 IO 密集型。",
  },
  {
    match: /拥塞|congestion/i,
    topic: "TCP 拥塞控制",
    source: "cs/network/tcp-congestion.md",
    tutor:
      "拥塞控制和流量控制是两件事：流量控制防的是**接收方被撑死**（滑动窗口），拥塞控制防的是**网络被撑死**。\n\n四个算法按顺序：慢开始 → 拥塞避免 → 快重传 → 快恢复。\n\n核心是两个变量：cwnd（拥塞窗口）和 ssthresh（慢开始门限）。\n- cwnd < ssthresh：慢开始，指数增长；\n- cwnd ≥ ssthresh：拥塞避免，线性增长；\n- 收到 3 个重复 ACK：快重传 + 快恢复，ssthresh 和 cwnd 都砍半。",
    peer:
      "补充一句容易说错的：慢开始**一点也不慢**——它是指数增长，只是起点低（1 个 MSS）。\n\n还有个实战角度：现在的 Linux 默认用的是 CUBIC 而不是传统 Reno，它用三次函数控制窗口增长，在高带宽长延迟网络（比如跨国专线）里表现更好。BBR 则是另一套思路，不靠丢包判断，而是测带宽和 RTT。",
    examiner:
      "追问一道：\n\n超时重传和快重传，触发条件有什么不同？为什么收到 3 个重复 ACK 就能判断丢包，而不是直接等超时？\n\n判断标准：能解释「3 个重复 ACK 意味着网络还在传数据，只是某个报文丢了」，所以不用把 cwnd 砍到 1。",
  },
  {
    match: /http|https|TLS|SSL/i,
    topic: "HTTP 与 HTTPS",
    source: "cs/network/http-https.md",
    tutor:
      "HTTPS = HTTP + TLS，默认端口 443（HTTP 是 80）。\n\nTLS 握手的核心流程：\n① 客户端发支持的加密套件和一个随机数；\n② 服务端回证书 + 选定的套件 + 一个随机数；\n③ 客户端验证证书，用证书里的公钥加密「预主密钥」发回去；\n④ 双方各自用三个随机数推导出会话密钥，之后走对称加密。\n\n为什么既用非对称又用对称：非对称安全但慢，只用来交换密钥；对称快，用来传数据。",
    peer:
      "补一个面试会加分的角度：TLS 1.3 相比 1.2 把握手从 2-RTT 压到了 1-RTT，复用会话时甚至是 0-RTT，代价是砍掉了一批不安全的老套件（RSA 密钥交换、RC4、SHA-1 都没了）。\n\n另外证书验证这一步，靠的是 CA 的信任链——浏览器内置根证书，逐级验签。自签名证书不是不安全，只是没人替你背书，所以浏览器会警告。",
    examiner:
      "追问一道：\n\n既然非对称加密能保证不被窃听，为什么不在会话密钥交换完之前，就用非对称加密直接传所有数据？\n\n判断标准：能说到「非对称的计算开销比对称高几个数量级」，而不是只重复「因为快所以快」。",
  },
];

function pickKb(message) {
  for (const item of KB) {
    if (item.match.test(message)) return item;
  }
  return null;
}

// --- 工具 -----------------------------------------------------------------

function sendJson(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(text),
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
  });
  res.end(text);
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
    });
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 按中文习惯切块：两三字一块，看起来才像真在打字
function chunk(text) {
  const out = [];
  let i = 0;
  while (i < text.length) {
    const size = 2 + (i % 3 === 0 ? 1 : 0);
    out.push(text.slice(i, i + size));
    i += size;
  }
  return out;
}

// --- POST /chat：SSE ------------------------------------------------------

async function handleChat(req, res) {
  const body = await readBody(req);
  const message = String(body.message || "");
  const cid = Number(body.classroom_id) || null;

  // 场景优先级：显式传值 > 建房时定的 > v1。与后端 resolve 顺序一致。
  const room = cid ? classrooms.get(cid) : null;
  const scenarioKey = normalizeScenario(
    body.scenario || (room && room.scenario) || "v1",
  );
  const spec = SCENARIOS[scenarioKey];

  if (cid) {
    appendMessage(cid, {
      user_id: body.user_id ?? null,
      username: "你",
      role: "student",
      content: message,
    });
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
  });

  const emit = (payload) => {
    res.write(`data: ${JSON.stringify(payload)}\n\n`);
  };
  const raw = (text) => {
    res.write(`data: ${text}\n\n`);
  };

  // 1) sources —— 契约规定它是第一个事件，在 token 之前
  const kb = pickKb(message);
  // 同一份数据既要发流式事件、也要落库，否则刷新前后"依据"会不一致
  const sourceItems = kb
    ? [
        {
          origin: "kb",
          source: kb.source,
          topic: kb.topic,
          category: kb.topic,
        },
      ]
    : [];
  emit({ type: "sources", items: sourceItems });

  await sleep(120);

  // 2) 每个角色发言前各发一条 agent，之后 token 归到该气泡
  for (const step of spec.steps) {
    emit({
      type: "agent",
      role: step.role,
      label: step.label,
      stance: step.stance,
      index: spec.steps.indexOf(step),
      total: spec.steps.length,
    });
    await sleep(160);

    const text =
      (kb &&
        (step.role === "tutor"
          ? kb.tutor
          : step.role === "peer"
            ? kb.peer
            : kb.examiner)) ||
      `【${step.label}】你说的是「${message || "（空）"}」。这一段是本地假后端生成的示例回答，用来演示一轮里多个 Agent 依次发言时气泡如何分开。`;

    for (const piece of chunk(text)) {
      emit({ type: "token", content: piece });
      await sleep(12);
    }

    if (cid) {
      appendMessage(cid, {
        user_id: null,
        username: null,
        role: "assistant",
        content: text,
        topic: kb ? kb.topic : null,
        agent_role: step.role,
        sources: sourceItems,
      });
    }

    await sleep(220);
  }

  // 3) state —— 流结束时最后一条
  emit({
    type: "state",
    topic: kb ? kb.topic : "",
    hint_level: 0,
    misconception: "",
    resolved: false,
    personalized: true,
  });

  // 4) [DONE] —— 字面量，不是 JSON
  raw("[DONE]");
  res.end();
}

// --- 路由 -----------------------------------------------------------------

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const path = url.pathname;
  const method = req.method;

  if (method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "*",
      "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    });
    res.end();
    return;
  }

  // 健康检查
  if (path === "/health" && method === "GET") {
    sendJson(res, 200, {
      status: "ok",
      provider: "openai",
      model: "mock-deepseek-flash",
      database: "up",
    });
    return;
  }

  // 场景列表
  if (path === "/scenarios" && method === "GET") {
    sendJson(res, 200, {
      items: Object.values(SCENARIOS).map((spec) => ({
        key: spec.key,
        label: spec.label,
        agents: spec.steps.map((s) => s.role),
        steps: spec.steps.map((s) => ({
          role: s.role,
          label: s.label,
          stance: s.stance,
          handoff: s.handoff,
        })),
      })),
    });
    return;
  }

  // 用户（幂等）
  if (path === "/users" && method === "POST") {
    const body = await readBody(req);
    const name = String(body.username || "").trim();
    if (!name) {
      sendJson(res, 422, { detail: "username 不能为空" });
      return;
    }
    if (users.has(name)) {
      sendJson(res, 200, users.get(name));
      return;
    }
    seq.user += 1;
    const user = { id: seq.user, username: name, role: body.role || "student" };
    users.set(name, user);
    sendJson(res, 200, user);
    return;
  }

  // 我的课堂：按成员关系列出某用户加入的全部课堂
  // 与后端 app.py:1340 对齐：不传 user_id 返回空列表，避免泄露全量课堂
  if (path === "/classrooms" && method === "GET") {
    const uid = url.searchParams.get("user_id");
    if (uid === null || uid === "") {
      sendJson(res, 200, { user_id: null, count: 0, classrooms: [] });
      return;
    }
    const target = Number(uid);
    const list = [];
    for (const room of classrooms.values()) {
      const hit = (members.get(room.id) ?? []).find(
        (mem) => mem.user_id === target,
      );
      if (hit) {
        list.push({ ...room, role: hit.role });
      }
    }
    sendJson(res, 200, {
      user_id: target,
      count: list.length,
      classrooms: list,
    });
    return;
  }

  // 建房（带场景）
  if (path === "/classrooms" && method === "POST") {
    const body = await readBody(req);
    seq.classroom += 1;
    const room = {
      id: seq.classroom,
      name: String(body.name || "未命名课堂"),
      subject: body.subject || "cs",
      description: body.description ?? null,
      is_private: false,
      scenario: normalizeScenario(body.scenario),
      created_at: now(),
    };
    classrooms.set(room.id, room);
    touchClassroom(room.id);
    const teacherId = Number(body.teacher_id) || 1;
    const teacher =
      [...users.values()].find((u) => u.id === teacherId) ?? null;
    if (teacher) {
      members.get(room.id).push({
        user_id: teacher.id,
        username: teacher.username,
        role: "teacher",
      });
    }
    sendJson(res, 200, room);
    return;
  }

  // 课堂详情
  let m = /^\/classrooms\/(\d+)$/.exec(path);
  if (m && method === "GET") {
    const room = classrooms.get(Number(m[1]));
    if (!room) {
      sendJson(res, 404, { detail: "classroom not found" });
      return;
    }
    sendJson(res, 200, room);
    return;
  }

  // 成员：加入 / 列表
  m = /^\/classrooms\/(\d+)\/members$/.exec(path);
  if (m) {
    const cid = Number(m[1]);
    if (!classrooms.has(cid)) {
      sendJson(res, 404, { detail: "classroom not found" });
      return;
    }
    touchClassroom(cid);
    if (method === "POST") {
      const body = await readBody(req);
      const uid = Number(body.user_id);
      const user = [...users.values()].find((u) => u.id === uid);
      const list = members.get(cid);
      if (!list.some((x) => x.user_id === uid)) {
        list.push({
          user_id: uid,
          username: user ? user.username : `用户${uid}`,
          role: body.role || "student",
        });
      }
      sendJson(res, 200, {
        id: list.length,
        classroom_id: cid,
        user_id: uid,
        role: body.role || "student",
      });
      return;
    }
    if (method === "GET") {
      const list = members.get(cid);
      sendJson(res, 200, {
        classroom_id: cid,
        count: list.length,
        members: list,
      });
      return;
    }
  }

  // 消息列表
  m = /^\/classrooms\/(\d+)\/messages$/.exec(path);
  if (m && method === "GET") {
    const cid = Number(m[1]);
    touchClassroom(cid);
    const list = messages.get(cid);
    sendJson(res, 200, { classroom_id: cid, count: list.length, messages: list });
    return;
  }

  // 学生画像 / 行为 / 提示词预览
  m = /^\/students\/(\d+)\/profile$/.exec(path);
  if (m && method === "GET") {
    const uid = Number(m[1]);
    const user = [...users.values()].find((u) => u.id === uid);
    sendJson(res, 200, {
      user_id: uid,
      classroom_id: Number(url.searchParams.get("classroom_id")) || 0,
      overall_level: "beginner",
      learning_preference: "偏好先给结论再展开，配合具体例子",
      knowledge_mastery: { "死锁": 0.4, "TCP 三次握手": 0.6, "进程与线程": 0.5 },
      weak_points: ["死锁的预防与检测区别", "拥塞控制的四个算法顺序"],
      mastery_average: 0.5,
      topic_count: 3,
      updated_at: now(),
      // 假数据里带个名字，方便界面显示
      username: user ? user.username : null,
    });
    return;
  }

  m = /^\/students\/(\d+)\/behaviors$/.exec(path);
  if (m && method === "GET") {
    sendJson(res, 200, {
      user_id: Number(m[1]),
      count: 0,
      behaviors: [],
    });
    return;
  }

  m = /^\/students\/(\d+)\/prompt-preview$/.exec(path);
  if (m && method === "GET") {
    sendJson(res, 200, {
      system: "你是一位面向计算机八股备考的主讲助教。",
      user: url.searchParams.get("topic") || "",
      hint_level: Number(url.searchParams.get("hint_level")) || 0,
    });
    return;
  }

  // 教师备课
  if (path === "/resources/ppt-outline" && method === "POST") {
    const body = await readBody(req);
    const topic = String(body.topic || "未命名主题");
    sendJson(res, 200, {
      topic,
      classroom_id: Number(body.classroom_id) || 0,
      audience: body.audience || "",
      retrieved: true,
      outline: {
        title: topic,
        objectives: [`理解 ${topic} 的基本概念`, `能够应对面试追问`],
        sections: [
          { title: "是什么", points: ["定义", "为什么需要它"] },
          { title: "怎么做", points: ["核心流程", "关键参数"] },
          { title: "面试怎么问", points: ["高频追问", "常见误区"] },
        ],
        section_titles: ["是什么", "怎么做", "面试怎么问"],
        key_points: ["定义", "流程", "边界条件"],
        examples: ["结合具体场景说明"],
        questions: [`${topic} 的典型追问是什么？`],
        summary: ["抓住定义、流程、边界三件事"],
      },
      file_path: "",
      file_name: null,
      // 不给下载链接：假后端没有真的 pptx，返回 null 界面会正常处理
      download_url: null,
    });
    return;
  }

  if (path === "/chat" && method === "POST") {
    await handleChat(req, res);
    return;
  }

  sendJson(res, 404, { detail: `mock 后端没有这个路由: ${method} ${path}` });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[mock-backend] http://127.0.0.1:${PORT}  （仅供前端自测，不入库 / 不调模型）`);
});

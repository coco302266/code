/**
 * 回答文本清洗。
 *
 * 背景：聊天区是纯文本渲染（`whitespace-pre-wrap`，见 TutorChat.tsx），
 * 不解析任何 HTML。排版规范（后端 agent/prompts.py 的 ANSWER_FORMAT_RULES）
 * 已经明令禁止模型输出标签，但模型输出不保证百分百听话——一旦漏出来，
 * 学生看到的就是一串裸露的 `<td>`，完全没法读。
 *
 * 所以这里兜一层：把漏出来的表格标签还原成对齐的纯文本表格，
 * 其余标签直接去掉。放在渲染边界而不是存库时处理，原因有两条：
 *   1. 流式输出下，一次请求里 `<td>` 可能被拆到两个 chunk，存库时清洗
 *      会漏掉跨 chunk 的半个标签；整段文本每次渲染时清洗则不会。
 *   2. 数据库保留原文，以后清洗规则改好了，历史消息重新渲染就能变干净。
 *
 * 刻意不做的事：不引入 Markdown / HTML 渲染器。那等于把模型输出当富文本
 * 执行，XSS 面直接打开，而且和项目「纯文本排版」的既定取向冲突。
 */

/** 列间距：与提示词里要求的「各列之间至少空两格」保持一致。 */
const COL_GAP = "  ";

/**
 * 短标签：只允许「标签名 + 可选空白 + 可选斜杠 + 右尖括号」，不带属性。
 *
 * 为什么卡这么死：`if (a<b && c>d)` 这种代码里 `<b && c>` 长得就像带属性的
 * 标签，放行属性的话整段代码会被吃掉。b / i / p / a / s / u 都是合法的
 * 单字母变量名，必须要求 `>` 紧跟其后才认。
 */
const SHORT_TAGS = "b|i|u|s|p|a|em|br|hr|sub|sup";
const SHORT_TAG_RE = new RegExp(`<\\/?(?:${SHORT_TAGS})\\s*\\/?>`, "gi");

/**
 * 长标签：放行属性。这些名字在代码里几乎不可能出现
 * （不像 `Map<K, V>` 或 `a<b` 那种），误伤风险可以接受。
 */
const LONG_TAGS =
  "table|thead|tbody|tfoot|tr|td|th|caption|colgroup|col|div|span|ul|ol|li|" +
  "h1|h2|h3|h4|h5|h6|code|pre|blockquote|strong|font|small|center|img|font";
const LONG_TAG_RE = new RegExp(`<\\/?(?:${LONG_TAGS})(?:\\s[^<>]*)?\\/?>`, "gi");

/** 流式输出时结尾可能出现的半个标签，例如 `</td`、`</tab`。 */
const PARTIAL_TAG_RE = new RegExp(
  `<\\/(?:${LONG_TAGS}|${SHORT_TAGS})(?:\\s[^<>]*)?$`,
  "i",
);

/** 连续的一串 <tr>，可能带 <table> 包裹，也可能不带（模型经常漏写外层）。 */
const ROW_RUN_RE = /(?:<tr\b[^<>]*>[\s\S]*?<\/tr\s*>\s*)+/gi;
const ROW_RE = /<tr\b[^<>]*>([\s\S]*?)<\/tr\s*>/gi;
const CELL_RE = /<t([dh])\b[^<>]*>([\s\S]*?)<\/t[dh]\s*>/gi;

interface ParsedRow {
  isHead: boolean;
  cells: string[];
}

/** 按等宽字体下的显示宽度计算：中日韩全角字符算 2，其余算 1。 */
function displayWidth(text: string): number {
  let width = 0;
  for (const ch of text) {
    width += isWideChar(ch.codePointAt(0) ?? 0) ? 2 : 1;
  }
  return width;
}

function isWideChar(cp: number): boolean {
  return (
    (cp >= 0x1100 && cp <= 0x115f) ||
    (cp >= 0x2e80 && cp <= 0x303e) ||
    (cp >= 0x3041 && cp <= 0x33ff) ||
    (cp >= 0x3400 && cp <= 0x4dbf) ||
    (cp >= 0x4e00 && cp <= 0x9fff) ||
    (cp >= 0xa000 && cp <= 0xa4cf) ||
    (cp >= 0xac00 && cp <= 0xd7a3) ||
    (cp >= 0xf900 && cp <= 0xfaff) ||
    (cp >= 0xfe10 && cp <= 0xfe19) ||
    (cp >= 0xfe30 && cp <= 0xfe6f) ||
    (cp >= 0xff00 && cp <= 0xff60) ||
    (cp >= 0xffe0 && cp <= 0xffe6) ||
    (cp >= 0x20000 && cp <= 0x3fffd)
  );
}

function padRight(text: string, width: number): string {
  return text + " ".repeat(Math.max(0, width - displayWidth(text)));
}

function decodeEntities(text: string): string {
  return text
    .replace(/&nbsp;/gi, " ")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&mdash;/gi, "—")
    .replace(/&amp;/gi, "&"); // 放最后，避免二次解码
}

/** 去掉标签并把空白压平，用于表格单元格内部。 */
function stripInline(text: string): string {
  return text
    .replace(LONG_TAG_RE, " ")
    .replace(SHORT_TAG_RE, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function renderRowRun(run: string): string {
  const parsed: ParsedRow[] = [];
  for (const rowMatch of run.matchAll(ROW_RE)) {
    const cells: string[] = [];
    let headCount = 0;
    for (const cellMatch of rowMatch[1].matchAll(CELL_RE)) {
      cells.push(decodeEntities(stripInline(cellMatch[2])));
      if (cellMatch[1].toLowerCase() === "h") headCount += 1;
    }
    if (cells.length === 0) continue;
    // 整行都是 th 才算表头；混用的行按普通行处理，免得多出一条分隔线。
    parsed.push({ isHead: headCount === cells.length, cells });
  }
  if (parsed.length === 0) return "";

  const colCount = Math.max(...parsed.map((row) => row.cells.length));
  const widths = new Array<number>(colCount).fill(0);
  for (const row of parsed) {
    for (let i = 0; i < colCount; i += 1) {
      widths[i] = Math.max(widths[i], displayWidth(row.cells[i] ?? ""));
    }
  }

  const lines: string[] = [];
  parsed.forEach((row, index) => {
    lines.push(
      widths
        .map((w, i) => padRight(row.cells[i] ?? "", w))
        .join(COL_GAP)
        .replace(/\s+$/, ""),
    );
    const nextIsHead = parsed[index + 1]?.isHead ?? false;
    if (row.isHead && !nextIsHead) {
      lines.push(widths.map((w) => "-".repeat(Math.max(w, 2))).join(COL_GAP));
    }
  });

  // 前后各留一个空行，跟提示词里「表格与正文之间空一行」的要求对齐。
  return `\n\n${lines.join("\n")}\n\n`;
}

/**
 * 清洗助手回答，保证渲染出来的是纯文本。
 *
 * 对不含尖括号的文本是零开销直通，正常情况不会影响任何既有回答。
 */
export function sanitizeAssistantText(text: string): string {
  // 快速通道要看两个字符：`&` 也要过一遍，否则 `&lt;stdio.h&gt;` 这种
  // 只有实体、没有尖括号的文本会被直接放行，实体原样显示。
  if (!text || (text.indexOf("<") === -1 && text.indexOf("&") === -1)) {
    return text;
  }

  let out = text;

  // 1. 表格：先抽掉 thead / tbody 这些只起分组作用的包裹标签，
  //    再把连续的行整段转成纯文本表格。放在最前面，否则后面「去掉标签」
  //    会把单元格之间的边界抹掉，内容全粘成一坨。
  out = out.replace(/<\/?(?:thead|tbody|tfoot)\b[^<>]*>/gi, "");
  out = out.replace(ROW_RUN_RE, renderRowRun);

  // 2. 剩下的块级标签换成换行，避免段落被粘在一起。
  out = out.replace(/<\s*br\s*\/?\s*>/gi, "\n");
  out = out.replace(/<\/\s*(?:p|div|tr|table|ul|ol|li|h[1-6]|blockquote|pre)\s*>/gi, "\n");
  out = out.replace(/<\/\s*(?:td|th)\s*>/gi, COL_GAP);

  // 3. 其余标签一律去掉（白名单内，且短标签要求紧跟 `>`）。
  out = out.replace(LONG_TAG_RE, "");
  out = out.replace(SHORT_TAG_RE, "");

  // 4. 流式输出时结尾残留的半个标签。只认 `</` 开头，不认 `<`，
  //    否则 `if (a<b` 这种代码会被砍掉尾巴。
  out = out.replace(PARTIAL_TAG_RE, "");

  // 5. 实体还原。
  out = decodeEntities(out);

  // 6. 收尾：去掉行尾空白，把连续三个以上空行压成两个。
  //    开头的空行也要去掉——表格恰好在消息开头时，插入的空行会顶出一段空白。
  //    只删换行不删空格，代码块的行首缩进不受影响。
  out = out
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/^\n+/, "");

  return out;
}

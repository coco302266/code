"use client";

import { Fragment, useMemo } from "react";

/**
 * 回答正文的结构化渲染。
 *
 * 背景：项目刻意不引入 Markdown / HTML 渲染器（见 lib/answerFormat.ts 顶部说明），
 * AI 的对比表在提示词里被要求用「至少空两格」对齐成纯文本。实测问题：
 * 正文是比例字体（不是等宽），用空格凑出来的列**必然错位**，中文尤其明显——
 * 一个汉字占两个英文字符宽，但空格只占一个字符宽，行与行的列起点对不上。
 *
 * 这里只做一件事：把「表头 + 虚线上划 + 数据行」这种文本块识别出来，
 * 用 CSS grid 重新排版。文本块仍走 whitespace-pre-wrap，其余格式一字不改。
 *
 * 为什么放在渲染层而不是让后端吐 HTML：保留纯文本契约（存库、SSE、Electron
 * 三处都不用改），也不给模型输出开一个 XSS 口子。
 */

type Block =
  | { kind: "text"; text: string }
  | { kind: "table"; head: string[]; rows: string[][] };

/** 列分隔：连续两个及以上空白。与提示词要求的「至少空两格」对齐。 */
const GAP = /\s{2,}/;
/** 分隔行：只由 - – — _ ─ = + 和空白组成，长度 >= 3。 */
const SEP = /^[\s\-–—_─=+]{3,}$/;

function cellsOf(line: string): string[] {
  return line
    .trim()
    .split(GAP)
    .map((c) => c.trim())
    .filter((c) => c.length > 0);
}

function parse(content: string): Block[] {
  const lines = content.split("\n");
  const blocks: Block[] = [];
  let buf: string[] = [];

  const flush = () => {
    if (buf.length === 0) return;
    // 去掉块尾的空行，避免气泡底部多出一截空白
    const text = buf.join("\n").replace(/\s+$/, "");
    if (text) blocks.push({ kind: "text", text });
    buf = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const head = cellsOf(line);
    const sepLine = (lines[i + 1] ?? "").trim();
    const firstRow = cellsOf(lines[i + 2] ?? "");

    // 表头的判据要同时满足三条，避免把「缩进的代码」「偶然的空格」误判成表格：
    //   ① 本行有 >= 2 个单元格  ② 下一行是分隔线  ③ 再下一行也有 >= 2 个单元格
    const isTable =
      head.length >= 2 && SEP.test(sepLine) && firstRow.length >= 2;

    if (!isTable) {
      buf.push(line);
      continue;
    }

    flush();
    const rows: string[][] = [];
    let j = i + 2;
    for (; j < lines.length; j++) {
      const raw = lines[j];
      if (raw.trim() === "") break;
      if (SEP.test(raw.trim())) continue; // 行间分隔线，忽略
      const row = cellsOf(raw);
      if (row.length < 2) break; // 表格结束，剩下的当正文
      rows.push(row);
    }
    blocks.push({ kind: "table", head, rows });
    i = j - 1;
  }

  flush();
  return blocks;
}

export function RichText({ text }: { text: string }) {
  const blocks = useMemo(() => parse(text), [text]);

  return (
    <>
      {blocks.map((block, idx) =>
        block.kind === "text" ? (
          <p key={idx} className="whitespace-pre-wrap">
            {block.text}
          </p>
        ) : (
          <TableBlock key={idx} head={block.head} rows={block.rows} />
        ),
      )}
    </>
  );
}

function TableBlock({ head, rows }: { head: string[]; rows: string[][] }) {
  const cols = head.length;
  // 列宽 = minmax(0, max-content)：
  //   空间够 → 按内容定宽（桌面下三列紧凑，不会出现大片空白）
  //   空间不够 → 允许压缩并在单元格内换行（390px 下三列都能读，不横向溢出）
  // 纯 max-content 会把表格撑出气泡宽度，纯 1fr 又会在桌面留空。
  const template = `repeat(${cols}, minmax(0, max-content))`;

  return (
    <div className="my-2.5 overflow-x-auto">
      <div
        className="grid min-w-full gap-x-4 gap-y-1.5 text-[14px] leading-[1.7]"
        style={{ gridTemplateColumns: template }}
      >
        {head.map((cell, i) => (
          <div
            key={`h-${i}`}
            className="border-b border-line pb-1.5 text-[12px] text-sub"
          >
            {cell}
          </div>
        ))}
        {rows.map((row, r) =>
          Array.from({ length: cols }).map((_, c) => (
            <Fragment key={`r-${r}-${c}`}>
              <div
                className={
                  c === 0
                    ? "break-words text-sub"
                    : "break-words text-ink"
                }
              >
                {row[c] ?? ""}
              </div>
            </Fragment>
          )),
        )}
      </div>
    </div>
  );
}

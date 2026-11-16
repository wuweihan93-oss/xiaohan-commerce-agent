import test from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown } from "../src/markdown.js";
import { createId } from "../src/id.js";
import { readEventStream } from "../src/stream.js";

test("模型文本作为不可信 Markdown，不能执行 HTML 或脚本链接", () => {
  const result = renderMarkdown(
    "<script>alert(1)</script>\n\n[链接](javascript:alert(1))\n\n![图片](https://example.com/a.png)",
  );
  assert.ok(!result.includes("<script>"));
  assert.ok(!result.includes('href="javascript:'));
  assert.ok(!result.includes("<img"));
  assert.ok(
    renderMarkdown("[文档](https://example.com)").includes(
      "noopener noreferrer",
    ),
  );
});
test("公网 HTTP 能生成标准不同的会话 UUID", () => {
  const first = createId(),
    second = createId();
  assert.match(
    first,
    /^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/,
  );
  assert.notEqual(first, second);
});
test("SSE 对跨网络包的中文与心跳保持正确", async () => {
  const input =
    ': 心跳\n\nevent: token\ndata: {"text":"你好"}\n\nevent: done\ndata: {"answer":"你好"}\n\n';
  const bytes = new TextEncoder().encode(input);
  const stream = new ReadableStream({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    },
  });
  const events = [];
  await readEventStream(
    new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
    (name, data) => events.push([name, data]),
  );
  assert.deepEqual(events, [
    ["token", { text: "你好" }],
    ["done", { answer: "你好" }],
  ]);
});

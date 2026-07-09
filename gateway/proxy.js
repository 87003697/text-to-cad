const http = require("http");
const https = require("https");

const UPSTREAM_URL = process.env.UPSTREAM_URL || "http://v2.open.venus.oa.com/llmproxy/v1";
const u = new URL(UPSTREAM_URL);
const UPSTREAM = u.protocol === "https:" ? https : http;
const UPSTREAM_HOST = u.hostname;
const UPSTREAM_PORT = u.port || (u.protocol === "https:" ? 443 : 80);
const UPSTREAM_BASE = u.pathname.replace(/\/$/, "");
const PORT = 18800;

function flattenTools(tools) {
  const out = [];
  for (const t of tools || []) {
    if (t.type === "function" && t.name) {
      out.push({ type: "function", function: { name: t.name, description: t.description || "", parameters: t.parameters || {} } });
    } else if (t.type === "namespace" && Array.isArray(t.tools)) {
      for (const sub of t.tools) {
        if (sub.type === "function" && sub.name) {
          out.push({ type: "function", function: { name: t.name + "." + sub.name, description: sub.description || "", parameters: sub.parameters || {} } });
        }
      }
    }
  }
  return out;
}

function convertInput(input) {
  const messages = [];
  for (const item of input || []) {
    // Top-level function_call (no role) — assistant tool invocation
    if (item.type === "function_call" && !item.role) {
      const last = messages[messages.length - 1];
      if (last && last.role === "assistant" && last.tool_calls) {
        last.tool_calls.push({ id: item.call_id || "call_x", type: "function", function: { name: item.name, arguments: item.arguments || "{}" } });
      } else {
        messages.push({ role: "assistant", content: null, tool_calls: [{ id: item.call_id || "call_x", type: "function", function: { name: item.name, arguments: item.arguments || "{}" } }] });
      }
      continue;
    }
    // Top-level function_call_output (no role) — tool result
    if (item.type === "function_call_output" && !item.role) {
      const out = typeof item.output === "string" ? item.output : JSON.stringify(item.output);
      messages.push({ role: "tool", tool_call_id: item.call_id || "call_x", content: out });
      continue;
    }

    const role = item.role === "developer" ? "system" : item.role;
    if (typeof item.content === "string") {
      messages.push({ role, content: item.content });
    } else if (Array.isArray(item.content)) {
      const textParts = item.content.filter((c) => c.type === "input_text" || c.type === "output_text");
      const text = textParts.map((c) => c.text).join("\n");
      const funcCalls = item.content.filter((c) => c.type === "function_call");
      const funcOutputs = item.content.filter((c) => c.type === "function_call_output");

      if (funcCalls.length > 0) {
        messages.push({
          role: "assistant",
          content: text || null,
          tool_calls: funcCalls.map((tc) => ({
            id: tc.call_id || tc.id || "call_x",
            type: "function",
            function: { name: tc.name, arguments: tc.arguments || "{}" },
          })),
        });
        for (const fo of funcOutputs) {
          messages.push({ role: "tool", tool_call_id: fo.call_id || "call_x", content: typeof fo.output === "string" ? fo.output : JSON.stringify(fo.output) });
        }
      } else if (funcOutputs.length > 0) {
        for (const fo of funcOutputs) {
          messages.push({ role: "tool", tool_call_id: fo.call_id || "call_x", content: typeof fo.output === "string" ? fo.output : JSON.stringify(fo.output) });
        }
      } else if (text) {
        messages.push({ role, content: text });
      }
    }
  }
  return messages;
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const rawBody = Buffer.concat(chunks).toString();

    if (req.method !== "POST" || !req.url.includes("/responses")) {
      const body = Buffer.from(rawBody);
      const path = UPSTREAM_BASE + req.url;
      const headers = { ...req.headers, host: UPSTREAM_HOST, "content-length": body.length };
      const proxyReq = UPSTREAM.request({ hostname: UPSTREAM_HOST, port: UPSTREAM_PORT, path, method: req.method, headers }, (proxyRes) => {
        res.writeHead(proxyRes.statusCode, proxyRes.headers);
        proxyRes.pipe(res);
      });
      proxyReq.on("error", (e) => { res.writeHead(502); res.end(e.message); });
      proxyReq.write(body);
      proxyReq.end();
      return;
    }

    console.error(`[proxy] POST /responses len=${rawBody.length}`);
    let data;
    try { data = JSON.parse(rawBody); } catch { res.writeHead(400); res.end("bad json"); return; }

    const messages = convertInput(data.input);
    const tools = flattenTools(data.tools);
    const chatReq = { model: data.model, messages, stream: true };
    if (tools.length > 0) chatReq.tools = tools;
    if (data.temperature != null) chatReq.temperature = data.temperature;
    if (data.max_output_tokens != null) chatReq.max_tokens = data.max_output_tokens;

    const chatBody = Buffer.from(JSON.stringify(chatReq));
    const headers = {
      host: UPSTREAM_HOST,
      "content-type": "application/json",
      "content-length": chatBody.length,
      authorization: req.headers.authorization || "",
    };

    const proxyReq = UPSTREAM.request({ hostname: UPSTREAM_HOST, port: UPSTREAM_PORT, path: UPSTREAM_BASE + "/chat/completions", method: "POST", headers }, (proxyRes) => {
      if (proxyRes.statusCode !== 200) {
        const errChunks = [];
        proxyRes.on("data", (c) => errChunks.push(c));
        proxyRes.on("end", () => {
          const errBody = Buffer.concat(errChunks).toString();
          console.error(`[proxy] upstream ${proxyRes.statusCode}: ${errBody.slice(0, 300)}`);
          res.writeHead(200, { "content-type": "text/event-stream" });
          emitTextResponse(res, "Error from upstream: " + errBody.slice(0, 300));
        });
        return;
      }

      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" });
      const state = { toolCalls: [], text: "" };
      let buffer = "";

      proxyRes.on("data", (chunk) => {
        buffer += chunk.toString();
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();
          if (payload === "[DONE]") {
            emitFinalEvents(res, state);
            return;
          }
          try {
            const parsed = JSON.parse(payload);
            const delta = parsed.choices?.[0]?.delta;
            if (!delta) return;
            if (delta.tool_calls) {
              for (const tc of delta.tool_calls) {
                const idx = tc.index ?? 0;
                if (!state.toolCalls[idx]) state.toolCalls[idx] = { id: tc.id || "call_x", name: "", arguments: "" };
                if (tc.id) state.toolCalls[idx].id = tc.id;
                if (tc.function?.name) state.toolCalls[idx].name += tc.function.name;
                if (tc.function?.arguments) state.toolCalls[idx].arguments += tc.function.arguments;
              }
            }
            if (delta.content) state.text += delta.content;
          } catch {}
        }
      });

      proxyRes.on("end", () => {
        if (!res.writableEnded) emitFinalEvents(res, state);
      });
    });

    proxyReq.on("error", (e) => {
      console.error("[proxy] request error:", e.message);
      res.writeHead(502);
      res.end(e.message);
    });
    proxyReq.write(chatBody);
    proxyReq.end();
  });
});

function emitFinalEvents(res, state) {
  let seq = 0;
  const write = (evt, d) => { d.sequence_number = seq++; res.write(`event: ${evt}\ndata: ${JSON.stringify(d)}\n\n`); };
  const respId = "resp_" + Date.now().toString(36);

  write("response.created", { type: "response.created", response: { id: respId, object: "response", status: "in_progress", output: [] } });

  const output = [];

  for (let i = 0; i < state.toolCalls.length; i++) {
    const tc = state.toolCalls[i];
    if (!tc || !tc.name) continue;
    const itemId = "fc_" + Math.random().toString(36).slice(2, 10);
    write("response.output_item.added", { type: "response.output_item.added", output_index: i, item: { id: itemId, type: "function_call", status: "in_progress", arguments: "", call_id: tc.id, name: tc.name } });
    write("response.function_call_arguments.done", { type: "response.function_call_arguments.done", arguments: tc.arguments, item_id: itemId, output_index: i });
    const doneItem = { id: itemId, type: "function_call", status: "completed", arguments: tc.arguments, call_id: tc.id, name: tc.name };
    write("response.output_item.done", { type: "response.output_item.done", output_index: i, item: doneItem });
    output.push(doneItem);
  }

  if (state.text) {
    const msgId = "msg_" + Math.random().toString(36).slice(2, 10);
    const textIdx = output.length;
    write("response.output_item.added", { type: "response.output_item.added", output_index: textIdx, item: { id: msgId, type: "message", status: "in_progress", role: "assistant", content: [] } });
    write("response.content_part.added", { type: "response.content_part.added", item_id: msgId, output_index: textIdx, content_index: 0, part: { type: "output_text", text: "" } });
    write("response.output_text.delta", { type: "response.output_text.delta", delta: state.text, content_index: 0, output_index: textIdx });
    write("response.output_text.done", { type: "response.output_text.done", text: state.text, content_index: 0, output_index: textIdx });
    const msgItem = { id: msgId, type: "message", status: "completed", role: "assistant", content: [{ type: "output_text", text: state.text }] };
    write("response.output_item.done", { type: "response.output_item.done", output_index: textIdx, item: msgItem });
    output.push(msgItem);
  }

  write("response.completed", { type: "response.completed", response: { id: respId, object: "response", status: "completed", output } });
  res.end();
}

function emitTextResponse(res, text) {
  let seq = 0;
  const write = (evt, d) => { d.sequence_number = seq++; res.write(`event: ${evt}\ndata: ${JSON.stringify(d)}\n\n`); };
  const respId = "resp_err";
  const msgId = "msg_err";
  write("response.created", { type: "response.created", response: { id: respId, object: "response", status: "in_progress", output: [] } });
  write("response.output_item.added", { type: "response.output_item.added", output_index: 0, item: { id: msgId, type: "message", status: "in_progress", role: "assistant", content: [] } });
  write("response.content_part.added", { type: "response.content_part.added", item_id: msgId, output_index: 0, content_index: 0, part: { type: "output_text", text: "" } });
  write("response.output_text.delta", { type: "response.output_text.delta", delta: text, content_index: 0, output_index: 0 });
  write("response.output_text.done", { type: "response.output_text.done", text, content_index: 0, output_index: 0 });
  const msgItem = { id: msgId, type: "message", status: "completed", role: "assistant", content: [{ type: "output_text", text }] };
  write("response.output_item.done", { type: "response.output_item.done", output_index: 0, item: msgItem });
  write("response.completed", { type: "response.completed", response: { id: respId, object: "response", status: "completed", output: [msgItem] } });
  res.end();
}

server.listen(PORT, "127.0.0.1", () => {
  console.error(`[gateway] Responses<->Chat bridge on 127.0.0.1:${PORT} -> ${UPSTREAM_URL}`);
});

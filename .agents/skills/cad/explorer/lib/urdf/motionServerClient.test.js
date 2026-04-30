import assert from "node:assert/strict";
import test from "node:test";

import {
  checkMotionServerLive,
  closeMotionServerConnection,
  motionServerAvailable,
  motionServerUrl,
  requestMotionServer,
} from "./motionServerClient.js";

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;

  static sockets = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.listeners = new Map();
    this.sent = [];
    FakeWebSocket.sockets.push(this);
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    this.listeners.set(type, listeners.filter((candidate) => candidate !== listener));
  }

  send(message) {
    this.sent.push(JSON.parse(message));
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close");
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open");
  }

  respond(message) {
    this.emit("message", { data: JSON.stringify(message) });
  }

  emit(type, event = {}) {
    for (const listener of this.listeners.get(type) || []) {
      listener(event);
    }
  }
}

test.beforeEach(() => {
  FakeWebSocket.sockets = [];
  closeMotionServerConnection();
});

test.afterEach(() => {
  closeMotionServerConnection();
});

test("requestMotionServer sends request IDs and resolves matching responses", async () => {
  const promise = requestMotionServer("urdf.solvePose", {
    id: "req-1",
    file: "sample_robot.urdf",
  }, {
    url: "ws://motion.test/ws",
    WebSocketImpl: FakeWebSocket,
    timeoutMs: 1000,
  });

  assert.equal(FakeWebSocket.sockets.length, 1);
  const socket = FakeWebSocket.sockets[0];
  assert.equal(socket.url, "ws://motion.test/ws");
  socket.open();
  assert.deepEqual(socket.sent[0], {
    id: "req-1",
    type: "urdf.solvePose",
    payload: {
      id: "req-1",
      file: "sample_robot.urdf",
    },
  });
  socket.respond({
    id: "req-1",
    ok: true,
    result: {
      jointValuesByNameDeg: {
        joint_2: 42,
      },
    },
  });

  assert.deepEqual(await promise, {
    jointValuesByNameDeg: {
      joint_2: 42,
    },
  });
});

test("requestMotionServer rejects server errors", async () => {
  const promise = requestMotionServer("urdf.solvePose", { id: "req-2" }, {
    url: "ws://motion.test/ws",
    WebSocketImpl: FakeWebSocket,
    timeoutMs: 1000,
  });
  const socket = FakeWebSocket.sockets[0];
  socket.open();
  socket.respond({
    id: "req-2",
    ok: false,
    error: {
      message: "planning failed",
    },
  });

  await assert.rejects(promise, /planning failed/);
});

test("motionServerAvailable reflects WebSocket support", () => {
  assert.equal(motionServerAvailable({ WebSocketImpl: FakeWebSocket }), true);
  assert.equal(motionServerAvailable({ WebSocketImpl: null }), false);
});

test("checkMotionServerLive resolves true when websocket opens", async () => {
  const promise = checkMotionServerLive({
    url: "ws://motion.test/ws",
    WebSocketImpl: FakeWebSocket,
    timeoutMs: 1000,
  });
  const socket = FakeWebSocket.sockets[0];
  assert.equal(socket.url, "ws://motion.test/ws");
  socket.open();

  assert.equal(await promise, true);
  assert.equal(socket.readyState, FakeWebSocket.CLOSED);
});

test("checkMotionServerLive resolves false when websocket errors before opening", async () => {
  const promise = checkMotionServerLive({
    url: "ws://motion.test/ws",
    WebSocketImpl: FakeWebSocket,
    timeoutMs: 1000,
  });
  FakeWebSocket.sockets[0].emit("error");

  assert.equal(await promise, false);
});

test("motionServerUrl uses the generic query override", () => {
  const originalWindow = globalThis.window;
  globalThis.window = {
    location: {
      href: "http://127.0.0.1:4178/?motionWs=ws%3A%2F%2Fmotion.test%2Fws",
    },
  };
  try {
    assert.equal(motionServerUrl(), "ws://motion.test/ws");
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }
  }
});

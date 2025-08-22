import asyncio
from collections import deque

class WebSocketLogBroadcaster:
    def __init__(self):
        self.connections = set()
        self._lock = asyncio.Lock()
        self.message_queue = deque()
        self._running = False

    async def start(self):
        if not self._running:
            self._running = True
            asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        while self._running:
            if self.message_queue:
                message = self.message_queue.popleft()
                await self.broadcast(message)
            await asyncio.sleep(0.1)

    async def register(self, websocket):
        async with self._lock:
            self.connections.add(websocket)

    async def unregister(self, websocket):
        async with self._lock:
            self.connections.discard(websocket)

    async def broadcast(self, message: str):
        async with self._lock:
            if not self.connections:
                return
            
            dead = []
            for ws in self.connections:
                try:
                    await ws.send_json({"log": message})
                except Exception:
                    dead.append(ws)
            
            for ws in dead:
                self.connections.discard(ws)

    def enqueue(self, message: str):
        self.message_queue.append(message)

# Global singleton
broadcaster = WebSocketLogBroadcaster()
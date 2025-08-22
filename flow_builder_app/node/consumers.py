import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .websocket_logger import broadcaster

class LogConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
        await broadcaster.register(self)
        await self.send_json({"log": "Connected to execution logs"})

    async def disconnect(self, close_code):
        await broadcaster.unregister(self)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data) if text_data else {}
            if data.get("command") == "ping":
                await self.send_json({"log": "pong"})
        except json.JSONDecodeError:
            await self.send_json({"error": "Invalid JSON"})

    async def send_json(self, data):
        await self.send(text_data=json.dumps(data))
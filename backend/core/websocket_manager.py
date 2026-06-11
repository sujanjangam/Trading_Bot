# backend/core/websocket_manager.py
from fastapi import WebSocket
import json
import numpy as np
import math
import asyncio
from typing import List, Dict

# --- Custom JSON encoder remains the same ---
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(CustomJSONEncoder, self).default(obj)

class ConnectionManager:
    def __init__(self):
        # CHANGED: Use a list to store multiple connections
        self.active_connections: List[WebSocket] = []
        self._locks: Dict[WebSocket, asyncio.Lock] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        # CHANGED: Add the new connection to the list
        self.active_connections.append(websocket)
        self._locks[websocket] = asyncio.Lock()
        print(f"Frontend client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        # CHANGED: Remove a specific connection from the list
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if websocket in self._locks:
            del self._locks[websocket]
        print(f"Frontend client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        if self.active_connections:
            json_message = json.dumps(message, cls=CustomJSONEncoder)
            
            # Create a copy of the list to iterate over
            disconnected = []
            for connection in self.active_connections[:]:
                try:
                    # Use lock to serialize writes to each WebSocket
                    await self._send_with_lock(connection, json_message)
                except Exception:
                    # Mark for disconnection
                    disconnected.append(connection)
            
            # Clean up disconnected clients
            for connection in disconnected:
                self.disconnect(connection)
    
    async def _send_with_lock(self, connection: WebSocket, message: str):
        """Send message with lock to prevent concurrent writes"""
        try:
            lock = self._locks.get(connection)
            if lock and connection in self.active_connections:
                async with lock:
                    await connection.send_text(message)
        except Exception:
            # Silently handle - disconnect will be called by the main handler
            pass

    async def close(self):
        """Forcefully closes all active WebSocket connections."""
        for connection in self.active_connections[:]:
            await connection.close()
            self.disconnect(connection)
        print("All WebSocket connections closed by server.")

manager = ConnectionManager()
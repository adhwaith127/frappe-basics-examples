#!/usr/bin/env python3
import asyncio
import websockets
import json
import requests
from datetime import datetime
import logging
import os
from typing import Dict, Optional, Tuple, List
import csv
import sqlite3
import time
from contextlib import asynccontextmanager

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ERPNext Configuration
ERP_URL = "http://192.168.0.68:8001/"
ERP_API = "/api/method/clean_plus.services.biometric_server_erp2.add_checkin"

HOST = "0.0.0.0"
PORT = 8190

# Connection and retry configuration
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_BASE = 2  # seconds
REQUEST_TIMEOUT = 10  # seconds
MAX_CONNECTIONS = 50
CONNECTION_TIMEOUT = 300  # 5 minutes


def _safe_join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _safe_remote_addr(addr: Optional[Tuple[str, int]]) -> str:
    if not addr:
        return "unknown:0"
    host, port = addr
    return f"{host}:{port}"


class LocalQueue:
    """SQLite-based local queue for failed ERP requests"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database for queue"""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS failed_requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        punchingcode TEXT NOT NULL,
                        employee_name TEXT NOT NULL,
                        timestamp_str TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        retry_count INTEGER DEFAULT 0,
                        last_error TEXT
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize local queue database: {e}")
            raise

    async def add_failed_request(self, punchingcode: str, name: str, timestamp_str: str, device_id: str, error: str):
        """Add failed request to queue"""
        try:
            def _insert():
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                        INSERT INTO failed_requests 
                        (punchingcode, employee_name, timestamp_str, device_id, last_error)
                        VALUES (?, ?, ?, ?, ?)
                    """, (punchingcode, name, timestamp_str, device_id, str(error)))
                    conn.commit()
            
            await asyncio.to_thread(_insert)
            logger.info(f"Added failed request to queue: {punchingcode} at {timestamp_str}")
            
        except Exception as e:
            logger.error(f"Failed to add request to queue: {e}")

    async def get_pending_requests(self, limit: int = 10) -> List[Dict]:
        """Get pending requests from queue"""
        try:
            def _fetch():
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.execute("""
                        SELECT * FROM failed_requests 
                        WHERE retry_count < ? 
                        ORDER BY created_at ASC 
                        LIMIT ?
                    """, (MAX_RETRY_ATTEMPTS, limit))
                    return [dict(row) for row in cursor.fetchall()]
            
            return await asyncio.to_thread(_fetch)
            
        except Exception as e:
            logger.error(f"Failed to fetch pending requests: {e}")
            return []

    async def update_retry_count(self, request_id: int, error: str = None):
        """Update retry count for a request"""
        try:
            def _update():
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                        UPDATE failed_requests 
                        SET retry_count = retry_count + 1, last_error = ?
                        WHERE id = ?
                    """, (str(error) if error else None, request_id))
                    conn.commit()
            
            await asyncio.to_thread(_update)
            
        except Exception as e:
            logger.error(f"Failed to update retry count: {e}")

    async def remove_request(self, request_id: int):
        """Remove successfully processed request"""
        try:
            def _delete():
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("DELETE FROM failed_requests WHERE id = ?", (request_id,))
                    conn.commit()
            
            await asyncio.to_thread(_delete)
            
        except Exception as e:
            logger.error(f"Failed to remove request: {e}")


class BiometricServer:
    def __init__(self, host="0.0.0.0", port=8190):
        self.host = host
        self.port = port
        self.connected_devices: Dict[websockets.WebSocketServerProtocol, str] = {}
        self.device_info: Dict[str, str] = {}
        self.connection_times: Dict[websockets.WebSocketServerProtocol, float] = {}
        
        # Ensure directories exist relative to the script location
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.commands_dir = os.path.join(script_dir, "commands")
        self.logs_dir = os.path.join(script_dir, "logs")
        self.queue_dir = os.path.join(script_dir, "queue")
        
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)
        os.makedirs(self.queue_dir, exist_ok=True)

        self.erp_endpoint = _safe_join_url(ERP_URL, ERP_API)
        
        # Initialize local queue
        queue_db_path = os.path.join(self.queue_dir, "failed_requests.db")
        self.local_queue = LocalQueue(queue_db_path)
        
        # Start background task for processing failed requests
        self._queue_processor_task = None

    async def register_device(self, websocket, data):
        """Register device with connection limits and validation"""
        try:
            serial_number = data.get("sn")
            if not serial_number:
                return {"ret": "reg", "result": False, "reason": "Missing serial number"}

            # Check connection limits
            if len(self.connected_devices) >= MAX_CONNECTIONS:
                logger.warning(f"Connection limit reached. Rejecting device: {serial_number}")
                return {"ret": "reg", "result": False, "reason": "Server connection limit reached"}

            client_addr = _safe_remote_addr(websocket.remote_address)
            self.connected_devices[websocket] = serial_number
            self.device_info[serial_number] = client_addr
            self.connection_times[websocket] = time.time()

            logger.info(f"Device registered: {serial_number} from {client_addr}")

            return {
                "ret": "reg",
                "result": True,
                "cloudtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            
        except Exception as e:
            logger.error(f"Error in device registration: {e}")
            return {"ret": "reg", "result": False, "reason": "Registration failed"}

    async def _send_to_erp_with_retry(self, punchingcode: str, name: str, timestamp_str: str, device_id: str) -> bool:
        """Send to ERP with retry logic and exponential backoff"""
        
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                # Parse and validate timestamp
                try:
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                except ValueError as e:
                    logger.error(f"Invalid timestamp format: {timestamp_str} - {e}")
                    return False

                payload = {
                    "punchingcode": punchingcode,
                    "employee_name": name,
                    "time": timestamp.strftime("%d-%m-%Y %H:%M:%S"),
                    "device_id": device_id,
                }

                # Make HTTP request with timeout
                response = await asyncio.to_thread(
                    self._make_http_request, payload
                )
                
                if response['success']:
                    logger.info(f"ERP Checkin log added for {name} at {payload['time']} from {device_id} (attempt {attempt + 1})")
                    return True
                else:
                    raise Exception(response['error'])

            except Exception as e:
                logger.warning(f"ERP request failed (attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}): {e}")
                
                if attempt == MAX_RETRY_ATTEMPTS - 1:
                    # Final attempt failed, queue for later
                    await self.local_queue.add_failed_request(punchingcode, name, timestamp_str, device_id, str(e))
                    logger.error(f"All retry attempts failed for {name}. Added to queue.")
                    return False
                else:
                    # Wait before retry with exponential backoff
                    delay = RETRY_DELAY_BASE ** (attempt + 1)
                    await asyncio.sleep(delay)
        
        return False

    def _make_http_request(self, payload: Dict) -> Dict:
        """Blocking HTTP request to be run in thread"""
        try:
            response = requests.post(
                self.erp_endpoint, 
                data=payload, 
                timeout=REQUEST_TIMEOUT
            )
            
            if response.status_code == 200:
                return {'success': True}
            else:
                return {
                    'success': False, 
                    'error': f"HTTP {response.status_code}: {response.text[:200]}"
                }
                
        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'Request timeout'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': 'Connection error'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def store_attendance(self, records, device_id):
        """Store attendance with improved error handling"""
        if not records:
            return {"ret": "sendlog", "result": False, "reason": "No records provided"}

        processed_count = 0
        failed_count = 0

        for record in records:
            try:
                name = record.get("name")
                enroll_id = record.get("enrollid")
                timestamp = record.get("time")

                if not enroll_id or not timestamp or not name:
                    logger.warning(f"Malformed record skipped: {record}")
                    continue

                # Send to ERP with retry logic
                success = await self._send_to_erp_with_retry(enroll_id, name, timestamp, device_id)
                
                if success:
                    processed_count += 1
                    status = "Success"
                else:
                    failed_count += 1
                    status = "Queued for retry"
                
                # Log to CSV (always log, regardless of ERP status)
                await asyncio.to_thread(
                    log_attendance_to_csv, enroll_id, name, device_id, status, timestamp
                )

            except Exception as e:
                logger.error(f"Error processing attendance record: {e}")
                failed_count += 1

        logger.info(f"Attendance processing complete: {processed_count} success, {failed_count} failed/queued")

        return {
            "ret": "sendlog",
            "result": True,
            "processed": processed_count,
            "failed": failed_count,
            "cloudtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
   

    async def handle_user_enrollment(self, data, device_id):
        """Handle user enrollment/fingerprint data from devices"""
        try:
            # Extract user information from the message
            enroll_id = data.get("enrollid")
            name = data.get("name", "")
            admin_level = data.get("admin", 0)
            backup_num = data.get("backupnum", 0)
            serial_number = data.get("sn", "")
            
            if not enroll_id:
                return {
                    "ret": "senduser", 
                    "result": False, 
                    "reason": "Missing enrollment ID"
                }
            
            # Log the user enrollment for your records
            logger.info(f"User enrollment - ID: {enroll_id}, Name: '{name}', Device: {device_id}")
            
            # Return success response to device
            return {
                "ret": "senduser",
                "result": True,
                "cloudtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            
        except Exception as e:
            logger.error(f"Error handling user enrollment: {e}")
            return {
                "ret": "senduser", 
                "result": False, 
                "reason": "Processing failed"
            }
    async def process_message(self, websocket, message):
        """Process incoming messages with comprehensive error handling"""
        try:
            # Validate JSON
            try:
                data = json.loads(message)
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error from {_safe_remote_addr(websocket.remote_address)}: {e}")
                return {"ret": "error", "result": False, "reason": "Invalid JSON format"}

            device_id = self.connected_devices.get(websocket, "unknown")

            # Validate command structure
            if "cmd" not in data:
                return {
                    "ret": "error",
                    "result": False,
                    "reason": "Missing 'cmd' field",
                    "cloudtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

            cmd = data["cmd"]

            # Route commands
            if cmd == "reg":
                return await self.register_device(websocket, data)
            elif cmd in ("sendlog", "getalllog"):
                return await self.store_attendance(data.get("record", []), device_id)
            elif cmd == "senduser":
                return await self.handle_user_enrollment(data, device_id)
            else:
                logger.warning(f"Unknown command received: {cmd}")
                return {
                    "ret": cmd,
                    "result": False,
                    "reason": "Unknown command",
                    "cloudtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

        except Exception as e:
            logger.error(f"Unexpected error processing message: {e}")
            return {
                "ret": "error",
                "result": False,
                "reason": "Internal server error",
                "cloudtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    # async def cleanup_stale_connections(self):
    #     """Remove connections that have been idle too long"""
    #     current_time = time.time()
    #     stale_connections = []
        
    #     for websocket, connect_time in self.connection_times.items():
    #         if current_time - connect_time > CONNECTION_TIMEOUT:
    #             stale_connections.append(websocket)
        
    #     for websocket in stale_connections:
    #         try:
    #             await websocket.close()
    #             logger.info("Closed stale connection")
    #         except:
    #             pass  # Connection might already be closed
    #         finally:
    #             self._cleanup_device_connection(websocket)

    # def _cleanup_device_connection(self, websocket):
    #     """Clean up device connection data"""
    #     if websocket in self.connected_devices:
    #         serial = self.connected_devices[websocket]
    #         del self.connected_devices[websocket]
    #         if serial in self.device_info:
    #             del self.device_info[serial]
        
    #     if websocket in self.connection_times:
    #         del self.connection_times[websocket]

    async def handle_device(self, websocket, path=None):
        """Handle device connection with proper cleanup"""
        client_addr = _safe_remote_addr(websocket.remote_address)
        logger.info(f"Device connected: {client_addr}")

        
        try:
            async for message in websocket:
                try:
                    response = await self.process_message(websocket, message)
                    if response:
                        await websocket.send(json.dumps(response))
                except Exception as e:
                    logger.error(f"Error handling message from {client_addr}: {e}")
                    # Try to send error response
                    try:
                        error_response = {
                            "ret": "error",
                            "result": False,
                            "reason": "Message processing failed"
                        }
                        await websocket.send(json.dumps(error_response))
                    except:
                        break  # Connection is likely broken

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Connection closed normally: {client_addr}")
        except websockets.exceptions.ConnectionClosedError:
            logger.info(f"Connection closed unexpectedly: {client_addr}")
        except Exception as e:
            logger.error(f"Unhandled connection error from {client_addr}: {e}")
        finally:
            # self._cleanup_device_connection(websocket)
            logger.info(f"Device disconnected and cleaned up: {client_addr}")

    async def process_queued_requests(self):
        """Background task to process queued failed requests"""
        logger.info("Started background queue processor")
        
        while True:
            try:
                # Get pending requests
                pending = await self.local_queue.get_pending_requests(limit=5)
                
                if not pending:
                    await asyncio.sleep(30)  # Wait 30 seconds before checking again
                    continue

                logger.info(f"Processing {len(pending)} queued requests")

                for request in pending:
                    try:
                        success = await self._send_to_erp_with_retry(
                            request['punchingcode'],
                            request['employee_name'], 
                            request['timestamp_str'],
                            request['device_id']
                        )
                        
                        if success:
                            await self.local_queue.remove_request(request['id'])
                            logger.info(f"Successfully processed queued request ID: {request['id']}")
                        else:
                            await self.local_queue.update_retry_count(
                                request['id'], "Retry failed"
                            )
                            
                    except Exception as e:
                        logger.error(f"Error processing queued request {request['id']}: {e}")
                        await self.local_queue.update_retry_count(request['id'], str(e))

                # Wait before next batch
                await asyncio.sleep(60)  # Process queue every minute
                
            except Exception as e:
                logger.error(f"Error in queue processor: {e}")
                await asyncio.sleep(60)

    # async def periodic_cleanup(self):
    #     """Periodic maintenance tasks"""
    #     while True:
    #         try:
    #             await asyncio.sleep(300)  # Every 5 minutes
    #             await self.cleanup_stale_connections()
    #             logger.debug(f"Active connections: {len(self.connected_devices)}")
    #         except Exception as e:
    #             logger.error(f"Error in periodic cleanup: {e}")

    async def start_server(self):
        """Start server with background tasks"""
        logger.info(f"WebSocket server starting on {self.host}:{self.port}")
        
        # Start background tasks
        self._queue_processor_task = asyncio.create_task(self.process_queued_requests())
        # cleanup_task = asyncio.create_task(self.periodic_cleanup())
        
        try:
            async with websockets.serve(
                self.handle_device, 
                self.host, 
                self.port,
                max_size=1000000,  # 1MB message limit
                ping_interval=30,   # Send ping every 30 seconds
                ping_timeout=10     # Wait 10 seconds for pong
            ):
                logger.info(f"Server listening on {self.host}:{self.port}")
                logger.info(f"Max connections: {MAX_CONNECTIONS}")
                logger.info(f"Connection timeout: {CONNECTION_TIMEOUT} seconds")
                
                # Keep server running
                await asyncio.Future()
                
        except Exception as e:
            logger.error(f"Server startup error: {e}")
            raise
        finally:
            # Cancel background tasks
            if self._queue_processor_task:
                self._queue_processor_task.cancel()
            # cleanup_task.cancel()


def log_attendance_to_csv(enroll_id, name, device_id, status, timestamp_str):
    """Log attendance to CSV with error handling"""
    try:
        try:
            punch_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            punch_time = datetime.now()

        filename = punch_time.strftime("%Y-%m") + ".csv"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(script_dir, "logs")
        
        filepath = os.path.join(log_dir, filename)
        file_exists = os.path.isfile(filepath)

        with open(filepath, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "Enroll ID", "Name", "Device ID", "ERP Status"])
            writer.writerow([
                punch_time.strftime("%Y-%m-%d %H:%M:%S"),
                enroll_id,
                name,
                device_id,
                status,
            ])
    except Exception as e:
        logger.error(f"Failed to log to CSV: {e}")


def main():
    """Main function with graceful shutdown"""
    server = BiometricServer(HOST, PORT)
    
    try:
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise


if __name__ == "__main__":
    main()

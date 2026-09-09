# -*- coding: utf-8 -*-
import logging
import socket
from threading import Thread
import struct
import time
logger = logging.getLogger('message')
traffic_logger = logging.getLogger('traffic')
COEFFICIENT = 100

class ModbusServer:
    def __init__(self, devices_data, data_lock):
        self.devices_data = devices_data
        self.data_lock = data_lock
        self.sock = None
        logger.info("Initializing ModbusServer with %d devices", len(devices_data))
        for key, value in self.devices_data.items():
            logger.info("Device %s: Slave ID %d, Data %s", key, value['slave_id'], str(value['data']))

    def run(self):
        logger.info("Starting Modbus server on 0.0.0.0:5021...")
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.sock.settimeout(0.5)
                self.sock.bind(('0.0.0.0', 5021))
                self.sock.listen(5)
                logger.info("Modbus server listening on 0.0.0.0:5021 after attempt %d", attempt + 1)
                break
            except socket.error as e:
                logger.warning("Attempt %d failed to bind port 5021: %s", attempt + 1, str(e))
                if attempt == max_attempts - 1:
                    logger.error("Failed to bind port 5021 after %d attempts", max_attempts)
                    raise
                time.sleep(1)
        while True:
            try:
                conn, addr = self.sock.accept()
                logger.info("Accepted connection from %s:%d", addr[0], addr[1])
                thread = Thread(target=self.handle_connection, args=(conn, addr))
                thread.daemon = True
                thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                logger.error("Accept failed: %s", str(e))
                time.sleep(1)

    def handle_connection(self, conn, addr):
        buffer = bytearray()
        try:
            while True:
                data = conn.recv(1024)
                if not data:
                    logger.info("Client %s disconnected", addr)
                    break
                buffer.extend(data)
                while len(buffer) >= 7:
                    length = int.from_bytes(buffer[4:6], byteorder='big')
                    if len(buffer) < 6 + length:
                        break
                    packet = buffer[:6 + length]
                    transaction_id = packet[0:2]
                    slave_id = packet[6]
                    logger.info("Processing request from %s for Slave ID: %d, data: %s", addr, slave_id, packet.hex())
                    for device_key, dev_info in self.devices_data.items():
                        if dev_info['slave_id'] == slave_id:
                            try:
                                self.process_request(packet, dev_info, device_key, transaction_id, conn)
                            except Exception as e:
                                logger.error("Request processing failed for %s: %s, data: %s", device_key, str(e), packet.hex())
                            break
                    else:
                        logger.info("No device found for Slave ID: %d from %s", slave_id, addr)
                        self.send_error_response(transaction_id, slave_id, 0x02, conn)
                    buffer = buffer[6 + length:]
        except Exception as e:
            logger.error("Connection handler error from %s: %s", addr, str(e))
        finally:
            conn.close()
            logger.info("Connection closed from %s", addr)

    def send_error_response(self, transaction_id, slave_id, exception_code, conn):
        response = bytearray(transaction_id) + b'\x00\x00' + struct.pack('>H', 3) + bytes([slave_id, 0x83 | exception_code])
        traffic_logger.info("Sending error response to %s: %s at %s", conn.getpeername(), response.hex(), time.strftime('%H:%M:%S'))
        try:
            conn.sendall(response)
            traffic_logger.info("Error response sent successfully at %s", time.strftime('%H:%M:%S'))
        except Exception as e:
            logger.error("Failed to send error response: %s", str(e))
            try:
                time.sleep(0.001)
                conn.sendall(response)
                traffic_logger.info("Error response resent successfully at %s", time.strftime('%H:%M:%S'))
            except Exception as e2:
                logger.error("Resend failed: %s", str(e2))

    def process_request(self, data, dev_info, device_key, transaction_id, conn):
        start_time = time.time()
        try:
            if len(data) < 6:
                logger.error("Invalid request length for %s: %d bytes", device_key, len(data))
                self.send_error_response(transaction_id, dev_info['slave_id'], 0x03, conn)
                return
            function_code = data[7]
            address = int.from_bytes(data[8:10], byteorder='big')
            logger.info("Processing request for %s, Function: %d, Address: %d, start time: %s, Slave ID: %d",
                        device_key, function_code, address, time.strftime('%H:%M:%S'), dev_info['slave_id'])
           
            max_address = dev_info.get('max_address', 65535)
            min_address = dev_info.get('min_address', 0)
            if function_code == 3:
                quantity = int.from_bytes(data[10:12], byteorder='big')
                traffic_logger.info("Quantity: %d", quantity)
                if quantity <= 0 or quantity > 125 or quantity % 2 != 0:
                    logger.error("Invalid quantity %d for %s, must be even and 1-125", quantity, device_key)
                    self.send_error_response(transaction_id, dev_info['slave_id'], 0x03, conn)
                    return
                if address < min_address or address + quantity - 1 > max_address:
                    logger.error("Register address range invalid: %d + %d exceeds [%d, %d] for %s",
                                 address, quantity, min_address, max_address, device_key)
                    self.send_error_response(transaction_id, dev_info['slave_id'], 0x02, conn)
                    return
                n_data_bytes = quantity * 2  # 每个寄存器2字节，总字节数
                length = 3 + n_data_bytes
                response = bytearray(transaction_id)
                response.extend(b'\x00\x00')
                response.extend(struct.pack('>H', length))
                response.append(dev_info['slave_id'])
                response.append(0x03)
                response.append(n_data_bytes)
                values = []
                with self.data_lock:
                    for i in range(0, quantity, 2):
                        reg_addr = address + i
                        value = dev_info['data'].get(reg_addr, 0.0)
                        int_value = int(value * COEFFICIENT)
                        int_value = max(-2147483648, min(2147483647, int_value))
                        bytes_value = int_value.to_bytes(4, byteorder='big', signed=True)
                        values.append(bytes_value)
                        logger.debug("Read %s reg %d: value=%.2f, int_value=%d, bytes=%s",
                                     device_key, reg_addr, value, int_value, bytes_value.hex())
                response.extend(b''.join(values))
                traffic_logger.info("Sending read response for %s: %s at %s, length: %d bytes",
                                   device_key, response.hex(), time.strftime('%H:%M:%S'), len(response))
                try:
                    conn.sendall(response)
                    traffic_logger.info("Read response sent successfully at %s, time taken: %.3f ms",
                                       time.strftime('%H:%M:%S'), (time.time() - start_time) * 1000)
                except Exception as e:
                    logger.error("Failed to send read response for %s: %s with data: %s",
                                 device_key, str(e), response.hex())
                    self.send_error_response(transaction_id, dev_info['slave_id'], 0x04, conn)
            elif function_code == 6:
                logger.warning("Function code 6 not supported for 32-bit values, use function code 16")
                self.send_error_response(transaction_id, dev_info['slave_id'], 0x01, conn)
                return
            elif function_code == 16:
                quantity = int.from_bytes(data[10:12], byteorder='big')
                if quantity <= 0 or quantity > 125 or quantity % 2 != 0:
                    logger.error("Invalid quantity %d for %s, must be even and 1-125", quantity, device_key)
                    self.send_error_response(transaction_id, dev_info['slave_id'], 0x03, conn)
                    return
                byte_count = data[12]
                if byte_count != quantity * 2:
                    logger.error("Byte count %d does not match quantity %d for %s", byte_count, quantity, device_key)
                    self.send_error_response(transaction_id, dev_info['slave_id'], 0x03, conn)
                    return
                if len(data) < 13 + byte_count:
                    logger.error("Invalid data length for multi-write: %d bytes, expected %d for %s",
                                 len(data), 13 + byte_count, device_key)
                    self.send_error_response(transaction_id, dev_info['slave_id'], 0x03, conn)
                    return
                if address < min_address or address + quantity - 1 > max_address:
                    logger.error("Register address range invalid: %d + %d exceeds [%d, %d] for %s",
                                 address, quantity, min_address, max_address, device_key)
                    self.send_error_response(transaction_id, dev_info['slave_id'], 0x02, conn)
                    return
                start_addr = address
                with self.data_lock:
                    for i in range(0, quantity, 2):
                        reg_addr = start_addr + i
                        if reg_addr not in dev_info['data']:
                            dev_info['data'][reg_addr] = 0.0
                            logger.warning("Initialized undefined reg_addr %d for %s with default value 0.0", reg_addr, device_key)
                        value_bytes = data[13 + i * 2 : 13 + (i + 2) * 2]
                        int_value = int.from_bytes(value_bytes, byteorder='big', signed=True)
                        value = int_value / COEFFICIENT
                        old_value = dev_info['data'][reg_addr]
                        dev_info['data'][reg_addr] = max(-21474836.48, min(21474836.48, value))
                        logger.info("Written to %s reg %d: %.2f -> %.2f (int: %d, bytes: %s)",
                                    device_key, reg_addr, old_value, value, int_value, value_bytes.hex())
                length = 6
                response = bytearray(transaction_id) + b'\x00\x00' + struct.pack('>H', length) + bytes([dev_info['slave_id'], 0x10]) + data[8:12]
                traffic_logger.info("Sending multi-write response for %s: %s at %s, length: %d bytes",
                                   device_key, response.hex(), time.strftime('%H:%M:%S'), len(response))
                try:
                    conn.sendall(response)
                    traffic_logger.info("Multi-write response sent successfully at %s, time taken: %.3f ms",
                                       time.strftime('%H:%M:%S'), (time.time() - start_time) * 1000)
                except Exception as e:
                    logger.error("Failed to send multi-write response for %s: %s with data: %s",
                                 device_key, str(e), response.hex())
                    self.send_error_response(transaction_id, dev_info['slave_id'], 0x04, conn)
            else:
                logger.warning("Unsupported function code %d for %s", function_code, device_key)
                self.send_error_response(transaction_id, dev_info['slave_id'], 0x01, conn)
        except Exception as e:
            logger.error("Process request error for %s: %s with data: %s", device_key, str(e), data.hex())
            self.send_error_response(transaction_id, dev_info['slave_id'], 0x04, conn)

    def stop(self):
        logger.info("Shutting down Modbus server...")
        if self.sock:
            self.sock.close()
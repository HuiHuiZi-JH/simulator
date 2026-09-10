# -*- coding: utf-8 -*-
import time
import threading
import logging
import logging.handlers
import json
import os
import sys
from src.communication.modbus_server import ModbusServer
from src.communication.web_server import WebServer
from utils.config_loader import load_devices_config
from utils.locks import data_lock
from src.models.pv_model import PVModel
from src.models.battery_model import BatteryModel
from src.models.ev_model import EVModel
from src.models.meter_model import MeterModel
from src.models.load_model import LoadModel
from src.models.jtc_load_model import JTCLoadModel
from src.models.t7_pv_model import T7PVModel
from src.models.virtual_grid_model import VirtualGridModel
from src.history import PowerHistory
from config.modbus_registers import REGISTERS

# Load logging configuration
log_config_path = 'config/logging_config.json'
default_config = {
    "log_level": "INFO",
    "log_dir": "log",
    "log_file": "simulation.log",
    "max_size": 10,
    "max_backups": 5,
    "message_log_file": "message.log",
    "message_log_level": "INFO",
    "traffic_log_file": "modbus_traffic.log"
}
log_config = default_config
if os.path.exists(log_config_path):
    try:
        with open(log_config_path, 'r') as f:
            log_config = json.load(f)
    except json.JSONDecodeError as e:
        logging.warning(f"Invalid JSON in {log_config_path}: {e}. Using default config: {default_config}")

# Create log directory
log_dir = log_config.get("log_dir", "log")
os.makedirs(log_dir, exist_ok=True)

# Set log levels
log_level = getattr(logging, log_config.get("log_level", "INFO").upper(), logging.INFO)
message_log_level = getattr(logging, log_config.get("message_log_level", "INFO").upper(), logging.INFO)

# Configure state logger
logging.getLogger('').handlers = []
state_logger = logging.getLogger('state')
state_logger.setLevel(log_level)
log_file_path = os.path.join(log_dir, log_config.get("log_file", "simulation.log"))
fh_state = logging.handlers.RotatingFileHandler(
    log_file_path,
    maxBytes=log_config.get("max_size", 10) * 1024 * 1024,
    backupCount=log_config.get("max_backups", 5)
)
fh_state.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
fh_state.setFormatter(formatter)
state_logger.addHandler(fh_state)
state_logger.propagate = False

# Configure message logger
message_logger = logging.getLogger('message')
message_logger.setLevel(message_log_level)
message_file_path = os.path.join(log_dir, log_config.get("message_log_file", "message.log"))
fh_message = logging.handlers.RotatingFileHandler(
    message_file_path,
    maxBytes=log_config.get("max_size", 10) * 1024 * 1024,
    backupCount=log_config.get("max_backups", 5)
)
fh_message.setLevel(message_log_level)
fh_message.setFormatter(formatter)
message_logger.addHandler(fh_message)
message_logger.propagate = False

# Configure traffic logger
traffic_logger = logging.getLogger('traffic')
traffic_logger.setLevel(logging.INFO)
traffic_file_path = os.path.join(log_dir, log_config.get("traffic_log_file", "modbus_traffic.log"))
fh_traffic = logging.handlers.RotatingFileHandler(
    traffic_file_path,
    maxBytes=log_config.get("max_size", 10) * 1024 * 1024,
    backupCount=log_config.get("max_backups", 5)
)
fh_traffic.setLevel(logging.INFO)
fh_traffic.setFormatter(formatter)
traffic_logger.addHandler(fh_traffic)
traffic_logger.propagate = False

logger = logging.getLogger(__name__)
devices_data = {}
# Types with no physics of their own: they read the other devices, so they tick
# only after every physical device has produced this cycle's values.
DERIVED_TYPES = ('Meter', 'VirtualGrid')
START_HOUR = 0.0
modbus_server = None
web_server = None
simulation_start_time = None
WEB_PORT = 8080
raw_config = {}
# The past the dashboard chart draws. Written by the simulation thread, read by
# the web thread; empty until the first sample, and empty again after a restart.
history = PowerHistory()


def restart_process():
    """Replace this process with a fresh one so device.json is re-read.

    execv keeps the working directory and argv, so relative config paths still
    resolve. The listening sockets are closed first and both set
    SO_REUSEADDR, so the replacement can rebind 5021 and the web port
    immediately. Works standalone and under systemd alike.
    """
    state_logger.info("Restart requested via web UI; re-executing %s %s",
                      sys.executable, ' '.join(sys.argv))
    for name, sock in (('modbus', getattr(modbus_server, 'sock', None)),
                       ('web', getattr(getattr(web_server, 'httpd', None), 'socket', None))):
        try:
            if sock:
                sock.close()
                state_logger.debug("Closed %s listening socket before restart", name)
        except Exception as e:
            state_logger.warning("Could not close %s socket before restart: %s", name, e)
    logging.shutdown()
    time.sleep(0.4)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def current_sim_hour():
    """Simulated hour of day, wrapping at midnight."""
    if simulation_start_time is None:
        return START_HOUR % 24
    return (START_HOUR + (time.time() - simulation_start_time) / 3600) % 24

def load_config():
    global devices_data, START_HOUR, simulation_start_time, WEB_PORT, raw_config
    config = load_devices_config('config/device.json')
    raw_config = config
    WEB_PORT = config.get('web_port', 8080)
    start_time = config.get('start_time', 'now')
    if isinstance(start_time, str) and start_time.strip().lower() == 'now':
        # Align the simulated clock to the host's local time, so every restart
        # picks up where the wall clock actually is instead of jumping back to
        # a fixed hour. Seconds are included so curves line up exactly.
        local = time.localtime()
        START_HOUR = local.tm_hour + local.tm_min / 60.0 + local.tm_sec / 3600.0
        state_logger.info("start_time is 'now': simulation clock aligned to local time %s",
                          time.strftime('%H:%M:%S', local))
    else:
        hh, mm = map(int, str(start_time).split(':'))
        START_HOUR = hh + mm / 60.0
        state_logger.info("start_time pinned to %02d:%02d", hh, mm)
    simulation_start_time = time.time()
    state_logger.info(f"Simulation start hour set to {START_HOUR}, start time: {time.ctime(simulation_start_time)}")
    slave_ids = set()
    next_slave_id = 1
    for device_type in config.get('Devices', []):
        for dev_type, dev_list in device_type.items():
            for dev in dev_list:
                device_key = dev['DeviceKey']
                initial_data = {}
                for reg_offset in range(0, 100, 2):  # Initialize even addresses for 32-bit
                    initial_data[reg_offset] = 0.0
                for reg_name, reg_offset in REGISTERS.get(dev_type, {}).get('points', {}).items():
                    initial_data[reg_offset] = 0.0
                    if dev_type == 'EV' and reg_name == 'PUB_CONN.ChargePWSet':
                        initial_data[reg_offset] = dev.get('PUB_CONN.RatedPW', 22.0)
                configured_slave_id = dev.get('slave_id')
                if configured_slave_id is not None:
                    if not 1 <= configured_slave_id <= 247:
                        state_logger.error(f"Invalid slave_id {configured_slave_id} for {device_key}, must be 1-247. Using auto-assigned ID.")
                        while next_slave_id in slave_ids:
                            next_slave_id += 1
                            if next_slave_id > 247:
                                state_logger.error("No available slave_id (1-247)")
                                return
                        slave_id = next_slave_id
                        next_slave_id += 1
                    elif configured_slave_id in slave_ids:
                        state_logger.error(f"Duplicate slave_id {configured_slave_id} for {device_key}. Using auto-assigned ID.")
                        while next_slave_id in slave_ids:
                            next_slave_id += 1
                            if next_slave_id > 247:
                                state_logger.error("No available slave_id (1-247)")
                                return
                        slave_id = next_slave_id
                        next_slave_id += 1
                    else:
                        slave_id = configured_slave_id
                else:
                    while next_slave_id in slave_ids:
                        next_slave_id += 1
                        if next_slave_id > 247:
                            state_logger.error("No available slave_id (1-247)")
                            return
                    slave_id = next_slave_id
                    next_slave_id += 1
                slave_ids.add(slave_id)
                devices_data[device_key] = {
                    'type': dev_type,
                    'data': initial_data,
                    'config': dev,
                    'model': None,
                    'slave_id': slave_id,
                    'is_idle': False
                }
                try:
                    if dev_type == 'PV':
                        devices_data[device_key]['model'] = PVModel(dev, START_HOUR)
                    elif dev_type == 'BESS':
                        devices_data[device_key]['model'] = BatteryModel(dev, START_HOUR)
                    elif dev_type == 'EV':
                        devices_data[device_key]['model'] = EVModel(dev, START_HOUR)
                    elif dev_type == 'Load':
                        devices_data[device_key]['model'] = LoadModel(dev, START_HOUR)
                    elif dev_type == 'JTCLoad':
                        devices_data[device_key]['model'] = JTCLoadModel(dev, START_HOUR)
                    elif dev_type == 'T7PV':
                        devices_data[device_key]['model'] = T7PVModel(dev, START_HOUR)
                    elif dev_type == 'VirtualGrid':
                        state_logger.debug(f"Initializing VirtualGridModel for {device_key}")
                        devices_data[device_key]['model'] = VirtualGridModel(dev, devices_data, data_lock, START_HOUR)
                        state_logger.debug(f"VirtualGridModel initialized for {device_key}")
                    elif dev_type == 'Meter':
                        state_logger.debug(f"Initializing MeterModel for {device_key}")
                        devices_data[device_key]['model'] = MeterModel(dev, devices_data, data_lock, START_HOUR)
                        state_logger.debug(f"MeterModel initialized for {device_key}")
                except Exception as e:
                    state_logger.error(f"Failed to initialize model for {device_key}: {str(e)} with config {dev}")
    state_logger.info(f"Loaded devices with slave IDs: {[(k, v['slave_id']) for k, v in devices_data.items()]}")
    return devices_data

def update_device_data():
    global modbus_server, simulation_start_time
    prev_pv_total = 0.0
    last_linkage_time = time.time()
    last_log_timestamp = time.time() - 1.0
    log_interval = 1.0
    while True:
        current_time = time.time()
        elapsed_seconds = current_time - simulation_start_time
        current_hour = START_HOUR + (elapsed_seconds / 3600)
        current_hour_display = current_hour % 24
        try:
            state_logger.debug(f"Starting update cycle, current_hour: {current_hour:.2f} (display: {current_hour_display:.2f}), devices_data length: {len(devices_data)}")
            for device_key, dev_info in devices_data.items():
                if dev_info['model'] and dev_info['type'] not in DERIVED_TYPES:
                    try:
                        if dev_info['type'] == 'PV':
                            new_data = dev_info['model'].update(p_limit=dev_info['data'].get(4, 0))
                        elif dev_info['type'] == 'BESS':
                            new_data = dev_info['model'].update(p_command=dev_info['data'].get(14, 0))
                        elif dev_info['type'] == 'EV':
                            new_data = dev_info['model'].update(devices_data, device_key)
                        elif dev_info['type'] in ('Load', 'JTCLoad', 'T7PV'):
                            new_data = dev_info['model'].update()
                        points = REGISTERS.get(dev_info['type'], {}).get('points', {})
                        for reg_name, reg_offset in points.items():
                            if reg_name in new_data:
                                dev_info['data'][reg_offset] = new_data[reg_name]
                                state_logger.debug(f"Updated {device_key} {reg_name} at {reg_offset} to {new_data[reg_name]}")
                    except Exception as e:
                        state_logger.error(f"Error updating {device_key}: {str(e)} with traceback {e.__traceback__}")
            for device_key, dev_info in devices_data.items():
                if dev_info['type'] in DERIVED_TYPES and dev_info['model']:
                    try:
                        state_logger.debug(f"Updating {dev_info['type']} {device_key}, devices_data length: {len(devices_data)}")
                        new_data = dev_info['model'].update()
                        # Publish ActivePower + the energy counters as one atomic set, so a Modbus
                        # read spanning registers 0-4 cannot mix values from two different cycles.
                        with data_lock:
                            for reg_name, value in REGISTERS.get(dev_info['type'], {}).get('points', {}).items():
                                if reg_name in new_data:
                                    dev_info['data'][value] = new_data[reg_name]
                                    state_logger.debug(f"Updated {device_key} {reg_name} at {value} to {new_data[reg_name]}")
                    except Exception as e:
                        state_logger.error(f"Error updating {device_key}: {str(e)} with traceback {e.__traceback__}")
                elif not dev_info['model']:
                    state_logger.error(f"Model for {device_key} is None, skipping update")
            total_power = 0.0
            for dev_info in devices_data.values():
                if dev_info['type'] == 'Meter':
                    total_power += dev_info['data'].get(0, 0)
            # Sampled outside every `with data_lock` block above -- record()
            # takes the lock itself, and this lock is not reentrant.
            history.record(current_time, current_hour_display, devices_data, data_lock)
        except Exception as e:
            state_logger.error(f"Error in update_device_data: {str(e)} with traceback {e.__traceback__}")
            continue
        if current_time - last_linkage_time >= 1.0:
            with data_lock:
                pv_total = sum(dev['data'].get(0, 0) for dev in devices_data.values() if dev['type'] == 'PV')
                prev_pv_total = pv_total
            last_linkage_time = current_time
        if current_time - last_log_timestamp >= log_interval:
            with data_lock:
                log_messages = []
                for device_key, dev_info in devices_data.items():
                    if dev_info['type'] == 'Meter':
                        log_messages.append(f"Meter {device_key} Total Power: {dev_info['data'].get(0, 0):.2f} kW")
                    elif dev_info['type'] == 'PV':
                        log_messages.append(f"PV {device_key} GenActivePW: {dev_info['data'].get(0, 0):.2f} kW, LimitPower: {dev_info['data'].get(4, 0):.2f} kW")
                    elif dev_info['type'] == 'BESS':
                        log_messages.append(f"BESS {device_key} ActivePW: {dev_info['data'].get(0, 0):.2f} kW, SOC: {dev_info['data'].get(2, 0):.1f}%, "
                                  f"ChargingEng: {dev_info['data'].get(20, 0):.2f} kWh, DischargingEng: {dev_info['data'].get(22, 0):.2f} kWh")
                    elif dev_info['type'] == 'EV':
                        log_messages.append(f"EV {device_key} ChargePW: {dev_info['data'].get(0, 0):.2f} kW")
                    elif dev_info['type'] == 'Load':
                        log_messages.append(f"Load {device_key} Power: {dev_info['data'].get(0, 0):.2f} kW")
                    elif dev_info['type'] == 'JTCLoad':
                        log_messages.append(f"JTC common load {device_key} Power: {dev_info['data'].get(0, 0):.2f} kW")
                    elif dev_info['type'] == 'T7PV':
                        log_messages.append(f"T7 PV {device_key} GenActivePW: {dev_info['data'].get(0, 0):.2f} kW")
                    elif dev_info['type'] == 'VirtualGrid':
                        log_messages.append(f"VirtualGrid {device_key} Incoming: {dev_info['data'].get(0, 0):.2f} kW "
                                  f"(cap {dev_info['data'].get(6, 0):.0f} kW)")
                if log_messages:
                    state_logger.info("Device States: " + " | ".join(log_messages))
                    state_logger.debug("Log messages generated: %s", log_messages)
            last_log_timestamp = current_time
        time.sleep(1.0)

def main():
    global modbus_server, web_server
    state_logger.info("Starting CIL Simulator...")
    load_config()
    state_logger.info(f"Loaded devices: {list(devices_data.keys())}")
    modbus_server = ModbusServer(devices_data, data_lock)
    state_logger.info("Initializing Modbus server...")
    server_thread = threading.Thread(target=modbus_server.run, daemon=False)
    server_thread.start()
    state_logger.info("Modbus server thread started")
    state_logger.info("Starting update thread...")
    update_thread = threading.Thread(target=update_device_data, daemon=False)
    update_thread.start()
    state_logger.info("Update thread started")
    web_server = WebServer(devices_data, data_lock, current_sim_hour, WEB_PORT,
                           raw_config, restart_process, history)
    web_thread = threading.Thread(target=web_server.run, daemon=True)
    web_thread.start()
    state_logger.info("Web dashboard thread started on port %d", WEB_PORT)
    try:
        while True:
            if not server_thread.is_alive():
                state_logger.error("Modbus server thread terminated unexpectedly")
                break
            if not update_thread.is_alive():
                state_logger.error("Update thread terminated unexpectedly")
                break
            if not web_thread.is_alive():
                state_logger.warning("Web dashboard thread stopped; simulation continues")
            time.sleep(1)
    except KeyboardInterrupt:
        state_logger.info("Shutting down CIL Simulator...")
    finally:
        state_logger.info("Shutting down CIL Simulator...")
        if modbus_server and modbus_server.sock:
            modbus_server.sock.close()
        if web_server:
            web_server.stop()
        update_thread.join(timeout=5)
        server_thread.join(timeout=5)

if __name__ == "__main__":
    main()
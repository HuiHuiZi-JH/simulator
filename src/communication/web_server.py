# -*- coding: utf-8 -*-
"""Dashboard, setpoint console and config editor for the running simulator.

Serves the single-page UI in web/ plus a small JSON API over the same
devices_data table the Modbus server reads, guarded by the same lock.
Standard library only, in keeping with the project's zero-dependency rule.
"""
import copy
import json
import logging
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config.modbus_registers import REGISTERS

logger = logging.getLogger('message')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
WEB_ROOT = os.path.join(PROJECT_ROOT, 'web')
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config', 'device.json')

# Registers the simulation reads back as control inputs. Anything not listed
# here is an output that the next tick would overwrite, so the UI refuses it.
WRITABLE = {
    'PV': {'INV.LimitPower': 4},
    'BESS': {'BS.SysAPSetPoint': 14},
    'EV': {'PUB_CONN.ChargePWSet': 8},
}

# Config values the UI needs to scale its sliders sensibly.
LIMIT_KEYS = {
    'PV': ('ratedPower',),
    'BESS': ('maxChargePower', 'maxDischargePower', 'ratedCapacity'),
    'EV': ('PUB_CONN.RatedPW', 'PUB_CONN.MinChargePW'),
    'Load': ('base_power',),
    'VirtualGrid': ('max_import', 'bess_zero_export', 't98_loop_limit'),
}

DEVICE_TYPES = ('Meter', 'PV', 'BESS', 'EV', 'Load', 'JTCLoad', 'T7PV',
                'VirtualGrid')

# Fields the config editor exposes, per device type:
#   (json key, label, kind, required)
# kind is one of: number, text, mode, schedule
CONFIG_FIELDS = {
    'Meter': [],
    'PV': [
        ('ratedPower', 'Rated power (kW)', 'number', True),
        ('mode', 'Source', 'mode', False),
        ('csv_file', 'Curve file', 'text', False),
    ],
    'BESS': [
        ('ratedCapacity', 'Capacity (kWh)', 'number', True),
        ('maxChargePower', 'Max charge (kW)', 'number', True),
        ('maxDischargePower', 'Max discharge (kW)', 'number', True),
        ('socMax', 'SOC max (0-1)', 'number', False),
        ('socMin', 'SOC min (0-1)', 'number', False),
        ('initial_soc', 'Initial SOC (0-1)', 'number', False),
        ('voltage_nominal', 'Nominal voltage (V)', 'number', False),
        ('resistance', 'Resistance (ohm)', 'number', False),
    ],
    'EV': [
        ('PUB_CONN.RatedPW', 'Rated power (kW)', 'number', True),
        ('PUB_CONN.MinChargePW', 'Min charge (kW)', 'number', False),
        ('charge_factor', 'Charge factor', 'number', False),
        ('charger_type', 'Charger type (AC/DC)', 'text', False),
        ('charge_schedule', 'Charging windows', 'schedule', False),
    ],
    'Load': [
        ('base_power', 'Base power (kW)', 'number', True),
        ('mode', 'Source', 'mode', False),
        ('csv_file', 'Curve file', 'text', False),
    ],
    # No base power and no synthetic mode: the JTC common load is the curve
    # the operator supplies, or nothing at all.
    'JTCLoad': [
        ('csv_file', 'Curve file', 'text', True),
    ],
    'T7PV': [
        ('csv_file', 'Curve file', 'text', True),
    ],
    'VirtualGrid': [
        ('max_import', 'Max import (kW)', 'number', False),
        ('bess_zero_export', 'BESS zero export (kW)', 'number', False),
        ('t98_loop_limit', 'T98 loop limit (kW)', 'number', False),
    ],
}


def _is_hhmm(value, allow_now=False):
    if allow_now and isinstance(value, str) and value.strip().lower() == 'now':
        return True
    if not isinstance(value, str) or ':' not in value:
        return False
    hh, _, mm = value.partition(':')
    if not (hh.isdigit() and mm.isdigit()):
        return False
    return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59


def validate_config(cfg):
    """Return a list of human-readable problems. Empty means the config is safe
    to write -- a bad device.json would stop the simulator starting at all."""
    errors = []
    if not isinstance(cfg, dict):
        return ['Configuration must be a JSON object']

    if not _is_hhmm(cfg.get('start_time', ''), allow_now=True):
        errors.append('Start time must be "now" or look like HH:MM, for example 08:30')

    port = cfg.get('web_port', 8080)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        errors.append('Web port must be a whole number between 1 and 65535')

    devices = cfg.get('Devices')
    if not isinstance(devices, list):
        return errors + ['"Devices" must be a list']

    seen_keys, seen_slaves = set(), set()
    total = 0
    for group in devices:
        if not isinstance(group, dict):
            errors.append('Each entry under "Devices" must be an object')
            continue
        for dev_type, dev_list in group.items():
            if dev_type not in DEVICE_TYPES:
                errors.append('Unknown device type "%s"' % dev_type)
                continue
            if not isinstance(dev_list, list):
                errors.append('"%s" must hold a list of devices' % dev_type)
                continue
            for dev in dev_list:
                total += 1
                if not isinstance(dev, dict):
                    errors.append('Each %s device must be an object' % dev_type)
                    continue
                key = dev.get('DeviceKey')
                if not key or not isinstance(key, str):
                    errors.append('Every device needs a DeviceKey')
                elif key in seen_keys:
                    errors.append('Duplicate DeviceKey "%s"' % key)
                else:
                    seen_keys.add(key)

                slave = dev.get('slave_id')
                if slave is not None:
                    if not isinstance(slave, int) or not 1 <= slave <= 247:
                        errors.append('%s: slave ID must be a whole number 1-247'
                                      % (key or dev_type))
                    elif slave in seen_slaves:
                        errors.append('%s: slave ID %s is already used'
                                      % (key or dev_type, slave))
                    else:
                        seen_slaves.add(slave)

                for field, label, kind, required in CONFIG_FIELDS[dev_type]:
                    value = dev.get(field)
                    if value is None:
                        if required:
                            errors.append('%s: %s is required'
                                          % (key or dev_type, label))
                        continue
                    if kind == 'number' and not isinstance(value, (int, float)):
                        errors.append('%s: %s must be a number'
                                      % (key or dev_type, label))
                    elif kind == 'mode' and value not in (0, 1):
                        errors.append('%s: source must be 0 (curve) or 1 (synthetic)'
                                      % (key or dev_type))
                    elif kind == 'schedule':
                        if not isinstance(value, list):
                            errors.append('%s: charging windows must be a list'
                                          % (key or dev_type))
                            continue
                        for window in value:
                            if (not isinstance(window, dict)
                                    or not _is_hhmm(window.get('start', ''))
                                    or not _is_hhmm(window.get('stop', ''))):
                                errors.append('%s: each charging window needs a '
                                              'start and stop as HH:MM'
                                              % (key or dev_type))
                                break

    if total == 0:
        errors.append('Configure at least one device')
    return errors


class WebServer:
    def __init__(self, devices_data, data_lock, get_sim_hour, port=8080,
                 loaded_config=None, restart_hook=None, history=None):
        self.devices_data = devices_data
        self.data_lock = data_lock
        self.get_sim_hour = get_sim_hour
        self.port = port
        self.httpd = None
        # Snapshot of what the running simulation was built from, so the UI can
        # tell the operator when the file on disk has drifted ahead of it.
        self.loaded_config = copy.deepcopy(loaded_config or {})
        self.restart_hook = restart_hook
        # The rolling sample ring the trend chart reads. None when the server is
        # constructed without one -- the endpoint then answers empty rather than
        # 404, so the chart shows its "no samples yet" state instead of an error.
        self.history = history

    # ---------- live state ----------

    def snapshot(self):
        """Every device's named points, read as one atomic set."""
        devices = []
        with self.data_lock:
            for key, dev in self.devices_data.items():
                points = REGISTERS.get(dev['type'], {}).get('points', {})
                devices.append({
                    'key': key,
                    'type': dev['type'],
                    'slave_id': dev['slave_id'],
                    'points': {name: dev['data'].get(off, 0.0)
                               for name, off in points.items()},
                    'writable': sorted(WRITABLE.get(dev['type'], {})),
                    'limits': {k: dev['config'].get(k)
                               for k in LIMIT_KEYS.get(dev['type'], ())
                               if dev['config'].get(k) is not None},
                })
        return {'sim_hour': self.get_sim_hour(),
                'wall_time': time.strftime('%H:%M:%S'),
                'restart_needed': self.restart_needed(),
                'devices': devices}

    def history_dump(self, path):
        """Serve the sample ring, or only what is newer than ?after=<seq>."""
        after = urllib.parse.parse_qs(
            urllib.parse.urlparse(path).query).get('after', [None])[0]
        try:
            after = int(after) if after is not None else None
        except (TypeError, ValueError):
            after = None
        if self.history is None:
            return {'interval': 0, 'span_hours': 0, 'seq': 0, 'first_seq': None,
                    'count': 0, 'sim_hour': [], 'series': {}, 'devices': []}
        return self.history.dump(after)

    def apply_setpoint(self, device_key, point, value):
        """Write a control register, exactly as a Modbus FC16 write would."""
        dev = self.devices_data.get(device_key)
        if dev is None:
            return False, 'No device named %s' % device_key
        offset = WRITABLE.get(dev['type'], {}).get(point)
        if offset is None:
            return False, '%s is not a control register on %s' % (point, device_key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return False, 'Value must be a number'
        value = max(-21474836.48, min(21474836.48, value))
        with self.data_lock:
            dev['data'][offset] = value
        logger.info('Web setpoint: %s %s = %.2f', device_key, point, value)
        return True, 'Set %s to %.2f' % (point, value)

    # ---------- configuration ----------

    def read_config(self):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)

    def restart_needed(self):
        """True when device.json no longer matches what the simulation loaded."""
        try:
            on_disk = self.read_config()
        except (OSError, ValueError):
            return False
        return (json.dumps(on_disk, sort_keys=True)
                != json.dumps(self.loaded_config, sort_keys=True))

    def save_config(self, cfg):
        errors = validate_config(cfg)
        if errors:
            return False, errors

        # Keep the last good file, then swap the new one in atomically so a
        # failure part-way through can never leave a truncated device.json.
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    previous = f.read()
                with open(CONFIG_PATH + '.bak', 'w', encoding='utf-8') as f:
                    f.write(previous)
            tmp = CONFIG_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
                f.write('\n')
            os.replace(tmp, CONFIG_PATH)
        except OSError as e:
            logger.error('Failed to write %s: %s', CONFIG_PATH, e)
            return False, ['Could not write device.json: %s' % e]

        logger.info('Configuration saved via web UI (%d device groups)',
                    len(cfg.get('Devices', [])))
        return True, []

    # ---------- server ----------

    def run(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, fmt, *args):
                logger.debug('Web %s - %s', self.address_string(), fmt % args)

            def _send(self, code, body, content_type):
                if isinstance(body, str):
                    body = body.encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, payload):
                self._send(code, json.dumps(payload), 'application/json; charset=utf-8')

            def _body(self):
                length = int(self.headers.get('Content-Length', 0))
                return json.loads(self.rfile.read(length) or b'{}')

            def do_GET(self):
                if self.path in ('/', '/index.html'):
                    path = os.path.join(WEB_ROOT, 'index.html')
                    try:
                        with open(path, 'rb') as f:
                            self._send(200, f.read(), 'text/html; charset=utf-8')
                    except OSError as e:
                        logger.error('Cannot read %s: %s', path, e)
                        self._send(500, 'Dashboard file missing: %s' % path,
                                   'text/plain; charset=utf-8')
                elif self.path.startswith('/api/state'):
                    self._json(200, server.snapshot())
                elif self.path.startswith('/api/history'):
                    self._json(200, server.history_dump(self.path))
                elif self.path.startswith('/api/config'):
                    try:
                        self._json(200, {
                            'config': server.read_config(),
                            'schema': CONFIG_FIELDS,
                            'types': list(DEVICE_TYPES),
                            'restart_needed': server.restart_needed(),
                            'can_restart': server.restart_hook is not None,
                        })
                    except (OSError, ValueError) as e:
                        self._json(500, {'error': 'Could not read device.json: %s' % e})
                else:
                    self._send(404, 'Not found', 'text/plain; charset=utf-8')

            def do_POST(self):
                if self.path.startswith('/api/control'):
                    try:
                        payload = self._body()
                    except (ValueError, TypeError) as e:
                        self._json(400, {'ok': False,
                                         'message': 'Malformed request: %s' % e})
                        return
                    ok, message = server.apply_setpoint(
                        payload.get('device'), payload.get('point'),
                        payload.get('value'))
                    self._json(200 if ok else 400, {'ok': ok, 'message': message})

                elif self.path.startswith('/api/restart'):
                    if server.restart_hook is None:
                        self._json(503, {'ok': False,
                                         'message': 'Restart is not available'})
                        return
                    # Answer first, then re-exec, so the browser sees the reply
                    # rather than a dropped connection.
                    self._json(200, {'ok': True, 'message': 'Restarting the simulator...'})
                    try:
                        self.wfile.flush()
                    except OSError:
                        pass
                    logger.info('Restart requested from %s', self.address_string())
                    threading.Timer(0.5, server.restart_hook).start()

                elif self.path.startswith('/api/config'):
                    try:
                        payload = self._body()
                    except (ValueError, TypeError) as e:
                        self._json(400, {'ok': False,
                                         'errors': ['Malformed request: %s' % e]})
                        return
                    ok, errors = server.save_config(payload.get('config'))
                    self._json(200 if ok else 400, {
                        'ok': ok,
                        'errors': errors,
                        'restart_needed': server.restart_needed(),
                        'message': ('Saved to config/device.json — restart the '
                                    'simulator to apply') if ok else
                                   'Nothing was written; fix the problems below',
                    })
                else:
                    self._send(404, 'Not found', 'text/plain; charset=utf-8')

        logger.info('Starting web dashboard on 0.0.0.0:%d...', self.port)
        try:
            self.httpd = ThreadingHTTPServer(('0.0.0.0', self.port), Handler)
            self.httpd.daemon_threads = True
        except OSError as e:
            logger.error('Failed to bind web port %d: %s', self.port, e)
            raise
        logger.info('Web dashboard listening on http://0.0.0.0:%d', self.port)
        self.httpd.serve_forever()

    def stop(self):
        logger.info('Shutting down web dashboard...')
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

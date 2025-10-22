#!/usr/bin/env python3
import os
import time
import subprocess
import xml.etree.ElementTree as ET
from xml.dom import minidom
import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

class Wardriving:
  def __init__(self):
    self.params = Params()
    self.scan_interval = 30  # default
    self.scan_intervals = [10, 20, 30, 60]  # corresponding to button indices
    self.sm = messaging.SubMaster(['gpsLocation'])

    # Find existing wardriving file
    data_dir = "/data"
    existing_files = []
    try:
      existing_files = [f for f in os.listdir(data_dir) if f.startswith("wardriving_") and f.endswith(".kml")]
    except OSError:
      pass

    # Find existing wardriving file
    data_dir = "/data"
    existing_files = []
    try:
      existing_files = [f for f in os.listdir(data_dir) if f.startswith("wardriving_") and f.endswith(".kml")]
    except OSError:
      pass
    if existing_files:
      # Sort by modification time, take the latest
      existing_files.sort(key=lambda f: os.path.getmtime(os.path.join(data_dir, f)), reverse=True)
      latest_file = os.path.join(data_dir, existing_files[0])
      # Parse start time from filename
      try:
        parts = existing_files[0].replace("wardriving_", "").replace(".kml", "").split("_")
        self.start_time = int(parts[0])
        self.current_end_time = int(parts[1]) if len(parts) > 1 else self.start_time
      except (ValueError, IndexError):
        self.start_time = time.time()
        self.current_end_time = self.start_time
      self.kml_file = latest_file
      # Load existing KML
      try:
        tree = ET.parse(self.kml_file)
        self.kml = tree.getroot()
        self.document = self.kml.find('.//{http://www.opengis.net/kml/2.2}Document')
        if self.document is None:
          raise ValueError("Invalid KML")
        self.folder = self.document.find('.//{http://www.opengis.net/kml/2.2}Folder')
        if self.folder is None:
          self.folder = ET.SubElement(self.document, "Folder")
          ET.SubElement(self.folder, "name").text = "WiFi Networks"
      except Exception as e:
        cloudlog.warning(f"Failed to load existing KML: {e}, creating new")
        self.start_time = time.time()
        self.current_end_time = self.start_time
        self.kml_file = f"/data/wardriving_{int(self.start_time)}_{int(self.current_end_time)}.kml"
        self.create_new_kml()
    else:
      self.start_time = time.time()
      self.current_end_time = self.start_time
      self.kml_file = f"/data/wardriving_{int(self.start_time)}_{int(self.current_end_time)}.kml"
      self.create_new_kml()

  def create_new_kml(self):
    self.kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    self.document = ET.SubElement(self.kml, "Document")
    ET.SubElement(self.document, "name").text = "Wardriving Data"
    self.folder = ET.SubElement(self.document, "Folder")
    ET.SubElement(self.folder, "name").text = "WiFi Networks"

  def scan_wifi(self):
    try:
      # Scan for Wi-Fi networks
      result = subprocess.run(['iw', 'wlan0', 'scan'], capture_output=True, text=True, timeout=10)
      if result.returncode != 0:
        # Try alternative interface
        result = subprocess.run(['iw', 'dev', 'scan'], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
          cloudlog.error("Wi-Fi scan failed on all interfaces")
          return []

      networks = []
      lines = result.stdout.split('\n')
      current_network = {}

      for line in lines:
        line = line.strip()
        if line.startswith('BSS '):
          if current_network and 'bssid' in current_network:
            networks.append(current_network)
          current_network = {'bssid': line.split()[1].replace('(', '').replace(')', '')}
        elif line.startswith('SSID:'):
          ssid = line.split(':', 1)[1].strip()
          if ssid:
            current_network['ssid'] = ssid
        elif line.startswith('signal:'):
          current_network['signal'] = line.split()[1]
        elif line.startswith('freq:'):
          current_network['freq'] = line.split()[1]
        elif line.startswith('capability:'):
          current_network['capability'] = line.split(':', 1)[1].strip()

      if current_network and 'bssid' in current_network:
        networks.append(current_network)

      return networks
    except Exception as e:
      cloudlog.error(f"Wi-Fi scan error: {e}")
      return []

  def get_location(self):
    self.sm.update(0)
    if self.sm.updated['gpsLocation']:
      gps = self.sm['gpsLocation']
      return gps.latitude, gps.longitude
    return None, None

  def add_network_to_kml(self, network, lat, lon):
    placemark = ET.SubElement(self.folder, "Placemark")
    ssid = network.get('ssid', 'Unknown')
    ET.SubElement(placemark, "name").text = ssid

    description = f"SSID: {ssid}<br>BSSID: {network.get('bssid', 'Unknown')}<br>"
    if 'signal' in network:
      description += f"Signal: {network['signal']} dBm<br>"
    if 'freq' in network:
      # Calculate channel from frequency
      freq = int(network['freq'])
      if 2412 <= freq <= 2484:
        channel = str((freq - 2412) // 5 + 1)
      elif 5170 <= freq <= 5825:
        channel = str((freq - 5170) // 5 + 34)
      else:
        channel = "Unknown"
      description += f"Frequency: {freq} MHz (Channel {channel})<br>"
    if 'capability' in network:
      description += f"Capabilities: {network['capability']}<br>"
    description += f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"

    ET.SubElement(placemark, "description").text = description

    point = ET.SubElement(placemark, "Point")
    ET.SubElement(point, "coordinates").text = f"{lon},{lat},0"

  def save_kml(self):
    rough_string = ET.tostring(self.kml, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    with open(self.kml_file, 'w') as f:
      f.write(reparsed.toprettyxml(indent="  "))

    # Update end time and rename file
    self.current_end_time = time.time()
    new_file = f"/data/wardriving_{int(self.start_time)}_{int(self.current_end_time)}.kml"
    if new_file != self.kml_file:
      try:
        os.rename(self.kml_file, new_file)
        self.kml_file = new_file
      except OSError as e:
        cloudlog.warning(f"Failed to rename KML file: {e}")

  def get_scan_interval(self):
    try:
      interval_index = int(self.params.get("WardrivingScanInterval", "2"))  # default to 30s (index 2)
      return self.scan_intervals[min(max(interval_index, 0), len(self.scan_intervals) - 1)]
    except (ValueError, IndexError):
      return 30

  def run(self):
    cloudlog.info("Starting wardriving service")
    last_scan = 0

    while True:
      if self.params.get_bool("WardrivingMode"):
        current_time = time.time()
        # Update scan interval from params
        self.scan_interval = self.get_scan_interval()
        if current_time - last_scan > self.scan_interval:
          lat, lon = self.get_location()
          if lat is not None and lon is not None:
            networks = self.scan_wifi()
            for network in networks:
              self.add_network_to_kml(network, lat, lon)
            self.save_kml()
            cloudlog.info(f"Scanned {len(networks)} networks at {lat}, {lon}")
          last_scan = current_time
      time.sleep(1)

if __name__ == "__main__":
  wardriving = Wardriving()
  wardriving.run()
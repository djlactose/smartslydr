# SmartSlydr Home Assistant Integration

The **SmartSlydr** custom integration brings full control of your SmartSlydr devices into Home Assistant via the official REST API v0.4. It provides:

- **Cover** entities for blinds/doors (open, close, stop, position)  
- **Sensor** entities for temperature (°C/°F), humidity (%), WLAN signal (dBm), sound (dB), and MAC address  
- **Petpass** toggle switch on each device (with allowed pet names listed)  
- **Automatic discovery** of new devices and petpass slots on every refresh  

---

## Prerequisites

- Home Assistant **2025.5** or later  
- A SmartSlydr account with at least one onboarded device  
- Integration files under:

  ```plain
  config/
  └─ custom_components/
     └─ smartslydr/
        ├─ __init__.py
        ├─ manifest.json
        ├─ const.py
        ├─ api_client.py
        ├─ config_flow.py
        ├─ cover.py
        ├─ sensor.py
        └─ switch.py
  ```

- (Optional) [HACS](https://hacs.xyz/) for one-click installation

---

## Installation

### A) Via HACS

1. In Home Assistant go to **HACS → Integrations → … (top-right) → Custom repositories**  
2. Set **Category to Integration** and enter the repository URL: https://github.com/djlactose/smartslydr
3. In **HACS → Integrations**, search for **SmartSlydr** and click **Install**  
4. Restart Home Assistant

### B) Manual

1. Copy the `smartslydr/` folder into `config/custom_components/` (all lowercase)  
2. Ensure your `manifest.json` contains:

   ```json
   {
     "domain": "smartslydr",
     "name": "SmartSlydr",
     "version": "0.4.0",
     "requirements": ["aiohttp"],
     "config_flow": true,
     "iot_class": "cloud_polling",
     "logo": "images/logo.png",
     "logo_dark": "images/dark_logo.png"
   }
   ```

3. Place your 256×256 `logo.png` (and `dark_logo.png`) under `smartslydr/images/`  
4. Restart Home Assistant

---

## Configuration

1. Navigate to **Settings → Devices & Services → Add Integration**  
2. Search for **SmartSlydr** and enter your **username** and **password**  
3. (Optional) Adjust the **Scan Interval** under **Settings → Devices & Services → SmartSlydr → Options**

---

## Entities & Devices

Once configured, Home Assistant will create:

- **Devices** (one per SmartSlydr unit) under **Settings → Devices**  
- **Covers** for any device reporting a `position` (0–100, 200 = stop)  
- **Sensors**:
  - Temperature (`°C` native, converts to `°F` if needed)  
  - Humidity (`%`)  
  - WLAN Signal (`dBm`)  

- **Petpass** switch (toggle open/close):
  - Reads initial state via **Get Status** API  
  - Toggles via **Set Command** API  

All entities share the same **Device Registry** entry (grouped by `device_id`).

---

## Troubleshooting

- **No logo?**
  - Ensure folder name is `smartslydr` (all lowercase)
  - Confirm `manifest.json` paths and keys (`logo`, `logo_dark`) are correct
  - Hard-refresh browser and clear HA’s `__pycache__`

- **Entities not appearing**
  - Remove the integration, delete old devices/entities, then re-add
  - Verify your `/devices` JSON includes the expected keys (`petpass`, `temperature`, etc.)

- **Authentication errors**
  - Double-check your credentials in the Add Integration dialog
  - Inspect HA logs for `Authentication failed` messages

---

With these steps, you’ll have full two-way control over your SmartSlydr devices directly from Home Assistant. Enjoy!

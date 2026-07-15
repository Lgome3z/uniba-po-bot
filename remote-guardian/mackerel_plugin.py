import urllib.request
import json
import time
import sys
import os

def main():
    try:
        # Changed to 127.0.0.1 to avoid background Docker networking confusion
        url = "http://127.0.0.1:8000/sensor-stream"
        req = urllib.request.Request(url)
        
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        
        mic_level = data.get("mic_level_db", 0)
        timestamp = int(time.time())


        if os.environ.get("MACKEREL_PLUGIN_META") == "1":
            meta = {
                "graphs": {
                    "audio_level": {
                        "label": "Audio Level",
                        "unit": "float",
                        "metrics": [
                            {
                                "name": "mic_level",
                                "label": "Mic Level (dB)"
                            }
                        ]
                    }
                }
            }
            print("# mackerel-meta")
            print(json.dumps(meta))
            sys.exit(0)
        
        # Mackerel-agent (v0.12+) automatically prefixes 'custom.' for plugin.metrics.* commands
        print(f"audio_level.mic_level\t{mic_level}\t{timestamp}")

    except Exception as e:
        # If it fails, print the exact error so we can read it in the logs!
        print(f"Plugin Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
    
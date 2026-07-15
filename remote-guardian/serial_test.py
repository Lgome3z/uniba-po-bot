import serial
import time
import binascii

# This is usually the default name for a USB-C device on a Raspberry Pi
# If it fails, we might need to change it to '/dev/ttyUSB0'
USB_PORT = '/dev/ttyACM0' 
BAUD_RATE = 115200

def main():
    print(f"[*] Attempting to open {USB_PORT}...")
    
    try:
        # Open the physical connection
        ser = serial.Serial(USB_PORT, BAUD_RATE, timeout=1)
        print("[+] Successfully connected to the M5Stack!")
        print("[*] Listening for raw bytes... (Press Ctrl+C to stop)\n")
        
        while True:
            # Read whatever is currently in the USB buffer
            if ser.in_waiting > 0:
                raw_bytes = ser.read(ser.in_waiting)
                
                # Convert the unreadable binary into clean Hex (e.g., AA 55 01 DE)
                hex_output = binascii.hexlify(raw_bytes, ' ').decode('utf-8').upper()
                
                print(f"RAW DATA IN: {hex_output}")
                
            time.sleep(0.01) # Tiny sleep to prevent maxing out the Pi's CPU
            
    except serial.SerialException as e:
        print(f"[-] ERROR: Could not open port. Is the M5 plugged in? ({e})")
    except KeyboardInterrupt:
        print("\n[*] Sniffer stopped by user.")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

if __name__ == "__main__":
    main()
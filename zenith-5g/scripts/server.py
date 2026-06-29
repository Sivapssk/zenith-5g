import subprocess
import re

def get_ue_ip(interface="oaitun_ue1"):
    try:
        result = subprocess.run(["ifconfig", interface], capture_output=True, text=True)
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Error: {e}")
    return None

ue_ip = get_ue_ip()

if ue_ip:
    print(f"UE IP Address: {ue_ip}")
    command = f"iperf -s -i 1 -u -B {ue_ip}"
    #command = f"iperf3 -s -i 1 -u -B {ue_ip} --udp-counters-64bit"

    print(f"Running: {command}")
    subprocess.run(command, shell=True)
else:
    print("Could not retrieve UE IP address.")

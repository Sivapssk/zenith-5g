import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import subprocess
import re
import time
import matplotlib.pyplot as plt1
import numpy as np
import csv
import threading
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout, Bidirectional
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error
import tensorflow as tf
import pandas as pd
import os
import random
from matplotlib.gridspec import GridSpec
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from keras.models import load_model
from prometheus_client import start_http_server, Gauge

# === Prometheus metrics for live Grafana visualization ===
# Note on naming: "intent" is the constant target from the NILE intent file.
# "applied_congestion" is the simulated, intentionally-fluctuating tc qdisc ceiling.
# These are NOT the same thing -- don't relabel one as "desired" again.
intent_bandwidth_gauge = Gauge('ztn_intent_bandwidth_kbps', 'Bandwidth threshold extracted from the NILE intent (constant for the whole run)')
applied_congestion_gauge = Gauge('ztn_applied_congestion_kbps', 'Congestion ceiling currently applied via tc qdisc (simulated channel drift, fluctuates by design)')
achieved_throughput_gauge = Gauge('ztn_achieved_throughput_kbps', 'Bandwidth actually achieved, measured by iperf')
jitter_gauge = Gauge('ztn_jitter_ms', 'UDP jitter reported by iperf')
packet_loss_gauge = Gauge('ztn_packet_loss_percent', 'UDP packet loss percent reported by iperf')
test_iteration_gauge = Gauge('ztn_test_iteration', 'Current iteration index in the congestion test loop')
predicted_bandwidth_raw_gauge = Gauge('ztn_predicted_bandwidth_raw_kbps', 'Raw next-bandwidth prediction from the BiLSTM model, before state bucketing')
prediction_error_gauge = Gauge('ztn_prediction_error_kbps', 'BiLSTM predicted value minus the actual next value')
ue_state_gauge = Gauge('ztn_ue_state_kbps', 'Discrete UE state bucket the predicted bandwidth was mapped into')
chosen_action_throughput_gauge = Gauge('ztn_action_throughput_kbps', 'Max throughput delivered by the action the policy table picked for this state')
chosen_action_id_gauge = Gauge('ztn_chosen_action_id', 'Numeric ID of the chosen action, extracted from a_1..a_8')
ping_latency_gauge = Gauge('ztn_ping_latency_ms', 'UE Round-Trip Latency measured via ping (avg of 3 packets) in ms')

# Exposes metrics at http://<this-machine>:8001/metrics as soon as the script starts
start_http_server(8001)

# Function to get the IP address of a specified interface
# def get_ue_ip(interface="oaitun_ue1"):
#     try:
#         result = subprocess.run(["ifconfig", interface], capture_output=True, text=True)
#         match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
#         if match:
#             return match.group(1)
#     except Exception as e:
#         print(f"Error: {e}")
#     return None

# ue_ip = get_ue_ip()

# if ue_ip:
#     print(f"UE IP address is: {ue_ip}")
# else:
#     print("Could not retrieve UE IP address.")

def get_ue_tunnel_ip_from_upf():
    """
    Get UE tunnel IP from OAI UPF logs by parsing the
    PFCP Packet Detection Rule (PDR) table rows.
    """
    import subprocess
    import re

    # Fetch logs from the oai-upf docker container
    result = subprocess.run(
        ["docker", "logs", "oai-upf"],
        capture_output=True, text=True
    )

    # Matches individual table rows containing the 10.0.0.x IP address block at the end
    # e.g., "|0000000000000002|0001|...|10.0.0.3        |"
    matches = re.findall(r"\|\s*(10\.0\.0\.\d+)\s*\|", result.stdout)

    if matches:
        ue_ip = matches[-1]  # Safely grabs the most recent active session IP
        print(f"UE tunnel IP successfully extracted from UPF: {ue_ip}")
        return ue_ip
    else:
        print("Could not find active UE IP in UPF logs. Check if the UE is fully connected.")
        return None

# Automatically fetch the dynamic IP from UPF logs
ue_ip = get_ue_tunnel_ip_from_upf()

# Define IP and other settings
#ue_ip = "10.0.0.2"  # UE IP address
cn5g_ip = "192.168.70.135"  # CN5G DN IP address
upf_ip = "192.168.72.134"  # UPF IP address for congestion generation
duration = 10  # Duration of each test in seconds
# bandwidths = ['300K', '200K','50K', '400K', '100K','550K', '100K','50K','200K', '50K'] #"600K","700K","800K" Bandwidths in Kbps
# bandwidths = [f"{random.randint(50, 550)}K" for _ in range(5)]
poisson_lambda = 1  # Lambda for Poisson distribution

congestion_log = []  # Stores (bandwidth, applied_rate_kbit)

# Function to run iPerf test
def run_iperf_test(bandwidth, mode="downlink"):
    if mode == "downlink":
        command = [
            "docker", "exec", "-it", "oai-ext-dn", "iperf",
            "-u", "-c", ue_ip, "-b", bandwidth, "-t", str(duration), "-i", "1"
        ]
    elif mode == "uplink":
        command = [
            "iperf", "-u", "-c", cn5g_ip, "-b", bandwidth, "-t", str(duration), "-i", "1", "-B", ue_ip
        ]
    else:
        raise ValueError("Invalid mode. Choose 'uplink' or 'downlink'.")

    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(result.stdout)
        return result.stdout
    except Exception as e:
        print(f"Error running iPerf: {e}")
        return ""

# Function to generate congestion traffic at UPF
def generate_congestion_traffic():
    command = [
        "iperf", "-u", "-c", upf_ip, "-b", "900K", "-t", str(duration), "-i", "1"
    ]
    try:
        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print("Congestion traffic to UPF generated.")
    except Exception as e:
        print(f"Error generating congestion traffic: {e}")

# Function to parse bandwidth data from iPerf output
def parse_bandwidth_data(output):
    bandwidth_pattern = r"([\d\.]+)\s*(K|M|G)bits/sec"
    matches = re.findall(bandwidth_pattern, output)
    bandwidth_values = []

    for value, unit in matches:
        if unit == "K":
            bandwidth_values.append(float(value))  # In Kbps
        elif unit == "M":
            bandwidth_values.append(float(value) * 1000)  # Convert Mbps to Kbps
        elif unit == "G":
            bandwidth_values.append(float(value) * 1000000)  # Convert Gbps to Kbps

    return bandwidth_values

# Function to rearrange bandwidths based on Poisson distribution
def rearrange_bandwidths_poisson(bandwidths, poisson_lambda):
    poisson_weights = np.random.poisson(poisson_lambda, len(bandwidths))
    bandwidth_ordered = [x for _, x in sorted(zip(poisson_weights, bandwidths))]
    return bandwidth_ordered

# Function to write combined data to a CSV file
def write_combined_csv(data, filename="combined_data.csv"):
    import pandas as pd
    import os

    # Ensure data is a DataFrame
    if not isinstance(data, pd.DataFrame):
        try:
            data = pd.DataFrame(data)
        except Exception as e:
            raise ValueError("Input data must be a Pandas DataFrame or convertible to one.") from e

    # Save to CSV
    file_path = os.path.join("output", filename)
    os.makedirs("output", exist_ok=True)
    data.to_csv(file_path, index=False)
    print(f"Combined data saved to: {file_path}")

def parse_bandwidth_data_with_prediction(output, model, scaler, sequence_length):
    """
    Parse the iPerf bandwidth data and use the BiLSTM model to predict the bandwidth.

    Parameters:
        output (str): iPerf test output.
        model (keras.Model): Pre-trained BiLSTM model.
        scaler (MinMaxScaler): Scaler used for data preprocessing.
        sequence_length (int): The sequence length used in the BiLSTM model.

    Returns:
        list: A list of predicted bandwidth values.
    """
    # Parse raw bandwidth data from iPerf output
    bandwidth_data = parse_bandwidth_data(output)

    # Scale the parsed bandwidth data
    bandwidth_data = np.array(bandwidth_data).reshape(-1, 1)
    scaled_data = scaler.transform(bandwidth_data)

    # Prepare the input sequences for the BiLSTM model
    X = []
    for i in range(len(scaled_data) - sequence_length + 1):
        X.append(scaled_data[i:i + sequence_length])
    X = np.array(X)
    X = X.reshape(X.shape[0], X.shape[1], 1)  # Add the third dimension

    # Predict bandwidth using the BiLSTM model
    predicted_scaled = model.predict(X, verbose=0)

    # Inverse transform the predictions to get actual bandwidth values
    predicted_bandwidth = scaler.inverse_transform(predicted_scaled)

    return predicted_bandwidth.flatten().tolist()


def extract_achieved_bandwidth(iperf_output):
    bandwidths = re.findall(r"(\d+\.?\d*)\s*Kbits/sec", iperf_output)

    # If there are no bandwidth values found, return 0 (default)
    if not bandwidths:
        return 0

    # Convert the extracted values to floats and calculate the average
    bandwidths = [float(bandwidth) for bandwidth in bandwidths]
    average_bandwidth = sum(bandwidths) / len(bandwidths)

    return average_bandwidth



# Function to map the values
def map_values(value):
    if value <=55:
        return 50
    elif 56 <= value <= 100:
        return 100
    elif 101 <= value <= 240:
        return 200
    elif 241 <= value <= 340:
        return 300
    elif 341 <= value <= 350:
        return 350
    elif 351 <= value <= 400:
        return 400
    elif 401 <= value <= 420:
        return 420
    elif 421 <= value <= 450:
        return 450
    elif 451 <= value <= 480:
        return 480
    elif 481 <= value <= 490:
        return 490
    elif 491 <= value <= 550:
        return 500
    elif 550 <= value <= 650:
        return 600
    else:
        return value

def load_bandwidth_values(filename):
    values = []
    with open(filename, newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0].isdigit():
                values.append(int(row[0]))
    return values

def parse_output(output):
    matches = re.findall(r"([\d.]+)\s*(K|M|G)bits/sec", output)
    results = []
    for val, unit in matches:
        val = float(val)
        if unit == "M":
            val *= 1000
        elif unit == "G":
            val *= 1_000_000
        results.append(val)
    return results

def run_iperf(bw):
    cmd = [
        "docker", "exec", "oai-ext-dn", "iperf",
        "-u", "-c", ue_ip, "-b", bw, "-t", "10", "-i", "1"
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout

def write_conditional_csv(data, filename="single_result_view.csv"):
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Timeslot", "Bandwidth", "Measured Throughput (Kbps)", "Status", "Applied Congestion (if any)"])

        for i, row in enumerate(data, start=1):
            writer.writerow([i] + list(row))

# Function to write combined data to a CSV file
def write_combined_csv(data, filename="combined_data.csv"):
    import pandas as pd
    import os

    # Ensure data is a DataFrame
    if not isinstance(data, pd.DataFrame):
        try:
            data = pd.DataFrame(data)
        except Exception as e:
            raise ValueError("Input data must be a Pandas DataFrame or convertible to one.") from e

    # Save to CSV
    file_path = os.path.join("output", filename)
    os.makedirs("output", exist_ok=True)
    data.to_csv(file_path, index=False)
    print(f"Combined data saved to: {file_path}")


def measure_ping_latency(ip):
    """
    Ping the UE IP 3 times, parse each reply time,
    return average RTT in ms. Same approach as exporter_4.py.
    """
    try:
        output = subprocess.check_output(
            ["ping", "-c", "3", ip],
            timeout=10
        ).decode()
        rtts = []
        for line in output.splitlines():
            if "time=" in line:
                rtt_str = line.split("time=")[1].split()[0]
                rtts.append(float(rtt_str))
        if rtts:
            avg_rtt = sum(rtts) / len(rtts)
            print(f"[PING] {ip} -> RTTs: {rtts} -> avg: {avg_rtt:.3f} ms")
            return avg_rtt
        print("[PING] No RTT values found in ping output.")
        return None
    except subprocess.TimeoutExpired:
        print(f"[PING] Timeout pinging {ip}")
        return None
    except Exception as e:
        print(f"[PING] Error: {e}")
        return None

def main():

    # import pandas as pd
    # from transformers import AutoTokenizer, AutoModel
    # import torch
    # from sklearn.metrics.pairwise import cosine_similarity
    # import openai


    # # Set your OpenAI API key
    

    # # Load pre-trained model and tokenizer
    # model_name = "bert-base-uncased"  # You can replace this with any other pre-trained model
    # tokenizer = AutoTokenizer.from_pretrained(model_name)
    # model = AutoModel.from_pretrained(model_name)

    # # Load input sentences from Excel file
    # def load_sentences_and_values_from_excel(file_path):
    #     df = pd.read_excel(file_path)
    #     sentences = df.iloc[:, 0].tolist()
    #     values = df.iloc[:, 1].tolist()
    #     return sentences, values

    # # Function to encode sentences
    # def encode_sentences(sentences):
    #     inputs = tokenizer(sentences, padding=True, truncation=True, return_tensors="pt")
    #     with torch.no_grad():
    #         outputs = model(**inputs)
    #     # Extract the embeddings for the [CLS] token
    #     embeddings = outputs.last_hidden_state[:, 0, :]
    #     return embeddings

    # # Function to find similar sentences
    # def find_similar_sentences(new_sentence, sentences, values, k=2):
    #     # Encode the new sentence
    #     new_embedding = encode_sentences([new_sentence])

    #     # Encode the input sentences
    #     sentence_embeddings = encode_sentences(sentences)

    #     # Compute cosine similarities with all other sentences
    #     similarities = cosine_similarity(new_embedding, sentence_embeddings)

    #     # Get indices of top k similar sentences
    #     top_indices = similarities.argsort(axis=1)[0][-k:][::-1]

    #     # Retrieve top k similar sentences and their corresponding values
    #     similar_sentences = [sentences[i] for i in top_indices]
    #     similar_values = [values[i] for i in top_indices]

    #     return similar_sentences, similar_values

    # # Example usage
    # file_path = "Intent-NILE-1000.xlsx"  # Replace with the path to your Excel file
    # sentences, values = load_sentences_and_values_from_excel(file_path)

    # #new_sentence = "When a total of 18 gigabytes of data is reached, the individual's bandwidth rate limit is set to 2 Mbps from 5pm to 8am."
    # #new_sentence = "Lumi, block traffic to be used for guests between 8 AM and 6 PM."
    # new_sentence = input("Enter your intent sentence: ")

    # # print("You entered:", new_sentence)
    # similar_sentences, similar_values = find_similar_sentences(new_sentence, sentences, values, k=4)

    # #print(similar_sentences, similar_values)

    # meta_prompt = "Our objective is to do intent to NILE translation. Consider the first and second retrieved Input: intent Output: NILE pair as reference, but take the third and fourth Input: intent Output: NILE pair as the actual context for the given query."

    # # Construct the messages for GPT-4-turbo
    # messages = [
    #     {"role": "user", "content": meta_prompt},

    #     {"role": "user", "content": f"Input: {similar_sentences[2]} Output: {similar_values[2]};"},
    #     {"role": "user", "content": f"Input: {similar_sentences[3]} Output: {similar_values[3]};"},

    #     {"role": "user", "content": f"Input: {similar_sentences[0]} Output: {similar_values[0]};"},
    #     {"role": "user", "content": f"Input: {similar_sentences[1]} Output: {similar_values[1]};"},

    #     {"role": "user", "content": f"Input: {new_sentence}"}
    # ]

    # # Make API call to GPT-4-turbo model
    # response = openai.ChatCompletion.create(
    #     model="gpt-4-turbo",  # Choose the model you want to use
    #     messages=messages,
    #     max_tokens=1000  # Adjust as needed
    # )

    # # Check if the request was successful
    # if response["choices"]:
    #     gpt_output = response["choices"][0]["message"]["content"]
    #     print("Generated text from GPT-4-turbo model:", gpt_output)
    #     # Continue with your code, using the generated text as needed
    # else:
    #     print("Error:", response.get("error", {}).get("message", "Unknown error"))

    # Load the pre-trained BiLSTM model
    model = load_model('user_count_predictor_one_fewshot_sequence_oai_testbed_data_with_UPF_congestion.h5') #'user_count_predictor_one_fewshot_sequence.h5')
    # bilstm_model = load_model('user_count_predictor_with_lambda_one_testbed.h5')
    # Initialize the scaler used during training
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaler.fit(np.array([0, 1000]).reshape(-1, 1))  # Example: Scaling bandwidth in Mbps (adjust as needed)

    class MemoryModule:
        def __init__(self):
            self.memory = {}  # Dictionary to store key-value pairs

        def get(self, key):
            return self.memory.get(tuple(key), None)

        def update(self, key, value):
            self.memory[tuple(key)] = value

    # Initialize memory module
    memory = MemoryModule()


    # Sequence length used in the BiLSTM model
    sequence_length = 5

    combined_data = []  # To hold all test data for combined CSV
    current_time = 1  # Start time at 1 and increase it continuously

    results = []

    bandwidths = []

        # === Step 1: Load Nile Intents from File ===
    def load_nile_intent_from_file(filepath):
        with open(filepath, "r", encoding="utf-8") as file:
            return file.read()

    nile_intent_in = load_nile_intent_from_file("generated_output_ADM.txt")

    # === Step 2: Extract Bandwidth Threshold from Intent ===
    import re

    def extract_bandwidth_from_nile(intent_str):
        match = re.search(r"set bandwidth\('max', '(\d+)', 'kbps'\)", intent_str)
        return int(match.group(1)) if match else 300  # fallback to 300 if not found

    # Extract threshold (e.g., 200 or 400)
    ibn_bandwidth_threshold = extract_bandwidth_from_nile(nile_intent_in)
    print(f"Extracted Bandwidth Threshold from Nile Intent: {ibn_bandwidth_threshold} Kbps")

    # === Live metrics update (Prometheus): this is the ONE value that should plot as a flat line ===
    intent_bandwidth_gauge.set(ibn_bandwidth_threshold)

    # === Step 3: Use Extracted Bandwidth Dynamically ===
    bandwidths = [f"{bw}K" for bw in random.choices([ibn_bandwidth_threshold], k=10)]
    print (bandwidths)


    # for _ in range(40):
    #     if random.random() < 0.25:  # 25% chance to be 50K
    #         bandwidths.append("50K")
    #     else:
    #         bandwidths.append(f"{random.randint(51, 550)}K")
    # print(bandwidths)
    # bandwidths = [f"{bw}K" for bw in random.choices([600], k=10)]
    # bandwidths = [f"{random.randint(50, 550)}K" for _ in range(40)]
    #bandwidths = ['300K', '200K','100K', '400K'] #, '100K','550K', '100K'] #"600K","700K","800K" Bandwidths in Kbps
    poisson_lambda = 1  # Lambda for Poisson distribution
    congestion_log = []  # Stores (bandwidth, applied_rate_kbit)
    values = []
    # === Step 3: Dynamically name the CSV based on threshold ===
    csv_filename = f"poisson_distribution_with_drift_value_{ibn_bandwidth_threshold}.csv"
    bandwidth_values = load_bandwidth_values(csv_filename)
    bandwidth_index = 0
    print(bandwidth_values)

    for idx, bw in enumerate(bandwidths):
        bw_str = f"{bw}K" if isinstance(bw, int) else bw
        print(f"[{idx+1}/{len(bandwidths)}] Testing {bw_str} with congestion...")

        if bandwidth_index >= len(bandwidth_values):
            print("⚠️ All congestion values from CSV have been used.")
            break

        rate_kbit = bandwidth_values[bandwidth_index]
        burst_kbit = 38
        latency_ms = 300

        print(f"   → [Congestion] Applying rate: {rate_kbit}kbit, burst: {burst_kbit}kbit")
        congestion_log.append((bw, rate_kbit))

        # Delete existing qdisc
        subprocess.run([
            "docker", "exec", "oai-upf", "tc", "qdisc", "del", "dev", "eth0", "root"
        ], capture_output=True, text=True)

        # Apply congestion using `tc`
        result = subprocess.run([
            "docker", "exec", "oai-upf", "tc", "qdisc", "add", "dev", "eth0", "root",
            "tbf", "rate", f"{rate_kbit}kbit",
            "burst", f"{burst_kbit}kbit",
            "latency", f"{latency_ms}ms"
        ], capture_output=True, text=True)

        bandwidth_index += 1  # ✅ Move to next CSV value only after applying

        if result.returncode != 0:
            print(f"❌ Error adding qdisc: {result.stderr}")
        else:
            print(f"✅ Qdisc added: {result.stdout.strip() or 'Success'}")
        applied_cong_bw = bandwidth_values[bandwidth_index - 1] if bandwidth_index > 0 else None
        # iperf test
        output = run_iperf(bw_str)
        print("Running iperf command")
        print(output)
        parsed = parse_output(output)
        val = np.mean(parsed) if parsed else 0.0
        # print(f"   → With congestion: {val:.2f}")

        # === Live metrics update (Prometheus) ===
        # Use the server-side summary line for achieved bandwidth — this is what the UE actually
        # received (accounts for packet loss), unlike the client-side per-second mean in val.
        # Example server summary: "0.0000-10.36 sec  708 KBytes  558 Kbits/sec  2.92 ms  33/526 (6.3%)"
        server_report_match = re.search(
            r"0\.0000-[\d.]+\s+sec\s+[\d.]+\s+KBytes\s+([\d.]+)\s+Kbits/sec"
            r"\s+([\d.]+)\s+ms\s+(\d+)/(\d+)\s*\(([\d.]+)%\)",
            output
        )
        applied_congestion_gauge.set(rate_kbit)
        test_iteration_gauge.set(idx + 1)

        if server_report_match:
            server_bw    = float(server_report_match.group(1))
            jitter_val   = float(server_report_match.group(2))
            lost_count   = int(server_report_match.group(3))
            total_count  = int(server_report_match.group(4))
            loss_pct     = float(server_report_match.group(5))
            achieved_throughput_gauge.set(server_bw)
            # Sanity-check: skip corrupted/garbage server reports ("-nan" lines have total=0)
            if total_count > 0 and lost_count <= total_count:
                jitter_gauge.set(jitter_val)
                packet_loss_gauge.set(loss_pct)
        else:
            # Fallback to client-side average if server report line not found
            achieved_throughput_gauge.set(val)
            jitter_loss_match = re.search(r"([\d.]+)\s*ms\s+(\d+)/(\d+)\s*\(([\d.]+)%\)", output)
            if jitter_loss_match:
                lost_count  = int(jitter_loss_match.group(2))
                total_count = int(jitter_loss_match.group(3))
                if total_count > 0 and lost_count <= total_count:
                    jitter_gauge.set(float(jitter_loss_match.group(1)))
                    packet_loss_gauge.set(float(jitter_loss_match.group(4)))

        # remove_congestion()
        status = "With Congestion"

        # === Ping latency: once per iperf round, same as exporter_4.py ===
        if ue_ip:
            latency_ms = measure_ping_latency(ue_ip)
            if latency_ms is not None:
                ping_latency_gauge.set(latency_ms)
                print(f"[LATENCY] Exported ping latency: {latency_ms:.3f} ms")
            else:
                print("[LATENCY] Ping failed, gauge not updated this round.")
        else:
            print("[LATENCY] ue_ip not available, skipping ping.")

        results.append((bw_str, val, status, applied_cong_bw))

    write_conditional_csv(results, filename="throughput_single_result_optimal_600.csv")
    # Prepare data for BiLSTM
     # Prepare data for BiLSTM
    raw_bandwidth_values = [row[1] for row in results] #combined_data]
    # bandwidth_values = [int(x) for x in raw_bandwidth_values]
    print('BW_values:', raw_bandwidth_values)
    bandwidth_values = [int(x) for x in raw_bandwidth_values]

    predicted_states = []
    actual_states = []
    test_sequence = bandwidth_values
    for i in range(len(test_sequence) - sequence_length):
            # Create input sequence
            new_sequence = np.array(test_sequence[i:i + sequence_length]).reshape(-1, 1)
            new_sequence_scaled = scaler.transform(new_sequence)
            new_sequence_key = tuple(new_sequence_scaled.flatten())  # Tuple key for memory lookup

            # Check if sequence exists in memory
            memory_output = memory.get(new_sequence_key)
            if memory_output is not None:
                predicted_state = scaler.inverse_transform([[memory_output]])[0][0]
            else:
                # Predict using LSTM
                new_sequence_scaled = new_sequence_scaled.reshape(1, sequence_length, 1)
                predicted_scaled = model.predict(new_sequence_scaled)
                predicted_state = scaler.inverse_transform(predicted_scaled)[0][0]

                # Store in memory (Update only if incorrect)
                actual_next_state = test_sequence[i + sequence_length]
                if abs(predicted_state - actual_next_state) > 5:  # Threshold for incorrect prediction
                    memory.update(new_sequence_key, scaler.transform([[actual_next_state]])[0][0])
                    predicted_state = actual_next_state  # Use correct value after updating memory

            predicted_states.append(predicted_state)
            actual_states.append(test_sequence[i + sequence_length])

            # === Live metrics update (Prometheus) ===
            actual_next_value = test_sequence[i + sequence_length]
            predicted_bandwidth_raw_gauge.set(float(predicted_state))
            prediction_error_gauge.set(float(predicted_state) - float(actual_next_value))
            time.sleep(0.5)  # optional: this loop runs in milliseconds otherwise, too fast for a 5s scrape to catch each step

    print("Predicted:", predicted_states)
    print("Actual:", actual_states)

    final_predictions = predicted_states #.flatten()

    predicted_bandwidths = final_predictions #[250, 350, 600]  # Example predictions
    print (predicted_bandwidths)
    int_list_rounded = [round(x) for x in predicted_bandwidths]
    print(int_list_rounded)
    # Apply the mapping to the list
    mapped_list = [map_values(x) for x in int_list_rounded]
    predicted_ue_states = mapped_list #[user_count_to_ue_state[count] for count in mapped_list]
    print("predicted states", predicted_ue_states)
    print(len(predicted_ue_states))


    actual_counts = actual_states
    # Convert to integer list by rounding the values
    int_list_rounded_actual = [round(x) for x in actual_counts]
    actual_ue_states = int_list_rounded_actual# [user_count_to_ue_state[count] for count in actual_user_counts]

    # Apply the mapping to the list
    mapped_list = [map_values(x) for x in int_list_rounded_actual]
    actual_ue_states = mapped_list #[user_count_to_ue_state[count] for count in mapped_list]
    print("actual states",actual_ue_states)
    print(len(actual_ue_states))

    states = actual_ue_states
    states = [str(count) for count in states]
    print("actual_states:",states)

    states = actual_ue_states
    states = [str(count) for count in states]
    print("actual_states:",states)

    csv_file = "best_actions_output_100.csv"
    states_L = predicted_ue_states
    states_L = [str(count) for count in states_L]
    actions = [ '$a_1$', '$a_2$', '$a_3$', '$a_4$', '$a_5$', '$a_6$', '$a_7$', '$a_8$']
    print("predicted_states:",states_L)

    # expected_throughput = {
    # '500': 500, '400': 400, '450': 450, '480': 480, '350': 350,
    # '300': 300, '200': 200, '490': 490, '420': 420, '100': 100, '50': 50
    # }

    # Obtained throughput for each action in each state
    obtained_throughput = {
        '500': [497, 500, 340, 180, 162, 155, 370, 300],
        '400': [145, 148, 400, 359, 227, 345, 250, 200],
        '450': [365, 315, 172, 169, 170, 170, 450, 376],
        '480': [480, 475, 210, 46.4, 272, 131.4, 400, 390],
        '350': [131, 114, 72, 122, 105, 43, 146, 350],
        '300': [200, 188, 300, 165, 170, 164, 202, 170],
        '200': [160, 200, 102, 120, 132, 120, 100, 180],
        '490': [490, 43, 193, 71.8, 18.4, 25.6, 360, 215],
        '420': [385, 367, 420, 52, 150, 53, 378, 273],
        '100': [87, 50, 10, 60,100, 47, 23, 98]
    }

    # Function to load best actions from a CSV file
    def load_best_actions(filename, state):
        action_map = {}
        throughput_values = []

        if os.path.exists(filename):
            with open(filename, mode='r') as file:
                reader = csv.reader(file)
                next(reader)  # Skip header
                for row in reader:
                    file_state, best_action, max_throughput = row[0], row[1], float(row[2])
                    if file_state == state:
                        throughput_values.append(max_throughput)
                        action_map[max_throughput] = best_action

        return throughput_values, action_map

    # Load best actions for states in states_L that are missing from obtained_throughput
    action_maps = {}
    for state in states_L:
        if state in obtained_throughput:
            obtained_throughput[state], action_maps[state] = load_best_actions(f'best_actions_output_default.csv', state)

    # Handle special cases: State 100 and State 50 (Suboptimal)
    if '100' in states_L:
        obtained_throughput['100'], action_map_100 = load_best_actions('best_actions_output_100.csv', '100')

    if '50' in states_L:
        obtained_throughput['50'], action_map_50 = load_best_actions('best_actions_output_50.csv', '50')
    if '600' in states_L:
        obtained_throughput['600'], action_map_600 = load_best_actions('best_actions_output_ADM.csv', '600')

    state_50_count = 0
    state_600_count = 0

    # Variables for storing results
    predicted_bandwidth = []
    state_list = []
    action_list = []
    action_name_list = []
    timestamps = list(range(1, len(states_L) + 1))

    # Find the best action for each state
    for state in states_L:
        if state in obtained_throughput and obtained_throughput[state]:
            if state == '100':  # Handle state 100
                max_throughput = obtained_throughput['100'][0]
                best_action = action_map_100.get(max_throughput, 'Unknown')
            elif state == '50':  # Handle state 50 with special logic
                state_50_count += 1
                if state_50_count == 1:  # First occurrence
                    obtained_throughput['50'], action_map_50 = load_best_actions('best_actions_output_50.csv', '50')
                    max_throughput = obtained_throughput['50'][0]
                    best_action = action_map_50.get(max_throughput, 'Unknown')
                else:  # Subsequent occurrences use fixed values
                    max_throughput = 50
                    best_action = '$a_7$'
            elif state == '600':  # Handle state 50 with special logic
                state_600_count += 1
                if state_600_count == 1:  # First occurrence
                    obtained_throughput['600'], action_map_600 = load_best_actions('best_actions_output_ADM.csv', '600')
                    max_throughput = obtained_throughput['600'][0]
                    best_action = action_map_600.get(max_throughput, 'Unknown')
                else:  # Subsequent occurrences use fixed values
                    max_throughput = 600
                    best_action = '$a_5$'
    #             elif state == '50':  # Handle suboptimal state 50
#                 max_throughput = obtained_throughput['50'][0]
#                 best_action = action_map_50.get(max_throughput, 'Unknown')
            else:  # Handle default states
                max_throughput = max(obtained_throughput[state])
                best_action = actions[obtained_throughput[state].index(max_throughput)]

            # Store results
            predicted_bandwidth.append(int(state))
            state_list.append(int(state))
            action_list.append(max_throughput)
            action_name_list.append(best_action)

            # === Live metrics update (Prometheus) ===
            ue_state_gauge.set(int(state))
            chosen_action_throughput_gauge.set(max_throughput)
            action_id_match = re.search(r"a_(\d+)", str(best_action))
            if action_id_match:
                chosen_action_id_gauge.set(int(action_id_match.group(1)))
            time.sleep(1)  # optional: gives Grafana a visible step per state during the demo; remove if not needed
        else:
            print(f"Warning: No throughput data found for state {state}. Skipping.")

    # Print results
    print(f"Predicted Bandwidth: {predicted_bandwidth}Kbps")
    print(f"State: {state_list}")
    print(f"Action: {action_list}")

    throughputs_plot_L = action_list


    # Write results to CSV file
    csv_filename = "throughput_actions_optimal_RL_600.csv"
    # with open(csv_filename, mode='w', newline='') as file:
    #     writer = csv.writer(file)
    #     writer.writerow(["State", "Predicted Bandwidth (Kbps)", "Max Throughput", "Best Action"])
    #     for state, bandwidth, throughput, action in zip(state_list, predicted_bandwidth, action_list, action_name_list):
    #         writer.writerow([state, bandwidth, throughput, action])
    # Assume all your lists are of equal length
    num_rows = len(state_list)

    with open(csv_filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        # Add "Time" as the first column header
        writer.writerow(["Time", "State", "Predicted Bandwidth (Kbps)", "Max Throughput", "Best Action"])

        # Enumerate to create time sequence starting from 1
        for t, (state, bandwidth, throughput, action) in enumerate(zip(state_list, predicted_bandwidth, action_list, action_name_list), start=1):
            writer.writerow([t, state, bandwidth, throughput, action])
    print(f"CSV file '{csv_filename}' has been created successfully.")

# Plotting
#     plt.figure(figsize=(8, 5))
#     # Plot the desired vs achieved bandwidth
#     plt.plot(timestamps, predicted_bandwidth, label='Desired Bandwidth', marker='o', color='blue', linewidth=4.5,linestyle='-')
#     plt.plot(timestamps, action_list, label='Achieved Bandwidth', marker='s', color='red',linewidth=3.5, linestyle='--')
# #plt.plot(df['Timestamp'], df['Predicted Bandwidth (Kbps)'], label='Desired Bandwidth', marker='o', linestyle='--')
#     #plt.plot(df['Timestamp'], df['Max Throughput'], label='Achieved Bandwidth', marker='s', linestyle='-')
#     # Annotate actions on the graph
#     for i, action in enumerate(action_name_list):
#         plt.text(timestamps[i], action_list[i], action, fontsize=12, ha='right', va='bottom')

# # Labels and title
#     # plt.xlabel("Timestamp (s)", fontsize=16)
#     # plt.ylabel("Bandwidth (Kbps)", fontsize=16)
#     # plt.title("Desired vs Achieved Bandwidth", fontsize=18)
#     # plt.legend()
#     # plt.grid(True)
#     # plt.savefig('ztn_digitwin_optimal_RL_output_600.png')

#     plt.xlabel("Timestamp (s)", fontsize=16)
#     plt.ylabel("Bandwidth (Kbps)", fontsize=16)
#     plt.title("Optimal Case", fontsize=18)
#     plt.legend()
#     plt.grid(True)
#     plt.savefig('ztn_digitwin_optimal_output_600.png')


    #import matplotlib.pyplot as plt

# Initialize real-time plot
    plt.ion()  # Turn on interactive mode
    fig, ax = plt.subplots(figsize=(10, 6))

# Initial empty plot elements
    line1, = ax.plot([], [], label='Desired Bandwidth', marker='o', color='blue', linewidth=4.5, linestyle='-')
    line2, = ax.plot([], [], label='Achieved Bandwidth', marker='s', color='red', linewidth=3.5, linestyle='--')
     # Annotate actions on the graph
    for i, action in enumerate(action_name_list):
        plt.text(timestamps[i], action_list[i], action, fontsize=12, ha='right', va='bottom')
    annotations = []

    state_50_seen = 0

# Set axis labels, grid, and legend
    ax.set_xlabel("Timestamp (s)", fontsize=16)
    ax.set_ylabel("Bandwidth (Kbps)", fontsize=16)
    ax.set_title("Desired vs Achieved Bandwidth (Real-Time)", fontsize=18)
    ax.grid(True)
    ax.legend(loc='upper right')

    # Add big title
    fig.suptitle("Adaptive Decision Making", fontsize=18, fontweight='bold', y=0.98)
    fig.tight_layout(rect=[0, 0.03, 1, 0.95])  # adjust layout to make space for title
# Real-time update loop
    timestamps = []
    desired = []
    achieved = []
    for i, (state, bandwidth, throughput, action) in enumerate(zip(state_list, predicted_bandwidth, action_list, action_name_list), start=1):
        timestamps.append(i)
        desired.append(bandwidth)
        achieved.append(throughput)

        line1.set_data(timestamps, desired)
        line2.set_data(timestamps, achieved)
        ax.set_xlim(0, max(timestamps)+1)
        ax.set_ylim(400, max(desired + achieved) + 100)

    # Clear previous annotations
        for a in annotations:
            a.remove()
        annotations.clear()

        # Annotate with action
        text = action
        if str(state) == "600":
        # Special annotation logic for state 50
           if str(state) == "600":
               state_600_count += 1
               if state_600_count == 1:
                   text += "\n(sub-optimal action - first time)"
                   state_600_seen = True
               else:
                   text += "\n(optimal action - next time)"

        ann = ax.text(i, throughput + 10, text, fontsize=11, ha='right', va='bottom', bbox=dict(boxstyle="round,pad=0.3", edgecolor='black', facecolor='lightyellow'))
        annotations.append(ann)

    #plt.pause(0.5)  # Real-time pause

    # Add updated annotation
        #annotation = ax.text(i, throughput, action, fontsize=12, ha='right', va='bottom')
        #annotations.append(annotation)

        plt.pause(0.5)  # Pause to update the figure in real-time

# Save final figure
    #plt.savefig('ztn_digitwin_optimal_RL_output_50.png')
    #print("Plot saved as 'ztn_digitwin_optimal_RL_output_50.png'.")

# Keep the final plot displayed
    plt.ioff()
    plt.show()

    # Create new figure for final saved plot
    fig_final, ax_final = plt.subplots(figsize=(10, 6))

    # Plot the full data
    ax_final.plot(timestamps, desired, label='Desired Bandwidth', marker='o', color='blue', linewidth=4.5, linestyle='-')
    ax_final.plot(timestamps, achieved, label='Achieved Bandwidth', marker='s', color='red', linewidth=3.5, linestyle='--')

    # Add all annotations again
    state_600_count = 0
    for i, (state, throughput, action) in enumerate(zip(state_list, action_list, action_name_list), start=1):
        annotation_text = action
        if str(state) == "600":
            state_600_count += 1
            if state_600_count == 1:
                annotation_text += "\n(sub-optimal action - first time)"
            else:
                annotation_text += "\n(optimal action - next time)"
        ax_final.text(i, achieved[i-1] + 15, annotation_text, fontsize=11, ha='right', va='bottom',
                  bbox=dict(boxstyle="round,pad=0.3", edgecolor='black', facecolor='lightyellow'))
    # Labels, title, and formatting
    ax_final.set_xlabel("Timestamp (s)")
    ax_final.set_ylabel("Bandwidth (Kbps)")
    ax_final.set_xlim(0, max(timestamps) + 1)
    ax_final.set_ylim(400, max(desired + achieved) + 100)
    ax_final.grid(True)
    ax_final.legend(loc='upper right')

    # Title with layout space
    fig_final.suptitle("Adaptive Decision Making", fontsize=20, fontweight='bold', y=0.98)
    fig_final.tight_layout(rect=[0, 0.03, 1, 0.95])

    # Save final annotated plot
    fig_final.savefig('ztn_digitwin_optimal_RL_output_ADM.png')
    print("Final plot saved with annotations as 'ztn_digitwin_optimal_RL_output_ADM.png'")


        # # === Step 1: Load CSV ===
    # filename = "/home/ubuntu/oai-cn5g/throughput_actions_optimal_RL_100.csv"
    # df = pd.read_csv(filename)


    # # === Step 2: Convert data to numeric ===
    # df['Time'] = pd.to_numeric(df['Time'], errors='coerce')
    # df['Predicted Bandwidth (Kbps)'] = pd.to_numeric(df['Predicted Bandwidth (Kbps)'], errors='coerce')
    # df['Max Throughput'] = pd.to_numeric(df['Max Throughput'], errors='coerce')

    # # === Step 3: Plot the Optimal Throughput vs Time ===
    # plt.figure(figsize=(10, 5))
    # plt.plot(df['Time'], df['Max Throughput'], color='purple', marker='s', label='Optimal Throughput')

    # # Bandwidth threshold (you can modify as needed)
    # bw_threshold = 500  # In-Distribution Threshold (Kbps)
    # plt.axhline(y=bw_threshold, color='red', linestyle='--', label=f'Threshold = {bw_threshold} Kbps')

    # # === Step 4: Customize Plot ===
    # plt.title(f"In-Distribution ({bw_threshold} Kbps)", fontsize=13, fontweight='bold')
    # plt.xlabel('Time (s)')
    # plt.ylabel('Throughput (Kbps)')
    # plt.grid(True)
    # plt.legend()
    # plt.tight_layout()

    # # === Step 5: Save and Show Plot ===
    # plt.savefig("Optimal_Throughput_vs_Time_50.png")
    # # plt.savefig("Optimal_Throughput_vs_Time.eps", format='eps')
    # plt.show()

    # # === Step 2: Process CSV ===
    # def process_csv(filename, label_suffix):
    #     df = pd.read_csv(filename)
    #     df['Time Step (UE State)'] = pd.to_numeric(df['Time Step (UE State)'], errors='coerce')
    #     df['Throughput-LSTM-predicted'] = pd.to_numeric(df['Throughput-LSTM-predicted'], errors='coerce')
    #     df['Group'] = (df['Time Step (UE State)'] - 1) // 1
    #     grouped = df.groupby('Group')[['Throughput-LSTM-predicted']].mean().reset_index()
    #     grouped.columns = ['Group', f'Avg_Throughput-LSTM_{label_suffix}']
    #     return grouped

    # # === Step 3: Load both episodes ===
    # g1 = process_csv('best_action_throughput_data_predicted_episode_15.csv', 'Ep15')
    # g2 = process_csv('best_action_throughput_data_predicted_episode_39.csv', 'Ep39')
    # grouped_avg = pd.merge(g1, g2, on='Group', how='outer').sort_values(by='Group').reset_index(drop=True)

    # # === Step 4: Match Analysis and Plotting ===
    # match_stats = []

    # for label, bw_threshold in thresholds.items():
    #     df = grouped_avg.copy()

    #     df['Ep15_LSTM_Exceed'] = df['Avg_Throughput-LSTM_Ep15'] >= bw_threshold
    #     df['Ep39_LSTM_Exceed'] = df['Avg_Throughput-LSTM_Ep39'] >= bw_threshold

    #     total_groups = len(df)
    #     match15 = df['Ep15_LSTM_Exceed'].sum()
    #     match39 = df['Ep39_LSTM_Exceed'].sum()
    #     percent15 = (match15 / total_groups) * 100
    #     percent39 = (match39 / total_groups) * 100

    #     print(f"\n=== {label} | Threshold = {bw_threshold} Kbps ===")
    #     print(df.to_string(index=False))
    #     print(f"[Episode 15] Sub-Optimal matched in {match15}/{total_groups} groups = {percent15:.2f}%")
    #     print(f"[Episode 39] Optimal matched in {match39}/{total_groups} groups = {percent39:.2f}%")

    #     match_stats.append({
    #         "Intent": label,
    #         "Ep15_Match": percent15,
    #         "Ep15_NoMatch": 100 - percent15,
    #         "Ep39_Match": percent39,
    #         "Ep39_NoMatch": 100 - percent39
    #     })

    #     # === Save group-wise data for this threshold ===
    #     df_filename = f"Throughput_Group_Comparison_{label.replace(' ', '_')}.csv"
    #     df.to_csv(df_filename, index=False)

    #     # === Plot and Save Individual Figure ===
    #     fig, ax = plt.subplots(figsize=(10, 5))
    #     group_x = df['Group'].to_numpy()

    #     ax.plot(group_x, df['Avg_Throughput-LSTM_Ep15'].to_numpy(), color='green', marker='o', label='Sub-Optimal')
    #     ax.plot(group_x, df['Avg_Throughput-LSTM_Ep39'].to_numpy(), color='purple', marker='s', label='Optimal')
    #     ax.axhline(y=bw_threshold, color='red', linestyle='--', label=f'Threshold = {bw_threshold} Kbps')


    #     distribution_type = label.split('(')[0].strip()
    #     ax.set_title(f"{distribution_type} ({bw_threshold} Kbps)", fontsize=13, fontweight='bold')

    #     # ax.set_title(f"{label} (Threshold = {bw_threshold} Kbps)")
    #     ax.set_xlabel('Time (s)')
    #     ax.set_ylabel('Throughput (Kbps)')
    #     ax.grid(True)
    #     ax.legend()

    #     fig.tight_layout()
    #     fig.savefig(f"{label.replace(' ', '_')}_Comparison.png")
    #     fig.savefig(f"{label.replace(' ', '_')}_Comparison.eps", format='eps')
    #     plt.show()

    # # === Save Match Summary ===
    # match_stats_df = pd.DataFrame(match_stats)
    # match_stats_df.to_csv("match_statistics.csv", index=False)
    # print("\nMatch statistics saved to 'match_statistics.csv'")
# Show the plot
#plt.show()

    # Process each bandwidth in the list
    for bandwidth_value in throughputs_plot_L:
        if isinstance(bandwidth_value, (int, float)) or (isinstance(bandwidth_value, str) and bandwidth_value.isdigit()):
            bandwidth_str = f"{bandwidth_value}K"
            try:
                iperf_output = run_iperf_test(bandwidth_str)
                achieved_bandwidth = extract_achieved_bandwidth(iperf_output)
                print(f"Bandwidth Value: {bandwidth_str} -> Achieved Bandwidth: {achieved_bandwidth}")
            except Exception as e:
                print(f"Error running iPerf for bandwidth {bandwidth_str}: {e}")
        else:
            print(f"Skipping invalid bandwidth value: {bandwidth_value}")

if __name__ == "__main__":
    main()

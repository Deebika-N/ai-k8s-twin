import os
import json
from groq import Groq

# Connect to Groq
client = Groq(api_key=os.environ["GROQ_API_KEY"])

# User/application input
user_input = input("""
Enter your application requirements.

Example:
Application: paymentservice
Expected traffic: 600 requests/second
Expected network latency: below 100ms
Application type: microservice

Input:
""")

prompt = f"""
You are a Kubernetes environment configuration expert.

Based on the following application requirements:

{user_input}

Generate a realistic Kubernetes simulation environment.

Return ONLY valid JSON.
Do not include markdown or explanations.

The JSON must contain exactly these fields:

{{
  "service": "string",
  "replicas": integer,
  "cpu_request": "string",
  "cpu_limit": "string",
  "memory_request": "string",
  "memory_limit": "string",
  "requests_per_second": integer,
  "network_latency_ms": integer,
  "packet_loss_percent": integer
}}

Choose reasonable CPU and memory values based on the expected traffic.
Do not invent extremely large values.
"""

response = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[
        {
            "role": "user",
            "content": prompt
        }
    ],
    temperature=0.2
)

# Get LLM response
result = response.choices[0].message.content.strip()

# Remove accidental markdown fences
if result.startswith("```"):
    result = result.replace("```json", "")
    result = result.replace("```", "")
    result = result.strip()

# Convert response to JSON
try:
    environment = json.loads(result)
except json.JSONDecodeError:
    print("Groq did not return valid JSON:")
    print(result)
    exit(1)

# Save JSON
output_file = "generated/environment.json"

with open(output_file, "w") as file:
    json.dump(environment, file, indent=4)

print("\nEnvironment generated successfully!")
print(json.dumps(environment, indent=4))
print(f"\nSaved to: {output_file}")




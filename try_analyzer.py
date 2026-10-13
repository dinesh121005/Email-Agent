from email_analyzer import analyze_email
from llm_client import OllamaClient

# Allow extra time since 30B model is running largely on CPU (88% CPU / 12% GPU)
client = OllamaClient(timeout_seconds=300.0)

result = analyze_email(
    subject="Hackathon registration closes tomorrow",
    body="Complete your registration before 5 PM tomorrow.",
    client=client,
)
print(result.model_dump_json(indent=2))

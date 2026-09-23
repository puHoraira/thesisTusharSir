"""
Test which Gemini models are available with the current API key
"""
import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print(f"Testing with API key: {api_key[:20]}...")

genai.configure(api_key=api_key)

print("\nAvailable Gemini models:")
print("-" * 60)

for model in genai.list_models():
    if 'generateContent' in model.supported_generation_methods:
        print(f"✓ {model.name}")
        print(f"  Display name: {model.display_name}")
        print(f"  Description: {model.description[:80]}...")
        print()

print("\nTesting gemini-1.5-flash:")
try:
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content("Say 'hello' in one word")
    print(f"✓ gemini-1.5-flash works! Response: {response.text}")
except Exception as e:
    print(f"✗ gemini-1.5-flash failed: {e}")

print("\nTesting gemini-1.5-pro:")
try:
    model = genai.GenerativeModel('gemini-1.5-pro')
    response = model.generate_content("Say 'hello' in one word")
    print(f"✓ gemini-1.5-pro works! Response: {response.text}")
except Exception as e:
    print(f"✗ gemini-1.5-pro failed: {e}")

print("\nTesting gemini-2.0-flash-exp:")
try:
    model = genai.GenerativeModel('gemini-2.0-flash-exp')
    response = model.generate_content("Say 'hello' in one word")
    print(f"✓ gemini-2.0-flash-exp works! Response: {response.text}")
except Exception as e:
    print(f"✗ gemini-2.0-flash-exp failed: {e}")

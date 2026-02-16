
import argparse
import os
import time
import uuid
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
import onnxruntime_genai as og

# -----------------------------------------------------------------------------
# Data Models (OpenAI Compatible)
# -----------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "phi-3.5-mini-instruct"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    max_tokens: Optional[int] = 1024
    stream: Optional[bool] = False

class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionResponseChoice]
    usage: Dict[str, int]

# -----------------------------------------------------------------------------
# Global State
# -----------------------------------------------------------------------------

app = FastAPI(title="ONNX GenAI Server (Phi-3.5)")
model: Optional[og.Model] = None
tokenizer: Optional[og.Tokenizer] = None

# -----------------------------------------------------------------------------
# Inference Logic
# -----------------------------------------------------------------------------

def load_model(model_path: str):
    global model, tokenizer
    print(f"Loading model from {model_path}...")
    model = og.Model(model_path)
    tokenizer = og.Tokenizer(model)
    print("Model loaded successfully.")

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(req: ChatCompletionRequest):
    global model, tokenizer
    if not model or not tokenizer:
        raise HTTPException(status_code=500, detail="Model not loaded")

    # 1. Format Prompt using Model's Chat Template
    # This automatically handles the specific format (Phi-3, Llama-3, etc.)
    # provided the tokenizer_config.json is correct.
    try:
        # Convert Pydantic messages to dict
        chat_messages = [{"role": m.role, "content": m.content} for m in req.messages]
        
        # Check if apply_chat_template exists on tokenizer (newer versions)
        if hasattr(tokenizer, "apply_chat_template"):
            prompt = tokenizer.apply_chat_template(
                chat_messages, 
                add_generation_prompt=True, 
                tokenize=False
            )
        else:
            # Fallback for older versions or missing template: Simple ChatML-like
            prompt = ""
            for msg in req.messages:
                prompt += f"<|{msg.role}|>\n{msg.content}<|end|>\n"
            prompt += "<|assistant|>\n"
            
    except Exception as e:
        print(f"Template error: {e}. Falling back to manual.")
        prompt = ""
        for msg in req.messages:
            prompt += f"<|{msg.role}|>\n{msg.content}<|end|>\n"
        prompt += "<|assistant|>\n"

    print(f"DEBUG Prompt:\n{prompt}")

    # 2. Tokenize
    input_tokens = tokenizer.encode(prompt)

    # 3. Generate
    params = og.GeneratorParams(model)
    search_options = {
        "max_length": req.max_tokens + len(input_tokens), # approximate
        "temperature": req.temperature,
        "top_p": req.top_p,
    }
    params.set_search_options(**search_options)
    
    generator = og.Generator(model, params)
    generator.append_tokens(input_tokens)

    output_tokens = []
    try:
        while not generator.is_done():
            generator.generate_next_token()
            new_token = generator.get_next_tokens()[0]
            output_tokens.append(new_token)
    except Exception as e:
        print(f"Generation error: {e}")

    output_text = tokenizer.decode(output_tokens)
    
    # 4. Response
    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4()}",
        created=int(time.time()),
        model=req.model,
        choices=[
            ChatCompletionResponseChoice(
                index=0,
                message=ChatMessage(role="assistant", content=output_text),
                finish_reason="stop"
            )
        ],
        usage={
            "prompt_tokens": len(input_tokens),
            "completion_tokens": len(output_tokens),
            "total_tokens": len(input_tokens) + len(output_tokens)
        }
    )

# -----------------------------------------------------------------------------
# Entry Point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_path", type=str, required=True, help="Path to ONNX model folder")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    load_model(args.model_path)
    uvicorn.run(app, host=args.host, port=args.port)

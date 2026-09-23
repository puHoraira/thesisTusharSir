"""
Local Qwen2 LLM implementation for drone command generation.
Provides offline, low-latency inference using the fine-tuned drone model.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Sequence

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from pydantic import Field
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult, ChatGeneration

logger = logging.getLogger("local_qwen")


class LocalQwenLLM(BaseChatModel):
    """
    Local Qwen2-0.5B fine-tuned model for drone command generation.
    
    This model is specifically trained to convert natural language commands
    into structured drone tool calls (waypoints, actions, etc.).
    
    Features:
    - Offline inference (no API calls)
    - Low latency (~1-2 seconds)
    - Fine-tuned for drone-specific commands
    - 500M parameters optimized for Jetson Nano
    """
    
    model_path: str
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=1000)
    device: str = Field(default="cpu")
    model: Any = Field(default=None, exclude=True)
    tokenizer: Any = Field(default=None, exclude=True)
    tools: Optional[List[Dict]] = Field(default=None, exclude=True)
    
    def __init__(self, model_path: str, temperature: float = 0.7, max_tokens: int = 1000, **kwargs):
        """Initialize with explicit parameters."""
        device = "cuda" if torch.cuda.is_available() else "cpu"
        super().__init__(
            model_path=model_path, 
            temperature=temperature, 
            max_tokens=max_tokens,
            device=device,
            **kwargs
        )
        # Load model after Pydantic initialization
        self._load_model()
    
    def __init__(self, model_path: str, temperature: float = 0.7, max_tokens: int = 1000, **kwargs):
        """Initialize with explicit parameters to avoid Pydantic validation issues."""
        super().__init__(model_path=model_path, temperature=temperature, max_tokens=max_tokens, **kwargs)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = None
        self.tokenizer = None
        self._tools = None
        self._load_model()
    
    def _load_model(self):
        """Load the model and tokenizer from disk."""
        logger.info(f"🔄 Loading local Qwen2 model from: {self.model_path}")
        logger.info(f"📊 Device: {self.device}")
        
        start_time = time.time()
        
        try:
            # Load tokenizer
            logger.info("  ├─ Loading tokenizer...")
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_path,
                trust_remote_code=True
            )
            
            # Load model
            logger.info("  ├─ Loading model (500M params)...")
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_path,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                device_map="auto" if self.device == "cuda" else None,
                trust_remote_code=True,
            )
            
            if self.device == "cpu":
                self.model = self.model.to(self.device)
            
            self.model.eval()
            
            load_time = time.time() - start_time
            logger.info(f"✅ Model loaded successfully in {load_time:.2f}s")
            logger.info(f"  └─ Parameters: ~500M, Context: 32,768 tokens")
            
        except Exception as e:
            logger.error(f"❌ Failed to load model: {e}")
            raise
    
    def bind_tools(self, tools_list: Sequence[Any]):
        """Bind tools to the model (stores tool definitions for prompt formatting)."""
        logger.info(f"🔧 Binding {len(tools_list)} tools to local model")
        self.tools = [self._format_tool_schema(tool) for tool in tools_list]
        return self
    
    def _format_tool_schema(self, tool: Any) -> Dict:
        """Convert LangChain tool to simple schema."""
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": getattr(tool, "args_schema", {})
        }
    
    def _format_messages_to_prompt(self, messages: List[BaseMessage]) -> str:
        """
        Convert LangChain messages to Qwen2 prompt format.
        
        Qwen2 format:
        <|im_start|>system
        You are a helpful assistant.<|im_end|>
        <|im_start|>user
        Hello!<|im_end|>
        <|im_start|>assistant
        """
        prompt_parts = []
        
        for msg in messages:
            if isinstance(msg, SystemMessage):
                role = "system"
                content = msg.content
            elif isinstance(msg, HumanMessage):
                role = "user"
                content = msg.content
            elif isinstance(msg, AIMessage):
                role = "assistant"
                content = msg.content
            else:
                continue
            
            prompt_parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
        
        # Add assistant start token
        prompt_parts.append("<|im_start|>assistant\n")
        
        return "\n".join(prompt_parts)
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any
    ) -> ChatResult:
        """Generate response from local model."""
        
        logger.info("=" * 60)
        logger.info("🤖 LOCAL QWEN2 MODEL PROCESSING")
        logger.info("=" * 60)
        
        # 1. Format prompt
        logger.info("\n📝 Step 1: Formatting prompt from messages")
        prompt = self._format_messages_to_prompt(messages)
        logger.info(f"  └─ Prompt length: {len(prompt)} characters")
        
        # 2. Add tool information if available
        if self.tools:
            logger.info(f"\n🔧 Step 2: Adding {len(self.tools)} tool definitions to context")
            tools_text = "\n\nAvailable tools:\n"
            for tool in self.tools:
                tools_text += f"- {tool['name']}: {tool['description']}\n"
            prompt = prompt.replace("<|im_start|>assistant\n", f"{tools_text}\n<|im_start|>assistant\n")
        
        # 3. Tokenize
        logger.info("\n🔤 Step 3: Tokenizing input")
        start_tokenize = time.time()
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        input_length = inputs.input_ids.shape[1]
        tokenize_time = time.time() - start_tokenize
        logger.info(f"  ├─ Input tokens: {input_length}")
        logger.info(f"  └─ Tokenization time: {tokenize_time*1000:.0f}ms")
        
        # 4. Generate
        logger.info("\n⚙️ Step 4: Generating response")
        logger.info(f"  ├─ Temperature: {self.temperature}")
        logger.info(f"  ├─ Max new tokens: {self.max_tokens}")
        logger.info(f"  └─ Device: {self.device}")
        
        start_generate = time.time()
        
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=self.max_tokens,
                temperature=self.temperature,
                do_sample=True,
                top_p=0.95,
                top_k=50,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        
        generate_time = time.time() - start_generate
        output_length = outputs.shape[1] - input_length
        tokens_per_second = output_length / generate_time if generate_time > 0 else 0
        
        logger.info(f"  ├─ Generated tokens: {output_length}")
        logger.info(f"  ├─ Generation time: {generate_time:.2f}s")
        logger.info(f"  └─ Speed: {tokens_per_second:.1f} tokens/second")
        
        # 5. Decode
        logger.info("\n📤 Step 5: Decoding output")
        start_decode = time.time()
        response_text = self.tokenizer.decode(
            outputs[0][input_length:],
            skip_special_tokens=True
        ).strip()
        decode_time = time.time() - start_decode
        logger.info(f"  ├─ Output length: {len(response_text)} characters")
        logger.info(f"  └─ Decoding time: {decode_time*1000:.0f}ms")
        
        # 6. Parse response
        logger.info("\n🔍 Step 6: Parsing response for tool calls")
        parsed_response = self._parse_tool_calls(response_text)
        
        if parsed_response.get("tool_calls"):
            logger.info(f"  ✅ Found {len(parsed_response['tool_calls'])} tool call(s):")
            for i, tc in enumerate(parsed_response["tool_calls"], 1):
                logger.info(f"     {i}. {tc['name']}({list(tc['args'].keys())})")
        else:
            logger.info("  ℹ️ No tool calls found (plain text response)")
        
        # 7. Create result
        total_time = time.time() - start_tokenize
        logger.info(f"\n⏱️ Total processing time: {total_time:.2f}s")
        logger.info("=" * 60)
        
        # Create AIMessage with tool calls if present
        ai_message = AIMessage(
            content=parsed_response.get("content", response_text),
            additional_kwargs={
                "tool_calls": parsed_response.get("tool_calls", []),
                "processing_time": total_time,
                "tokens_generated": output_length,
            }
        )
        
        generation = ChatGeneration(message=ai_message)
        return ChatResult(generations=[generation])
    
    def _parse_tool_calls(self, response: str) -> Dict:
        """
        Parse tool calls from model response.
        
        Expected format from fine-tuned model:
        create_waypoint_sequence(waypoints=[...])
        or
        execute_action(action='takeoff', params={'altitude': 5})
        """
        tool_calls = []
        content = response
        
        # Look for tool call patterns
        # Pattern: function_name(arguments)
        import re
        
        # Simple pattern matching for common tools
        patterns = [
            (r'create_waypoint_sequence\((.*?)\)', 'create_waypoint_sequence'),
            (r'execute_action\((.*?)\)', 'execute_action'),
            (r'present_plan_for_confirmation\((.*?)\)', 'present_plan_for_confirmation'),
            (r'send_message_to_user\((.*?)\)', 'send_message_to_user'),
        ]
        
        for pattern, tool_name in patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            for match in matches:
                try:
                    # Try to parse arguments
                    args = self._parse_function_args(match)
                    tool_calls.append({
                        "name": tool_name,
                        "args": args,
                        "id": f"call_{len(tool_calls)}"
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse tool call {tool_name}: {e}")
        
        return {
            "content": content,
            "tool_calls": tool_calls
        }
    
    def _parse_function_args(self, args_str: str) -> Dict:
        """Parse function arguments from string."""
        # Simple eval-based parsing (safe since model is trusted)
        # In production, use proper AST parsing
        try:
            # Create a safe namespace
            namespace = {}
            exec(f"result = dict({args_str})", namespace)
            return namespace.get("result", {})
        except:
            # Fallback: try JSON parsing
            try:
                return json.loads(f"{{{args_str}}}")
            except:
                return {}
    
    @property
    def _llm_type(self) -> str:
        """Return LLM type."""
        return "local_qwen2"
    
    def _call(self, *args, **kwargs):
        """Synchronous call (not implemented, use async)."""
        raise NotImplementedError("Use ainvoke for async calls")
    
    async def _agenerate(self, *args, **kwargs):
        """Async generate."""
        return self._generate(*args, **kwargs)

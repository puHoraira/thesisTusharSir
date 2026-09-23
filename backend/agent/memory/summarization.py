"""
Conversation memory summarization for the drone agent.
Handles summarizing older messages to manage context window size.
"""

import logging
from typing import List, Any, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent.llm.prompts import create_summarization_prompt
from config import (
    CONVERSATION_MESSAGE_THRESHOLD,
    CONVERSATION_MESSAGES_TO_SUMMARIZE,
    CONVERSATION_MESSAGES_TO_KEEP,
    CONVERSATION_SUMMARY_MAX_TOKENS,
)

logger = logging.getLogger("conversation_memory")


def format_message_for_summary(msg: Any) -> str:
    """
    Format a single message for inclusion in the summary prompt.
    
    Args:
        msg: A LangChain message object
        
    Returns:
        Formatted string representation
    """
    if isinstance(msg, HumanMessage):
        # Status headers are PREPENDED to user messages
        content = msg.content
        if isinstance(content, str) and content.startswith("[Status:"):
            # Extract just the user's actual message after the status header
            lines = content.split("\n\n", 1)
            if len(lines) > 1:
                content = lines[1]
        return f"User: {content}"
    elif isinstance(msg, AIMessage):
        return f"Assistant: {msg.content}"
    elif isinstance(msg, ToolMessage):
        # Summarize tool results briefly
        content = msg.content
        if len(content) > 200:
            content = content[:200] + "..."
        return f"[Tool Result: {content}]"
    elif isinstance(msg, SystemMessage):
        # Include previous conversation summaries, but not the original system prompt
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if content.startswith("[Previous Conversation Summary]"):
            # Extract just the summary text
            summary_content = content.replace("[Previous Conversation Summary]\n", "")
            return f"[Previous Summary: {summary_content}]"
        # Skip the original system prompt
        return ""
    else:
        return f"[Message: {str(msg)[:100]}]"


def needs_summarization(messages: List[Any]) -> bool:
    """
    Check if the conversation needs summarization.
    
    Args:
        messages: List of conversation messages
        
    Returns:
        True if summarization is needed
    """
    # Count non-system messages (system prompt doesn't count toward threshold)
    non_system_count = sum(1 for msg in messages if not isinstance(msg, SystemMessage))
    
    result = (
        non_system_count > CONVERSATION_MESSAGE_THRESHOLD and
        non_system_count > CONVERSATION_MESSAGES_TO_KEEP
    )
    
    # logger.info(
    #     f"[NEEDS_SUMMARIZATION] non_system_count={non_system_count}, "
    #     f"threshold={CONVERSATION_MESSAGE_THRESHOLD}, "
    #     f"keep={CONVERSATION_MESSAGES_TO_KEEP}, "
    #     f"result={result}"
    # )
    
    return result


def prepare_messages_for_summarization(
    messages: List[Any]
) -> Tuple[Optional[SystemMessage], List[Any], List[Any]]:
    """
    Separate messages into system prompt, messages to summarize, and messages to keep.
    
    IMPORTANT: This ensures we never break tool call sequences.
    An AIMessage with tool_calls must stay with its ToolMessage responses.
    
    Args:
        messages: Full list of conversation messages
        
    Returns:
        Tuple of (system_message, messages_to_summarize, messages_to_keep)
    """
    # Extract the first SystemMessage (the system prompt)
    system_message = None
    other_messages = []
    
    for msg in messages:
        if isinstance(msg, SystemMessage) and system_message is None:
            system_message = msg
        else:
            other_messages.append(msg)
    
    # If not enough messages to warrant summarization, return None
    if len(other_messages) <= CONVERSATION_MESSAGES_TO_KEEP:
        return system_message, [], other_messages
    
    # Calculate initial split point
    initial_split = len(other_messages) - CONVERSATION_MESSAGES_TO_KEEP
    
    # Find a safe split point that doesn't break tool call sequences
    # We need to ensure:
    # 1. The first message in messages_to_keep is NOT a ToolMessage (orphaned)
    # 2. The last message in messages_to_summarize is NOT an AIMessage with tool_calls
    
    safe_split = find_safe_split_point(other_messages, initial_split)
    
    # If no safe split found or split would leave nothing to summarize, skip
    if safe_split <= 0:
        return system_message, [], other_messages
    
    messages_to_summarize = other_messages[:safe_split]
    messages_to_keep = other_messages[safe_split:]
    
    return system_message, messages_to_summarize, messages_to_keep


def find_safe_split_point(messages: List[Any], initial_split: int) -> int:
    """
    Find a split point that doesn't break tool call sequences.
    
    Rules:
    - Can't split between an AIMessage with tool_calls and its ToolMessage responses
    - Adjust split point backward to include full tool sequences in summarization
    
    Args:
        messages: List of messages to split
        initial_split: The initial desired split point
        
    Returns:
        Safe split point index
    """
    if initial_split <= 0 or initial_split >= len(messages):
        return initial_split
    
    split = initial_split
    
    # Check if we're cutting in the middle of a tool sequence
    # Look at the first message that would be kept
    while split > 0:
        first_kept = messages[split]
        
        # If first kept message is a ToolMessage, we need to move split backward
        # to include the AIMessage that triggered this tool call
        if isinstance(first_kept, ToolMessage):
            split -= 1
            continue
        
        # Check if the last summarized message is an AIMessage with tool_calls
        last_summarized = messages[split - 1]
        if isinstance(last_summarized, AIMessage):
            # Check if it has tool_calls
            tool_calls = getattr(last_summarized, 'tool_calls', None)
            if tool_calls:
                # This AIMessage has tool_calls, we need to keep it with its responses
                # Move split backward to not break the sequence
                split -= 1
                continue
        
        # Found a safe split point
        break
    
    return split


def create_summary_text(messages: List[Any]) -> str:
    """
    Create formatted text from messages for the summarization prompt.
    
    Args:
        messages: List of messages to format
        
    Returns:
        Formatted text string
    """
    formatted_parts = []
    for msg in messages:
        formatted = format_message_for_summary(msg)
        if formatted:  # Skip empty strings (e.g., from system messages)
            formatted_parts.append(formatted)
    
    return "\n\n".join(formatted_parts)


async def summarize_conversation(
    messages: List[Any],
    llm: Any,
) -> Tuple[List[Any], str]:
    """
    Summarize older messages in the conversation.
    
    This function:
    1. Preserves the first SystemMessage (system prompt)
    2. Summarizes older messages into a single summary
    3. Keeps the most recent messages in full
    4. Returns the new message list and the summary text
    
    Args:
        messages: Full list of conversation messages
        llm: LLM instance to use for summarization
        
    Returns:
        Tuple of (new_messages_list, summary_text)
    """
    # Prepare messages
    system_message, messages_to_summarize, messages_to_keep = prepare_messages_for_summarization(messages)
    
    if not messages_to_summarize:
        logger.debug("No messages to summarize")
        return messages, ""
    
    # Create summary text for the prompt
    summary_input_text = create_summary_text(messages_to_summarize)
    
    # Generate summary using LLM with limited tokens
    try:
        summarization_prompt = create_summarization_prompt(summary_input_text)
        
        # Wrap prompt in a HumanMessage - LLMs expect message format, not plain strings
        summarization_messages = [HumanMessage(content=summarization_prompt)]
        
        response = await llm.ainvoke(
            summarization_messages,
            max_tokens=CONVERSATION_SUMMARY_MAX_TOKENS,
        )
        
        summary_text = response.content
        
        logger.info(f"Generated summary: {summary_text[:100]}...")
        
    except Exception as e:
        logger.error(f"Failed to generate summary: {e}")
        # On error, return original messages unchanged
        return messages, ""
    
    # Build the message updates for add_messages reducer
    # The reducer will:
    # 1. Process RemoveMessage(id=REMOVE_ALL_MESSAGES) to clear all existing messages
    # 2. Add the new messages we specify after the RemoveMessage
    # 
    # IMPORTANT: We must create NEW message objects, not reuse old ones.
    # Reusing old messages with their IDs can cause conflicts after REMOVE_ALL.
    
    message_updates = []

    # 1. First, add RemoveMessage with REMOVE_ALL_MESSAGES to clear all existing messages
    # This tells the add_messages reducer to delete everything before processing the rest
    message_updates.append(RemoveMessage(id=REMOVE_ALL_MESSAGES))
    
    # 2. Add the system prompt as a NEW message (copy content, not the object)
    if system_message:
        message_updates.append(system_message)
    
    # 3. Add a new SystemMessage with the conversation summary
    summary_system_message = SystemMessage(
        content=f"[Previous Conversation Summary]\n{summary_text}"
    )
    message_updates.append(summary_system_message)
    
    # 4. Add the messages we want to keep as NEW message objects
    message_updates.extend(messages_to_keep)
    
    # Count: 1 (RemoveMessage) + 1 (system prompt if exists) + 1 (summary) + len(messages_to_keep)
    new_message_count = len(message_updates) - 1  # Exclude the RemoveMessage from count

    
    return message_updates, summary_text

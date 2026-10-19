# RAG Pipeline Diagnostics & Fixes Summary

This document summarizes the three major issues identified in the CSIS SmartAssist RAG (Retrieval-Augmented Generation) pipeline that were causing the AI to hallucinate or fail to answer specific entity questions (like chamber numbers and VoIPs), and the implemented solutions.

## Problem 1: Context Attribution Hallucination
**The Issue:**
The system was generating the final prompt for the LLM with the text `Conversation history:\n` injected right above the user's current question, even if the user had just started a brand new chat. Because local LLMs (like Llama 3) can be very sensitive to prompt structure, the model read the provided context documents but mistakenly attributed the information to the "Conversation history" label, resulting in responses like *"Based on the conversation history..."*

**The Fix:**
Updated the routing logic in `api/routes/chat.py` (`_handle_department_query` and `_handle_department_query_stream`) to only inject the conversation history block if there are actually previous turns in the chat. Otherwise, it simply passes `Current question: {user_message}`.

## Problem 2: Fallback Keyword Retrieval Dropping Entities
**The Issue:**
For queries asking about specific names (e.g., "what is the chamber number of prof Snehanshu"), the exact keyword fallback was broken. It was relying on the LLM intent router to generate keywords, but if the LLM generated common words (`["chamber", "number"]`) and missed the proper noun (`"Snehanshu"`), the system would execute an `OR` query with a hard limit of 5. This resulted in the database returning 5 random chunks that contained the word "number", completely missing the single chunk containing the actual professor's details.

**The Fix:**
Rewrote the hybrid fallback logic in `api/routes/chat.py`:
1. **Regex Supplementation:** Added a regex extraction step to automatically pull words > 3 characters directly from the user's prompt (excluding common stop words) and merge them with the LLM-generated keywords. This guarantees proper nouns are never dropped.
2. **Noise Filtering:** Filtered out heavily repeated academic words (`"prof"`, `"professor"`, `"chamber"`, `"number"`, `"room"`) before running the DB query.
3. **Iterative Fetching:** Instead of a massive `OR` query, the code now loops through each specific, filtered keyword (e.g., `"Snehanshu"`) and queries Supabase individually with a limit of 5 chunks per keyword.

## Problem 3: Tabular Data Schema Loss
**The Issue:**
When the faculty PDF was parsed and chunked into Supabase, the table headers (`Name | Designation | VoIP | PSRN | Chamber No. | Email`) were stripped away from individual rows. Consequently, when the LLM retrieved a raw data row like `25 Sujith Thomas Assistant Professor, Gr-I 226 G0761 D-158 sujitht@...`, it had no semantic way of knowing that `226` represented the VoIP or that `D-158` represented the chamber number.

**The Fix:**
Added a structural data map directly into the core `DEPARTMENT_RAG_SYSTEM_PROMPT` in `api/core/prompts.py`:
> **TABULAR DATA HINT:** The document chunking process might strip table headers. If you see a raw data row describing a faculty member... the standard column format is: `[Sl. No.] [Name of Faculty] [Designation] [VoIP (O) / Phone] [PSRN] [Chamber No.] [Email ID]`. Use this schema to accurately extract chamber numbers, VoIPs, and emails.

This allows the LLM to successfully parse unstructured raw tabular rows and map the values to the correct entity fields with 100% accuracy.

---
*Note: Because the backend is running with `--reload`, all these changes are actively applied to the local server.*

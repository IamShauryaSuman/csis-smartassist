# CSIS SmartAssist: Local Deployment Proposal

## Overview
We're proposing to migrate the CSIS SmartAssist platform from cloud-hosted free tiers to the department's local servers. This will ensure complete data privacy, eliminate API costs, and provide zero-latency access for students and faculty.

We recommend a **Two-Tier Architecture** to keep the web services running smoothly while isolating the heavy AI workloads.

## 1. Application Server (Web & Database)
This server will run the website, handle user logins, and store chat histories. It needs to be stable and online 24/7, but it doesn't need a GPU.

**Hardware Requirements:**
- **CPU:** 8 to 16 cores
- **RAM:** 32 GB
- **Storage:** 100 GB SSD (for database and logs)
- **Network:** Static IP within the CSIS Intranet (or an official department domain).

**Software Stack:**
- Docker & Docker Compose
- Next.js (Frontend)
- FastAPI (Backend)
- Supabase (PostgreSQL & Auth)

## 2. AI Inference Node (GPU Server)
This server will host our open-source LLMs and text embedding models. The application server will talk to this node locally instead of calling the Gemini/OpenAI APIs.

To achieve the highest quality of responses and ensure the system can handle concurrent queries from multiple students during peak times, securing adequate GPU hardware is critical. We recommend aiming for the highest tier possible to guarantee a seamless, GPT-4 level experience.

**Tier 1: Recommended Production Hardware**
*Requires: 80GB+ VRAM (e.g., 1x A100 or 4x 24GB GPUs)*
- **LLM:** Meta Llama 3 (70B)
- **Embeddings:** BAAI/bge-m3
- *Impact:* Delivers state-of-the-art intelligence and complex reasoning, effortlessly managing heavy student traffic. This is highly recommended to maximize the utility of the assistant.

**Tier 2: Mid-Range Fallback**
*Requires: 2x 24GB GPUs or 1x 48GB+ GPU (e.g., RTX 6000 Ada)*
- **LLM:** Mistral Nemo (12B)
- **Embeddings:** BAAI/bge-m3
- *Impact:* Provides a strong context window for long documents, though with slightly reduced reasoning capability under heavy concurrency.

**Tier 3: Minimum Viable Hardware**
*Requires: 1x 24GB VRAM GPU (e.g., RTX 3090 / 4090 / A5000)*
- **LLM:** Meta Llama 3 (8B)
- **Embeddings:** Nomic Embed Text
- *Impact:* Capable for basic queries, but risks performance bottlenecks if many users access the system simultaneously.

## Additional Administrative Requirements
1. **Google Workspace Account:** We need a dedicated department account (e.g., `smartassist@goa.bits-pilani.ac.in`) to restrict logins to university members, send out automated emails, and store the RAG source documents in a central Drive.
2. **Network Resolution & SSL:** Proper internal DNS resolution on the CSIS Intranet (or an official domain) configured with SSL certificates, as HTTPS is strictly required for Google Login and browser security features.
3. **Data Backups:** A routine backup policy for the PostgreSQL database on the Application Server.

#!/bin/bash
set -e

echo "==========================================="
echo "   CSIS SmartAssist Deployment Manager     "
echo "==========================================="
echo ""

PERSISTENT_DIR="/mnt/syncstore/smartassist"
APP_DIR="${PERSISTENT_DIR}/app"

# 0. Ensure we are running from persistent storage
if [[ "$PWD" != "$PERSISTENT_DIR"* ]]; then
    echo "⚠️  WARNING: Running from $PWD which may not be persistent!"
    echo "   Moving application to $APP_DIR..."
    mkdir -p "$APP_DIR"
    cp -r . "$APP_DIR/"
    echo "✅ Files moved. Please cd into $APP_DIR and run ./deploy.sh"
    exit 1
fi

# 1. Prerequisite checks
if ! command -v podman &> /dev/null; then
    echo "❌ ERROR: Podman is not installed. Contact your sysadmin."
    exit 1
fi

# Determine LAN IP for client browser connections
HOST_IP=$(echo "$SSH_CONNECTION" | awk '{print $3}')
if [ -z "$HOST_IP" ]; then
    HOST_IP=$(ip route get 1 2>/dev/null | awk '{print $7;exit}')
fi
if [ -z "$HOST_IP" ]; then
    HOST_IP="10.1.19.203"
fi

ensure_network() {
    if ! podman network inspect smartassist-net >/dev/null 2>&1; then
        echo "Creating Podman network 'smartassist-net'..."
        SUCCESS=0
        for i in {1..10}; do
            RANDOM_SUBNET="192.168.$((RANDOM % 200 + 10)).0/24"
            if podman network create --subnet "$RANDOM_SUBNET" smartassist-net >/dev/null 2>&1; then
                echo "✅ Successfully created network on $RANDOM_SUBNET"
                SUCCESS=1
                break
            fi
        done
        if [ $SUCCESS -eq 0 ]; then
            echo "❌ ERROR: Could not find a free subnet. Routing table may be restricted."
            exit 1
        fi
    fi
}

ensure_dirs() {
    mkdir -p "$PERSISTENT_DIR/data/postgres"
    mkdir -p "$PERSISTENT_DIR/data/ollama"
}

ensure_env() {
    if [ ! -f .env.local ]; then
        echo "⚠️  .env.local not found. Creating default configuration..."
        cat <<EOF > .env.local
# Local AI Provider Overrides
LLM_PROVIDER=ollama
EMBEDDING_PROVIDER=ollama
OLLAMA_BASE_URL=http://smartassist-ollama:11434/v1
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Postgres Database Setup
POSTGRES_USER=postgres
POSTGRES_PASSWORD=smartassist_local_pass
POSTGRES_DB=smartassist

# Local Auth Mode (bypasses Google OAuth for intranet)
LOCAL_AUTH=true
NEXT_PUBLIC_LOCAL_AUTH=true
LOCAL_USER_ID=1cce9d10-6970-4c9f-9f5e-39bc6b6c6671
NEXT_PUBLIC_LOCAL_USER_ID=1cce9d10-6970-4c9f-9f5e-39bc6b6c6671
EOF
        echo "✅ Default .env.local created."
    fi
}

start_db() {
    if podman ps --format "{{.Names}}" | grep -q "^smartassist-db$"; then
        echo "✅ Postgres database is already running."
    else
        echo "📦 Starting Postgres container..."
        podman run -d --replace --name smartassist-db \
            --network smartassist-net \
            --restart unless-stopped \
            -p 5432:5432 \
            --env-file .env.local \
            -v "$PERSISTENT_DIR/data/postgres:/var/lib/postgresql/data" \
            -v ./supabase/migrations:/docker-entrypoint-initdb.d \
            docker.io/pgvector/pgvector:pg16
        echo "✅ Postgres started."
    fi
}

start_ollama() {
    if podman ps --format "{{.Names}}" | grep -q "^smartassist-ollama$"; then
        echo "✅ Ollama is already running."
    else
        echo "📦 Starting Ollama container with GPU support..."
        podman run -d --replace --name smartassist-ollama \
            --network smartassist-net \
            --restart unless-stopped \
            -p 11434:11434 \
            -v "$PERSISTENT_DIR/data/ollama:/root/.ollama" \
            --device nvidia.com/gpu=0 \
            docker.io/ollama/ollama:latest
        sleep 5
        echo "✅ Ollama started."
    fi
}

pull_models() {
    set -a
    [ -f .env.local ] && . .env.local
    set +a

    TARGET_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
    EMBED_MODEL="${OLLAMA_EMBEDDING_MODEL:-nomic-embed-text}"

    echo "🔍 Checking installed AI models in Ollama..."
    INSTALLED=$(podman exec smartassist-ollama ollama list 2>/dev/null || true)

    if echo "$INSTALLED" | grep -q "$TARGET_MODEL"; then
        echo "✅ Model '$TARGET_MODEL' is already downloaded."
    else
        echo "⬇️  Pulling '$TARGET_MODEL' (this may take a few minutes)..."
        podman exec -it smartassist-ollama ollama pull "$TARGET_MODEL"
    fi

    if echo "$INSTALLED" | grep -q "$EMBED_MODEL"; then
        echo "✅ Embedding model '$EMBED_MODEL' is already downloaded."
    else
        echo "⬇️  Pulling '$EMBED_MODEL'..."
        podman exec -it smartassist-ollama ollama pull "$EMBED_MODEL"
    fi
}

start_api() {
    echo "📦 Building and updating API container..."
    podman build -t smartassist-api ./api
    podman run -d --replace --name smartassist-api \
        --network smartassist-net \
        --restart unless-stopped \
        -p 8000:8000 \
        --env-file .env.local \
        smartassist-api
    echo "✅ API updated and running on http://${HOST_IP}:8000"
}

start_ui() {
    echo "📦 Building and updating UI container..."
    set -a
    [ -f .env.local ] && . .env.local
    set +a

    echo "Baking API URL into UI build: http://${HOST_IP}:8000"
    podman build \
        --build-arg NEXT_PUBLIC_API_URL="http://${HOST_IP}:8000" \
        --build-arg NEXT_PUBLIC_SUPABASE_URL="${NEXT_PUBLIC_SUPABASE_URL}" \
        --build-arg NEXT_PUBLIC_SUPABASE_ANON_KEY="${NEXT_PUBLIC_SUPABASE_ANON_KEY}" \
        --build-arg NEXT_PUBLIC_GOOGLE_CLIENT_ID="${NEXT_PUBLIC_GOOGLE_CLIENT_ID}" \
        --build-arg NEXT_PUBLIC_LOCAL_AUTH="${NEXT_PUBLIC_LOCAL_AUTH:-false}" \
        --build-arg NEXT_PUBLIC_LOCAL_USER_ID="${NEXT_PUBLIC_LOCAL_USER_ID:-1cce9d10-6970-4c9f-9f5e-39bc6b6c6671}" \
        --build-arg LOCAL_AUTH="${LOCAL_AUTH:-false}" \
        -t smartassist-ui ./ui

    podman run -d --replace --name smartassist-ui \
        --network smartassist-net \
        --restart unless-stopped \
        -p 3000:3000 \
        --env-file .env.local \
        smartassist-ui
    echo "✅ UI updated and running on http://${HOST_IP}:3000"
}

# ── Command Dispatcher ────────────────────────────────────────────────────────

COMMAND="${1:-all}"

case "$COMMAND" in
    api)
        ensure_network
        ensure_dirs
        ensure_env
        start_api
        ;;
    ui)
        ensure_network
        ensure_dirs
        ensure_env
        start_ui
        ;;
    models|pull-models)
        start_ollama
        pull_models
        ;;
    restart)
        echo "🔄 Restarting all containers..."
        podman restart smartassist-db smartassist-ollama smartassist-api smartassist-ui
        echo "✅ All containers restarted."
        ;;
    status|ps)
        echo "📊 Container Status:"
        podman ps --filter "name=smartassist"
        ;;
    logs)
        TARGET_CONTAINER="smartassist-${2:-api}"
        echo "📜 Streaming logs for $TARGET_CONTAINER (Ctrl+C to stop)..."
        podman logs -f "$TARGET_CONTAINER"
        ;;
    clean)
        echo "🧹 Cleaning up dangling images..."
        podman image prune -f
        echo "✅ Disk space recovered."
        ;;
    all)
        ensure_network
        ensure_dirs
        ensure_env
        start_db
        start_ollama
        start_api
        start_ui
        pull_models
        echo ""
        echo "==========================================="
        echo "✅ Full Deployment Complete!"
        echo "   - Frontend: http://${HOST_IP}:3000"
        echo "   - Backend:  http://${HOST_IP}:8000"
        echo "   - API Docs: http://${HOST_IP}:8000/docs"
        echo "==========================================="
        ;;
    help|--help|-h)
        echo "Usage: ./deploy.sh [COMMAND]"
        echo ""
        echo "Commands:"
        echo "  ./deploy.sh          Full deployment of DB, Ollama, API, UI, and Models"
        echo "  ./deploy.sh api      Fast rebuild & restart of API only (~3 seconds)"
        echo "  ./deploy.sh ui       Rebuild & restart UI only (uses cached build layers)"
        echo "  ./deploy.sh models   Verify and pull Ollama models without rebuilding apps"
        echo "  ./deploy.sh restart  Quickly restart all containers without rebuilding"
        echo "  ./deploy.sh status   Show running containers and port mappings"
        echo "  ./deploy.sh logs [api|ui|ollama|db]  View live container logs"
        echo "  ./deploy.sh clean    Prune dangling images to free up host disk"
        ;;
    *)
        echo "❌ Unknown command: $COMMAND"
        echo "Run './deploy.sh help' for a list of available commands."
        exit 1
        ;;
esac

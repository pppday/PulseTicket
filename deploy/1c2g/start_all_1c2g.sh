#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/jdk17/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

BASE_DIR="/opt/pulseticket"
JAR_DIR="$BASE_DIR/jars"
LOG_DIR="$BASE_DIR/logs"
if [ -d "$BASE_DIR/agent" ]; then
    AGENT_DIR="$BASE_DIR/agent"
else
    AGENT_DIR="$BASE_DIR/ticket-agent"
fi
mkdir -p "$LOG_DIR"

if [ -f "$BASE_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source <(tr -d '\r' < "$BASE_DIR/.env")
    set +a
fi

# 1C2G 专属极限 JVM 参数：
# - UseSerialGC: 单核 CPU 零 GC 线程争抢，省去 G1 RememberedSet 内存开销
# - TieredStopAtLevel=1: 仅使用 C1 即时编译器，省去 40MB+ CodeCache 且防止多个 JVM 抢占单核 CPU 导致假死
# - MaxMetaspaceSize=144m + ExitOnOutOfMemoryError + Xshare:auto: 放宽元空间上限避免 Spring Cloud 类元数据 OOM，并启用 OOM 快速退出防假活
JVM_OPTS="-Xms48m -Xmx112m -Xss256k -XX:ReservedCodeCacheSize=24m -XX:MaxMetaspaceSize=144m -XX:+ExitOnOutOfMemoryError -XX:+UseSerialGC -XX:TieredStopAtLevel=1 -Xshare:auto -Djava.security.egd=file:/dev/./urandom -Dspring.jmx.enabled=false"
SPRING_SLIM_ARGS="--server.tomcat.threads.max=20 --server.tomcat.threads.min-spare=2 --spring.datasource.hikari.maximum-pool-size=5 --spring.datasource.hikari.minimum-idle=1"

echo "===================================================="
echo " [PulseTicket 1C2G] 1. Starting Slim Middleware Containers"
echo "===================================================="
cd "$BASE_DIR/deploy/1c2g"
docker compose -f docker-compose-1c2g.yml up -d

echo "-> Waiting for Nacos HTTP (8848), gRPC (9848) & MySQL (3306) readiness..."
for i in {1..60}; do
    if curl -s http://127.0.0.1:8848/nacos/v1/console/health/readiness | grep -qi "ok" && ss -tln | grep -q ":9848 "; then
        sleep 3
        echo "   [OK] Nacos HTTP + gRPC are ready!"
        break
    fi
    sleep 2
done

python3 "$BASE_DIR/tools/init_nacos_configs.py" || true

start_service() {
    local name=$1
    local port=$2
    local jar_path="$JAR_DIR/${name}-1.0.0.jar"
    local pid_file="$LOG_DIR/${name}.pid"

    if [ -f "$pid_file" ]; then
        local old_pid
        old_pid=$(tr -d '[:space:]' < "$pid_file")
        if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null && ps -p "$old_pid" -o args= 2>/dev/null | grep -q "${name}-1.0.0.jar"; then
            echo "-> [$name] already running (PID $old_pid), skipping."
            return
        fi
        rm -f "$pid_file"
    fi

    echo "-> Starting [$name] on port $port (nice -n 10 + 1C2G SerialGC + C1 Mode)..."
    if [ "$name" = "ticket-gateway" ]; then
        nohup nice -n 10 java $JVM_OPTS -jar "$jar_path" > "$LOG_DIR/${name}.log" 2>&1 &
    else
        nohup nice -n 10 java $JVM_OPTS -jar "$jar_path" $SPRING_SLIM_ARGS > "$LOG_DIR/${name}.log" 2>&1 &
    fi
    echo $! > "$pid_file"

    # 错峰串行等待：确保单核 CPU 峰值平滑过渡后再启动下一个微服务
    for i in {1..40}; do
        if ss -tln | grep -q ":${port} "; then
            echo "   [OK] $name listening on port $port"
            break
        fi
        sleep 1
    done
}

start_agent_service() {
    local port=8204
    local pid_file="$LOG_DIR/ticket-agent.pid"

    if [ ! -d "$AGENT_DIR" ]; then
        echo "-> [ticket-agent] directory not found, skipping."
        return
    fi

    if [ -f "$pid_file" ]; then
        local old_pid
        old_pid=$(tr -d '[:space:]' < "$pid_file")
        if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null && ps -p "$old_pid" -o args= 2>/dev/null | grep -q "8204"; then
            echo "-> [ticket-agent] already running (PID $old_pid), skipping."
            return
        fi
        rm -f "$pid_file"
    fi

    local py_bin="python3"
    if [ -x "$AGENT_DIR/venv/bin/python3" ]; then
        py_bin="$AGENT_DIR/venv/bin/python3"
    elif [ -x "/opt/pulseflow/agent/venv/bin/python3" ]; then
        py_bin="/opt/pulseflow/agent/venv/bin/python3"
    fi

    echo "-> Starting [ticket-agent] on 127.0.0.1:$port using $py_bin ..."
    (
        cd "$AGENT_DIR"
        export MALLOC_ARENA_MAX=2
        export DEV_MOCK=false
        nohup nice -n 10 "$py_bin" -m uvicorn app.main:app --host 127.0.0.1 --port "$port" --workers 1 > "$LOG_DIR/ticket-agent.log" 2>&1 &
        echo $! > "$pid_file"
    )

    for i in {1..30}; do
        if ss -tln | grep -q ":${port} "; then
            echo "   [OK] ticket-agent listening on port $port"
            break
        fi
        sleep 1
    done
}

echo "===================================================="
echo " [PulseTicket 1C2G] 2. Staggered Serial Microservice & Agent Boot"
echo "===================================================="
start_service "user-service" 8201
start_service "show-service" 8202
start_service "order-service" 8203
start_agent_service
start_service "ticket-gateway" 8200

echo "===================================================="
echo " [PulseTicket 1C2G] All 5 Services Started! Memory Status:"
echo "===================================================="
free -h

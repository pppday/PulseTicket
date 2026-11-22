@echo off
chcp 65001 >nul
echo ============================================================================
echo   PulseTicket · 在线选座微服务与 Multi-Agent 智能购票系统 (一键启动脚本)
echo   Spring Cloud Alibaba 2023 + Seata AT + RabbitMQ DLX + LangGraph (:8204)
echo ============================================================================

cd /d "%~dp0"

echo [1/5] 正在检查并启动 Docker 中间件容器 (Redis / RabbitMQ / Nacos / Seata)...
docker compose -f deploy/docker-compose.yml up -d

echo [2/5] 正在向 Nacos 推送 4 份共享配置 (shared-jdbc / shared-redis / shared-mq / shared-jwt)...
python tools/init_nacos_configs.py

echo [3/5] 正在启动 4 个 Spring Cloud 微服务 (Gateway:8200 / User:8201 / Show:8202 / Order:8203)...
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
)

start "PulseTicket-UserService:8201" cmd /k "java -Xms128m -Xmx256m -jar backend/user-service/target/user-service-1.0.0.jar"
start "PulseTicket-ShowService:8202" cmd /k "java -Xms128m -Xmx256m -jar backend/show-service/target/show-service-1.0.0.jar"
start "PulseTicket-OrderService:8203" cmd /k "java -Xms128m -Xmx256m -jar backend/order-service/target/order-service-1.0.0.jar"
start "PulseTicket-Gateway:8200" cmd /k "java -Xms128m -Xmx256m -jar backend/ticket-gateway/target/ticket-gateway-1.0.0.jar"

echo [4/5] 正在启动 Python Multi-Agent 智能体服务 (agent / ticket-agent :8204)...
if exist "agent\.venv\Scripts\python.exe" (
    start "PulseTicket-Agent:8204" cmd /k "cd agent && .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8204"
) else (
    start "PulseTicket-Agent:8204" cmd /k "cd agent && python -m uvicorn app.main:app --host 0.0.0.0 --port 8204"
)

echo [5/5] 正在启动 Vue 3 + Tailwind CSS 选座与 AI 购票前端 (:5190)...
start "PulseTicket-Frontend:5190" cmd /k "cd frontend && npm run dev"

echo ============================================================================
echo   启动指令已下发！请访问前端地址: http://localhost:5190
echo   网关统一入口: http://localhost:8200  ^|  AI Agent 文档: http://localhost:8204/docs
echo ============================================================================
pause

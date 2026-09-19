FROM python:3.11-slim
# 网络隔离：agent 不需要外网（pip 依赖预装）
RUN pip install --no-cache-dir pytest==8.2.0

WORKDIR /workspace
COPY workspace/ /workspace/

# 非 root 运行，限制破坏范围
RUN useradd -m agent && chown -R agent:agent /workspace
USER agent

# 资源与超时限制由 runner 在 docker run 时注入（--memory --cpus --timeout）
CMD ["sleep", "infinity"]

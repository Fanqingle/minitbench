FROM python:3.11-slim
WORKDIR /workspace
COPY workspace/ /workspace/
ENV PYTHONPATH=/workspace
# 故障注入器只在 verifier 容器挂载，agent 不可见
CMD ["bash"]

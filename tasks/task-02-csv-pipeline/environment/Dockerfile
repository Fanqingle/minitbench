FROM python:3.11-slim
WORKDIR /workspace
COPY workspace/ /workspace/
# 注意：tests/ 与 solution/ 不在此层；verifier 阶段才挂载，agent 不可见
ENV PYTHONPATH=/workspace
CMD ["bash"]

FROM python:3.11-slim
WORKDIR /workspace
# agent 需要自己跑测试来验证重构是否保持语义；运行时是断网的，装不了依赖，
# 所以 pytest 必须预装进镜像。同时补 `python` 别名——镜像里只有 python3，
# 缺别名会让 agent 白白花几步在「命令不存在」上，把环境噪音混进失败归因。
RUN pip install --no-cache-dir "pytest==8.4.1" \
    && ln -sf /usr/local/bin/python3 /usr/local/bin/python
COPY workspace/ /workspace/
ENV PYTHONPATH=/workspace
# 隐藏验收测试只在 verifier 容器挂载，agent 不可见
CMD ["bash"]

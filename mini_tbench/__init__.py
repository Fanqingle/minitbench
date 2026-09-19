"""Mini-TBench：coding agent RL 数据任务实验台。

一个用于生成 coding/agentic 方向 post-training 训练数据的可复现实验台：
任务构造 → 可复现沙箱 → 可执行 verifier + 反作弊 → 批量轨迹采集 →
奖励/数据质量（去重/人工抽检）→ 导出 SFT/RLVR 样本。

模块对应 JD 职责：
  - task_spec / tasks/*       职责1：任务构造、难度分层、rubric/reward 设计
  - sandbox / rollout         职责2·3：agent 实操复盘、可复现执行环境、超时/资源限制、批量 rollout
  - verifier / gaming         职责4：可执行验证、验证器被钻空子检测
  - contamination             职责4：数据污染检测
  - dedup / review / sft      职责5：数据质量体系、人工抽检、去重、SFT/RLVR 导出
  - reward                    职责1：reward 设计（pass/partial/format/penalty）
  - examples/*                职责6：把模糊能力目标翻译成任务规范与验收标准
"""
from .task_spec import Task, RubricItem, DesignNotes, load_task
from .sandbox import SandboxResult, run_in_docker, run_local
from .verifier import VerificationResult, run_verifier
from .rollout import Trajectory, do_rollout, batch_rollout
from .reward import compute_reward
from .sft import extract_sft, extract_rlvr
from .dedup import DedupIndex, dedup_jsonl
from .review import ReviewQueue, GoldenDataset
from .contamination import ContaminationScanner
from .gaming import GamingAnalyzer
# 注意：不在此导入 .cli（会造成 `python -m mini_tbench.cli` 的循环导入告警）；
# 命令行入口请直接使用 `python -m mini_tbench.cli`。

__all__ = [
    "Task", "RubricItem", "DesignNotes", "load_task",
    "SandboxResult", "run_in_docker", "run_local",
    "VerificationResult", "run_verifier",
    "Trajectory", "do_rollout", "batch_rollout",
    "compute_reward", "extract_sft", "extract_rlvr",
    "DedupIndex", "dedup_jsonl", "ReviewQueue", "GoldenDataset",
    "ContaminationScanner", "GamingAnalyzer",
]

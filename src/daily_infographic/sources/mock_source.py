from __future__ import annotations

from datetime import datetime, timedelta

from ..models import NewsItem
from .base import SourceAdapter


class MockSource(SourceAdapter):
    def fetch(self, since: datetime, until: datetime) -> list[NewsItem]:
        # Deterministic mock items to validate daily infographic rendering.
        rows = [
            ("MWC2026开幕，AI与通信技术成焦点", "2026世界移动通信大会开幕，聚焦AI与5G融合，终端与算网协同成为热点。", "https://example.com/ai/mwc2026", "财联社"),
            ("OpenAI签约五角大楼，加速AI军事化应用", "OpenAI宣布与美国防部达成合作，模型将用于受控环境下的任务自动化。", "https://example.com/ai/openai-dod", "新浪财经"),
            ("中国大模型周调用量首超美国", "多家平台披露调用数据，中国大模型在周活跃请求量上首次领先。", "https://example.com/ai/model-usage", "综合信息整理"),
            ("阿里千问AI眼镜开启全渠道预约", "阿里发布首款AI眼镜并开启全渠道预约，主打实时翻译与智能助理。", "https://example.com/ai/qwen-glasses", "财联社"),
            ("人形机器人国家标准发布，产业规范化", "工信部发布相关标准体系，加快人形机器人测试认证与场景落地。", "https://example.com/ai/humanoid-standard", "财联社"),
            ("苹果新品发布活动启动，聚焦智能穿戴", "苹果宣布新一轮产品发布活动，智能穿戴与端侧AI成为主线。", "https://example.com/ai/apple-event", "财联社"),
            ("小鹏第二代VLA发布，搭载新车同步亮相", "小鹏发布第二代VLA技术，面向自动驾驶与座舱协同。", "https://example.com/ai/xpeng-vla", "财联社"),
            ("蔚来联合中科大斩获AI最高奖", "蔚来与中科大合作项目获奖，世界模型方向获得行业认可。", "https://example.com/ai/nio-award", "CNMO"),
            ("MiniMax公布2025全年业绩", "MiniMax披露全年业绩并更新多模态路线图，强调AGI长期投入。", "https://example.com/ai/minimax-earnings", "MiniMax官方公告"),
            ("摩尔线程披露上市后首份业绩快报", "摩尔线程发布业绩快报，强调AI算力产品交付节奏与收入增长。", "https://example.com/ai/mthreads-report", "综合信息整理"),
            ("Meta终止自研AI芯片项目", "Meta调整芯片战略，暂停部分自研训练芯片计划并强化外部合作。", "https://example.com/ai/meta-chip", "综合信息整理"),
            ("文远知行迪拜Robotaxi车队暂时停运", "文远知行在迪拜的Robotaxi运营临时调整，后续视监管窗口恢复。", "https://example.com/ai/weride-dubai", "财联社"),
            ("Anthropic发布新模型评测基准", "Anthropic公开新版模型安全评测框架，并扩展企业合规工具链。", "https://example.com/ai/anthropic-benchmark", "Anthropic News"),
            ("Google扩展Gemini企业订阅能力", "Google为Workspace新增Gemini自动化功能，提升企业流程效率。", "https://example.com/ai/google-gemini", "Google Blog"),
            ("英伟达公布新一代推理芯片路线", "英伟达更新推理芯片路线图，重点覆盖低时延大规模部署场景。", "https://example.com/ai/nvidia-inference", "NVIDIA News"),
        ]

        out: list[NewsItem] = []
        for idx, (title, summary, url, source_name) in enumerate(rows):
            published_at = until - timedelta(hours=idx * 2)
            out.append(
                NewsItem(
                    source_id=self.cfg.id,
                    source_name=source_name,
                    source_tier=self.cfg.tier,
                    title=title,
                    summary=summary,
                    url=url,
                    published_at=published_at,
                    tags=list(self.cfg.tags),
                )
            )
        return out

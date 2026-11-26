"""Structured 2026-11 watchOS 27 / iOS 27 China Mainland Apple Watch feature catalog."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WatchFeature:
    name: str
    cn_status: str
    unlock_method: str
    models: str
    path: str


# Verified against Apple official watchOS 27 Feature Availability & Apple CN Newsroom (2026-11).
WATCH_FEATURES: tuple[WatchFeature, ...] = (
    WatchFeature(
        name="高血压通知 (Hypertension Notifications)",
        cn_status="大陆未开放（仍在监管审批）",
        unlock_method="本工具 USB iAP2 临时定位 HK/US（iOS 27.2.1 + S12 实测通过）",
        models="S9 / S10 / S11 / S12、Ultra 2 / 3 / 4（不含 SE）；iPhone 11+；22岁+未怀孕未确诊",
        path="健康 App → 右上角头像 → 健康核对清单 → 高血压通知",
    ),
    WatchFeature(
        name="睡眠呼吸暂停提示 (Sleep Apnea)",
        cn_status="大陆已于 2026-10-14 正式开放",
        unlock_method="更新最新系统直接开；旧系统/未灰度到可用本工具辅助",
        models="S9+、Ultra 2+、SE 3；18岁+未确诊；需开启睡眠追踪",
        path="健康 App → 健康核对清单 或 搜索 → 呼吸 → 睡眠呼吸暂停通知",
    ),
    WatchFeature(
        name="心电图 (ECG / 移动心电图房颤提示软件)",
        cn_status="大陆已官方开放",
        unlock_method="无需改定位，直接在健康/Watch App 开通",
        models="S4+、Ultra 全系（不含 SE）；22岁+",
        path="健康 App → 浏览 → 心脏 → 心电图",
    ),
    WatchFeature(
        name="心律不齐提示 (Irregular Rhythm)",
        cn_status="大陆已官方开放",
        unlock_method="无需改定位，直接开启",
        models="S1+ / SE / Ultra 全系；22岁+未确诊房颤",
        path="Watch App → 心脏 → 心律不齐提示",
    ),
    WatchFeature(
        name="房颤历史 (AFib History)",
        cn_status="大陆已于 2026-03 官方开放",
        unlock_method="无需改定位（仅限医生已确诊房颤人群，与心律不齐互斥）",
        models="S4+ / Ultra / SE；22岁+已确诊房颤",
        path="健康 App → 浏览 → 心脏 → 房颤历史",
    ),
    WatchFeature(
        name="血氧 / 生命体征 / 准备指数 / 睡眠评分 / 围绝经期追踪",
        cn_status="大陆 S12 官方原生全支持",
        unlock_method="无需改定位，升级 watchOS 27 直接使用",
        models="准备指数/白天生命体征/恢复HRV 为 S12 & Ultra 4 专属",
        path="手表内置 App + iPhone 健康 App",
    ),
    WatchFeature(
        name="跑道检测 (Track Detection)",
        cn_status="大陆未开放（HK/TW/US 支持）",
        unlock_method="依赖运动当下实时 GPS，非一次性激活开关",
        models="S4+ / SE / Ultra",
        path="体能训练 → 户外跑步（自动识别田径跑道）",
    ),
    WatchFeature(
        name="卫星紧急联络 / 卫星消息 / 国行快充 / FaceTime Audio",
        cn_status="大陆硬件或系统级限制",
        unlock_method="不可通过改定位开启（硬件/基带/服务区限制）",
        models="视销售地区硬件版本而定",
        path="不适用",
    ),
)


def render_feature_table() -> str:
    lines = [
        "Apple Watch 中国大陆功能状态矩阵（核对日期: 2026-11 / watchOS 27 & iOS 27）",
        "=" * 78,
    ]
    for feat in WATCH_FEATURES:
        lines.extend(
            [
                f"• {feat.name}",
                f"  大陆状态: {feat.cn_status}",
                f"  开启方式: {feat.unlock_method}",
                f"  适用条件: {feat.models}",
                f"  入口路径: {feat.path}",
            ]
        )
    lines.append("=" * 78)
    lines.append("提示: 健康类通知仅作趋势筛查参考，非医疗诊断工具；身体不适请及时就医。")
    return "\n".join(lines)

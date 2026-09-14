from __future__ import annotations

import re
from typing import Any


GMS_RELEASE_TYPES = {
    "IR": ("Initial Release", "设备首次正式 GMS 认证"),
    "MR": ("Maintenance Release", "普通维护版本送测"),
    "SMR": ("Security Maintenance Release", "安全补丁维护版本送测"),
    "ESMR": ("Emergency Security Release", "紧急安全漏洞修复送测"),
    "EMR": ("Emergency Maintenance Release", "紧急重大问题修复送测"),
    "LR": ("Letter Release", "Android 大版本升级送测"),
}
GMS_CYCLE_STATUSES = {"active", "approved", "cancelled"}
GMS_CURRENT_STAGES = {"self_test", "submission"}
GMS_SELF_TEST_RESULTS = {"not_started", "in_progress", "failed", "passed"}
GMS_SUBMISSION_RESULTS = {"not_submitted", "under_review", "returned", "passed", "cancelled"}
GMS_TARGET_RE = re.compile(r"^A[1-9][0-9]*$")
GMS_CURRENT_FIELDS = (
    "gms_release_type",
    "gms_target",
    "gms_cycle_status",
    "gms_current_stage",
    "gms_self_test_round",
    "gms_self_test_result",
    "gms_submission_count",
    "gms_submission_result",
)
GMS_PLAN_FIELDS = ("gms_release_type", "gms_target")


def clean_gms_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_gms_target(value: Any) -> str:
    target = clean_gms_text(value)
    return target.upper() if re.fullmatch(r"A[1-9][0-9]*", target, re.IGNORECASE) else target


def normalize_gms_fields(value: dict[str, Any], *, plan: bool = False) -> dict[str, Any]:
    if clean_gms_text(value.get("work_type")) != "GMS":
        return {}
    result: dict[str, Any] = {
        "gms_release_type": clean_gms_text(value.get("gms_release_type")).upper(),
        "gms_target": normalize_gms_target(value.get("gms_target")),
    }
    if plan:
        return result
    result.update(
        {
            "gms_cycle_status": clean_gms_text(value.get("gms_cycle_status")),
            "gms_current_stage": clean_gms_text(value.get("gms_current_stage")),
            "gms_self_test_round": value.get("gms_self_test_round"),
            "gms_self_test_result": clean_gms_text(value.get("gms_self_test_result")),
            "gms_submission_count": value.get("gms_submission_count"),
            "gms_submission_result": clean_gms_text(value.get("gms_submission_result")),
        }
    )
    return result


def gms_scope_identity(value: dict[str, Any]) -> tuple[str, str]:
    return (
        clean_gms_text(value.get("gms_release_type")).upper(),
        normalize_gms_target(value.get("gms_target")).casefold(),
    )


def gms_release_heading(value: dict[str, Any]) -> str:
    release_type = clean_gms_text(value.get("gms_release_type")).upper()
    target = normalize_gms_target(value.get("gms_target"))
    label = release_type or "需成员确认"
    return f"GMS：{label}" + (f"（{target}）" if target else "")


def gms_release_description(value: dict[str, Any]) -> str:
    release_type = clean_gms_text(value.get("gms_release_type")).upper()
    name, description = GMS_RELEASE_TYPES.get(release_type, ("", ""))
    return f"{release_type} / {name}，{description}" if name else "需成员确认"


def gms_stage_label(value: Any) -> str:
    return {"self_test": "自测", "submission": "送测"}.get(clean_gms_text(value), "")


def gms_cycle_status_label(value: Any) -> str:
    return {"active": "进行中", "approved": "已通过", "cancelled": "已取消"}.get(
        clean_gms_text(value), "需成员确认"
    )


def gms_self_test_result_label(value: Any) -> str:
    return {
        "not_started": "尚未开始",
        "in_progress": "进行中",
        "failed": "未通过",
        "passed": "通过",
    }.get(clean_gms_text(value), "需成员确认")


def gms_submission_result_label(value: Any) -> str:
    return {
        "not_submitted": "尚未送测",
        "under_review": "审核中",
        "returned": "返回问题",
        "passed": "通过",
        "cancelled": "已取消",
    }.get(clean_gms_text(value), "需成员确认")


def validate_gms_fields(
    value: dict[str, Any],
    *,
    prefix: str,
    plan: bool = False,
) -> list[str]:
    if clean_gms_text(value.get("work_type")) != "GMS":
        return []
    errors: list[str] = []
    release_type = clean_gms_text(value.get("gms_release_type")).upper()
    target = normalize_gms_target(value.get("gms_target"))
    if release_type not in GMS_RELEASE_TYPES:
        errors.append(f"{prefix}.gms_release_type 必须是 IR、MR、SMR、ESMR、EMR 或 LR")
    if not GMS_TARGET_RE.fullmatch(target):
        errors.append(f"{prefix}.gms_target 必须是 Android 主版本，格式为 A<数字>，例如 A14")
    if plan:
        for field in set(GMS_CURRENT_FIELDS) - set(GMS_PLAN_FIELDS):
            if field in value:
                errors.append(f"{prefix}.{field} 不属于明日计划")
        return errors

    cycle_status = clean_gms_text(value.get("gms_cycle_status"))
    stage = clean_gms_text(value.get("gms_current_stage"))
    self_round = value.get("gms_self_test_round")
    self_result = clean_gms_text(value.get("gms_self_test_result"))
    submission_count = value.get("gms_submission_count")
    submission_result = clean_gms_text(value.get("gms_submission_result"))
    if cycle_status not in GMS_CYCLE_STATUSES:
        errors.append(f"{prefix}.gms_cycle_status 必须是 active、approved 或 cancelled")
    if cycle_status == "active" and stage not in GMS_CURRENT_STAGES:
        errors.append(f"{prefix}.gms_current_stage 进行中时必须是 self_test 或 submission")
    if cycle_status in {"approved", "cancelled"} and stage:
        errors.append(f"{prefix}.gms_current_stage 周期结束后必须为空")
    if not isinstance(self_round, int) or isinstance(self_round, bool) or self_round < 0:
        errors.append(f"{prefix}.gms_self_test_round 必须是非负整数")
    if self_result not in GMS_SELF_TEST_RESULTS:
        errors.append(
            f"{prefix}.gms_self_test_result 必须是 not_started、in_progress、failed 或 passed"
        )
    if not isinstance(submission_count, int) or isinstance(submission_count, bool) or submission_count < 0:
        errors.append(f"{prefix}.gms_submission_count 必须是非负整数")
    if submission_result not in GMS_SUBMISSION_RESULTS:
        errors.append(
            f"{prefix}.gms_submission_result 必须是 not_submitted、under_review、returned、passed 或 cancelled"
        )
    if isinstance(self_round, int) and not isinstance(self_round, bool):
        if self_round == 0 and self_result in {"failed", "passed"}:
            errors.append(f"{prefix} 尚无自测轮次时不能声明自测未通过或通过")
        if self_round > 0 and self_result == "not_started":
            errors.append(f"{prefix} 已有自测轮次时不能声明自测尚未开始")
    if isinstance(submission_count, int) and not isinstance(submission_count, bool):
        if submission_count == 0 and submission_result != "not_submitted":
            errors.append(f"{prefix} 尚未正式送测时送测结果必须是 not_submitted")
        if submission_count > 0 and submission_result == "not_submitted":
            errors.append(f"{prefix} 已正式送测时送测结果不能是 not_submitted")
    if stage == "submission" and self_result != "passed":
        errors.append(f"{prefix} 自测未通过时不得进入送测阶段")
    if stage == "submission" and submission_result == "returned":
        errors.append(f"{prefix} 送测返回问题后当前阶段应切回 self_test")
    if stage == "self_test" and submission_result == "under_review":
        errors.append(f"{prefix} 正在审核时当前阶段应是 submission")
    if cycle_status == "active" and submission_result == "passed":
        errors.append(f"{prefix} 送测通过后周期状态必须是 approved")
    if cycle_status == "approved":
        if self_result != "passed":
            errors.append(f"{prefix} 周期通过时最终自测结果必须是 passed")
        if not isinstance(submission_count, int) or submission_count < 1:
            errors.append(f"{prefix} 周期通过时必须至少完成一次正式送测")
        if submission_result != "passed":
            errors.append(f"{prefix} 周期通过时最新送测结果必须是 passed")
    return errors

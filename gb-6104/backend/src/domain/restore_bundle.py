"""备份恢复域：把换库前的引航开账批按备份逐批恢复查账。

会计分次补回备份，每个备份是一个恢复任务，内含若干批次。批次三样材料
（开账资料、复核结果、签核）齐备且相互对得上才恢复；缺件或对不上一律挂起，
其余批次不受影响。恢复中断后可重试，已恢复批次不会重复出现。
"""
import re

BATCH_CODE = re.compile(r"[A-Z0-9][A-Z0-9-]{0,29}")
BATCH_LIMIT = 200
MAX_TEXT = 500
MAX_NAME = 48


def _is_object(value):
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _require_object(value, label):
    if not _is_object(value):
        raise ValueError(f"{label}必须是 JSON 对象")
    return value


def _require_str(value, label, max_len):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}不能为空")
    value = value.strip()
    if len(value) > max_len:
        raise ValueError(f"{label}不能超过 {max_len} 个字符")
    return value


def _require_int(value, label):
    if type(value) is not int or isinstance(value, bool):
        raise ValueError(f"{label}必须是整数")
    return value


def _require_positive_int(value, label):
    value = _require_int(value, label)
    if value <= 0:
        raise ValueError(f"{label}必须为正整数")
    return value


def validate_bundle(bundle):
    """校验备份包结构，返回规范化后的批次映射（批次号 -> 三样材料）。

    缺件属于业务上的挂起情形而不是输入错误：缺件批次只登记批次号和缺件清单，
    交给恢复流程挂起，整个备份仍可接收。
    """
    _require_object(bundle, "备份包")
    batches = bundle.get("batches")
    if not isinstance(batches, list) or not 1 <= len(batches) <= BATCH_LIMIT:
        raise ValueError(f"备份包须包含 1–{BATCH_LIMIT} 个批次")
    normalized = {}
    for index, entry in enumerate(batches, start=1):
        label = f"第 {index} 个批次"
        _require_object(entry, label)
        code = _require_str(entry.get("batch_no"), f"{label}的批次号", 30)
        if not BATCH_CODE.fullmatch(code):
            raise ValueError(f"{label}的批次号须为 1–30 位大写字母、数字或连字符")
        if code in normalized:
            raise ValueError(f"备份内批次号重复：{code}")

        billing = entry.get("billing")  # 开账资料
        review = entry.get("review")    # 复核结果
        signoff = entry.get("signoff")  # 签核
        missing = [name for name, part in (
            ("开账资料", billing), ("复核结果", review), ("签核", signoff),
        ) if part is None]
        if missing:
            normalized[code] = {"billing": None, "review": None, "signoff": None,
                                "missing": missing, "billing_amount": None}
            continue

        _require_object(billing, f"{code} 开账资料")
        _require_object(review, f"{code} 复核结果")
        _require_object(signoff, f"{code} 签核")
        amount = _require_positive_int(billing.get("amount_cents"), f"{code} 开账资料的原金额")
        normalized_billing = dict(billing)
        normalized_billing["amount_cents"] = amount
        normalized[code] = {
            "billing": normalized_billing,
            "review": dict(review),
            "signoff": dict(signoff),
            "missing": [],
            "billing_amount": amount,
        }
    return normalized


def _compare_billing_review(code, billing, review):
    """复核结果必须回指同一批次号，且金额与开账资料的原金额一致。"""
    review_batch = review.get("batch_no")
    if not isinstance(review_batch, str) or not review_batch.strip():
        return f"{code} 复核结果缺少批次号"
    if review_batch.strip() != code:
        return f"{code} 复核结果批次号为 {review_batch.strip()}，与开账资料不一致"
    review_amount = review.get("amount_cents")
    if type(review_amount) is not int or isinstance(review_amount, bool):
        return f"{code} 复核结果缺少原金额"
    if review_amount != billing["amount_cents"]:
        return f"{code} 复核金额 {review_amount} 与开账原金额 {billing['amount_cents']} 对不上"
    return None


def _compare_signoff(code, billing, review, signoff):
    """签核必须回指本批次，签署人、意见齐备，金额口径也要对得上。"""
    sign_batch = signoff.get("batch_no")
    if not isinstance(sign_batch, str) or not sign_batch.strip():
        return f"{code} 签核缺少批次号"
    if sign_batch.strip() != code:
        # 签核串批：A 批的签核挂到 B 批上，直接判对不上。
        return f"{code} 签核批次号为 {sign_batch.strip()}，签核串批，不予恢复"
    actor = signoff.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        return f"{code} 签核缺少签署人"
    if len(actor.strip()) > MAX_NAME:
        return f"{code} 签署人不能超过 {MAX_NAME} 个字符"
    note = signoff.get("note")
    if not isinstance(note, str) or not note.strip():
        return f"{code} 签核缺少签署意见"
    if len(note.strip()) > MAX_TEXT:
        return f"{code} 签署意见不能超过 {MAX_TEXT} 个字符"
    sign_amount = signoff.get("amount_cents")
    if sign_amount is not None:
        if type(sign_amount) is not int or isinstance(sign_amount, bool):
            return f"{code} 签核金额格式不正确"
        if sign_amount != billing["amount_cents"]:
            return f"{code} 签核金额 {sign_amount} 与开账原金额 {billing['amount_cents']} 对不上"
    # 复核结论需要能和签核互相印证（两者都带结论字段时才比对）。
    review_decision = review.get("decision")
    sign_decision = signoff.get("decision")
    if review_decision is not None and sign_decision is not None and \
            str(review_decision).strip() != str(sign_decision).strip():
        return f"{code} 签核结论与复核结论不一致"
    return None


def reconcile(code, parts):
    """对单个批次做三样核对，返回 (是否通过, 原因列表)。"""
    reasons = []
    if parts["missing"]:
        reasons.append("缺少" + "、".join(parts["missing"]))
        return False, reasons
    billing, review, signoff = parts["billing"], parts["review"], parts["signoff"]
    problem = _compare_billing_review(code, billing, review)
    if problem:
        reasons.append(problem)
    problem = _compare_signoff(code, billing, review, signoff)
    if problem:
        reasons.append(problem)
    return not reasons, reasons

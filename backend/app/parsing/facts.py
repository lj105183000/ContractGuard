"""启发式事实抽取。

用途：
1. mock 模式下没有 LLM，也能跑规则引擎；
2. live 模式下作为 LLM 抽取失败/漏字段时的兜底；
3. 规则基线评测需要确定性的事实来源。

这里只做“够用”的正则提取，不追求覆盖所有合同写法；覆盖不到就返回 None，
让规则跳过，而不是猜。
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas import (
    AcceptanceFacts,
    Clause,
    ClauseType,
    ComplianceFacts,
    ContractFacts,
    DeliveryFacts,
    DisputeFacts,
    LiabilityFacts,
    PaymentFacts,
    TerminationFacts,
    WarrantyFacts,
)

CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn_to_int(text: str) -> int | None:
    if not text:
        return None
    text = text.strip()
    if text.isdigit():
        return int(text)
    total = 0
    number = 0
    for char in text:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
        elif char in CN_UNITS:
            unit = CN_UNITS[char]
            if number == 0:
                number = 1
            total += number * unit
            number = 0
        else:
            return None
    return total + number


def _first_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    # 兼容没有捕获组或捕获组未参与匹配的正则，避免拖垮整个任务。
    value = match.group(1) if match.re.groups >= 1 else match.group(0)
    if value is None:
        value = match.group(0)
    value = value.strip(" ：:，,。；;\n\t")
    return value or None


def _ratio(value: str | None) -> float | None:
    if value is None:
        return None
    number = cn_to_int(value) if re.fullmatch(r"[一二三四五六七八九十百零〇两]+", value) else None
    try:
        number = float(value) if number is None else float(number)
    except (TypeError, ValueError):
        return None
    if number > 1:
        number = number / 100.0
    if number < 0 or number > 1:
        return None
    return round(number, 6)


def _int_value(value: str | None) -> int | None:
    if value is None:
        return None
    number = cn_to_int(value)
    return number


def _money_value(value: str | None, unit: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.replace(",", "").replace("，", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if unit and ("万" in unit):
        number *= 10000
    return number


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _clause_types(clauses: list[Clause]) -> set[ClauseType]:
    return {clause.clause_type for clause in clauses}


def _days_after_event(
    text: str,
    events: str = "验收合格|验收后|交付后|收货后|收到货物后|货到后|收到发票后",
) -> int | None:
    patterns = [
        rf"(?:{events})[^。；\n]{{0,20}}?(\d{{1,3}})\s*(?:个)?(?:工作)?日(?:内)?[^。；\n]{{0,10}}(?:支付|付款|返还|退还)",
        r"(?:账期|付款期限|付款时间)[^。；\n]{0,15}?(\d{1,3})\s*(?:个)?(?:工作)?日",
        r"(\d{1,3})\s*(?:个)?(?:工作)?日(?:内)?(?:支付|付款)",
    ]
    for pattern in patterns:
        value = _first_group(pattern, text)
        if value:
            return _int_value(value)
    return None


def _penalty_rate(text: str) -> float | None:
    match = re.search(r"(?:日|每日|按日)?万分之([一二三四五六七八九十0-9]+)", text)
    if match:
        value = cn_to_int(match.group(1))
        if value is not None:
            return round(value / 10000.0, 8)
    for pattern in [
        r"(?:每日|按日|日)[^。；\n]{0,12}?([0-9]+(?:\.[0-9]+)?)\s*%",
        r"每逾期一日[^。；\n]{0,12}?([0-9]+(?:\.[0-9]+)?)\s*%",
        r"逾期[^。；\n]{0,25}?([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:/日|每日|按日)",
    ]:
        match = re.search(pattern, text)
        if match:
            try:
                return round(float(match.group(1)) / 100.0, 8)
            except ValueError:
                continue
    return None


def extract_heuristic_facts(text: str, clauses: list[Clause]) -> ContractFacts:
    buyer = _first_group(r"(?:甲方|买方|采购方|采购人)[：:]\s*([^\n，,（(；;]{2,50})", text)
    supplier = _first_group(r"(?:乙方|卖方|供方|供应商|出卖人)[：:]\s*([^\n，,（(；;]{2,50})", text)

    value_match = re.search(
        r"(?:合同总价|合同金额|总价款|总金额|采购金额|价款合计)[^0-9]{0,15}"
        r"(?:人民币|RMB|¥)?\s*([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(万元|万|元|美元|USD)?",
        text,
        flags=re.IGNORECASE,
    )
    contract_value = None
    currency = "USD" if _has(text, r"美元|USD") else "CNY"
    if value_match:
        contract_value = _money_value(value_match.group(1), value_match.group(2))
        if value_match.group(2) and "美元" in value_match.group(2).upper():
            currency = "USD"
    tax_included = None
    if "不含税" in text:
        tax_included = False
    elif "含税" in text:
        tax_included = True

    prepayment_text = _first_group(
        r"预付款[^。；\n]{0,45}?([0-9]+(?:\.[0-9]+)?)\s*%", text
    ) or _first_group(
        r"(?:支付|预付)[^。；\n]{0,35}?([0-9]+(?:\.[0-9]+)?)\s*%[^。；\n]{0,8}预付款", text
    ) or _first_group(
        r"预付款[^。；\n]{0,30}?百分之([一二三四五六七八九十百零〇\d]+)", text
    )
    prepayment_ratio = _ratio(prepayment_text)
    if prepayment_ratio is None and _has(text, r"无预付款|不需要预付款|货到付款|款到发货"):
        prepayment_ratio = 0.0

    retention_text = _first_group(
        r"(?:质保金|质量保证金)[^。；\n]{0,30}?([0-9]+(?:\.[0-9]+)?)\s*%", text
    )
    retention_ratio = _ratio(retention_text)
    retention_days_text = _first_group(
        r"(?:质保金|质量保证金)[^。；\n]{0,45}?(\d{1,3})\s*(?:个)?(?:工作)?日(?:内)?(?:返还|退还|支付)",
        text,
    ) or _first_group(
        r"(?:质保期满|质保期届满)[^。；\n]{0,20}?(\d{1,3})\s*(?:个)?(?:工作)?日", text
    )
    retention_release_days = _int_value(retention_days_text)

    payment_days = _days_after_event(text)
    full_payment_on_delivery = _has(
        text,
        r"(?:交货|交付|发货|货到)[^。；\n]{0,25}?(?:支付全部|支付全款|一次性支付|付清|结清)|款到发货|先款后货",
    )
    payment_after_acceptance = None
    if _has(text, r"验收(?:合格)?后[^。；\n]{0,35}?(?:支付|付款)"):
        payment_after_acceptance = True
    elif _has(
        text, r"(?:交货|交付|收货|发货|货到|收到货物)后[^。；\n]{0,25}?(?:支付|付款)"
    ):
        payment_after_acceptance = False
    if payment_after_acceptance is True:
        # “交付并验收合格后付款”不是“交付即全款”
        full_payment_on_delivery = False

    delivery_date = _first_group(
        r"(?:交货|交付|送货)(?:时间|日期|期限)[：:为]?\s*([^\n。；]{2,60})", text
    ) or _first_group(r"(?:于|在)\s*([^。；\n]{0,25}?)(?:前|内)\s*(?:交货|交付|送货)", text)
    delivery_days_text = _first_group(
        r"(?:合同生效|签订|签约)(?:之|后)?[^。；\n]{0,15}?(\d{1,3})\s*(?:个)?(?:工作)?日(?:内)?\s*(?:交货|交付|送货|完成交付)",
        text,
    ) or _first_group(r"(\d{1,3})\s*(?:个)?(?:工作)?日(?:内)?\s*(?:交货|交付|送货)", text)
    delivery_place = _first_group(
        r"(?:交货|交付|送货)地点[：:为]?\s*([^\n。；]{2,60})", text
    ) or _first_group(r"(?:交货|交付|送货)(?:至|到)\s*([^\n。；]{2,50})", text)
    delivery_method = _first_group(
        r"(?:运输方式|交货方式|交付方式)[：:为]?\s*([^\n。；]{2,60})", text
    )
    delivery_vague = _has(text, r"尽快|另行通知|视情况|适时|待定")
    if delivery_place and _has(delivery_place, r"待定|另行通知"):
        delivery_vague = True
        delivery_place = None

    clause_types = _clause_types(clauses)
    acceptance_present = ClauseType.ACCEPTANCE in clause_types or "验收" in text
    acceptance_days_text = _first_group(
        r"(?:验收|检验)(?:期限|期)[^。；\n]{0,15}?(\d{1,3})\s*(?:个)?(?:工作)?日", text
    ) or _first_group(
        r"(\d{1,3})\s*(?:个)?(?:工作)?日(?:内)?[^。；\n]{0,10}(?:完成)?(?:验收|检验)", text
    )
    acceptance_period = _int_value(acceptance_days_text)
    deemed_acceptance = _has(
        text, r"视为(?:验收)?合格|未(?:提出|及时提出)(?:质量)?异议[^。；\n]{0,12}视为|逾期未[^。；\n]{0,12}视为"
    )
    acceptance_standard = _first_group(
        r"(?:验收标准|质量标准)[：:为]?\s*([^\n。；]{2,100})", text
    )

    warranty_months = None
    month_text = _first_group(
        r"(?:质保期|保修期|质量保证期)[^。；\n]{0,25}?(\d{1,3})\s*个月", text
    )
    if month_text:
        warranty_months = _int_value(month_text)
    else:
        year_text = _first_group(
            r"(?:质保期|保修期|质量保证期)[^。；\n]{0,25}?([一二三四五六七八九十两\d]+)\s*年", text
        )
        if year_text:
            years = _int_value(year_text)
            warranty_months = years * 12 if years is not None else None

    penalty_rate = _penalty_rate(text)
    cap_text = _first_group(
        r"(?:违约金|赔偿|责任)[^。；\n]{0,35}?(?:不超过|上限为|最高为|以)[^。；\n]{0,12}?([0-9]+(?:\.[0-9]+)?)\s*%",
        text,
    )
    penalty_cap = _ratio(cap_text)
    unlimited_liability = None
    if _has(text, r"无限责任|不受(?:责任)?上限限制|不设(?:责任)?上限|承担一切责任"):
        unlimited_liability = True
    elif _has(text, r"责任上限|赔偿上限|累计责任不超过|不超过合同总价"):
        unlimited_liability = False
    indirect_damages = None
    if _has(text, r"间接损失|可得利益|预期利益"):
        indirect_damages = True
    if _has(text, r"不包括间接损失|排除间接损失|不承担间接损失"):
        indirect_damages = False
    quality_remedy = None
    if _has(
        text,
        r"退换|退货|换货|修理|维修|修复|重作|减少价款|质量索赔|质量异议|赔偿损失",
    ):
        quality_remedy = True
    elif "质量" in text or ClauseType.ACCEPTANCE in clause_types or ClauseType.LIABILITY in clause_types:
        quality_remedy = False

    buyer_right = _has(
        text, r"(?:买方|甲方|采购方)[^。；\n]{0,35}?(?:有权|可以|可)[^。；\n]{0,20}?(?:解除|终止)"
    )
    supplier_right = _has(
        text, r"(?:卖方|乙方|供方|供应商)[^。；\n]{0,35}?(?:有权|可以|可)[^。；\n]{0,20}?(?:解除|终止)"
    )
    buyer_termination_right = True if buyer_right else (False if supplier_right else None)
    one_sided = True if supplier_right and not buyer_right else (False if buyer_right else None)
    auto_renewal = True if _has(text, r"自动续约|自动续期|自动顺延") else None
    notice_days = _int_value(
        _first_group(r"提前\s*(\d{1,3})\s*(?:个)?(?:工作)?日[^。；\n]{0,12}(?:通知|书面)", text)
    )

    court_text = _first_group(
        r"((?:由|向|提交)[^。；\n]{0,35}?(?:人民法院|法院)(?:管辖|提起诉讼)?)", text
    ) or _first_group(r"([^。；\n]{0,35}(?:人民法院|法院)管辖)", text)
    arbitration_text = _first_group(
        r"((?:提交|由)[^。；\n]{0,35}?仲裁委员会[^。；\n]{0,30})", text
    )
    if court_text and arbitration_text:
        both = True
    elif court_text or arbitration_text:
        both = False
    else:
        both = None
    unfavorable_venue = None
    if court_text:
        if _has(court_text, r"供方所在地|出卖人所在地|乙方所在地|供应商所在地|卖方所在地"):
            unfavorable_venue = True
        elif _has(court_text, r"买方所在地|甲方所在地|采购方所在地"):
            unfavorable_venue = False
    clause_present = ClauseType.DISPUTE in clause_types or _has(text, r"争议|管辖|仲裁|诉讼|法院")

    confidentiality_present = ClauseType.CONFIDENTIALITY in clause_types or "保密" in text
    confidentiality_mutual = None
    if "保密" in text:
        if _has(text, r"双方[^。；\n]{0,40}保密|相互[^。；\n]{0,10}保密|甲乙双方[^。；\n]{0,40}保密"):
            confidentiality_mutual = True
        elif _has(text, r"(?:乙方|供方|供应商)[^。；\n]{0,20}(?:承担)?保密"):
            confidentiality_mutual = False
    ip_ownership = _first_group(
        r"((?:知识产权|著作权|专利|商标|软件)[^。；\n]{0,80}?(?:归|属于)[^。；\n]{0,50})",
        text,
    )
    attachments_present = _has(text, r"附件|技术规格书|规格书|图纸|技术协议")
    effective_condition = _first_group(
        r"((?:自|在)[^。；\n]{0,40}?(?:签字|盖章|签章)[^。；\n]{0,25}?生效|(?:生效条件|合同生效条件)[^。；\n]{0,60})",
        text,
    )
    signatory_clear = _has(text, r"签字|盖章|签章|授权代表|法定代表人")

    subject_matter = _first_group(
        r"(?:产品名称|货物名称|标的名称|品名|采购内容)[：:]\s*([^\n。；]{2,80})", text
    )
    if not subject_matter:
        candidate = _first_group(r"(?:采购|购买)(?:的)?([^。；\n]{2,80})", text)
        if candidate:
            candidate = candidate.split("，")[0].strip()
            if candidate and "合同" not in candidate:
                subject_matter = candidate

    return ContractFacts(
        buyer=buyer,
        supplier=supplier,
        contract_value=contract_value,
        currency=currency,
        tax_included=tax_included,
        subject_matter=subject_matter,
        payment=PaymentFacts(
            prepayment_ratio=prepayment_ratio,
            payment_days=payment_days,
            retention_ratio=retention_ratio,
            retention_release_days=retention_release_days,
            payment_after_acceptance=payment_after_acceptance,
            full_payment_on_delivery=full_payment_on_delivery,
        ),
        delivery=DeliveryFacts(
            date_text=delivery_date,
            days=_int_value(delivery_days_text),
            place=delivery_place,
            method=delivery_method,
            vague=delivery_vague,
        ),
        acceptance=AcceptanceFacts(
            present=acceptance_present,
            period_days=acceptance_period,
            standard=acceptance_standard,
            deemed_acceptance=deemed_acceptance,
        ),
        warranty=WarrantyFacts(
            present=ClauseType.WARRANTY in clause_types or "质保" in text or "保修" in text,
            months=warranty_months,
        ),
        liability=LiabilityFacts(
            penalty_daily_rate=penalty_rate,
            penalty_cap_ratio=penalty_cap,
            unlimited_liability=unlimited_liability,
            indirect_damages=indirect_damages,
            quality_remedy=quality_remedy,
        ),
        termination=TerminationFacts(
            buyer_right=buyer_termination_right,
            one_sided=one_sided,
            notice_days=notice_days,
            auto_renewal=auto_renewal,
        ),
        dispute=DisputeFacts(
            court=court_text,
            arbitration=arbitration_text,
            both_litigation_and_arbitration=both,
            unfavorable_venue=unfavorable_venue,
            clause_present=clause_present,
        ),
        compliance=ComplianceFacts(
            confidentiality_present=confidentiality_present,
            confidentiality_mutual=confidentiality_mutual,
            ip_ownership=ip_ownership,
            attachments_present=attachments_present,
            effective_condition=effective_condition,
            signatory_clear=signatory_clear,
        ),
    )


def _merge_dict(primary: Any, fallback: Any) -> Any:
    if isinstance(primary, dict) and isinstance(fallback, dict):
        return {
            key: _merge_dict(primary.get(key), fallback.get(key))
            for key in set(primary) | set(fallback)
        }
    if primary is None or primary == [] or primary == "":
        return fallback
    return primary


def merge_facts(primary: ContractFacts, fallback: ContractFacts) -> ContractFacts:
    """LLM 抽取优先，启发式只补空缺。"""
    merged = _merge_dict(primary.model_dump(), fallback.model_dump())
    return ContractFacts.model_validate(merged)

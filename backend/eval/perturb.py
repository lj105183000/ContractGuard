"""Deterministic contract-text perturbations for robustness checks."""

from __future__ import annotations

import random
import re
from dataclasses import replace

from app.parsing.clause_splitter import split_clauses
from app.parsing.contract_parser import ParsedContract


def rename_parties(text: str) -> str:
    names = [
        name.strip()
        for name in re.findall(r"(?:甲方|乙方)[：:]\s*([^\n，,；;]{2,40})", text)
    ]
    for index, name in enumerate(dict.fromkeys(names), start=1):
        if name:
            text = text.replace(name, f"某方公司{index}")
    return text


def normalize_amount_formats(text: str) -> str:
    def yuan(match: re.Match) -> str:
        amount = round(float(match.group(1)) * 10000)
        return f"{amount:,} 元"

    text = re.sub(r"(\d+(?:\.\d+)?)\s*万元", yuan, text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"\1 %", text)
    return text


def add_ocr_noise(text: str, rate: float = 0.02, seed: int = 7) -> str:
    rng = random.Random(seed)
    replacements = "一二三四五六七八九十"
    lines = []
    for line in text.split("\n"):
        if not line.strip() or line.strip().startswith("第"):
            lines.append(line)
            continue
        chars = list(line)
        candidates = [i for i, char in enumerate(chars) if "一" <= char <= "鿿"]
        for index in candidates:
            if rng.random() < rate:
                chars[index] = rng.choice(replacements)
        lines.append("".join(chars))
    return "\n".join(lines)


def reorder_clauses(parsed: ParsedContract) -> ParsedContract:
    clauses = split_clauses(parsed)
    if len(clauses) < 3:
        return parsed
    preamble = parsed.text[: clauses[0].start]
    tail = parsed.text[clauses[-1].end :]
    ordered = [clauses[0], *reversed(clauses[1:])]
    text = preamble + "\n\n".join(clause.text for clause in ordered) + tail
    return replace(parsed, text=text)


PERTURBATIONS = {
    "party_names": rename_parties,
    "amount_formats": normalize_amount_formats,
    "ocr_noise": add_ocr_noise,
}


def apply_text_perturbation(parsed: ParsedContract, kind: str) -> ParsedContract:
    if kind == "reordered_clauses":
        return reorder_clauses(parsed)
    try:
        transform = PERTURBATIONS[kind]
    except KeyError as exc:
        raise ValueError(f"unknown perturbation: {kind}") from exc
    return replace(parsed, text=transform(parsed.text))

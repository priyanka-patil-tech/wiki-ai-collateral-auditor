"""
grounding_agent.py — LangChain/LLM audit & hallucination classifier.

Implements the classification logic of Subsystem 3B (Agentic Academic
Grounding Auditor), System Design Document §4.3.

Flow, per (claim, reference) pair:
    1. Call `tool_doi_lookup` / `tool_isbn_lookup` (src/verification/
       academic_tools.py) to resolve the citation against academic indices.
    2. If no record is found -> "FABRICATED_CITATION" (a true-positive
       revert: the citation never existed).
    3. If a record is found, ask the LLM evaluator whether the retrieved
       abstract supports the encyclopedic claim:
         - SUPPORTED -> "GROUNDED_FACT"   (Type I Error / collateral damage)
         - REFUTED   -> "MISATTRIBUTED_FACT" (misleading citation, true positive)
         - otherwise -> "AMBIGUOUS"

This mirrors the reference implementation in the System Design Document,
§4.3, kept here as the canonical classification function so
src/econometrics/panel_builder.py has a single source of truth for
`audit_verifications.classification`.
"""

from __future__ import annotations

import json
from typing import Literal

from langchain_community.chat_models import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

Classification = Literal[
    "FABRICATED_CITATION", "GROUNDED_FACT", "MISATTRIBUTED_FACT", "AMBIGUOUS"
]


class GroundingEvaluation(BaseModel):
    classification: str = Field(
        description="Must be GROUNDED_FACT, MISATTRIBUTED, or FABRICATED"
    )
    confidence_score: float = Field(description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(description="Brief explanation of the verdict")


class CitationAuditor:
    """Agentic workflow to evaluate the grounding of Wikipedia claims against academic abstracts."""

    def __init__(self, model_name: str = "llama3"):
        self.llm = ChatOllama(model=model_name, temperature=0.0)
        self.parser = JsonOutputParser(pydantic_object=GroundingEvaluation)

        self.prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a strict academic auditor. Evaluate if the provided encyclopedic claim "
                    "is supported by the academic abstract. Output JSON with strictly matching the schema: \n"
                    "{format_instructions}",
                ),
                (
                    "human",
                    "Claim deleted from Wikipedia: {claim}\n"
                    "Abstract of cited paper: {abstract}",
                ),
            ]
        )

        self.chain = self.prompt | self.llm | self.parser

    def evaluate_claim(self, claim_text: str, paper_abstract: str) -> dict:
        """Runs the LLM evaluation to determine if the deletion was a Type I error."""
        if not paper_abstract:
            return {
                "classification": "FABRICATED",
                "confidence_score": 1.0,
                "reasoning": "No abstract found; citation could not be verified in academic indices.",
            }

        try:
            result = self.chain.invoke(
                {
                    "claim": claim_text,
                    "abstract": paper_abstract,
                    "format_instructions": self.parser.get_format_instructions(),
                }
            )
            return result
        except Exception as e:
            return {"error": str(e), "classification": "ERROR"}


def classify_citation_record(record: dict) -> Classification:
    """Classify a resolved (or unresolved) citation against its claim.

    See System Design Document §4.3 for the reference pseudocode this
    function implements.
    """
    if not record.get("academic_record_found"):
        return "FABRICATED_CITATION"

    abstract = record.get("abstract_text")
    claim = record.get("encyclopedic_claim")

    grounding_score = evaluate_grounding(claim=claim, evidence=abstract)

    verdict = grounding_score.get("verdict")
    if verdict == "SUPPORTED":
        return "GROUNDED_FACT"
    if verdict == "REFUTED":
        return "MISATTRIBUTED_FACT"
    return "AMBIGUOUS"


def evaluate_grounding(claim: str | None, evidence: str | None) -> dict:
    """LLM structured-prompt call: does `evidence` support `claim`?"""
    auditor = CitationAuditor()
    if not evidence:
        return {"verdict": "REFUTED", "confidence": 0.0, "reason": "No evidence provided."}

    result = auditor.evaluate_claim(claim or "", evidence)
    classification = result.get("classification", "ERROR")

    if classification in {"GROUNDED_FACT", "SUPPORTED"}:
        verdict = "SUPPORTED"
    elif classification in {"MISATTRIBUTED", "REFUTED"}:
        verdict = "REFUTED"
    elif classification == "FABRICATED":
        verdict = "REFUTED"
    else:
        verdict = "UNCLEAR"

    return {
        "verdict": verdict,
        "confidence": float(result.get("confidence_score", 0.0)),
        "reason": result.get("reasoning", json.dumps(result)),
    }

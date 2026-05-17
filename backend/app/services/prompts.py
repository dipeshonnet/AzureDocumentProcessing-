from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import ROOT_DIR


PROMPTS_DIR = ROOT_DIR / "prompts"


class PromptTemplateError(Exception):
    """Raised when a versioned prompt template cannot be loaded or rendered."""


@dataclass(frozen=True)
class PromptTemplate:
    prompt_name: str
    prompt_version: str
    input_schema: str
    output_schema: str
    safety_constraints: list[str]
    body: str
    path: Path

    def render(self, values: dict[str, Any]) -> str:
        rendered = self.body
        for key, value in values.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", stringify_prompt_value(value))
        return rendered

    def identity(self) -> dict[str, Any]:
        return {
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
        }

    def metadata(self) -> dict[str, Any]:
        return {
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "safety_constraints": self.safety_constraints,
        }


@lru_cache
def load_prompt(prompt_name: str, prompt_version: str = "v1") -> PromptTemplate:
    path = PROMPTS_DIR / f"{prompt_name}.{prompt_version}.md"
    if not path.exists():
        raise PromptTemplateError(f"Prompt template not found: {path}")

    content = path.read_text(encoding="utf-8")
    metadata, body = parse_prompt_template(content, path=path)
    if metadata.get("prompt_name") != prompt_name:
        raise PromptTemplateError(f"Prompt name mismatch in {path}")
    if metadata.get("prompt_version") != prompt_version:
        raise PromptTemplateError(f"Prompt version mismatch in {path}")

    safety_constraints = metadata.get("safety_constraints") or []
    if not isinstance(safety_constraints, list) or not safety_constraints:
        raise PromptTemplateError(f"Prompt safety constraints are required in {path}")

    return PromptTemplate(
        prompt_name=str(metadata["prompt_name"]),
        prompt_version=str(metadata["prompt_version"]),
        input_schema=str(metadata["input_schema"]),
        output_schema=str(metadata["output_schema"]),
        safety_constraints=[str(item) for item in safety_constraints],
        body=body.strip(),
        path=path,
    )


def prompt_identity(prompt_name: str, prompt_version: str = "v1") -> dict[str, Any]:
    return load_prompt(prompt_name, prompt_version).identity()


def parse_prompt_template(content: str, *, path: Path) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        raise PromptTemplateError(f"Prompt template is missing YAML front matter: {path}")
    try:
        _prefix, front_matter, body = content.split("---", 2)
    except ValueError as exc:
        raise PromptTemplateError(f"Prompt template front matter is invalid: {path}") from exc
    metadata = yaml.safe_load(front_matter) or {}
    required = {
        "prompt_name",
        "prompt_version",
        "input_schema",
        "output_schema",
        "safety_constraints",
    }
    missing = sorted(required.difference(metadata))
    if missing:
        raise PromptTemplateError(f"Prompt metadata missing {missing}: {path}")
    return metadata, body


def stringify_prompt_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, list):
        if not value:
            return "- None detected"
        return "\n".join(f"- {item}" for item in value)
    if isinstance(value, tuple):
        return stringify_prompt_value(list(value))
    return str(value)

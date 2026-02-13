"""Step 4: Semantic parsing — LLM extracts per-function features (batched by file)."""

import logging
from typing import List, Optional, Dict, Any

from api.semantic_tree.models import CodebaseSnapshot, CodeEntity, SemanticFeature, FileInfo
from api.semantic_tree.llm.prompt_loader import load_prompt, format_prompt, parse_solution_json
from api.semantic_tree.llm.completion import complete

logger = logging.getLogger(__name__)

# Max entities per LLM call to stay within context
BATCH_SIZE = 20


def _format_file_context(file_info: FileInfo) -> str:
    """Format one file's entities for the prompt."""
    lines = [f"### File: {file_info.fpath}", ""]
    for e in file_info.entities:
        lines.append(f"#### {e.name}")
        if e.signature:
            lines.append(f"Signature: {e.signature}")
        if e.docstring:
            lines.append(f"Docstring: {e.docstring}")
        if e.body_text:
            body = e.body_text[:1500] + ("..." if len(e.body_text) > 1500 else "")
            lines.append(f"```\n{body}\n```")
        lines.append("")
    return "\n".join(lines)


def run_semantic_parsing(
    snapshot: CodebaseSnapshot,
    repo_name: str = "",
    repo_info: str = "",
    provider: str = "openai",
    model: Optional[str] = None,
) -> List[SemanticFeature]:
    """
    Run semantic parsing per file (batched). Uses prompts/semantic_parsing.txt.
    Returns one SemanticFeature per entity; merges batches.
    """
    template = load_prompt("semantic_parsing")
    all_features: List[SemanticFeature] = []

    for file_info in snapshot.files:
        if not file_info.entities:
            continue

        # Batch entities within the file
        for i in range(0, len(file_info.entities), BATCH_SIZE):
            batch = file_info.entities[i : i + BATCH_SIZE]
            file_slice = FileInfo(fpath=file_info.fpath, language=file_info.language, entities=batch, imports=[])
            context = _format_file_context(file_slice)

            prompt = format_prompt(template, repo_name=repo_name, repo_info=repo_info)
            prompt += "\n\n" + context

            response = complete(prompt=prompt, provider=provider, model=model)
            data = parse_solution_json(response)

            if not isinstance(data, dict):
                logger.warning("semantic_parsing response not a dict: %s", type(data))
                continue

            for entity_name, features in data.items():
                if isinstance(features, list):
                    feat_list = [str(f) for f in features]
                else:
                    feat_list = []
                all_features.append(SemanticFeature(entity_name=entity_name.strip(), features=feat_list))

    return all_features

"""Step 5: Hierarchy construction — LLM assigns top-level groups to 3-level paths."""

import logging
from typing import List, Optional, Dict, Any

from api.semantic_tree.models import FunctionalArea, HierarchyMapping
from api.semantic_tree.llm.prompt_loader import load_prompt, parse_solution_json
from api.semantic_tree.llm.completion import complete

logger = logging.getLogger(__name__)


def _parsed_folder_tree_from_entity_groups(group_to_entities: Dict[str, List[str]]) -> str:
    """Format for prompt: each top-level group (e.g. file path) with its entity names."""
    lines = []
    for group, entities in group_to_entities.items():
        lines.append(f'  "{group}": {entities}')
    return "{\n" + ",\n".join(lines) + "\n}"


def run_hierarchical_construction(
    functional_areas: List[FunctionalArea],
    group_to_entities: Dict[str, List[str]],
    provider: str = "openai",
    model: Optional[str] = None,
) -> List[HierarchyMapping]:
    """
    Call LLM to map top-level groups to 3-level paths. Uses prompts/hierarchical_construction.txt.
    group_to_entities: e.g. {"path/to/file.py": ["func1", "class1"], ...}.
    Returns list of HierarchyMapping(path, entity_groups).
    """
    area_names = [a.name for a in functional_areas]
    areas_str = "\n".join(f"- {a}" for a in area_names)
    tree_str = _parsed_folder_tree_from_entity_groups(group_to_entities)

    template = load_prompt("hierarchical_construction")
    prompt = template + f"""

### Functional areas
<functional_areas>
{areas_str}
</functional_areas>

### Parsed folder tree (top-level groups and their entities)
<parsed_folder_tree>
{tree_str}
</parsed_folder_tree>
"""

    response = complete(prompt=prompt, provider=provider, model=model)
    data = parse_solution_json(response)

    if not isinstance(data, dict):
        raise ValueError(f"hierarchical_construction expected dict, got {type(data)}")

    return [
        HierarchyMapping(path=path.strip(), entity_groups=[str(g) for g in groups])
        for path, groups in data.items()
    ]

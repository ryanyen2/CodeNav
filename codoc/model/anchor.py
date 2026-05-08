from pydantic import BaseModel, model_validator


class Anchor(BaseModel):
    file: str  # repo-relative posix path
    symbol_path: str | None = None  # format: "pkg/module.py::ClassName.method_name"
    ts_query: str | None = None  # tree-sitter S-expression query string
    occurrence_index: int = 0  # disambiguates when ts_query matches multiple regions

    @model_validator(mode="after")
    def _require_at_least_one_locator(self) -> "Anchor":
        if self.symbol_path is None and self.ts_query is None:
            raise ValueError("At least one of symbol_path or ts_query must be set")
        return self

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field


def _not_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


# A string the model must actually fill in. The check runs in Pydantic, not in the JSON schema
# sent to Gemini, which only supports a subset of JSON Schema keywords.
Text = Annotated[str, AfterValidator(_not_blank)]


# What the Planner returns. The field descriptions are sent to the model as part of the schema.
# (A docstring would be sent too, so this note is a comment instead.)
class ImplementationPlan(BaseModel):
    summary: Text = Field(description="One sentence describing the change.")
    subtasks: list[Text] = Field(
        min_length=1,
        max_length=10,
        description="Small, concrete steps in the order a developer would do them, including tests.",
    )
    acceptance_criteria: list[Text] = Field(
        min_length=1,
        max_length=10,
        description="Checks that prove the task is done, preferably ones an automated test can verify.",
    )
    constraints: list[Text] = Field(
        max_length=10,
        description="What the change must not break or alter. Use an empty list if there are none.",
    )
    needs_research: bool = Field(
        description="True only if the change depends on library or API details worth looking up in documentation."
    )


class RunRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    user_request: str = Field(min_length=1, max_length=4000)


class RunResponse(BaseModel):
    task_id: str
    status: Literal["planned"]
    plan: ImplementationPlan
    # Which model produced the plan, so results can be compared across models later.
    model: str
